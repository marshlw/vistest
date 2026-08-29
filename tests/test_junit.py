# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""JUnit XML и решение для CI.

Зачем это вообще нужно: код возврата есть только у pytest. Прогон подключённого
проекта в режиме наблюдения (их тесты уже отработали, наш вердикт пересчитан
отдельно) и прогон, запущенный кнопкой в интерфейсе, никакого pytest не имеют —
их результат для конвейера не существовал вовсе.

Проверяется прежде всего то, что легко сделать неправильно: разные вердикты не
должны схлопываться в один, а разобранное падение не должно ронять сборку
второй раз.
"""

from __future__ import annotations

from xml.etree import ElementTree as ET

import pytest

from vistest.report.junit import gate, normalize, render_junit


def _parse(xml: str) -> ET.Element:
    return ET.fromstring(xml)


def _cases(xml: str) -> dict[str, ET.Element]:
    return {c.get("name"): c for c in _parse(xml).iter("testcase")}


COMPARISONS = [
    {"name": "shop.example/login.png", "verdict": "fail",
     "metrics": {"max_severity": 44.0, "changed_area_pct": 0.4,
                 "ssim_global": 0.97},
     "regions": [{"x": 1, "y": 2, "w": 10, "h": 8, "kind": "content",
                  "severity": 44.0, "selector": ".header"}]},
    {"name": "shop.example/cart.png", "verdict": "pass",
     "metrics": {"max_severity": 0.0, "ssim_global": 1.0}},
    {"name": "checkout.png", "verdict": "error", "error": "page did not answer"},
    {"name": "profile.png", "verdict": "new_baseline"},
]


# --------------------------------------------------------------------------- #
def test_it_is_valid_xml_with_the_expected_totals():
    root = _parse(render_junit(COMPARISONS, suite="demo"))
    assert root.get("tests") == "4"
    assert root.get("failures") == "1"
    assert root.get("errors") == "1"
    assert root.get("skipped") == "1"


def test_each_verdict_maps_to_its_own_thing():
    """Смешать «упало» и «не проверилось» — значит потерять и то, и другое."""
    cases = _cases(render_junit(COMPARISONS))

    assert cases["login.png"].find("failure") is not None
    assert cases["cart.png"].find("failure") is None
    # Ошибка съёмки — сломанная проверка, а не найденный дефект.
    assert cases["checkout.png"].find("error") is not None
    assert cases["checkout.png"].find("failure") is None
    # Новый эталон сравнивать было не с чем: записать это «прошло» значило бы
    # заявить проверку, которой не происходило.
    assert cases["profile.png"].find("skipped") is not None


def test_the_failure_text_says_what_a_person_needs():
    body = _cases(render_junit(COMPARISONS))["login.png"].find("failure").text
    assert "severity 44.0" in body
    assert "SSIM" in body
    assert ".header" in body          # селектор — самое полезное в этом тексте


def test_the_project_prefix_becomes_a_classname():
    """`shop.example/login.png` — это дерево в интерфейсе сборки, а не простыня."""
    cases = _cases(render_junit(COMPARISONS))
    assert cases["login.png"].get("classname") == "shop.example"
    assert cases["checkout.png"].get("classname") == "visual"


def test_an_accepted_failure_does_not_fail_the_build_twice():
    """Человек посмотрел и принял как новую норму — решение уже принято.

    Красный CI после этого просто шум. Пересобрать артефакт после разбора и
    получить зелёный — ровно то, чего от такой выгрузки ждут.
    """
    reviewed = [{**COMPARISONS[0], "review": "approved", "reviewed_by": "anna",
                 "reviewed_at": "2026-01-01T10:00:00"}]

    honored = _parse(render_junit(reviewed))
    assert honored.get("failures") == "0"
    out = next(honored.iter("testcase")).find("system-out")
    assert out is not None and "anna" in out.text

    # А в момент самого прогона разбирать ещё нечего.
    fresh = _parse(render_junit(reviewed, honor_review=False))
    assert fresh.get("failures") == "1"


def test_control_characters_do_not_break_the_whole_file():
    """Один такой байт из чужого трейсбека делает отчёт нечитаемым целиком.

    CI показывает не «упал тест», а «отчёт битый», и причина теряется вся.
    """
    xml = render_junit([{"name": "a.png", "verdict": "error",
                         "error": "boom \x00\x08 traceback \x1b[31m"}])
    root = _parse(xml)                      # не должно бросить
    assert root.get("errors") == "1"


def test_quotes_in_a_snapshot_name_survive():
    xml = render_junit([{"name": 'search "all".png', "verdict": "pass"}])
    assert _parse(xml).find(".//testcase").get("name") == 'search "all".png'


def test_both_shapes_of_a_comparison_are_understood():
    """Из БД приходят плоские колонки, из run.json — вложенный `metrics`.

    Разбирать это в двух местах — гарантированный способ получить два разных
    отчёта об одном прогоне.
    """
    from_db = normalize({"snapshot_name": "a.png", "verdict": "fail",
                         "max_severity": 30.0, "ssim": 0.9})
    from_file = normalize({"name": "a.png", "verdict": "fail",
                           "metrics": {"max_severity": 30.0, "ssim_global": 0.9}})
    assert from_db["name"] == from_file["name"] == "a.png"
    assert from_db["max_severity"] == from_file["max_severity"] == 30.0
    assert from_db["ssim"] == from_file["ssim"] == 0.9


def test_an_empty_run_is_still_valid_xml():
    root = _parse(render_junit([]))
    assert root.get("tests") == "0"


# --------------------------------------------------------------------------- #
#  Решение для конвейера
# --------------------------------------------------------------------------- #
def test_a_failure_stops_the_pipeline():
    verdict = gate(COMPARISONS)
    assert verdict["ok"] is False
    names = [b["name"] for b in verdict["blocking"]]
    assert "shop.example/login.png" in names


def test_a_capture_error_stops_it_too():
    """Проверки не было. Делать вид, что всё хорошо, — единственное, чего
    воротам делать нельзя."""
    verdict = gate([{"name": "a.png", "verdict": "error", "error": "timeout"}])
    assert verdict["ok"] is False
    assert verdict["blocking"][0]["why"] == "timeout"


def test_an_accepted_failure_lets_it_through():
    verdict = gate([{"name": "a.png", "verdict": "fail", "review": "approved",
                     "metrics": {"max_severity": 40.0}}])
    assert verdict["ok"] is True
    assert verdict["accepted_as_normal"] == ["a.png"]
    assert "accepted as normal" in verdict["reason"]


def test_new_baselines_pass_by_default_and_can_be_made_to_block():
    fresh = [{"name": "a.png", "verdict": "new_baseline"}]
    assert gate(fresh)["ok"] is True
    assert gate(fresh, allow_new=False)["ok"] is False


def test_a_run_with_nothing_compared_is_not_a_success():
    """«0 из 0 упало» — это не зелёный прогон, это прогон, которого не было."""
    verdict = gate([])
    assert "nothing was compared" in verdict["reason"]


def test_the_reason_names_the_first_offender():
    verdict = gate(COMPARISONS)
    assert "shop.example/login.png" in verdict["reason"] or \
           "checkout.png" in verdict["reason"]
    assert "of 4" in verdict["reason"]


# --------------------------------------------------------------------------- #
#  Файл рядом с прогоном
# --------------------------------------------------------------------------- #
def test_the_runner_writes_junit_next_to_run_json(tmp_path, monkeypatch):
    """Раннер работает офлайн, и артефакт для CI обязан появляться без сервиса.

    Иначе выгрузка есть только там, где поднят сервис, — то есть ровно не в CI.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VISTEST_ROOT", str(tmp_path / ".vistest"))

    from vistest.models import CompareResult, Verdict
    from vistest.runner import VisualTester

    tester = VisualTester(run_id="ci-run-1")
    tester.results.append(CompareResult(name="a.png", verdict=Verdict.FAIL,
                                        max_severity=51.0))
    path = tester.flush()

    junit = path.parent / "junit.xml"
    assert junit.exists()
    root = ET.fromstring(junit.read_text("utf-8"))
    assert root.get("failures") == "1"


@pytest.mark.parametrize("filename", ["run.json", "external.json"])
def test_the_cli_gate_reads_either_shape(tmp_path, monkeypatch, capsys, filename):
    import json

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VISTEST_ROOT", str(tmp_path / ".vistest"))

    run_dir = tmp_path / ".vistest" / "runs" / "some-run"
    run_dir.mkdir(parents=True)
    key = "comparisons" if filename == "run.json" else "results"
    (run_dir / filename).write_text(json.dumps({
        "run_id": "some-run", "platform": "linux-chromium-1x",
        key: [{"name": "a.png", "verdict": "fail",
               "metrics": {"max_severity": 51.0}}],
    }), encoding="utf-8")

    from vistest.cli import main
    code = main(["gate"])

    assert code == 1, "падение обязано вернуть ненулевой код"
    assert "STOP:" in capsys.readouterr().out
    assert (run_dir / "junit.xml").exists()


def test_the_cli_gate_is_green_on_a_clean_run(tmp_path, monkeypatch, capsys):
    import json

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VISTEST_ROOT", str(tmp_path / ".vistest"))
    run_dir = tmp_path / ".vistest" / "runs" / "clean"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({
        "run_id": "clean",
        "comparisons": [{"name": "a.png", "verdict": "pass"}],
    }), encoding="utf-8")

    assert main_gate() == 0
    assert "PASSED:" in capsys.readouterr().out


def main_gate() -> int:
    from vistest.cli import main
    return main(["gate"])


def test_the_cli_gate_says_so_when_there_are_no_runs(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VISTEST_ROOT", str(tmp_path / ".vistest"))
    assert main_gate() == 2
    assert "no runs found" in capsys.readouterr().out
