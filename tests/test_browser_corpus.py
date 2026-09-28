# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The browser corpus: its templates, its capture and its frozen frames.

The checks on the templates run everywhere. The checks that draw run only
where Chromium can be started; without it they are skipped with the reason,
never red.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import browser_corpus as bc  # noqa: E402

#: Anything that could reach the network from a template.
_REMOTE = re.compile(
    r"""(?:src|href)\s*=\s*["']?\s*(?:https?:)?//"""
    r"""|url\(\s*["']?\s*(?:https?:)?//""", re.IGNORECASE)


def _template(key: str) -> str:
    return (bc.TEMPLATE_DIR / f"{key}.html").read_text("utf-8")


# --------------------------------------------------------------------------- #
#  Templates
# --------------------------------------------------------------------------- #
def test_there_are_six_templates_and_nothing_else():
    on_disk = sorted(p.stem for p in bc.TEMPLATE_DIR.glob("*.html"))
    assert on_disk == sorted(t.key for t in bc.TEMPLATES)
    assert len(bc.TEMPLATES) >= 6


@pytest.mark.parametrize("key", [t.key for t in bc.TEMPLATES])
def test_a_template_loads_nothing_from_the_network(key):
    html = _template(key)
    assert not _REMOTE.search(html), f"{key}.html references a remote resource"
    css = (bc.TEMPLATE_DIR / "fonts.css").read_text("utf-8")
    assert not _REMOTE.search(css)
    for url in re.findall(r"""url\(["']?([^"')]+)""", css):
        assert (bc.TEMPLATE_DIR / url).is_file(), f"fonts.css: {url} is missing"


@pytest.mark.parametrize("key", [t.key for t in bc.TEMPLATES])
def test_a_template_carries_every_mutation_target(key):
    """One element per kind of change, or a family silently loses a page."""
    tokens = set()
    for value in re.findall(r'data-m="([^"]+)"', _template(key)):
        tokens.update(value.split())
    missing = [t for t in bc.REQUIRED_TARGETS if t not in tokens]
    assert not missing, f"{key}.html has no data-m target for {missing}"


def test_the_fonts_are_shipped_with_their_licence():
    fonts = sorted(p.name for p in (bc.TEMPLATE_DIR / "fonts").glob("*.ttf"))
    assert fonts, "no fonts next to the templates"
    licence = (bc.TEMPLATE_DIR / "fonts" / "LICENSE").read_text("utf-8")
    assert "Bitstream" in licence and "DejaVu" in licence


# --------------------------------------------------------------------------- #
#  Capture — only where Chromium runs
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def pw():
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


def test_every_template_is_drawn_with_the_shipped_fonts_only(pw):
    """A system font here means a glyph fell outside the subset."""
    s = bc.Session(pw)
    try:
        for t in bc.TEMPLATES:
            s.open(t.key)
            fonts = bc.fonts_used(s.page)
            assert fonts, t.key
            system = [f for f in fonts if not f["custom"]]
            assert not system, f"{t.key}: drawn partly with {system}"
    finally:
        s.close()


def test_two_fresh_browsers_draw_the_same_pixels(pw):
    """The control the whole corpus stands on."""
    a, b = bc.Session(pw), bc.Session(pw)
    try:
        for t in bc.TEMPLATES:
            a.open(t.key)
            b.open(t.key)
            assert bc.differing_pixels(a.shoot(), b.shoot()) == 0, t.key
    finally:
        a.close()
        b.close()


def test_the_environment_names_the_build_that_drew_the_frame(pw):
    env = bc.environment(pw)
    assert env["browser_build"].startswith("chromium_headless_shell-")
    assert env["browser_product"].startswith("HeadlessChrome/")
    assert env["viewport"] == bc.VIEWPORT
    assert env["capture"]["via"] == "vistest.library.targets.capture"
    assert not bc.env_mismatch(env, env)
    other = dict(env, chromium="0.0")
    assert bc.env_mismatch(env, other) == [
        f"chromium: recorded {env['chromium']!r}, here '0.0'"]
