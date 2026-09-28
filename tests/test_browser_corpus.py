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


@pytest.mark.parametrize("key", [t.key for t in bc.TEMPLATES])
def test_every_target_is_one_element(key):
    """A mutation changes exactly one element: its label is about that element."""
    counts: dict[str, int] = {}
    for value in re.findall(r'data-m="([^"]+)"', _template(key)):
        for token in value.split():
            counts[token] = counts.get(token, 0) + 1
    assert {k: n for k, n in counts.items() if n != 1} == {}


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
            _, fonts = s.frame(t.key, fonts=True)
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
            assert bc.differing_pixels(a.frame(t.key), b.frame(t.key)) == 0, t.key
    finally:
        a.close()
        b.close()


def test_every_family_changes_the_page_the_same_way_twice(pw):
    """One magnitude of every family, on one template, in two browsers."""
    a, b = bc.Session(pw), bc.Session(pw)
    try:
        base = a.frame("table")
        for fam in bc.FAMILIES:
            mag = fam.magnitudes[-1]
            mutation, _ = bc.plan_mutation(a, "table", fam, mag)
            png = a.frame("table", mutation)
            assert bc.differing_pixels(base, png), f"{fam.key} changed nothing"
            assert bc.differing_pixels(png, b.frame("table", mutation)) == 0, fam.key
    finally:
        a.close()
        b.close()


def test_a_mutation_that_matches_nothing_is_refused(pw):
    s = bc.Session(pw)
    try:
        with pytest.raises(bc.CorpusError, match="0 elements"):
            s.frame("table", ('[data-m~="nothing"]', "", None))
        with pytest.raises(bc.CorpusError, match="did not run"):
            s.frame("table", ('[data-m~="fill"]', "throw new Error('x')", None))
    finally:
        s.close()


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


# --------------------------------------------------------------------------- #
#  Mutations and noise — the definitions
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("lab1, lab2, expected", [
    #  Sharma, Wu, Dalal (2005), test pairs 1, 7, 17, 25, 13 and 34.
    ((50, 2.6772, -79.7751), (50, 0, -82.7485), 2.0425),
    ((50, 0, 0), (50, -1, 2), 2.3669),
    ((50, 2.5, 0), (73, 25, -18), 27.1492),
    ((60.2574, -34.0099, 36.2677), (60.4626, -34.1751, 39.4387), 1.2644),
    ((50, 2.49, -0.001), (50, -2.49, 0.0009), 7.1792),
    ((2.0776, 0.0795, -1.135), (0.9033, -0.0636, -0.5514), 0.9082),
])
def test_the_colour_ruler_is_ciede2000(lab1, lab2, expected):
    assert round(bc.delta_e00_lab(lab1, lab2), 4) == expected


@pytest.mark.parametrize("rgb", [(37, 99, 235), (31, 41, 55), (229, 231, 235),
                                 (56, 189, 248), (234, 88, 12), (15, 118, 110)])
@pytest.mark.parametrize("target", [2, 4, 8, 15])
def test_a_colour_step_lands_near_its_delta_e(rgb, target):
    new, de = bc.shift_colour(rgb, target)
    assert abs(de - target) <= 0.25
    assert de == pytest.approx(bc.delta_e00(rgb, new))


def test_there_are_at_least_twenty_families_each_with_a_reason():
    assert len(bc.FAMILIES) >= 20
    assert len({f.key for f in bc.FAMILIES}) == len(bc.FAMILIES)
    for f in bc.FAMILIES:
        assert f.target in bc.REQUIRED_TARGETS, f.key
        assert f.why and f.what and f.magnitudes, f.key
        assert len({m.key for m in f.magnitudes}) == len(f.magnitudes), f.key
        for m in f.magnitudes:
            assert m.label in (bc.SIGNAL, bc.NOISE, bc.DISPUTED), (f.key, m.key)
            if m.label != bc.SIGNAL:
                assert m.why, f"{f.key}/{m.key}: a label that is not SIGNAL says why"


def test_the_judgement_calls_are_the_maintainers_and_say_so():
    by = {(f.key, m.key): m for f in bc.FAMILIES for m in f.magnitudes}
    for fam in ("fill", "text_color", "link_color"):
        assert by[(fam, "de2")].label == bc.DISPUTED
        assert by[(fam, "de4")].label == bc.SIGNAL
    assert by[("opacity", "0.98")].label == bc.NOISE
    assert by[("letter_spacing", "plus0.2px")].label == bc.SIGNAL
    for key in (("padding", "plus1px"), ("padding", "minus1px"),
                ("offset", "plus1px"), ("line_height", "plus1px")):
        assert by[key].label == bc.SIGNAL
    decided = [m for m in by.values() if m.why]
    assert decided and all(bc.DECIDED in m.why for m in decided)


def test_the_noise_configurations_are_the_ones_asked_for():
    keys = {c.key for c in bc.NOISE_CONFIGS}
    assert len(keys) >= 6
    assert {"hinting_none", "hinting_full", "no_lcd_no_subpixel",
            "geometric_precision", "shift_0.25px", "shift_0.5px",
            "full_chromium", "gpu_raster"} <= keys
