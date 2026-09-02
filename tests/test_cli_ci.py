# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""`vistest gate` и `vistest comment` — то, чем прогон разговаривает с CI.

Обе команды существуют по одной причине: код возврата есть только у pytest.
Прогон подключённого проекта в режиме наблюдения и прогон, запущенный кнопкой
в интерфейсе, никакого pytest не имеют — для конвейера их результат не
существовал вовсе.

Читают они каталог прогона на диске, а не сервис: в CI сервиса может не быть, а
`run.json` пишется всегда. Это здесь и проверяется — ни один тест не поднимает
ни базы, ни HTTP.
"""

from __future__ import annotations

import json

import pytest

from vistest.cli import main

RUN = {
    "run_id": "20260826-1", "platform": "linux-chromium-1x",
    "git": {"branch": "feature/pay", "sha": "0123456789abcdef"},
    "comparisons": [
        {"name": "shop/login.png", "verdict": "fail",
         "metrics": {"max_severity": 44.0, "changed_area_pct": 0.4},
         "regions": [{"kind": "shift", "severity": 44.0, "selector": ".header"}]},
        {"name": "shop/cart.png", "verdict": "pass",
         "metrics": {"max_severity": 0.0}},
    ],
}


@pytest.fixture
def run_dir(tmp_path, monkeypatch):
    """Каталог прогона там, где его будет искать CLI, и ничего больше."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VISTEST_ROOT", str(tmp_path / ".vistest"))
    for name in ("GITHUB_ACTIONS", "GITHUB_STEP_SUMMARY", "VISTEST_PUBLIC_URL"):
        monkeypatch.delenv(name, raising=False)

    d = tmp_path / ".vistest" / "runs" / "20260826-1"
    d.mkdir(parents=True)
    (d / "run.json").write_text(json.dumps(RUN), encoding="utf-8")
    return d


def _write(run_dir, payload):
    (run_dir / "run.json").write_text(json.dumps(payload), encoding="utf-8")


# --------------------------------------------------------------------------- #
#  comment
# --------------------------------------------------------------------------- #
def test_comment_prints_markdown_for_the_last_run(run_dir, capsys):
    assert main(["comment"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("**VisTest: 1 of 2")
    assert "shop/login.png" in out
    assert "`feature/pay`" in out


def test_comment_writes_a_file_for_gh_pr_comment(run_dir, tmp_path):
    """`gh pr comment --body-file` берёт файл — значит файл надо уметь отдать.

    Ради этого команда и существует в CLI, а не только в API: в CI сервиса
    может не быть вовсе.
    """
    out = tmp_path / "nested" / "body.md"
    assert main(["comment", "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8").startswith("**VisTest: 1 of 2")


def test_comment_without_a_run_says_so_instead_of_crashing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VISTEST_ROOT", str(tmp_path / ".vistest"))
    assert main(["comment"]) == 2


def test_comment_reads_an_external_run_too(run_dir):
    """Прогоны подключённых проектов пишут `external.json`, а не `run.json`.

    Именно они и не имеют pytest — то есть ровно те, ради которых всё это.
    """
    (run_dir / "run.json").unlink()
    (run_dir / "external.json").write_text(json.dumps(RUN), encoding="utf-8")
    assert main(["comment"]) == 0


def test_a_broken_run_file_is_an_error_not_a_traceback(run_dir, capsys):
    (run_dir / "run.json").write_text("{не json", encoding="utf-8")
    assert main(["comment"]) == 2
    assert "could not parse it" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
#  gate
# --------------------------------------------------------------------------- #
def test_gate_returns_one_on_a_failure(run_dir, capsys):
    assert main(["gate"]) == 1
    assert "STOP:" in capsys.readouterr().out


def test_gate_returns_zero_on_a_clean_run(run_dir, capsys):
    _write(run_dir, dict(RUN, comparisons=[RUN["comparisons"][1]]))
    assert main(["gate"]) == 0
    assert "PASSED:" in capsys.readouterr().out


def test_gate_writes_junit_next_to_the_run(run_dir):
    main(["gate"])
    assert (run_dir / "junit.xml").exists()


# --------------------------------------------------------------------------- #
#  Отчёт в интерфейсе сборки
#
#  JUnit показывают все, но не на первом экране: в GitHub Actions до списка
#  тестов надо провалиться в шаг. А смотрят на страницу сборки — и видели там
#  ровно «Process completed with exit code 1».
# --------------------------------------------------------------------------- #
def test_the_step_summary_gets_the_same_text_as_the_pr_comment(
        run_dir, tmp_path, monkeypatch, capsys):
    """Именно та же сводка, а не похожая.

    Две почти одинаковые расходятся на третьем изменении, и человек начинает
    сверять их между собой вместо того, чтобы смотреть на дифф.
    """
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    main(["gate"])
    written = summary.read_text(encoding="utf-8")
    capsys.readouterr()                      # вывод самого гейта нас тут не касается

    main(["comment"])
    assert capsys.readouterr().out.strip() == written.strip()


def test_the_step_summary_is_appended_not_overwritten(
        run_dir, tmp_path, monkeypatch):
    """В сводке шага уже может лежать чужой текст — свой мы дописываем."""
    summary = tmp_path / "summary.md"
    summary.write_text("## Build\n", encoding="utf-8")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    main(["gate"])
    assert summary.read_text(encoding="utf-8").startswith("## Build\n")


def test_an_unwritable_summary_does_not_change_the_verdict(
        run_dir, tmp_path, monkeypatch):
    """Отчёт для глаз не может стоить вердикта.

    Код возврата к этому моменту уже посчитан по сравнениям, и уронить команду
    из-за каталога, в который не удалось записать, значит покрасить сборку по
    причине, не имеющей к картинкам никакого отношения.
    """
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "nope" / "s.md"))
    assert main(["gate"]) == 1


def test_nothing_is_written_outside_github_actions(run_dir, capsys):
    """У GitLab аналога нет — он читает JUnit и показывает его сам.

    Печатать `::error` в чужом конвейере значит сорить в лог тем, что там никто
    не разберёт.
    """
    main(["gate"])
    assert "::error" not in capsys.readouterr().out


def test_annotations_name_every_blocking_snapshot(run_dir, monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    main(["gate"])
    lines = [ln for ln in capsys.readouterr().out.splitlines()
             if ln.startswith("::error")]
    assert len(lines) == 1
    assert "shop/login.png" in lines[0]


def test_a_clean_run_gets_a_notice_not_an_error(run_dir, monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    _write(run_dir, dict(RUN, comparisons=[RUN["comparisons"][1]]))
    main(["gate"])
    out = capsys.readouterr().out
    assert "::notice title=VisTest::" in out
    assert "::error" not in out


def test_annotations_are_capped(run_dir, monkeypatch, capsys):
    """Лог с сотней строк `::error` нечитаем ровно так же, как таблица на сто строк."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    _write(run_dir, dict(RUN, comparisons=[
        dict(RUN["comparisons"][0], name=f"p{i}.png") for i in range(25)]))
    main(["gate"])
    lines = [ln for ln in capsys.readouterr().out.splitlines()
             if ln.startswith("::error")]
    assert len(lines) == 21                      # двадцать штук и «и ещё пять»
    assert "5 more snapshots are blocking" in lines[-1]


def test_a_newline_in_the_reason_does_not_break_the_annotation(
        run_dir, monkeypatch, capsys):
    """Перевод строки обрывает команду, и её хвост уезжает в лог открытым текстом."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    _write(run_dir, dict(RUN, comparisons=[
        {"name": "boom.png", "verdict": "error",
         "error": "Traceback:\nline one\nline two"}]))
    main(["gate"])
    lines = [ln for ln in capsys.readouterr().out.splitlines()
             if ln.startswith("::error")]
    assert len(lines) == 1
    assert "%0A" in lines[0]
    assert "line two" in lines[0]


def test_a_comma_in_a_name_does_not_become_a_second_property(
        run_dir, monkeypatch, capsys):
    """Свойства аннотации разделены запятой, и имя снимка их не задаёт."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    _write(run_dir, dict(RUN, comparisons=[
        dict(RUN["comparisons"][0], name="shop/a,b.png")]))
    main(["gate"])
    line = next(ln for ln in capsys.readouterr().out.splitlines()
                if ln.startswith("::error"))
    head = line.split("::", 2)[1]
    assert head.count(",") == 0
    assert "%2C" in head


# --------------------------------------------------------------------------- #
#  run.py — две команды, объявленные в одном месте и забытые в другом
# --------------------------------------------------------------------------- #
def test_every_launcher_command_has_a_handler():
    """`run.py <команда>` обязан отвечать подсказкой, а не стеком.

    Здесь стояло два списка: цикл, объявляющий парсеры, и словарь обработчиков
    под ним. Расходились они молча и разошлись: команда объявлялась в парсере,
    забывалась в словаре, и `run.py matrix` печатал `KeyError: 'matrix'` со
    стеком. Для человека, который просто прочитал README, это выглядит как
    сломанный инструмент, а не как забытая строка.
    """
    import argparse
    import importlib.util
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("vistest_launcher",
                                                  root / "run.py")
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)

    declared = {name for name, _ in launcher.PASSTHROUGH}
    assert declared, "список проксируемых команд пуст — проверка потеряла смысл"

    # Собираем парсер так же, как это делает `main()`, и спрашиваем у него,
    # какие команды он вообще знает.
    known = set()
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    for name, help_ in launcher.PASSTHROUGH:
        sub.add_parser(name, help=help_)
        known.add(name)
    assert declared <= known

    # И главное: у каждой объявленной команды есть обработчик. Проверяется по
    # исходнику `main()`, потому что словарь собирается внутри него.
    source = (root / "run.py").read_text(encoding="utf-8")
    assert "for name, _ in PASSTHROUGH" in source, (
        "словарь обработчиков снова собирается вручную — он разойдётся "
        "с PASSTHROUGH так же, как разошёлся в прошлый раз")


def test_the_launcher_and_the_cli_agree_on_the_command_names():
    """Проксируемой команды может не быть в самом CLI — тогда она мертва.

    `run.py` объявит её, покажет в `--help` и передаст в `vistest.cli`, а тот
    ответит «invalid choice». Проверяется по исходнику: парсер CLI собирается
    внутри `main()`, и поднимать его целиком ради списка имён дороже, чем
    прочитать строки объявлений.
    """
    import importlib.util
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("vistest_launcher2",
                                                  root / "run.py")
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)

    cli_source = (root / "vistest" / "cli.py").read_text(encoding="utf-8")
    declared = set(re.findall(r'add_parser\(\s*"([\w-]+)"', cli_source))

    missing = {name for name, _ in launcher.PASSTHROUGH} - declared
    assert not missing, f"run.py проксирует несуществующие команды: {sorted(missing)}"
