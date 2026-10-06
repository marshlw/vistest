# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""What `expect_screenshot` accepts, and how it says no (review v1: 1.6–1.9)."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import numpy as np
import pytest

import vistest.library as lib
from vistest import expect_screenshot
from vistest.core import pngio
from vistest.library import context as _context
from vistest.library.errors import BaselineMissing, ScreenshotMismatch


@pytest.fixture
def ctx(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    context = _context.LibraryContext(root=tmp_path, platform_override="test")
    _context.install(context)
    yield context
    _context.uninstall()


def _png(fill: int = 200) -> bytes:
    return pngio.encode(np.full((20, 30, 3), fill, np.uint8))


# --- 1.6: Playwright's async API ------------------------------------------ #
class AsyncPage:
    __module__ = "playwright.async_api._generated"

    async def screenshot(self, **kw):  # pragma: no cover - never awaited
        return b""

    async def goto(self, url):  # pragma: no cover
        return None


def test_an_async_page_is_refused_by_name(ctx):
    with pytest.raises(TypeError, match="async API.*sync one.*await page.screenshot"):
        expect_screenshot(AsyncPage(), "home.png")


def test_a_screenshot_that_returns_a_coroutine_is_refused_and_closed(ctx):
    class Wrapper:
        def __init__(self):
            self.coroutine = None

        def screenshot(self, **kw):
            async def later():  # pragma: no cover - closed, never run
                return b""

            self.coroutine = later()
            return self.coroutine

    w = Wrapper()
    with pytest.raises(TypeError, match="async API"):
        expect_screenshot(w, "home.png")
    assert w.coroutine.cr_frame is None          # closed: no «never awaited» warning


ASYNC_CASE = textwrap.dedent('''
    import asyncio, sys, warnings
    warnings.simplefilter("ignore")
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("SKIP no playwright"); sys.exit(0)
    from vistest import expect_screenshot
    async def main():
        async with async_playwright() as p:
            try:
                browser = await p.chromium.launch()
            except Exception as e:
                print("SKIP", str(e).splitlines()[0]); return
            page = await browser.new_page()
            await page.set_content("<h1>hi</h1>")
            try:
                expect_screenshot(page, "async.png")
            except TypeError as e:
                print("TypeError:", e)
            await browser.close()
    asyncio.run(main())
''')


def test_a_real_async_page_gets_the_sentence_not_a_coroutine_error(tmp_path):
    script = tmp_path / "case.py"
    script.write_text(ASYNC_CASE, encoding="utf-8")
    done = subprocess.run([sys.executable, str(script)], cwd=tmp_path,
                          capture_output=True, text=True, timeout=120)
    out = done.stdout + done.stderr
    if out.startswith("SKIP"):
        pytest.skip(out.strip())
    assert "TypeError: expect_screenshot: this is Playwright's async API" in out, out
    assert "coroutine" not in out, out


# --- 1.7: the docstring says what the second look does -------------------- #
def test_the_docstring_does_not_promise_a_mask_the_check_never_makes():
    doc = " ".join(expect_screenshot.__doc__.split())
    assert "masked as live" not in doc
    assert "Nothing it sees is masked" in doc


# --- 1.8: targets of the wrong kind or range ------------------------------ #
def test_a_float_array_is_refused_with_the_conversion(ctx):
    unit = np.linspace(0.0, 1.0, 300).reshape(10, 10, 3)
    with pytest.raises(TypeError, match=r"float array, values 0…1.*\(a \* 255\)"):
        expect_screenshot(unit, "f.png")
    with pytest.raises(TypeError, match=r"values 200…200.*a\.round\(\)"):
        expect_screenshot(np.full((10, 10, 3), 200.0), "f.png")


def test_an_integer_array_within_the_range_is_taken_exactly(ctx):
    ctx.update = True
    expect_screenshot(np.full((10, 10, 3), 255, np.int64), "i.png")
    stored = pngio.decode(ctx.store.get(ctx.key("i.png", "test")))
    assert stored.dtype == np.uint8 and int(stored.min()) == 255


@pytest.mark.parametrize("array, words", [
    (np.full((10, 10, 3), 300, np.int64), "values 300…300"),
    (np.zeros((10, 10, 4), np.uint8), r"RGBA.*a\[\.\.\., :3\]"),
    (np.zeros((10, 10), np.uint8), "grey.*np.stack"),
    (np.zeros((10, 10, 3), bool), "array of bool"),
])
def test_an_array_that_is_not_a_picture_says_how_to_make_it_one(ctx, array, words):
    with pytest.raises(TypeError, match=words):
        expect_screenshot(array, "x.png")


def test_an_address_is_not_a_page(ctx):
    with pytest.raises(TypeError, match="is an address.*page.goto"):
        expect_screenshot("https://example.com", "home.png")


def test_a_selenium_element_is_told_what_to_pass(ctx):
    class WebElement:
        __module__ = "selenium.webdriver.remote.webelement"

        def screenshot(self, filename):  # pragma: no cover - refused by its options
            return True

    with pytest.raises(TypeError, match="screenshot_as_png"):
        expect_screenshot(WebElement(), "el.png")


def test_a_selenium_driver_is_told_what_to_pass(ctx):
    class WebDriver:
        __module__ = "selenium.webdriver.chrome.webdriver"

    with pytest.raises(TypeError, match=r"get_screenshot_as_png\(\)"):
        expect_screenshot(WebDriver(), "page.png")


def test_a_wrapper_whose_screenshot_takes_no_options_is_told_to_pass_bytes(ctx):
    class Wrapper:
        def screenshot(self, path):  # pragma: no cover - refused by its options
            return b""

    with pytest.raises(TypeError, match="does not take Playwright's options.*PNG bytes"):
        expect_screenshot(Wrapper(), "w.png")


# --- 1.9: names ------------------------------------------------------------ #
@pytest.mark.parametrize("name, words", [
    ("", "empty"), ("   ", "empty"), (".png", "empty"),
    ("/tmp/evil.png", "absolute path"), ("C:\\shots\\a.png", "absolute path"),
    ("\\\\server\\a.png", "absolute path"),
    ("../../evil.png", r"leads out.*'\.\.'"), ("shop/../../x.png", "leads out"),
])
def test_a_name_that_is_not_a_path_inside_the_baselines_is_refused(ctx, name, words):
    with pytest.raises(ValueError, match=words):
        expect_screenshot(_png(), name)
    assert not list(Path(ctx.baselines).rglob("*.png")) if ctx.baselines.exists() else True


def test_the_message_names_the_file_that_is_really_there(ctx):
    with pytest.raises(BaselineMissing) as missing:
        expect_screenshot(_png(), "checkout?step=2.png")
    head = str(missing.value).splitlines()[0]
    assert head == ("vistest: no baseline for 'checkout_step_2.png' "
                    "(test; the name given was 'checkout?step=2.png')")
    assert str(missing.value).splitlines()[1].endswith("checkout_step_2.png")

    ctx.update = True
    expect_screenshot(_png(), "checkout?step=2.png")
    ctx.update = False
    with pytest.raises(ScreenshotMismatch) as differs:
        expect_screenshot(_png(30), "checkout?step=2.png")
    assert str(differs.value).splitlines()[0].startswith(
        "vistest: 'checkout_step_2.png' differs from the baseline (test; the name given")


def test_an_ordinary_name_is_shown_as_it_was_given(ctx):
    with pytest.raises(BaselineMissing) as missing:
        expect_screenshot(_png(), "shop/home")
    assert str(missing.value).splitlines()[0] == \
        "vistest: no baseline for 'shop/home.png' (test)"


def test_the_library_exports_the_capture_error():
    assert lib.CaptureError.__name__ == "CaptureError"
