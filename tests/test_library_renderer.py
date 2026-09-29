# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The renderer's canary in the library: drawn once per context, kept by sha.

The first half runs anywhere: fake pages in fake contexts, and a canary
that is whatever the test says it is. The second half starts a real
Chromium, twice started otherwise, and is skipped — not failed — without one.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from vistest import expect_screenshot
from vistest.core import pngio
from vistest.core.settings import ConfigError
from vistest.library import canary as _canary
from vistest.library import context as _context
from vistest.library import fingerprint as fp
from vistest.library.errors import ScreenshotMismatch
from vistest.storage.base import SnapshotKey, SnapshotMeta
from vistest.storage.file import FileStore


def frame(fill: int = 40, size: tuple[int, int] = (60, 80)) -> bytes:
    return pngio.encode(np.full((*size, 3), fill, np.uint8))


def canary_png(dots: int = 0) -> bytes:
    img = np.full((20, 40, 3), 255, np.uint8)
    img[5:9, 5:30] = 17
    img[15, :dots] = 0
    return pngio.encode(img)


@pytest.fixture
def ctx(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    context = _context.LibraryContext(root=tmp_path)
    _context.install(context)
    yield context
    _context.uninstall()


def accept(ctx, target, name="page.png", **kw):
    ctx.update = True
    try:
        return expect_screenshot(target, name, **kw)
    finally:
        ctx.update = False


def rows(ctx) -> list[dict]:
    found = [json.loads(p.read_text("utf-8")) for p in ctx.parts_dir.glob("*.json")]
    return sorted(found, key=lambda r: r["written_at"])


class FakeContext:
    """A browser context whose canary is whatever `drawn` says."""

    def __init__(self, drawn: bytes | Exception):
        self.drawn = drawn
        self.draws = 0

    def new_page(self):  # pragma: no cover - `draw` is replaced below
        raise AssertionError("the canary is drawn by the patched draw()")


class FakePage:
    viewport_size = {"width": 320, "height": 240}

    def __init__(self, png: bytes, context: FakeContext):
        self.png = png
        self.context = context

    def goto(self, *_): ...

    def evaluate(self, script, *args):
        return 1.0 if "devicePixelRatio" in script else True

    def screenshot(self, **kwargs):
        return self.png


@pytest.fixture(autouse=True)
def fake_draw(monkeypatch):
    def draw(context, *, stable_timeout_ms):
        if not isinstance(context, FakeContext):   # a real one: the real canary
            return real(context, stable_timeout_ms=stable_timeout_ms)
        context.draws += 1
        if isinstance(context.drawn, Exception):
            raise context.drawn
        return context.drawn
    real = _canary.draw
    monkeypatch.setattr(fp._canary, "draw", draw)


# --------------------------------------------------------------------------- #
#  The passport and the store
# --------------------------------------------------------------------------- #
def test_a_passport_without_a_canary_reads_and_writes_as_it_always_did():
    meta = SnapshotMeta(width=2, height=3, sha256="x")
    assert "renderer" not in meta.to_dict()
    assert SnapshotMeta.from_dict(meta.to_dict()) == meta


def test_a_passport_names_its_canary_by_sha_and_version():
    rec = {"sha256": "a" * 64, "canary_version": 1}
    meta = SnapshotMeta(width=2, height=3, renderer=rec)
    assert meta.to_dict()["renderer"] == rec
    assert SnapshotMeta.from_dict(meta.to_dict()).renderer == rec


@pytest.mark.parametrize("bad", [
    {"sha256": "a" * 64},
    {"sha256": "not hex", "canary_version": 1},
    {"sha256": "a" * 64, "canary_version": "1"},
    {"sha256": "a" * 64, "canary_version": True},
    "a" * 64,
])
def test_a_passport_with_a_malformed_canary_is_loud(bad):
    with pytest.raises(ConfigError, match="renderer must be"):
        SnapshotMeta.from_dict({"renderer": bad}, source="x.json")


def test_the_canaries_are_not_baselines(tmp_path):
    store = FileStore(tmp_path)
    store.put(SnapshotKey("home.png", "chromium-320x240"), frame())
    folder = tmp_path / fp.RENDERERS_DIR
    folder.mkdir()
    (folder / ("f" * 64 + ".png")).write_bytes(canary_png())
    assert [k.as_str() for k in store.list()] == ["chromium-320x240/home"]


# --------------------------------------------------------------------------- #
#  Once per context
# --------------------------------------------------------------------------- #
def test_the_canary_is_drawn_once_per_context():
    context = FakeContext(canary_png())
    page = FakePage(frame(), context)
    first = fp.of_target(page, stable_timeout_ms=0)
    again = fp.of_target(page, stable_timeout_ms=0)
    assert context.draws == 1
    assert first.png == again.png and first.drawn_ms is not None and again.drawn_ms is None
    other = FakeContext(canary_png(3))
    assert fp.of_target(FakePage(frame(), other), stable_timeout_ms=0).png != first.png


def test_no_page_no_canary_and_the_reason():
    assert fp.of_target(frame(), stable_timeout_ms=0).why.startswith(
        "the screenshot was handed in")
    broken = FakeContext(RuntimeError("tab refused"))
    run = fp.of_target(FakePage(frame(), broken), stable_timeout_ms=0)
    assert run.png is None and "tab refused" in run.why


def test_a_canary_is_stored_once_per_sha(tmp_path):
    store = FileStore(tmp_path)
    run = fp.RunCanary(png=canary_png(), sha256=fp._sha(canary_png()))
    rec = fp.keep(store, run)
    assert rec == {"sha256": run.sha256, "canary_version": _canary.CANARY_VERSION}
    path = tmp_path / fp.RENDERERS_DIR / f"{run.sha256}.png"
    stamp = path.stat().st_mtime_ns
    assert fp.keep(store, run) == rec and path.stat().st_mtime_ns == stamp
    assert fp.of_baseline(store, SnapshotMeta(renderer=rec)) == (canary_png(), "")


@pytest.mark.parametrize("passport, why", [
    (None, "names no canary"),
    (SnapshotMeta(renderer={"sha256": "b" * 64, "canary_version": 1}), "commit it"),
    (SnapshotMeta(renderer={"sha256": "b" * 64, "canary_version": 99}), "version 99"),
])
def test_a_baseline_canary_that_cannot_be_had_says_why(tmp_path, passport, why):
    data, said = fp.of_baseline(FileStore(tmp_path), passport)
    assert data is None and why in said


# --------------------------------------------------------------------------- #
#  expect_screenshot
# --------------------------------------------------------------------------- #
def test_an_accepted_baseline_carries_the_canary_of_its_renderer(ctx):
    context = FakeContext(canary_png())
    accept(ctx, FakePage(frame(), context), "home.png")
    [path] = [p for p in ctx.store.root.rglob("home.json")]
    passport = json.loads(path.read_text("utf-8"))
    sha = fp._sha(canary_png())
    assert passport["renderer"] == {"sha256": sha, "canary_version": _canary.CANARY_VERSION}
    assert (ctx.store.root / fp.RENDERERS_DIR / f"{sha}.png").read_bytes() == canary_png()
    assert rows(ctx)[-1]["capture"]["canary_ms"] >= 0


def test_the_same_renderer_is_one_line_in_the_report(ctx):
    context = FakeContext(canary_png())
    accept(ctx, FakePage(frame(), context), "home.png")
    expect_screenshot(FakePage(frame(), FakeContext(canary_png())), "home.png")
    row = rows(ctx)[-1]
    assert row["renderer"] == {"status": "same", "pixels": None,
                               "line": "renderer: same as the baseline's"}


def test_another_renderer_is_named_with_its_pixels_in_the_failure(ctx):
    accept(ctx, FakePage(frame(), FakeContext(canary_png())), "home.png")
    other = FakePage(frame(90), FakeContext(canary_png(dots=7)))
    with pytest.raises(ScreenshotMismatch) as e:
        expect_screenshot(other, "home.png")
    assert "\n  renderer: different from the baseline's (canary: 7 px)\n" in str(e.value)
    assert rows(ctx)[-1]["renderer"]["line"] == \
        "renderer: different from the baseline's (canary: 7 px)"


def test_bytes_have_no_renderer_and_the_report_says_so(ctx):
    accept(ctx, frame(), "flat.png")
    [path] = list(ctx.store.root.rglob("flat.json"))
    assert "renderer" not in json.loads(path.read_text("utf-8"))
    with pytest.raises(ScreenshotMismatch) as e:
        expect_screenshot(frame(90), "flat.png")
    line = rows(ctx)[-1]["renderer"]["line"]
    assert line.startswith("renderer: unknown — the baseline names no canary")
    assert "the screenshot was handed in" in line
    assert f"\n  {line}\n" in str(e.value)


def test_an_accept_that_changes_nothing_does_not_touch_the_passport(ctx):
    context = FakeContext(canary_png())
    page = FakePage(frame(), context)
    accept(ctx, page, "home.png")
    [path] = list(ctx.store.root.rglob("home.json"))
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    accept(ctx, page, "home.png")
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


# --------------------------------------------------------------------------- #
#  A real Chromium
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def playwright():
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        manager = sync_api.sync_playwright().start()
    except Exception as e:  # pragma: no cover - depends on the machine
        pytest.skip(f"Playwright could not start: {e}")
    try:
        manager.chromium.launch().close()
    except Exception as e:  # pragma: no cover - depends on the machine
        manager.stop()
        pytest.skip(f"no Chromium to run against: {str(e).splitlines()[0]}")
    yield manager
    manager.stop()


PAGE = "<body style='margin:0;font:14px sans-serif'><p>Hamburgefonstiv 0123</p></body>"


def _page(browser):
    context = browser.new_context(viewport={"width": 320, "height": 120})
    page = context.new_page()
    page.set_content(PAGE)
    return context, page


def test_a_real_context_draws_its_canary_once_in_a_tab_of_its_own(ctx, playwright):
    browser = playwright.chromium.launch()
    try:
        context, page = _page(browser)
        accept(ctx, page, "real.png")
        assert "canary_ms" in rows(ctx)[-1]["capture"]
        assert len(context.pages) == 1                    # the tab is closed again
        expect_screenshot(page, "real.png")
        row = rows(ctx)[-1]
        assert "canary_ms" not in row["capture"]          # drawn once per context
        assert row["renderer"]["line"] == "renderer: same as the baseline's"
        second, page2 = _page(browser)                     # another context, same renderer
        expect_screenshot(page2, "real.png")
        assert rows(ctx)[-1]["renderer"]["status"] == "same"
        second.close()
        context.close()
    finally:
        browser.close()


def test_a_browser_started_otherwise_is_another_renderer(ctx, playwright):
    plain = playwright.chromium.launch()
    other = playwright.chromium.launch(args=["--font-render-hinting=none"])
    try:
        _, page = _page(plain)
        accept(ctx, page, "hinting.png")
        _, page2 = _page(other)
        try:
            expect_screenshot(page2, "hinting.png")
        except ScreenshotMismatch as e:
            assert "renderer: different from the baseline's (canary: " in str(e)
        row = rows(ctx)[-1]
        assert row["renderer"]["status"] == "changed" and row["renderer"]["pixels"] > 0
    finally:
        plain.close()
        other.close()
