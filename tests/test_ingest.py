# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Приём готовых артефактов: их прогон уже был, мы читаем результат.

Третий вход, и он закрывает то, чего не закрывают первые два. Запуск чужой
командой требует, чтобы ИХ инструмент стоял в НАШЕМ образе: `npx playwright
test` внутри контейнера VisTest — это node, `npm ci` и вся их сборка у нас.
Для Node это решается образом; для JVM и .NET не решается разумной ценой, и
делать вид, что решается, — значит обещать поддержку, которой не будет.

Здесь порядок обратный: их CI гоняет тесты там, где у него всё стоит, а к нам
приезжает каталог с картинками. Ни одной строки их кода мы не исполняем.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from vistest import suites
from vistest.config import VisTestConfig
from vistest.external import ingest_project
from vistest.projects import BaselineDir, Project


def _png(path: Path, color=(30, 90, 200), blot=False) -> None:
    from vistest.capture.playwright_capture import _write_png

    image = np.zeros((80, 120, 3), np.uint8)
    image[:] = color
    if blot:
        image[10:50, 10:70] = (240, 40, 40)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_png(path, image)


@pytest.fixture
def playwright_repo(tmp_path, monkeypatch):
    """Каталог ровно такой, какой оставляет после себя Playwright."""
    monkeypatch.chdir(tmp_path)
    suites.forget()
    root = tmp_path / "web-e2e"
    (root / "tests" / "shots").mkdir(parents=True)
    (root / "playwright.config.ts").write_text("export default {};\n")

    out = root / "test-results" / "checkout-Checkout-chromium"
    _png(out / "login-expected.png")
    _png(out / "login-actual.png")
    _png(out / "cart-expected.png")
    _png(out / "cart-actual.png", blot=True)

    project = Project(key="web", name="Web", root=str(root), runner="command",
                      command=["npx", "playwright", "test"],
                      baselines=[BaselineDir(dir="tests/shots")])
    yield project
    suites.forget()


# --------------------------------------------------------------------------- #
def test_a_finished_run_is_judged_without_starting_anything(playwright_repo):
    """Их команда не выполняется. Проверяется именно это: node в нашем образе
    не нужен вовсе, а вердикт при этом полный."""
    run = ingest_project(playwright_repo, cfg=VisTestConfig.load(),
                         log=lambda *_a: None)

    by_name = {r["name"]: r for r in run.results}
    assert set(by_name) == {"login.png", "cart.png"}
    assert by_name["login.png"]["verdict"] == "pass"
    assert by_name["cart.png"]["verdict"] == "fail"
    assert by_name["cart.png"]["regions"], "регионы — то, ради чего всё и было"
    assert run.command[0] == "(ingest)", "в истории видно, что это разбор"


def test_the_run_is_written_and_can_be_reopened(playwright_repo):
    """Прогон без записи на диск — это прогон, который нельзя открыть."""
    run = ingest_project(playwright_repo, cfg=VisTestConfig.load(),
                         log=lambda *_a: None)

    assert (Path(run.run_dir) / "external.json").exists()


def test_a_folder_from_the_command_does_not_change_the_project(playwright_repo):
    """Подсказка «вот сюда положил мой пайплайн» — разовая.

    Сохранять её в описание проекта значило бы менять проект побочным
    эффектом чтения: следующий обычный прогон пошёл бы искать в каталоге,
    который назвали один раз для одного CI.
    """
    ingest_project(playwright_repo, cfg=VisTestConfig.load(),
                   dirs=["test-results"], log=lambda *_a: None)

    assert playwright_repo.search_dirs == []


def test_it_lands_in_the_shared_history(playwright_repo, tmp_path):
    """Иначе разбор — это красивый вывод в терминал и больше ничего."""
    from vistest.api.db import Database
    from vistest.external import publish

    cfg = VisTestConfig.load()
    run = ingest_project(playwright_repo, cfg=cfg, browser="chromium",
                         log=lambda *_a: None)
    run_id = publish(run, playwright_repo, cfg=cfg, log=lambda *_a: None)

    db = Database(cfg.root_path / "vistest.db")
    row = db.one("SELECT browser, total, failed FROM run WHERE id=?", (run_id,))
    assert row == {"browser": "chromium", "total": 2, "failed": 1}


def test_the_browser_is_part_of_the_key_and_not_a_label(playwright_repo):
    """Набор, снятый в firefox, не эталон для chromium: отрисовка шрифтов в
    этих движках физически разная."""
    cfg = VisTestConfig.load()

    one = ingest_project(playwright_repo, cfg=cfg, browser="chromium",
                         baseline_source="vistest", update_baselines=True,
                         log=lambda *_a: None)
    two = ingest_project(playwright_repo, cfg=cfg, browser="firefox",
                         baseline_source="vistest", update_baselines=True,
                         log=lambda *_a: None)

    assert one.baseline_dir != two.baseline_dir
    assert "chromium" in one.baseline_dir and "firefox" in two.baseline_dir


def test_nothing_found_is_not_silence(playwright_repo, tmp_path):
    """Пустой разбор обязан назвать, где искали и по каким правилам."""
    import shutil

    shutil.rmtree(Path(playwright_repo.root) / "test-results")

    run = ingest_project(playwright_repo, cfg=VisTestConfig.load(),
                         log=lambda *_a: None)

    assert not run.results
    assert any("test-results" in e for e in run.errors), run.errors
