# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Прогон по матрице целиком — от вариантов до строк в базе.

Браузера здесь нет намеренно: проверяется не Playwright, а всё, что вокруг
него, — и ровно в этом «вокруг» живут ошибки, которые матрица приносит с собой.
Их три, и все три тихие:

* эталоны вариантов складываются в один каталог — и вариант перезаписывает
  соседний, а сравнение идёт с чужим кадром;
* артефакты вариантов складываются в один каталог — и разбор показывает кадр
  firefox под подписью chromium;
* сравнения вариантов складываются в одну строку `snapshot` — и история
  каждого затирает предыдущую.

Ни одна из них не падает и не пишет в лог. Поэтому тест смотрит на диск и в
базу, а не на код возврата.
"""

from __future__ import annotations

import sys
import types

import numpy as np
import pytest


class _FakeBrowser:
    def __init__(self, kind):
        self.kind = kind
        self.closed = False

    def close(self):
        self.closed = True


class _FakeBrowserType:
    def __init__(self, kind, opened):
        self.kind, self._opened = kind, opened

    def launch(self, **_kw):
        br = _FakeBrowser(self.kind)
        self._opened.append(br)
        return br


class _FakePlaywright:
    """Ровно та поверхность, которой пользуется прогон: три типа браузеров."""

    def __init__(self, opened):
        for kind in ("chromium", "firefox", "webkit"):
            setattr(self, kind, _FakeBrowserType(kind, opened))


@pytest.fixture
def fake_playwright(monkeypatch):
    opened: list[_FakeBrowser] = []

    class _Ctx:
        def __enter__(self):
            return _FakePlaywright(opened)

        def __exit__(self, *exc):
            return False

    module = types.ModuleType("playwright.sync_api")
    module.sync_playwright = lambda: _Ctx()
    package = types.ModuleType("playwright")
    package.sync_api = module
    monkeypatch.setitem(sys.modules, "playwright", package)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", module)
    return opened


@pytest.fixture
def bl(tmp_path, monkeypatch):
    """Модуль роутов на временном корне данных."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VISTEST_ROOT", str(tmp_path / ".vistest"))
    monkeypatch.delenv("VISTEST_AUTH", raising=False)

    import importlib
    import threading

    import vistest.api.db as dbmod
    dbmod._local = threading.local()

    import vistest.api.main as mainmod
    importlib.reload(mainmod)
    import vistest.api.baselines as blmod
    return blmod, mainmod


def _picture(seed: int, w: int, h: int) -> np.ndarray:
    """Кадр, зависящий и от варианта, и от размера окна.

    Одинаковые кадры не показали бы ничего: перепутанные каталоги выглядели бы
    как правильно разложенные.
    """
    rng = np.random.default_rng(seed)
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:] = (seed * 7 % 200, seed * 31 % 200, seed * 53 % 200)
    img[: h // 4, : w // 4] = rng.integers(0, 255, (h // 4, w // 4, 3), dtype=np.uint8)
    return img


class _Job:
    """Задача без раннера: прогон пишет в неё лог и прогресс."""

    def __init__(self):
        self.lines: list[str] = []
        self.cancelled = False
        self.progress = 0.0

    def say(self, text, level="info"):
        self.lines.append(f"{level}: {text}")


@pytest.fixture
def run_matrix(bl, fake_playwright, monkeypatch):
    blmod, mainmod = bl
    seen: list[tuple[str, str, int, int]] = []

    def fake_shoot(browser, cfg, target, job, state=None):
        w, h = blmod._viewport(target.get("viewport"))
        seen.append((browser.kind, target["name"], w, h))
        # Размер кадра идёт от размера окна: иначе сравнение вариантов между
        # собой прошло бы просто потому, что картинки одинаковые.
        seed = abs(hash((browser.kind, w, h))) % 997
        return _picture(seed, max(w // 4, 8), max(h // 4, 8)), None, None

    monkeypatch.setattr(blmod, "_shoot", fake_shoot)
    monkeypatch.setattr(blmod, "_auth_state", lambda *a, **k: None)

    def go(variants, targets, job=None):
        job = job or _Job()
        out = blmod._run_suite(job, targets, variants)
        return out, job, seen

    return blmod, mainmod, go, seen


TARGETS = [{"name": "checkout.png", "url": "http://example.test/checkout",
            "viewport": None, "selector": None, "wait": 0, "steps": []}]


# --------------------------------------------------------------------------- #
def test_each_variant_gets_its_own_baseline_directory(run_matrix):
    """Общий каталог означал бы, что вариант сравнивается с чужим кадром."""
    blmod, mainmod, go, _seen = run_matrix
    from vistest.matrix import expand

    variants = expand(["chromium", "firefox"], ["1440x900", "390x844"])
    go(variants, TARGETS)

    # Каталог спрашивается у самого хранилища: имя снимка проходит через
    # `_safe()`, и собирать путь руками значит проверять свою же копию правил.
    stores = {v.platform: blmod._store(v.platform, None) for v in variants}
    assert len(stores) == 4
    for platform, store in stores.items():
        assert (store.dir_for("checkout.png") / "baseline.png").exists(), \
            f"у варианта {platform} не появился эталон"
    # И это РАЗНЫЕ файлы, а не один и тот же под четырьмя путями.
    blobs = {(store.dir_for("checkout.png") / "baseline.png").read_bytes()
             for store in stores.values()}
    assert len(blobs) == 4, "варианты записали один и тот же кадр"

    # Базовый вариант — под ПРЕЖНИМ ключом, без размера в имени каталога.
    base = [v for v in variants if v.base]
    assert base and all("-1440x900" not in v.platform for v in base)


def test_every_variant_was_actually_shot_in_its_own_size(run_matrix):
    """Размер варианта обязан перекрыть размер из паспорта снимка.

    Иначе половина набора молча снимается не в тех размерах, а человек считает,
    что проверил мобильную вёрстку.
    """
    _blmod, _mainmod, go, seen = run_matrix
    from vistest.matrix import expand

    go(expand(["chromium"], ["1440x900", "390x844"]), TARGETS)
    sizes = {(w, h) for _br, _name, w, h in seen}
    assert sizes == {(1440, 900), (390, 844)}


def test_artifacts_of_two_variants_do_not_overwrite_each_other(run_matrix):
    """Каталог артефактов берётся по имени снимка, а имя у вариантов общее."""
    blmod, mainmod, go, _seen = run_matrix
    from vistest.matrix import expand

    variants = expand(["chromium", "firefox"], [])
    go(variants, TARGETS)                      # первый прогон — новые эталоны
    out, _job, _ = go(variants, TARGETS)       # второй — уже сравнение

    run_dir = blmod._cfg_fresh().runs_path() / out["run_id"]
    got = {p.parent.parent.name for p in run_dir.glob("*/*/result.json")}
    assert got == {v.slug for v in variants}, \
        f"варианты сложили артефакты в общий каталог: {got}"


def test_the_whole_matrix_lands_in_one_run(run_matrix):
    """Один прогон и один вердикт — ради этого матрица и делалась."""
    blmod, mainmod, go, _seen = run_matrix
    from vistest.matrix import expand

    variants = expand(["chromium", "firefox"], ["1440x900", "390x844"])
    go(variants, TARGETS)                      # эталоны
    out, job, _ = go(variants, TARGETS)        # сравнение

    runs = mainmod.db.query("SELECT id, run_key FROM run")
    assert len(runs) == 2, "каждый прогон матрицы обязан быть ОДНИМ прогоном"

    run_id = [r["id"] for r in runs if r["run_key"] == out["run_id"]][0]
    comps = mainmod.db.query(
        "SELECT s.platform FROM comparison c JOIN snapshot s ON s.id=c.snapshot_id"
        " WHERE c.run_id=?", (run_id,))
    assert len(comps) == 4
    assert {c["platform"] for c in comps} == {v.platform for v in variants}


def test_variants_do_not_collapse_into_one_snapshot_row(run_matrix):
    """Четыре разреза одного снимка — четыре строки, иначе история затирается."""
    blmod, mainmod, go, _seen = run_matrix
    from vistest.matrix import expand

    go(expand(["chromium", "firefox"], ["1440x900", "390x844"]), TARGETS)
    rows = mainmod.db.query("SELECT platform FROM snapshot WHERE name='checkout.png'")
    assert len(rows) == 4, f"варианты слиплись: {rows}"


def test_a_run_without_a_matrix_is_unchanged(run_matrix):
    """Один вариант — ровно прежнее поведение, включая ключ хранения."""
    blmod, mainmod, go, _seen = run_matrix
    from vistest.config import platform_key
    from vistest.matrix import expand

    variants = expand([], [])
    out, job, _ = go(variants, TARGETS)

    assert len(variants) == 1
    row = mainmod.db.one("SELECT platform FROM snapshot WHERE name='checkout.png'")
    assert row["platform"] == platform_key("chromium", 1.0)
    run = mainmod.db.one("SELECT platform FROM run WHERE run_key=?", (out["run_id"],))
    assert run["platform"] == platform_key("chromium", 1.0)


def test_a_snapshot_pinned_to_another_size_is_named_out_loud(run_matrix):
    """Матрица перекрывает размер из паспорта — и обязана про это сказать.

    Это единственное место, где матрица меняет смысл уже снятого эталона.
    Промолчать значит выдать человеку пачку падений и позволить решить, что
    сломался движок.
    """
    _blmod, _mainmod, go, _seen = run_matrix
    from vistest.matrix import expand

    targets = [dict(TARGETS[0], viewport="1280x800")]
    _out, job, _ = go(expand(["chromium"], ["1440x900", "390x844"]), targets)
    warned = [ln for ln in job.lines if "1280x800" in ln]
    assert warned and warned[0].startswith("warn:"), \
        f"расхождение размеров прошло молча: {job.lines}"


def test_every_browser_of_the_matrix_is_actually_launched(run_matrix, fake_playwright):
    """Проверка на самую обидную ошибку: матрица объявлена, а гоняет один браузер."""
    _blmod, _mainmod, go, _seen = run_matrix
    from vistest.matrix import expand

    go(expand(["chromium", "firefox", "webkit"], ["1440x900"]), TARGETS)
    assert {br.kind for br in fake_playwright} == {"chromium", "firefox", "webkit"}
    assert all(br.closed for br in fake_playwright), "браузер варианта не закрыт"
