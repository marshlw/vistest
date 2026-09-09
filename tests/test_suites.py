# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Раскладки чужих инструментов на диске.

Тесты здесь закрывают ровно один дефект, но он стоил всей поддержки других
языков. Классификация картинок шла по ПРЕФИКСУ — `actual_login.png`, — а вся
JS-экосистема называет по СУФФИКСУ: `login-actual.png` у Playwright,
`login.actual.png` у Cypress, `login-received.png` у jest-image-snapshot. Всё
это опознавалось как эталон, свежих фактов не находилось ни одного, и прогон
заканчивался фразой «не нашли ни одного actual_*.png» — то есть обвинял чужой
набор в том, чего не умели мы.

Второй дефект тише и потому хуже: имя снимка бралось из имени файла целиком.
Два теста, каждый со своим `login.png`, схлопывались в один — и второй молча
затирал результат первого.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from vistest import suites
from vistest.config import VisTestConfig
from vistest.external import ExternalRun, _collect_observed, _observe_roots
from vistest.projects import Project


# --------------------------------------------------------------------------- #
#  Классификация
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("profile_id,filename,kind,name", [
    # Playwright: суффикс и платформа в имени эталона.
    ("playwright", "login-actual.png", "actual", "login.png"),
    ("playwright", "login-expected.png", "expected", "login.png"),
    ("playwright", "login-diff.png", "diff", "login.png"),
    ("playwright", "login-chromium-linux-actual.png", "actual", "login.png"),
    ("playwright", "login-darwin-actual.png", "actual", "login.png"),
    # Cypress пишет через точку.
    ("cypress", "checkout.actual.png", "actual", "checkout.png"),
    ("cypress", "checkout.diff.png", "diff", "checkout.png"),
    # jest-image-snapshot: `-snap` и `-received`.
    ("jest-image-snapshot", "card-received.png", "actual", "card.png"),
    ("jest-image-snapshot", "card-snap.png", "expected", "card.png"),
    # pytest — как было.
    ("pytest", "actual_login_filled.png", "actual", "login_filled.png"),
    ("pytest", "diff_login_filled.png", "diff", "login_filled.png"),
])
def test_the_naming_of_each_tool_is_understood(profile_id, filename, kind, name):
    found = suites.get(profile_id).classify(Path(filename))
    assert (found.kind, found.name) == (kind, name)


def test_a_diff_is_never_taken_for_a_result():
    """Худший исход: сравнить картинку с красными рамками и показать регресс.

    Правило, достаточно широкое, чтобы поймать `login-actual.png`, ловит и
    `login-diff.png`, — поэтому diff проверяется первым.

    BackstopJS исключён осознанно, и это не поблажка: у него в
    `bitmaps_test` лежат только свежие снимки, а разница называется
    `failed_diff_…`. Файл `login-diff.png` там — обычный снимок сценария с
    таким именем, и считать его разницей значило бы выбросить результат.
    """
    for profile in suites.PROFILES:
        if profile.id == "backstop":
            continue
        found = profile.classify(Path("login-diff.png"))
        assert found.kind in ("diff", "other"), profile.id


def test_an_ordinary_picture_is_not_a_result():
    """`test_` в списке префиксов делал фактом любую картинку-фикстуру.

    `test_data.png` в чужом репозитории — это картинка для теста, а не
    результат сравнения, и попадание её в отчёт означало сравнение с эталоном,
    которого нет, и строку «нет эталона» на пустом месте.
    """
    assert suites.get("pytest").classify(Path("test_data.png")).kind == "other"


def test_the_project_may_correct_any_rule():
    """Профиль — пол, а не потолок: раскладка, которую никто не предсказал."""
    profile = suites.get("generic").with_overrides(
        naming={"actual": r"snap_(?P<name>.+)_new\.png"})

    found = profile.classify(Path("snap_cart_new.png"))
    assert (found.kind, found.name) == ("actual", "cart.png")


# --------------------------------------------------------------------------- #
#  Определение инструмента
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("marker,expected", [
    ("playwright.config.ts", "playwright"),
    ("cypress.config.js", "cypress"),
    ("backstop.json", "backstop"),
    ("pom.xml", "maven"),
    ("conftest.py", "pytest"),
])
def test_the_tool_is_recognised_by_its_marker(tmp_path, marker, expected):
    suites.forget()
    (tmp_path / marker).write_text("{}\n")

    assert suites.detect(tmp_path).id == expected
    suites.forget()


def test_nothing_recognised_is_said_so_rather_than_guessed(tmp_path):
    """Неверный профиль хуже отсутствующего: он молча классифицирует по чужим
    правилам, и прогон кончается «пар не найдено» без причины."""
    suites.forget()
    (tmp_path / "readme.txt").write_text("hello\n")

    assert suites.detect(tmp_path) is None
    suites.forget()


def test_a_pytest_project_is_never_given_a_foreign_profile(tmp_path):
    """Иначе адаптер выключился бы у набора, где он работает."""
    suites.forget()
    (tmp_path / "playwright.config.ts").write_text("export default {};\n")
    project = Project(key="p", root=str(tmp_path), runner="pytest")
    try:
        assert project.profile().intercept is True
    finally:
        suites.forget()


# --------------------------------------------------------------------------- #
#  Где искать
# --------------------------------------------------------------------------- #
def test_the_folder_the_tool_writes_to_is_scanned(tmp_path):
    """`test-results` лежит в корне репозитория — вне папки эталонов и вне
    каталога тестов, то есть вне всех мест, куда смотрели раньше."""
    suites.forget()
    (tmp_path / "playwright.config.ts").write_text("export default {};\n")
    (tmp_path / "test-results").mkdir()
    project = Project(key="p", root=str(tmp_path), runner="command",
                      command=["npx", "playwright", "test"])
    try:
        roots = _observe_roots(project, project.profile(),
                               tmp_path / "shots", tmp_path / "run")
        assert (tmp_path / "test-results").resolve() in roots
    finally:
        suites.forget()


def test_node_modules_is_not_walked(tmp_path):
    """Один Playwright-проект держит там десятки тысяч файлов, и обход по ним
    был самым долгим местом прогона."""
    junk = tmp_path / "node_modules" / "pkg"
    junk.mkdir(parents=True)
    (junk / "logo-actual.png").write_bytes(b"")
    (tmp_path / "shot-actual.png").write_bytes(b"")

    found = [s.path.name for s in
             suites.get("generic").walk([tmp_path]) if s.kind == "actual"]

    assert found == ["shot-actual.png"]


# --------------------------------------------------------------------------- #
#  Сбор результатов
# --------------------------------------------------------------------------- #
def _png(path: Path, color=(40, 90, 200), size=(60, 80)) -> None:
    from vistest.capture.playwright_capture import _write_png

    image = np.zeros((size[0], size[1], 3), np.uint8)
    image[:] = color
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_png(path, image)


def _playwright_project(tmp_path) -> Project:
    suites.forget()
    root = tmp_path / "their-repo"
    (root / "tests").mkdir(parents=True)
    (root / "playwright.config.ts").write_text("export default {};\n")
    return Project(key="shop", root=str(root), runner="command",
                   command=["npx", "playwright", "test"],
                   baselines=[])


def _run(project, cfg, baseline_dir, run_dir, **kw) -> ExternalRun:
    run = ExternalRun(project=project.key, mode="observe",
                      baseline_dir=str(baseline_dir), run_dir=str(run_dir))
    _collect_observed(run, project, cfg, baseline_dir, run_dir,
                      before={}, log=lambda *_a: None, **kw)
    return run


def test_a_playwright_run_is_understood_end_to_end(tmp_path, monkeypatch):
    """Тройка actual/expected/diff в `test-results` — и есть результат прогона.

    Эталон берётся из их же `expected`: какой файл соответствует этому тесту,
    решает их конфигурация, и вычислить это снаружи нельзя.
    """
    monkeypatch.chdir(tmp_path)
    cfg = VisTestConfig.load()
    project = _playwright_project(tmp_path)
    root = project.root_path
    shots = root / "tests" / "checkout.spec.ts-snapshots"
    shots.mkdir(parents=True)

    out = root / "test-results" / "checkout-Checkout-chromium"
    _png(out / "login-expected.png", color=(40, 90, 200))
    _png(out / "login-actual.png", color=(40, 90, 200))
    _png(out / "login-diff.png", color=(255, 0, 0))

    run_dir = tmp_path / "runs" / "r1"
    run_dir.mkdir(parents=True)
    try:
        run = _run(project, cfg, shots, run_dir)
    finally:
        suites.forget()

    assert [r["name"] for r in run.results] == ["login.png"]
    assert run.results[0]["baseline_source"] == "suite"
    assert run.results[0]["verdict"] == "pass"


def test_the_run_says_what_it_looked_for_when_it_finds_nothing(tmp_path, monkeypatch):
    """«Не нашли ни одного actual_*.png» было правдой и бесполезно.

    Три разные причины — набор ничего не написал, написал не туда, назвал
    иначе — чинятся в трёх разных местах, и сообщение обязано их различать.
    """
    monkeypatch.chdir(tmp_path)
    cfg = VisTestConfig.load()
    project = _playwright_project(tmp_path)
    shots = project.root_path / "tests" / "shots"
    shots.mkdir(parents=True)
    run_dir = tmp_path / "runs" / "r1"
    run_dir.mkdir(parents=True)

    try:
        run = _run(project, cfg, shots, run_dir)
    finally:
        suites.forget()

    note = " ".join(run.errors)
    assert "test-results" in note and "playwright" in note
    assert "-actual" in note, "правила, по которым искали, названы"


def test_namesakes_from_different_tests_do_not_overwrite_each_other(
        tmp_path, monkeypatch):
    """Второй `login.png` молча вытеснял первый — и в отчёте оставался один."""
    monkeypatch.chdir(tmp_path)
    cfg = VisTestConfig.load()
    project = _playwright_project(tmp_path)
    root = project.root_path
    shots = root / "tests" / "shots"
    shots.mkdir(parents=True)

    for spec in ("checkout-chromium", "profile-chromium"):
        out = root / "test-results" / spec
        _png(out / "login-expected.png")
        _png(out / "login-actual.png")

    run_dir = tmp_path / "runs" / "r1"
    run_dir.mkdir(parents=True)
    try:
        run = _run(project, cfg, shots, run_dir)
    finally:
        suites.forget()

    assert any("claim this name" in n for n in run.errors), run.errors


def test_the_folder_can_be_kept_in_the_name(tmp_path, monkeypatch):
    """Разрешение коллизии, о котором сообщение и говорит."""
    monkeypatch.chdir(tmp_path)
    cfg = VisTestConfig.load()
    project = _playwright_project(tmp_path)
    project.keep_dir = True
    root = project.root_path
    shots = root / "tests" / "shots"
    shots.mkdir(parents=True)

    for spec in ("checkout-chromium", "profile-chromium"):
        out = root / "test-results" / spec
        _png(out / "login-expected.png")
        _png(out / "login-actual.png")

    run_dir = tmp_path / "runs" / "r1"
    run_dir.mkdir(parents=True)
    try:
        run = _run(project, cfg, shots, run_dir)
    finally:
        suites.forget()

    names = sorted(r["name"] for r in run.results)
    assert names == ["test-results/checkout-chromium/login.png",
                     "test-results/profile-chromium/login.png"]
    assert not [n for n in run.errors if "claim this name" in n]


def test_the_vistest_set_can_be_captured_outside_pytest(tmp_path, monkeypatch):
    """Кнопка «Snap VisTest baselines» вне pytest не делала ничего.

    В разборе готовых PNG стояло `update_baseline=False`, комплект оставался
    пустым — а прогон при пустом комплекте отправлял к этой же кнопке. Круг не
    размыкался ничем, кроме перехода на pytest.
    """
    monkeypatch.chdir(tmp_path)
    cfg = VisTestConfig.load()
    project = _playwright_project(tmp_path)
    out = project.root_path / "test-results" / "checkout-chromium"
    _png(out / "login-actual.png")

    baselines = tmp_path / "own" / "docker-chromium-1x"
    run_dir = tmp_path / "runs" / "r1"
    run_dir.mkdir(parents=True)
    try:
        run = _run(project, cfg, baselines, run_dir,
                   own=True, update_baselines=True)
    finally:
        suites.forget()

    from vistest.external import has_own_baselines

    assert [r["verdict"] for r in run.results] == ["new_baseline"]
    assert has_own_baselines(baselines)


def test_a_stale_picture_is_not_passed_off_as_the_result(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = VisTestConfig.load()
    project = _playwright_project(tmp_path)
    out = project.root_path / "test-results" / "checkout-chromium"
    _png(out / "login-actual.png")

    run_dir = tmp_path / "runs" / "r1"
    run_dir.mkdir(parents=True)
    before = {str((out / "login-actual.png").resolve()):
              (out / "login-actual.png").stat().st_mtime}

    run = ExternalRun(project="shop", mode="observe",
                      baseline_dir=str(tmp_path / "own"), run_dir=str(run_dir))
    try:
        _collect_observed(run, project, cfg, tmp_path / "own", run_dir,
                          before=before, log=lambda *_a: None, own=True)
    finally:
        suites.forget()

    assert not run.results
    assert any("left over" in n for n in run.errors), run.errors


def test_a_jest_run_compares_against_the_committed_baseline(tmp_path, monkeypatch):
    """Вторая половина: эталон из папки проекта, а не из их артефактов.

    Здесь работает всё, ради чего инструмент и нужен: апрув пишет в их файл,
    он попадает в обычный `git diff` и на обычное ревью.
    """
    monkeypatch.chdir(tmp_path)
    suites.forget()
    cfg = VisTestConfig.load()
    root = tmp_path / "their-repo"
    (root / "src").mkdir(parents=True)
    (root / "jest.config.js").write_text("module.exports = {};\n")
    project = Project(key="ui", root=str(root), runner="command",
                      command=["npx", "jest"], tests="src")

    shots = root / "src" / "__image_snapshots__"
    _png(shots / "card.png", color=(10, 200, 120))
    _png(root / "src" / "__received_output__" / "card-received.png",
         color=(10, 200, 120))

    run_dir = tmp_path / "runs" / "r1"
    run_dir.mkdir(parents=True)
    try:
        assert project.profile().id == "jest-image-snapshot"
        run = _run(project, cfg, shots, run_dir)
    finally:
        suites.forget()

    assert [r["name"] for r in run.results] == ["card.png"]
    assert run.results[0]["baseline_source"] == "project"
    assert run.results[0]["verdict"] == "pass"


def test_a_backstop_run_reads_its_two_parallel_folders(tmp_path, monkeypatch):
    """Ни префиксов, ни суффиксов: вид картинки решает каталог."""
    monkeypatch.chdir(tmp_path)
    suites.forget()
    cfg = VisTestConfig.load()
    root = tmp_path / "their-repo"
    root.mkdir(parents=True)
    (root / "backstop.json").write_text("{}\n")
    project = Project(key="site", root=str(root), runner="command",
                      command=["npx", "backstop", "test"])

    reference = root / "backstop_data" / "bitmaps_reference"
    _png(reference / "site_home_0_body_0_desktop.png", color=(200, 30, 60))
    _png(root / "backstop_data" / "bitmaps_test" / "20260903-120000"
         / "site_home_0_body_0_desktop.png", color=(200, 30, 60))

    run_dir = tmp_path / "runs" / "r1"
    run_dir.mkdir(parents=True)
    try:
        assert project.profile().id == "backstop"
        run = _run(project, cfg, reference, run_dir)
    finally:
        suites.forget()

    assert [r["name"] for r in run.results] == ["site_home_0_body_0_desktop.png"]
    assert run.results[0]["verdict"] == "pass"


# --------------------------------------------------------------------------- #
#  Detection on a tree bigger than the scan limit
# --------------------------------------------------------------------------- #
def test_a_truncated_scan_says_so_instead_of_answering_quietly(
        tmp_path, monkeypatch, caplog):
    """«Nothing was found» and «we stopped looking» are not the same answer.

    The limit itself is right: detection runs while somebody waits for a
    dialog, and a monorepo holds more paths than anyone wants walked. What was
    wrong is that hitting it looked exactly like an empty repository — and past
    the limit the result depends on the order `os.walk` happens to return
    directories in, so the same repository can detect differently twice.
    """
    monkeypatch.setattr(suites, "_INDEX_LIMIT", 5)
    root = tmp_path / "monorepo"
    (root / "packages").mkdir(parents=True)
    for i in range(40):
        (root / "packages" / f"module_{i:02d}.txt").write_text("x", "utf-8")

    with caplog.at_level("WARNING", logger="vistest.suites"):
        suites._shallow_index(root)

    said = caplog.text
    assert "suite detection stopped after" in said
    assert str(root) in said
    assert "set the suite explicitly" in said.lower()
    #  The count is in the message: «we stopped» without a number does not say
    #  whether the tree is twice the limit or a thousand times it.
    assert any(part.isdigit() for part in said.split())


def test_a_small_tree_is_scanned_whole_and_says_nothing(
        tmp_path, caplog):
    root = tmp_path / "small"
    (root / "tests").mkdir(parents=True)
    (root / "package.json").write_text("{}", "utf-8")

    with caplog.at_level("WARNING", logger="vistest.suites"):
        names = suites._shallow_index(root)

    assert "package.json" in names
    assert caplog.records == []
