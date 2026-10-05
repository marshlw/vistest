# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""0.2.0.dev2: the capture and the words of its messages (no browser needed).

The readiness race has its own test in test_capture_ready.py. Here: the second
look counts a page as settled only when nothing was in flight; the pointer and
focus switches; the window scroll's one line; the device scale factor line;
the baseline folder; and the rewritten messages.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from vistest.capture import inflight
from vistest.capture.ready import LIBRARY_CAPTURE_VERSION, old_way
import vistest.library as lib
from vistest.library import targets
from vistest.library.errors import plain
from vistest.storage.file import default_root


class _Req:
    resource_type = "fetch"
    method = "GET"
    frame = None

    def __init__(self, path="/api/x"):
        self.url = f"http://x{path}?token=secret"


@pytest.fixture
def clock(monkeypatch):
    state = SimpleNamespace(now=100.0)
    monkeypatch.setattr(inflight, "time", SimpleNamespace(monotonic=lambda: state.now))
    return state


# --- the request count ------------------------------------------------------ #
def test_idle_is_zero_while_a_request_is_out_and_counts_from_its_end(clock):
    count = inflight.RequestCount()
    clock.now = 101.0
    r = _Req()
    count.started(r)
    assert count.idle_ms() == 0 and count.snapshot()[2] == 0
    clock.now = 101.2
    count.ended(r)
    assert count.idle_ms() == 0
    clock.now = 101.25
    assert count.idle_ms() == 50
    assert count.snapshot() == (0, [], 50)


def test_the_idle_of_a_page_does_not_see_the_ends_of_requests_it_ignores(clock):
    count = inflight.RequestCount()
    beacon = _Req("/track")
    count.started(beacon)
    clock.now = 100.5
    count.ended(beacon)
    clock.now = 101.0
    assert count.idle_ms(None, ("*/track",)) == 1000     # since the context began
    assert count.idle_ms() == 500


def test_touched_since_names_requests_without_their_query(clock):
    count = inflight.RequestCount()
    a, b = _Req("/api/a"), _Req("/api/b")
    count.started(a)
    clock.now = 100.4
    count.ended(a)
    count.started(b)
    page = SimpleNamespace(url="http://x/p")
    got = count.touched_since(100.1, page)
    assert got == ["GET /api/b", "GET /api/a"] or got == ["GET /api/a", "GET /api/b"]
    assert all("secret" not in n for n in got)
    assert count.touched_since(100.9, page)[0] == "GET /api/b"      # still out
    count.ended(b)
    clock.now = 101.0
    assert count.touched_since(100.9, page) == []
    assert count.calm_since(100.9) and not count.calm_since(100.1)


# --- the second look -------------------------------------------------------- #
def test_two_identical_later_frames_are_settled_only_when_the_page_was_calm():
    ticks = {"t": 0.0}

    def clock():
        return ticks["t"]

    def sleep(s):
        ticks["t"] += s

    asked: list[float] = []

    def calm(since):
        asked.append(since)
        return len(asked) > 2          # a request is still around for the first two pairs

    frames, settled = targets.frames_after(lambda: b"x", b"x", timeout_ms=5000,
                                           clock=clock, sleep=sleep, calm=calm)
    assert settled and len(asked) == 3 and len(frames) >= 3
    frames, settled = targets.frames_after(lambda: b"x", b"x", timeout_ms=600,
                                           clock=clock, sleep=sleep,
                                           calm=lambda since: False)
    assert not settled


# --- the pointer and the focus ---------------------------------------------- #
@pytest.mark.parametrize("reset,keep,blur,want", [
    (None, None, None, (True, False)),          # the default: pointer away, focus left
    (True, None, None, (True, True)),           # as it was
    (False, None, None, (False, False)),        # as it was: nothing touched
    (None, True, None, (False, False)),         # a hover on purpose
    (None, None, True, (True, True)),
    (True, True, None, (False, True)),          # the pointer kept, the focus off
    (None, True, True, (False, True)),
])
def test_pointer_plan(reset, keep, blur, want):
    cfg = SimpleNamespace(reset_hover_focus=False, keep_pointer=False, blur_focus=False)
    assert targets.pointer_plan(cfg, reset, keep, blur) == want


def test_the_config_decides_when_the_call_does_not():
    assert targets.pointer_plan(SimpleNamespace(reset_hover_focus=True), None, None, None) \
        == (True, True)
    assert targets.pointer_plan(SimpleNamespace(keep_pointer=True), None, None, None) \
        == (False, False)
    assert targets.pointer_plan(SimpleNamespace(blur_focus=True), None, None, None) \
        == (True, True)


# --- the pointer hint: a concrete element only ------------------------------ #
@pytest.mark.parametrize("item,size,concrete", [
    ({"sel": "#root", "box": [0, 0, 1440, 900]}, (1440, 900), False),
    ({"sel": "html"}, None, False),
    ({"sel": "body"}, None, False),
    ({"sel": "body > div"}, None, True),
    ({"sel": "#app", "box": [0, 0, 400, 300]}, (400, 300), False),
    ({"sel": "main.page", "box": [0, 0, 1400, 880]}, (1440, 900), False),  # window-sized
    ({"sel": "#save", "box": [30, 30, 80, 40]}, (400, 300), True),
    # a button whose path runs through #root is the button, not the page
    ({"sel": "#root > div > button.save", "box": [3, 3, 60, 20]}, (400, 300), True),
])
def test_only_a_concrete_element_is_blamed(item, size, concrete):
    assert lib._concrete(item, size) is concrete


# --- the window scroll's line ----------------------------------------------- #
def test_the_window_note_says_where_it_was_and_where_it_was_set():
    note = targets._window_note({"x": 0, "y": 646}, {"x": 0, "y": 0}, {"at": [0, 0], "max": [0, 900]})
    assert "(0, 646)" in note and "(0, 0)" in note and "restore_scroll" in note
    assert targets._window_note({"x": 0, "y": 0}, {"x": 0, "y": 0}, {"at": [0, 0]}) == ""


def test_a_page_too_short_to_scroll_that_far_says_so():
    note = targets._window_note({"x": 0, "y": 0}, {"x": 0, "y": 991},
                                {"at": [0, 300], "max": [0, 300]})
    assert "could not be set to (0, 991)" in note and "(0, 300)" in note
    assert "difference stays" in note


# --- the device scale factor ------------------------------------------------ #
def test_a_different_device_scale_factor_is_said_in_one_line():
    passport = SimpleNamespace(capture={"device_scale_factor": 1.0})
    shot = SimpleNamespace(facts={"dpr": 2.0})
    line = lib._dpr_line(passport, shot)
    assert "device scale factor 1" in line and "2" in line
    assert lib._dpr_line(passport, SimpleNamespace(facts={"dpr": 1.0})) == ""
    assert lib._dpr_line(SimpleNamespace(capture={}), shot) == ""
    assert lib._dpr_line(None, shot) == ""


# --- the baseline folder ---------------------------------------------------- #
def test_the_baseline_folder_does_not_make_a_tests_folder(tmp_path):
    assert default_root(tmp_path) == tmp_path / "__vistest__"
    (tmp_path / "tests").mkdir()
    assert default_root(tmp_path) == tmp_path / "tests" / "__vistest__"


# --- the words -------------------------------------------------------------- #
def test_a_redrawn_text_with_the_same_colour_is_explained():
    text = plain("strokes redrawn within 1 px; ink #889ab3 → #889ab3, ΔE00 0.00 (below 2)")
    assert "letters moved by less than 1 px" in text
    assert "the colour is the same (#889ab3)" in text
    assert "not explained as noise" in text
    assert "ΔE00 0.00" not in text


def test_a_redrawn_text_with_a_close_colour_keeps_the_numbers():
    text = plain("strokes redrawn within 1 px; ink #889ab3 → #8a9bb3, ΔE00 0.90 (below 2)")
    assert "within 2 ΔE00" in text and "#889ab3 → #8a9bb3" in text


def test_other_sentences_are_left_alone():
    s = "ink colour: #343649 → #586074, ΔE00 14.2"
    assert plain(s) == s


def test_the_old_way_line_names_the_library_changes():
    text = old_way("pytest --vistest-update=changed", LIBRARY_CAPTURE_VERSION)
    assert "pointer" in text and "window scroll" in text and "version 3" in text
    assert "readiness wait" in old_way("x", 2)
