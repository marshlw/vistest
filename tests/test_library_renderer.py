# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The renderer's canary in the library: drawn only where it decides something.

The first half runs anywhere: fake pages in fake contexts, and a canary
that is whatever the test says it is; then the lazy path against the eager
one on pairs of the browser corpus. The second half starts a real Chromium,
twice started otherwise, and is skipped — not failed — without one.
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


class FakeBrowser:
    """What a context belongs to: the canary is kept per browser."""


class FakeContext:
    """A browser context whose canary is whatever `drawn` says."""

    def __init__(self, drawn: bytes | Exception, browser: FakeBrowser | None = None):
        self.drawn = drawn
        self.draws = 0
        self.browser = browser

    def new_page(self):  # pragma: no cover - `draw` is replaced below
        raise AssertionError("the canary is drawn by the patched draw()")


class FakePage:
    viewport_size = {"width": 320, "height": 240}

    def __init__(self, png: bytes, context: FakeContext, ratio: float = 1.0):
        self.png = png
        self.context = context
        self.ratio = ratio

    def goto(self, *_): ...

    def evaluate(self, script, *args):
        return self.ratio if "devicePixelRatio" in script else True

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
def test_the_canary_is_drawn_once_per_browser_and_scale():
    """pytest-playwright opens a context per test; the canary is not paid per test."""
    browser = FakeBrowser()
    one, two = FakeContext(canary_png(), browser), FakeContext(canary_png(), browser)
    first = fp.of_target(FakePage(frame(), one), stable_timeout_ms=0)
    again = fp.of_target(FakePage(frame(), two), stable_timeout_ms=0)
    assert one.draws + two.draws == 1
    assert first.png == again.png and first.drawn_ms is not None and again.drawn_ms is None
    hidpi = FakeContext(canary_png(5), browser)
    assert fp.of_target(FakePage(frame(), hidpi, ratio=2.0),
                        stable_timeout_ms=0).png == canary_png(5)       # another scale
    other = FakeContext(canary_png(3), FakeBrowser())
    assert fp.of_target(FakePage(frame(), other), stable_timeout_ms=0).png != first.png
    alone = FakeContext(canary_png(4))                  # no browser: the context is the key
    assert fp.of_target(FakePage(frame(), alone), stable_timeout_ms=0).png == canary_png(4)


def test_a_canary_that_could_not_be_drawn_is_tried_again_next_time():
    """A failure is this check's, not the browser's for good."""
    browser = FakeBrowser()
    flaky = FakeContext(RuntimeError("busy for a moment"), browser)
    first = fp.of_target(FakePage(frame(), flaky), stable_timeout_ms=0)
    assert first.png is None and "busy for a moment" in first.why
    flaky.drawn = canary_png()
    again = fp.of_target(FakePage(frame(), flaky), stable_timeout_ms=0)
    assert again.png == canary_png() and flaky.draws == 2
    kept = fp.of_target(FakePage(frame(), flaky), stable_timeout_ms=0)
    assert kept.png == canary_png() and flaky.draws == 2       # a drawn one is kept


class OwnedContext(FakeContext):
    """The context of `browser.new_page()`: it opens no other tab."""

    def __init__(self, browser):
        super().__init__(_canary.TabRefused("Error: Please use browser.new_context()"),
                         browser)


class BrowserWithContexts(FakeBrowser):
    def __init__(self, drawn: bytes):
        self.drawn = drawn
        self.opened: list[tuple[dict, FakeContext]] = []

    def new_context(self, **options):
        context = FakeContext(self.drawn, self)
        context.closed = False

        def close():
            context.closed = True
        context.close = close
        self.opened.append((options, context))
        return context


@pytest.mark.parametrize("scale", [1.0, 2.0])
def test_a_page_that_opens_no_tab_has_its_canary_drawn_in_a_new_context(scale):
    browser = BrowserWithContexts(canary_png(6))
    run = fp.of_target(FakePage(frame(), OwnedContext(browser), ratio=scale),
                       stable_timeout_ms=0)
    assert run.png == canary_png(6), run.why
    [(options, fresh)] = browser.opened
    assert options == {"device_scale_factor": scale,
                       "viewport": FakePage.viewport_size}           # the page's own
    assert fresh.closed and fresh.draws == 1


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


def test_a_check_that_passes_draws_no_canary_and_says_so(ctx):
    accept(ctx, FakePage(frame(), FakeContext(canary_png())), "home.png")
    context = FakeContext(canary_png())
    expect_screenshot(FakePage(frame(), context), "home.png")
    row = rows(ctx)[-1]
    assert context.draws == 0 and "canary_ms" not in row["capture"]
    assert row["renderer"] == {"status": "not_checked", "pixels": None,
                               "why": "the check passed",
                               "line": "renderer: not checked (the check passed)"}


def test_a_failure_against_a_baseline_with_a_canary_draws_this_runs(ctx):
    accept(ctx, FakePage(frame(), FakeContext(canary_png())), "home.png")
    context = FakeContext(canary_png())
    with pytest.raises(ScreenshotMismatch) as e:
        expect_screenshot(FakePage(frame(90), context), "home.png")
    assert context.draws == 1
    row = rows(ctx)[-1]
    assert row["renderer"]["line"] == "renderer: same as the baseline's"
    assert row["capture"]["canary_ms"] >= 0
    assert "\n  renderer: same as the baseline's\n" in str(e.value)


def test_a_failure_against_a_baseline_without_a_canary_draws_nothing(ctx):
    accept(ctx, frame(), "flat.png")                      # bytes: no canary to keep
    context = FakeContext(canary_png())
    with pytest.raises(ScreenshotMismatch):
        expect_screenshot(FakePage(frame(90), context), "flat.png", platform="")
    assert context.draws == 0
    assert rows(ctx)[-1]["renderer"]["line"].startswith(
        "renderer: unknown — the baseline names no canary")


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
    assert line == ("renderer: unknown — the baseline names no canary — accepted "
                    "before the canary existed, or not from a page")
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
#  Lazy is eager: on pairs of the browser corpus, with engine v2
# --------------------------------------------------------------------------- #
def _corpus_sample():
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import browser_corpus as bc

    m = bc.load_manifest()
    picked: dict = {}
    #  Pairs another renderer drew (where the canary can change something),
    #  every pair of opacity 0.98 (the ones that pass), and a few that fail
    #  whatever the renderer: a sample the suite can afford. (Every one of the
    #  462 pairs was run the same way for the E1 report.)
    wanted = ("render:hinting_none", "render:full_chromium", "os_windows",
              "text_color@hinting_none", "one_char@full_chromium", "fill de2",
              "render:geometric_precision", "padding", "one_char")
    for c in m["cases"]:
        family = c["family"] if c["label"] != "DISPUTED" or c["kind"] != "mutation" \
            else f"{c['family']} {c['magnitude']}"
        if family in wanted:
            picked.setdefault((family,), c)
        elif c["family"] == "opacity" and c["magnitude"] == "0.98":
            picked.setdefault((c["name"],), c)
        elif c["name"] == "article/render/shift_0.25px":    # its bookmark: moments
            picked.setdefault((c["name"],), c)
    return bc, m, list(picked.values())


class _Result:
    def __init__(self, failed):
        self.failed = failed


@pytest.mark.parametrize("first, base, run, compares, drawn, used", [
    (False, canary_png(), canary_png(3), 1, 0, False),   # passed: nothing to decide
    (True, None, canary_png(3), 1, 0, False),            # the baseline names no canary
    (True, canary_png(), None, 1, 1, False),             # this run cannot draw one
    (True, canary_png(), canary_png(), 2, 1, True),      # the same renderer: again, with it
    (True, canary_png(), canary_png(3), 2, 1, True),     # another renderer: again, with it
])
def test_the_canary_is_drawn_and_used_only_where_it_decides(first, base, run, compares,
                                                             drawn, used):
    calls, draws = [], []

    def compare_with(renderer):
        calls.append(renderer)
        return _Result(first if renderer is None else False)

    def this_run():
        draws.append(1)
        return fp.RunCanary(png=run, why="" if run else "no page")

    result, renderer = fp.compare_lazily(compare_with, lambda: base, this_run)
    assert (len(calls), len(draws), renderer is not None) == (compares, drawn, used)
    assert calls[0] is None


def test_the_lazy_path_gives_the_eager_verdicts_on_the_corpus():
    """A known renderer only lets rules take regions out: what passes without
    the canary passes with it, and a failure is judged again with it — also
    when the renderer is the same (a page moved by a fraction, f5)."""
    from vistest.core import compare

    bc, m, sample = _corpus_sample()
    fps = {k: (bc.CORPUS_DIR / r["file"]).read_bytes()
           for k, r in m["renderers"]["fingerprints"].items()}
    passed_without = 0
    for c in sample:
        a = pngio.read(bc.CORPUS_DIR / c["expected"])
        b = pngio.read(bc.CORPUS_DIR / c["actual"])
        pair = (fps.get(c["renderer"]["expected"]), fps.get(c["renderer"]["actual"]))

        def compare_with(renderer, a=a, b=b):
            return compare(a, b, engine="v2", renderer=renderer)

        eager = compare_with(pair)
        drawn = []
        lazy, used = fp.compare_lazily(
            compare_with, lambda pair=pair: pair[0],
            lambda pair=pair, drawn=drawn: drawn.append(1) or fp.RunCanary(png=pair[1]))
        assert lazy.verdict == eager.verdict, c["name"]
        assert [(r.x, r.y, r.w, r.h) for r in lazy.regions] == \
            [(r.x, r.y, r.w, r.h) for r in eager.regions], c["name"]
        if not lazy.failed:
            passed_without += used is None
            assert not drawn or used is not None, c["name"]
        if c["name"] == "article/render/shift_0.25px":
            #  The same renderer lets the coverage moments in (f5): the
            #  first comparison fails, the second, with the canaries, passes.
            assert eager.verdict.value == "pass" and used is not None and drawn
    assert len(sample) == 16 and passed_without > 0


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


def test_a_real_browser_draws_its_canary_once_in_a_tab_of_its_own(ctx, playwright):
    browser = playwright.chromium.launch()
    try:
        context, page = _page(browser)
        accept(ctx, page, "real.png")                      # writing: the canary is drawn
        assert "canary_ms" in rows(ctx)[-1]["capture"]
        assert len(context.pages) == 1                    # the tab is closed again
        expect_screenshot(page, "real.png")                # passes: nothing drawn
        row = rows(ctx)[-1]
        assert "canary_ms" not in row["capture"]
        assert row["renderer"]["status"] == "not_checked"
        second, page2 = _page(browser)                     # another context, same browser
        page2.set_content(PAGE.replace("Hamburgefonstiv 0123", "Quite another line of text"))
        with pytest.raises(ScreenshotMismatch) as e:
            expect_screenshot(page2, "real.png")           # fails: the kept canary decides
        row = rows(ctx)[-1]
        assert "canary_ms" not in row["capture"]           # not drawn again for this browser
        assert row["renderer"]["line"] == "renderer: same as the baseline's"
        assert "renderer: same as the baseline's" in str(e.value)
        second.close()
        context.close()
    finally:
        browser.close()


@pytest.mark.parametrize("scale, viewport", [
    (1, None), (2, None),
    #  Narrower than the canary (360 px): there the canary is drawn otherwise,
    #  so the new context takes the page's viewport as well.
    (1, {"width": 320, "height": 120}),
])
def test_browser_new_page_draws_the_canary_a_context_of_the_user_draws(
        playwright, scale, viewport):
    """`browser.new_page()` makes a context that opens no other tab. The canary
    is then drawn in a new context of the same browser at the same device
    scale factor and viewport, closed again — and is the one, pixel for
    pixel, that a context the user opened in that browser draws."""
    options = {"device_scale_factor": scale, **({"viewport": viewport} if viewport else {})}
    browser = playwright.chromium.launch()
    try:
        mine = browser.new_context(**options)
        theirs = fp.of_target(mine.new_page(), stable_timeout_ms=5000)
        assert theirs.png is not None, theirs.why
        fp._BY_BROWSER.clear()                             # not from the cache
        alone = browser.new_page(**options)
        contexts = len(browser.contexts)
        run = fp.of_target(alone, stable_timeout_ms=5000)
        assert run.png is not None, run.why
        assert run.drawn_ms is not None                    # drawn, not kept
        assert len(browser.contexts) == contexts           # and its context closed
        assert np.array_equal(pngio.decode(run.png), pngio.decode(theirs.png))
        alone.close()
        mine.close()
    finally:
        browser.close()


def test_a_browser_started_otherwise_is_another_renderer(ctx, playwright):
    plain = playwright.chromium.launch()
    other = playwright.chromium.launch(args=["--font-render-hinting=none"])
    try:
        _, page = _page(plain)
        accept(ctx, page, "hinting.png")
        _, page2 = _page(other)
        #  A change, drawn otherwise.
        page2.set_content(PAGE.replace("Hamburgefonstiv 0123", "Quite another line of text"))
        with pytest.raises(ScreenshotMismatch) as e:
            expect_screenshot(page2, "hinting.png")
        assert "renderer: different from the baseline's (canary: " in str(e.value)
        row = rows(ctx)[-1]
        assert row["renderer"]["status"] == "changed" and row["renderer"]["pixels"] > 0
    finally:
        plain.close()
        other.close()
