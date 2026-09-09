# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""`expect_screenshot` itself: the arguments, and what each of them does.

`test_library_mode.py` runs the scenario a person meets — four pytest runs in a
temporary project. This file is the other half: everything that is cheaper to
check in-process, and everything whose failure would be silent rather than red.

The silent ones are the point. A threshold that is accepted and not applied, a
mask that is taken and ignored, a passport whose numbers never reach the
comparison — none of those fail a test suite. They make one pass when it should
not, which is the only bug a testing tool cannot afford.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from vistest import expect_screenshot
from vistest.core import pngio
from vistest.core.thresholds import ThresholdError
from vistest.library import context as _context
from vistest.library.errors import BaselineMissing, ScreenshotMismatch
from vistest.storage.base import SnapshotKey, SnapshotMeta
from vistest.storage.file import FileStore


def frame(fill: int = 40, box: tuple[int, int, int, int] | None = None) -> bytes:
    picture = np.full((80, 120, 3), fill, np.uint8)
    if box:
        x, y, w, h = box
        picture[y:y + h, x:x + w] = 235
    return pngio.encode(picture)


@pytest.fixture
def ctx(tmp_path: Path, monkeypatch):
    """A context of our own, so nothing here depends on the plugin."""
    monkeypatch.chdir(tmp_path)
    context = _context.LibraryContext(root=tmp_path)
    _context.install(context)
    yield context
    _context.uninstall()


def accept(ctx, png: bytes, name: str = "page.png", **kw):
    """Put a baseline in place the way `--vistest-update` would."""
    ctx.update = True
    try:
        return expect_screenshot(png, name, **kw)
    finally:
        ctx.update = False


# --------------------------------------------------------------------------- #
#  What can be passed as the target
# --------------------------------------------------------------------------- #
class FakePage:
    """A page as duck-typed by `targets`: it can screenshot, and it navigates."""

    viewport_size = {"width": 1440, "height": 900}

    def __init__(self, png: bytes):
        self.png = png
        self.calls: list[dict] = []

    def goto(self, *_): ...

    def locator(self, selector):
        return f"locator({selector})"

    def screenshot(self, **kwargs):
        self.calls.append(kwargs)
        return self.png


def test_bytes_a_path_an_array_and_a_pil_image_are_all_accepted(ctx, tmp_path):
    png = frame()
    accept(ctx, png, "page.png")

    path = tmp_path / "shot.png"
    path.write_bytes(png)
    array = pngio.decode(png)
    from PIL import Image

    image = Image.fromarray(array)

    for target in (png, str(path), path, array, image):
        assert expect_screenshot(target, "page.png").verdict.value == "pass"


def test_a_playwright_page_is_recognised_without_importing_playwright(ctx):
    import sys

    page = FakePage(frame())
    accept(ctx, page, "page.png")
    assert "playwright" not in sys.modules
    assert page.calls[0]["type"] == "png"


def test_something_that_cannot_be_photographed_says_what_is_accepted(ctx):
    with pytest.raises(TypeError) as e:
        expect_screenshot(object(), "page.png")
    assert "Page" in str(e.value) and "bytes" in str(e.value)


# --------------------------------------------------------------------------- #
#  profile
# --------------------------------------------------------------------------- #
def test_the_platform_decides_the_directory(ctx):
    accept(ctx, frame(), "page.png", platform="chromium-1440x900")
    assert (ctx.baselines / "chromium-1440x900" / "page.png").exists()


def test_without_a_live_page_there_is_no_platform_directory(ctx):
    """Guessing a browser name would write it into somebody's repository."""
    accept(ctx, frame(), "page.png")
    assert (ctx.baselines / "page.png").exists()


def test_a_page_supplies_its_own_browser_and_size(ctx):
    accept(ctx, FakePage(frame()), "page.png")
    written = [p for p in ctx.baselines.rglob("*.png")]
    assert len(written) == 1
    assert "1440x900" in written[0].parent.name


# --------------------------------------------------------------------------- #
#  threshold
# --------------------------------------------------------------------------- #
def test_a_bare_number_is_the_severity_and_it_reaches_the_engine(ctx):
    """`threshold=60` has to arrive in the `DiffConfig` the comparison uses.

    Asserted through the limit the failure reports rather than through a
    changed verdict, and deliberately: a solid block on a flat background
    scores severity 100, the top of the scale, so no threshold can make that
    particular picture pass and a verdict flip would prove nothing here. The
    number in the message is read straight off the config the engine was
    given, so it is the plumbing itself that is being checked.

    The verdict side is covered where it is achievable — see the mask tests.
    """
    accept(ctx, frame(), "page.png")
    changed = frame(box=(10, 10, 60, 30))

    with pytest.raises(ScreenshotMismatch) as default:
        expect_screenshot(changed, "page.png")
    assert "limit 25.0" in str(default.value)

    with pytest.raises(ScreenshotMismatch) as raised:
        expect_screenshot(changed, "page.png", threshold=60)
    assert "limit 60.0" in str(raised.value)

    with pytest.raises(ScreenshotMismatch) as both:
        expect_screenshot(changed, "page.png",
                          threshold={"fail_severity": 60,
                                     "max_changed_area_pct": 30})
    assert "limit 60.0" in str(both.value) and "limit 30.00%" in str(both.value)


def test_an_unknown_threshold_is_refused_at_the_call(ctx):
    accept(ctx, frame(), "page.png")
    with pytest.raises(ThresholdError) as e:
        expect_screenshot(frame(), "page.png", threshold={"fail_severtiy": 40})
    assert "fail_severtiy" in str(e.value)
    assert "fail_severity" in str(e.value)          # the correct spelling is offered


def test_a_threshold_outside_its_range_is_refused_at_the_call(ctx):
    accept(ctx, frame(), "page.png")
    with pytest.raises(ThresholdError):
        expect_screenshot(frame(), "page.png", threshold=900)


def test_the_passport_thresholds_reach_the_comparison(ctx):
    """A snapshot may carry its own limits, and they have to be applied.

    Accepted silently, this is the worst failure available: the file says the
    threshold was raised, the verdict is computed with the old one, and nothing
    anywhere disagrees out loud.
    """
    accept(ctx, frame(), "page.png")
    changed = frame(box=(10, 10, 60, 30))
    with pytest.raises(ScreenshotMismatch) as before:
        expect_screenshot(changed, "page.png")
    assert "limit 25.0" in str(before.value)

    store = FileStore(ctx.baselines)
    key = SnapshotKey("page.png")
    store.put(key, store.get(key),
              meta=SnapshotMeta(thresholds={"fail_severity": 70.0}))

    with pytest.raises(ScreenshotMismatch) as after:
        expect_screenshot(changed, "page.png")
    assert "limit 70.0" in str(after.value)


def test_the_call_beats_the_passport(ctx):
    """Layers: config -> project -> passport -> call, and the call is innermost.

    It is written next to one particular check and knows more about it than a
    number stored months ago.
    """
    accept(ctx, frame(), "page.png")
    store = FileStore(ctx.baselines)
    store.put(SnapshotKey("page.png"), store.get(SnapshotKey("page.png")),
              meta=SnapshotMeta(thresholds={"fail_severity": 70.0}))

    with pytest.raises(ScreenshotMismatch) as e:
        expect_screenshot(frame(box=(10, 10, 60, 30)), "page.png", threshold=42)
    assert "limit 42.0" in str(e.value)


def test_setting_a_passport_threshold_does_not_need_a_new_picture(ctx):
    """`put` skips an identical picture — it must not skip the passport with it.

    The shortcut exists so that `--vistest-update` over an unchanged suite
    leaves the working tree alone. Applied to an explicit passport it would
    accept a threshold, store none of it, and report success.
    """
    accept(ctx, frame(), "page.png")
    store = FileStore(ctx.baselines)
    key = SnapshotKey("page.png")

    stored = store.put(key, store.get(key),
                       meta=SnapshotMeta(thresholds={"fail_severity": 70.0}))
    assert stored.thresholds == {"fail_severity": 70.0}
    assert store.meta(key).thresholds == {"fail_severity": 70.0}


def test_a_damaged_passport_is_refused_by_name(ctx):
    accept(ctx, frame(), "page.png")
    (ctx.baselines / "page.json").write_text('{"fail_severity": 40}', "utf-8")
    with pytest.raises(ValueError) as e:
        expect_screenshot(frame(), "page.png")
    assert "page.json" in str(e.value) and "fail_severity" in str(e.value)


# --------------------------------------------------------------------------- #
#  mask
# --------------------------------------------------------------------------- #
def test_a_box_is_excluded_from_the_comparison_and_not_painted_in(ctx):
    accept(ctx, frame(), "page.png")
    changed = frame(box=(10, 10, 60, 30))

    with pytest.raises(ScreenshotMismatch):
        expect_screenshot(changed, "page.png")
    assert expect_screenshot(changed, "page.png",
                             mask=[(10, 10, 60, 30)]).verdict.value == "pass"

    #  And the baseline still holds the real pixels: a mask is a decision about
    #  this comparison, not an edit of what was approved.
    stored = pngio.decode((ctx.baselines / "page.png").read_bytes())
    assert int(stored[20, 20].max()) == 40


def test_a_selector_mask_goes_to_playwright(ctx):
    page = FakePage(frame())
    accept(ctx, page, "page.png", mask=["#promo"])
    assert page.calls[0]["mask"] == ["locator(#promo)"]


def test_a_selector_mask_without_a_page_says_so(ctx):
    with pytest.raises(ValueError) as e:
        expect_screenshot(frame(), "page.png", mask=["#promo"])
    assert "box" in str(e.value)


def test_a_mask_that_is_neither_is_refused(ctx):
    with pytest.raises(TypeError):
        expect_screenshot(frame(), "page.png", mask=[42])


# --------------------------------------------------------------------------- #
#  The rest of the surface
# --------------------------------------------------------------------------- #
def test_a_missing_baseline_names_the_file_and_the_flag(ctx):
    with pytest.raises(BaselineMissing) as e:
        expect_screenshot(frame(), "page.png")
    message = str(e.value)
    assert str(ctx.baselines / "page.png") in message
    assert "--vistest-update" in message
    assert not (ctx.baselines / "page.png").exists()


def test_both_failures_are_assertion_errors(ctx):
    """So pytest colours them as failures and soft-assert wrappers keep working."""
    assert issubclass(BaselineMissing, AssertionError)
    assert issubclass(ScreenshotMismatch, AssertionError)


def test_the_mismatch_carries_the_result_and_the_artifacts(ctx):
    accept(ctx, frame(), "page.png")
    with pytest.raises(ScreenshotMismatch) as e:
        expect_screenshot(frame(box=(10, 10, 60, 30)), "page.png")

    error = e.value
    assert error.result.max_severity > 0
    assert Path(error.artifacts["diff"]).exists()
    assert Path(error.artifacts["actual"]).exists()


def test_a_store_can_be_passed_in(ctx, tmp_path):
    """The whole point of the protocol: somewhere else to keep them."""
    elsewhere = FileStore(tmp_path / "vendor" / "snapshots")
    ctx.update = True
    expect_screenshot(frame(), "page.png", store=elsewhere)
    ctx.update = False

    assert (tmp_path / "vendor" / "snapshots" / "page.png").exists()
    assert not (ctx.baselines / "page.png").exists()
    assert expect_screenshot(frame(), "page.png",
                             store=elsewhere).verdict.value == "pass"


def test_every_check_leaves_a_row_for_the_report(ctx):
    accept(ctx, frame(), "one.png")
    expect_screenshot(frame(), "one.png")
    with pytest.raises(BaselineMissing):
        expect_screenshot(frame(), "two.png")

    from vistest.report.library import read_parts

    parts = read_parts(ctx.parts_dir)
    assert {r["name"] for r in parts.entries} == {"one.png", "two.png"}
    assert {r["verdict"] for r in parts.entries} == {"pass", "new_baseline"}
    assert parts.collisions == {} and parts.unreadable == 0


# --------------------------------------------------------------------------- #
#  Saying which platform, and which platform has one
# --------------------------------------------------------------------------- #
def test_a_target_with_no_platform_says_where_the_baselines_went(ctx, recwarn):
    """Not guessing a browser is right; doing it silently is not.

    Somebody driving Selenium and passing `bytes` gets one directory for every
    machine that runs the suite, and then a developer's macOS and a Linux CI
    compare against the same file. Font rendering differs between them
    physically, so the run goes red for a reason that has nothing to do with
    the page — and nothing in the layout hints that this is what happened.
    """
    _context.reset_warning()
    accept(ctx, frame(), "page.png")

    said = [str(w.message) for w in recwarn
            if "byte targets have no platform" in str(w.message)]
    assert said, [str(w.message) for w in recwarn]
    assert "vistest_platform" in said[0]

    #  Once per process. A line per check would be noise in a suite of two
    #  hundred, and noise is not read.
    before = len(recwarn)
    accept(ctx, frame(), "second.png")
    assert not [w for w in list(recwarn)[before:]
                if "byte targets" in str(w.message)]


def test_a_live_page_does_not_trigger_that_warning(ctx, recwarn):
    _context.reset_warning()
    accept(ctx, FakePage(frame()), "page.png")
    assert not [w for w in recwarn if "byte targets" in str(w.message)]


def test_a_baseline_on_another_platform_is_named_in_the_message(ctx):
    """The most confusing failure this tool has: «no baseline» in a repository
    that visibly contains one.

    The reason is always the same — the baseline belongs to another platform —
    and until now nothing in the message said so.
    """
    accept(ctx, frame(), "page.png", platform="darwin-chromium-2x-1440x900")

    with pytest.raises(BaselineMissing) as e:
        expect_screenshot(frame(), "page.png",
                          platform="linux-chromium-1x-1440x900")

    message = str(e.value)
    assert "darwin-chromium-2x-1440x900" in message
    assert "per-platform" in message


def test_the_root_counts_as_a_platform_in_that_message(ctx):
    """Approved from bytes, then compared from a page — the same trap, mirrored."""
    accept(ctx, frame(), "page.png")

    with pytest.raises(BaselineMissing) as e:
        expect_screenshot(frame(), "page.png", platform="chromium-1440x900")
    assert "the root" in str(e.value)


def test_a_first_ever_baseline_says_nothing_about_other_platforms(ctx):
    with pytest.raises(BaselineMissing) as e:
        expect_screenshot(frame(), "page.png")
    assert "found for" not in str(e.value)


# --------------------------------------------------------------------------- #
#  What the report is allowed to hide
# --------------------------------------------------------------------------- #
def test_two_tests_writing_one_key_are_a_collision(ctx):
    """Grouped by key, counted by test. The unit-level half of the -n 2 test."""
    from vistest.report.library import read_parts, write_part

    write_part(ctx.parts_dir, {"key": "k/page", "name": "page.png",
                               "verdict": "pass", "nodeid": "tests/a.py::test_a"})
    write_part(ctx.parts_dir, {"key": "k/page", "name": "page.png",
                               "verdict": "fail", "nodeid": "tests/b.py::test_b"})

    parts = read_parts(ctx.parts_dir)
    assert parts.collisions == {
        "k/page": ["tests/a.py::test_a", "tests/b.py::test_b"]}
    assert len(parts.entries) == 1        # still one row; now it is explained


def test_one_test_writing_twice_is_a_retry(ctx):
    from vistest.report.library import read_parts, write_part

    for verdict in ("fail", "pass"):
        write_part(ctx.parts_dir, {"key": "k/page", "name": "page.png",
                                   "verdict": verdict,
                                   "nodeid": "tests/a.py::test_a"})

    parts = read_parts(ctx.parts_dir)
    assert parts.collisions == {}
    assert len(parts.entries) == 1


def test_an_unreadable_part_is_counted_not_hidden(ctx):
    from vistest.report.library import build, write_part

    write_part(ctx.parts_dir, {"key": "k/page", "name": "page.png",
                               "verdict": "pass", "nodeid": "tests/a.py::test_a"})
    (ctx.parts_dir / "truncated.json").write_text('{"key": "x", "verd', "utf-8")
    (ctx.parts_dir / "not-an-object.json").write_text("[1, 2, 3]", "utf-8")

    parts = build(ctx.parts_dir, ctx.report)
    assert parts.unreadable == 2
    assert len(parts.entries) == 1
    assert "2 checks could not be read into this report" in \
        ctx.report.read_text("utf-8")
