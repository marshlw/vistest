# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Прогон подключённого проекта: что попадает в отчёт и что мешает старту.

Здесь закрыты два дефекта, каждый из которых выглядел для человека одинаково —
«тесты не запускаются», — но ломался в разных местах:

* снятый комплект эталонов VisTest считался пустым, и сравнение отказывалось
  стартовать сразу после успешного снятия;
* снимки, на которых перехват не сработал, молча исчезали из отчёта: прогон
  выглядел чистым, только коротким.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest

from vistest.config import VisTestConfig
from vistest.external import (
    ExternalRun,
    _collect_adapter,
    _spawn,
    has_own_baselines,
    run_project,
)
from vistest.projects import Project


# --------------------------------------------------------------------------- #
#  Эталоны VisTest: раскладка каталогов, а не плоские PNG
# --------------------------------------------------------------------------- #
def test_captured_set_is_not_mistaken_for_empty(tmp_path):
    """`FileBaselineStore` кладёт снимок в `<имя>/baseline.png`."""
    platform_dir = tmp_path / "win-chromium-1x"
    (platform_dir / "login_filled").mkdir(parents=True)

    assert not has_own_baselines(platform_dir)

    (platform_dir / "login_filled" / "baseline.png").write_bytes(b"png")

    assert has_own_baselines(platform_dir)
    # Ровно та проверка, которая стояла раньше и всегда давала «пусто».
    assert not any(platform_dir.glob("*.png"))


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "their-repo"
    (root / "screens").mkdir(parents=True)
    return Project(key="t", name="t", root=str(root), tests="screens",
                   baseline_store="vistest")


def test_run_refuses_when_nothing_was_captured(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = VisTestConfig.load()

    from vistest.external import ExternalRunRefused

    # Отдельный тип, а не голый RuntimeError: на объяснённом отказе задача не
    # дописывает в лог питоновский трейс — он говорил бы «инструмент сломался»
    # там, где инструмент отработал как задумано.
    with pytest.raises(ExternalRunRefused, match="has not been captured"):
        run_project(_project(tmp_path), cfg=cfg, log=lambda _t: None)


def test_run_starts_once_the_set_is_captured(tmp_path, monkeypatch):
    """После «Снять эталоны VisTest» сравнение обязано стартовать."""
    monkeypatch.chdir(tmp_path)
    cfg = VisTestConfig.load()
    project = _project(tmp_path)

    from vistest.config import platform_key

    snap = project.vistest_baselines_path(cfg, platform_key()) / "login_filled"
    snap.mkdir(parents=True)
    (snap / "baseline.png").write_bytes(b"png")

    # Дальше прогон упрётся в отсутствие тестов, и это нормально: важно, что он
    # дошёл до запуска, а не был отвергнут на пороге.
    run = run_project(project, cfg=cfg, log=lambda _t: None)
    assert isinstance(run, ExternalRun)


# --------------------------------------------------------------------------- #
#  Запуск чужого pytest
# --------------------------------------------------------------------------- #
def test_exit_code_is_always_a_number(tmp_path):
    code, lines = _spawn([sys.executable, "-c", "print('hi')"], tmp_path,
                         dict(os.environ), log=lambda _t: None,
                         should_stop=lambda: False)
    assert code == 0 and lines == ["hi"]


def test_missing_interpreter_is_explained(tmp_path):
    with pytest.raises(RuntimeError, match="interpreter"):
        _spawn([str(tmp_path / "nope" / "python")], tmp_path, dict(os.environ),
               log=lambda _t: None, should_stop=lambda: False)


def test_stop_keeps_draining_and_returns_a_code(tmp_path):
    """Остановленный pytest дописывает хвост — его нельзя терять."""
    code, lines = _spawn(
        [sys.executable, "-c", "for i in range(200): print(i)"],
        tmp_path, dict(os.environ), log=lambda _t: None,
        should_stop=lambda: True, grace_s=10)
    assert code is not None
    assert lines, "вывод после сигнала остановки должен дочитываться"


# --------------------------------------------------------------------------- #
#  Отчёт: снимки и тесты считаются отдельно
# --------------------------------------------------------------------------- #
def _adapter_report(tmp_path: Path, payload: dict) -> ExternalRun:
    (tmp_path / "adapter.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    run = ExternalRun(project="t", mode="adapter")
    _collect_adapter(run, tmp_path, log=lambda _t: None)
    return run


def test_tests_that_died_before_the_screenshot_are_named(tmp_path):
    run = _adapter_report(tmp_path, {
        "patched": "pkg.Base.assert_screenshot",
        "collected": 11,
        "errors": [],
        "results": [{"name": "login.png", "verdict": "pass"},
                    {"name": "index.png", "verdict": "pass"}],
        "tests": [{"id": "t.py::test_login", "outcome": "passed"},
                  {"id": "t.py::test_index", "outcome": "passed"}]
                 + [{"id": f"t.py::test_{i}", "outcome": "failed"}
                    for i in range(9)],
    })

    assert run.summary() == {
        "total": 2, "failed": 0, "new": 0, "errored": 0, "passed": 2,
        "tests": 11, "tests_failed": 9, "tests_not_started": 0,
        "max_severity": 0,
    }
    assert any("broke on their way to the screenshot" in e for e in run.errors)


def test_setup_failures_name_the_cause_and_the_empty_fixture(tmp_path):
    """Девять одинаковых падений на setup — это одна причина, а не девять.

    Такой прогон нельзя считать успешным: тела тестов не стартовали, сравнивать
    было нечего. Отчёт обязан назвать и причину, и пустую фикстуру — иначе
    человек ищет её в трейсбеке из двухсот строк.
    """
    cause = "TypeError: Frame.fill() missing 1 required positional argument: 'value'"
    run = _adapter_report(tmp_path, {
        "patched": "pkg.Base.assert_screenshot",
        "collected": 11,
        "results": [{"name": "login.png", "verdict": "pass"},
                    {"name": "login_bad.png", "verdict": "pass"}],
        "tests": [{"id": "t.py::ok1", "phase": "call", "outcome": "passed"},
                  {"id": "t.py::ok2", "phase": "call", "outcome": "passed"}]
                 + [{"id": f"t.py::test_{i}", "phase": "setup",
                     "outcome": "failed", "error": cause,
                     "empty_args": ["user_pass"]} for i in range(9)],
    })

    assert len(run.setup_failures) == 9
    assert run.summary()["tests_not_started"] == 9

    note = " ".join(run.errors)
    assert "fell at fixture setup" in note
    assert cause in note
    assert "user_pass" in note


def test_diagnosis_names_the_variable_from_their_conftest(tmp_path):
    """«Заведите переменную» — половина ответа. Нужна вторая: какую именно."""
    import textwrap

    root = tmp_path / "their-repo"
    (root / "screens").mkdir(parents=True)
    (root / "conftest.py").write_text(textwrap.dedent('''
        import os
        import pytest

        @pytest.fixture(scope="session")
        def user_pass():
            return os.environ.get("ACME_PASSWORD")
    '''), encoding="utf-8")
    project = Project(key="t", root=str(root), tests="screens")

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "adapter.json").write_text(json.dumps({
        "patched": "pkg.Base.assert_screenshot",
        "collected": 3,
        "results": [{"name": "a.png", "verdict": "pass"}],
        "tests": [{"id": "t.py::a", "phase": "call", "outcome": "passed"}]
                 + [{"id": f"t.py::b{i}", "phase": "setup", "outcome": "failed",
                     "error": "TypeError: fill() missing 'value'",
                     "empty_args": ["user_pass"]} for i in range(2)],
    }), encoding="utf-8")

    run = ExternalRun(project="t", mode="adapter")
    _collect_adapter(run, run_dir, project=project, log=lambda _t: None)

    note = " ".join(run.errors)
    assert "ACME_PASSWORD" in note
    assert re.search(r"conftest\.py:\d+", note), note


def test_every_remark_is_said_exactly_once(tmp_path):
    """Один диагноз — одна строка в логе. Раньше он повторялся четыре раза."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "adapter.json").write_text(json.dumps({
        "patched": "pkg.Base.assert_screenshot",
        "collected": 2,
        "results": [],
        "tests": [{"id": "t.py::a", "phase": "setup", "outcome": "failed",
                   "error": "boom"}],
    }), encoding="utf-8")

    run = ExternalRun(project="t", mode="adapter")
    said: list[str] = []
    _collect_adapter(run, run_dir, log=said.append)

    for note in run.errors:
        assert said.count(note) == 1, note


def test_body_failures_are_not_confused_with_setup(tmp_path):
    """Упал в теле — это результат теста; упал на setup — это сломанное условие."""
    run = _adapter_report(tmp_path, {
        "patched": "pkg.Base.assert_screenshot",
        "collected": 3,
        "results": [{"name": "a.png", "verdict": "pass"}],
        "tests": [{"id": "t.py::a", "phase": "call", "outcome": "passed"},
                  {"id": "t.py::b", "phase": "call", "outcome": "failed",
                   "error": "TimeoutError: Locator.click"},
                  {"id": "t.py::c", "phase": "setup", "outcome": "failed",
                   "error": "KeyError: 'BASE_URL'"}],
    })

    assert [t["id"] for t in run.setup_failures] == ["t.py::c"]
    note = " ".join(run.errors)
    assert "broke on their way to the screenshot" in note
    assert "fell at fixture setup" in note


def test_green_tests_without_comparisons_are_reported(tmp_path):
    """Тесты прошли, а сравнений нет — значит точка перехвата не та."""
    run = _adapter_report(tmp_path, {
        "patched": "pkg.Base.assert_screenshot",
        "collected": 4,
        "results": [],
        "tests": [{"id": f"t.py::t{i}", "outcome": "passed"} for i in range(4)],
    })
    assert any("without a single comparison" in e for e in run.errors)


def test_a_broken_adapter_report_does_not_kill_the_run(tmp_path):
    (tmp_path / "adapter.json").write_text("{not json", encoding="utf-8")
    run = ExternalRun(project="t", mode="adapter")
    _collect_adapter(run, tmp_path, log=lambda _t: None)
    assert any("unreadable" in e for e in run.errors)
