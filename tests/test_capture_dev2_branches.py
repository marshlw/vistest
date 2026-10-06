# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The branches of 0.2.0.dev2 that only the capture-hazard stand used to reach
(review v1, 7.4): the request count's view of one page, the «still on its way»
hints, the pictures that finished late, the service's count, and the
passport's records when they are missing or broken. No browser needed."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import vistest.library as lib
from vistest.capture import inflight
from vistest.integrations.driver import PlaywrightDriver
from vistest.library import page_facts, targets
from vistest.models import CompareResult, DiffRegion, Verdict
from vistest.storage.base import SnapshotMeta


# --- the request count, for one page ----------------------------------------- #
@pytest.fixture
def clock(monkeypatch):
    state = SimpleNamespace(now=100.0)
    monkeypatch.setattr(inflight, "time", SimpleNamespace(monotonic=lambda: state.now))
    return state


class _Req:
    resource_type = "fetch"
    method = "GET"

    def __init__(self, path, page=None):
        self.url = f"http://x{path}?token=secret"
        self.frame = SimpleNamespace(page=page) if page is not None else None


def test_calm_since_is_false_while_a_request_of_the_page_is_out(clock):
    count = inflight.RequestCount()
    page = object()
    out = _Req("/api/orders", page)
    count.started(out)
    clock.now = 105.0
    assert count.calm_since(101.0, page) is False
    count.ended(out)
    assert count.calm_since(101.0, page) is False      # it ended after 101
    assert count.calm_since(106.0, page) is True


def test_a_request_of_another_page_is_not_this_pages(clock):
    count = inflight.RequestCount()
    mine, other = object(), object()
    count.started(_Req("/api/theirs", other))
    assert count.inflight(mine)[0] == 0
    assert count.calm_since(100.5, mine) is True
    assert count.inflight(other)[0] == 1


def test_a_request_whose_frame_cannot_be_read_is_counted(clock):
    class ServiceWorker(_Req):
        @property
        def frame(self):
            raise RuntimeError("a service worker's request has no frame")

        @frame.setter
        def frame(self, value):
            pass

    count = inflight.RequestCount()
    count.started(ServiceWorker("/sw.js"))
    assert count.inflight(object())[0] == 1


def test_a_request_whose_url_cannot_be_read_is_still_counted(clock):
    class NoUrl:
        resource_type = "fetch"
        method = "GET"
        frame = None

        @property
        def url(self):
            raise RuntimeError("gone")

    count = inflight.RequestCount()
    count.started(NoUrl())
    assert count.inflight()[0] == 1


# --- the service's count ------------------------------------------------------ #
class _Context:
    def __init__(self):
        self.handlers = {}

    def on(self, event, handler):
        self.handlers[event] = handler


def test_the_services_driver_reads_the_count_of_its_pages_context(clock):
    context = _Context()
    page = SimpleNamespace(context=context, url="http://x/shop")
    driver = PlaywrightDriver(page)
    assert driver.inflight() is None                   # nobody counts yet
    count = inflight.track(context)
    context.handlers["request"](_Req("/api/a", page))
    held = driver.inflight()
    assert held is not None and held()[0] == 1
    assert held()[1] == ["GET /api/a"]
    assert count is inflight.for_page(page)


# --- what finished after the picture ----------------------------------------- #
class _Page:
    def __init__(self, answer):
        self.answer = answer
        self.asked = []

    def evaluate(self, script, *args):
        self.asked.append((script, args))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def test_pictures_that_finished_late_are_named_without_their_query():
    page = _Page(["http://x/img/1.png?v=3#a", "http://x/img/2.png"])
    assert targets._pictures_since(page, 12.5) == ["image http://x/img/1.png",
                                                    "image http://x/img/2.png"]
    assert page.asked[0][1] == ({"t": 12.5},)


def test_no_mark_or_no_answer_names_no_picture():
    assert targets._pictures_since(_Page(["a"]), None) == []
    assert targets._pictures_since(_Page(RuntimeError("no")), 1.0) == []
    assert targets._pictures_since(_Page(None), 1.0) == []


# --- the hints of a failure, when something was still on its way ----------- #
def _shot(moving=(), late=("GET /api/orders",), stable=False, namer=None):
    return SimpleNamespace(
        facts={}, moving=tuple(moving), namer=namer, retake=None,
        stability=SimpleNamespace(stable=stable),
        late=(lambda: list(late)) if late is not None else None)


def _result(notes=()):
    r = CompareResult(name="p.png", verdict=Verdict.FAIL)
    r.regions = [DiffRegion(x=0, y=0, w=10, h=10)]
    r.notes = list(notes)
    return r


def test_an_element_changed_by_a_late_request_is_told_to_wait_not_to_mask():
    hints = lib._failure_hints(_shot(moving=[{"sel": "#orders", "tag": "table"}]),
                               _result(), None, None, True, False)
    assert hints == ["#orders changed after the picture because GET /api/orders was "
                     "still on its way: wait for it in the test (the element it fills, "
                     "or the response) before the check — a mask would hide it"]


def test_a_late_request_with_no_element_to_name_is_still_said():
    hints = lib._failure_hints(_shot(), _result(), None, None, True, False)
    assert hints == ["after the picture GET /api/orders was still on its way and the "
                     "page changed: wait for it in the test before the check"]


def test_a_late_request_on_a_page_that_did_not_change_is_not_blamed():
    hints = lib._failure_hints(_shot(moving=[{"sel": "#x", "tag": "div"}], stable=True),
                               _result(), None, None, True, False)
    assert hints and "still on its way" not in hints[0]
    assert "changes by itself" in hints[0]


def test_the_late_names_leave_out_inline_pictures_and_survive_an_error():
    shot = _shot(late=("image data:image/png;base64,xx", "GET /a"))
    assert lib._late_names(shot) == ["GET /a"]

    def broken():
        raise RuntimeError("the page went away")

    assert lib._late_names(SimpleNamespace(late=broken)) == []
    assert lib._late_names(SimpleNamespace(late=None)) == []


# --- the passport's records, missing or broken -------------------------------- #
def _with_capture(capture):
    return SnapshotMeta(capture=capture)


@pytest.mark.parametrize("capture, dpr", [
    ({"version": 3}, 2.0),                       # no record in the passport
    ({"device_scale_factor": "2"}, 2.0),         # not a number
    ({"device_scale_factor": 2.0}, None),        # this run could not read it
    ({"device_scale_factor": 2.0}, 2.0),         # the same
])
def test_the_dpr_line_is_said_only_for_two_different_numbers(capture, dpr):
    shot = SimpleNamespace(facts={"dpr": dpr} if dpr is not None else {"x": 1})
    assert lib._dpr_line(_with_capture(capture), shot) == ""


def test_the_dpr_line_names_both_numbers():
    shot = SimpleNamespace(facts={"dpr": 1.0})
    line = lib._dpr_line(_with_capture({"device_scale_factor": 2.0}), shot)
    assert "device scale factor 2" in line and "this check at 1" in line


@pytest.mark.parametrize("record", [None, "1,2", {"x": 1}, {"x": "a", "y": 2}, {"y": 1}])
def test_a_broken_window_scroll_record_is_no_record(record):
    assert page_facts.window_scroll(record) is None


def test_a_window_scroll_record_is_read_in_whole_pixels():
    assert page_facts.window_scroll({"x": "3", "y": 1200.0}) == {"x": 3, "y": 1200}


def test_a_window_scroll_from_a_passport_that_cannot_be_read_is_none(tmp_path):
    class Store:
        def meta(self, key):
            raise OSError("unreadable passport")

    ctx = lib.LibraryContext(root=tmp_path, platform_override="p")
    page = SimpleNamespace(goto=lambda *a: None, screenshot=lambda **k: b"",
                           viewport_size={"width": 8, "height": 8})
    assert lib._window_from_passport(ctx, Store(), page, "a.png", None, "css") is None
