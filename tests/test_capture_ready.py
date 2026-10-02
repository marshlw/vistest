# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Readiness before the frames, and the second look after a failure.

The capture-hazard stand (tests/capture_hazards) measured what the first
versions of both did on a page whose data comes late: frames «held still»
because the spinner was frozen, and the second look masked the data that
arrived between two frames — a real change in it as green as the plain page.
These are the properties that close that, without a browser:

* the wait asks every step at once, gives each up at its own limit, and says
  which one gave up;
* a page that was not ready gets no masking from the second look;
* a one-off change after the page looked ready — spinner → data — is not
  life: the later frame is compared, nothing is masked, and a change in the
  data fails;
* what keeps changing is named, not masked.
"""

from __future__ import annotations

import numpy as np
import pytest

from vistest.capture import inflight, ready
from vistest.core.retry import Later, live_mask, second_look
from vistest.models import CompareResult, Verdict


# --------------------------------------------------------------------------- #
#  The wait
# --------------------------------------------------------------------------- #
class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def _page(**over):
    base = {"readyState": "complete", "fonts": "loaded", "loaders": [], "images": [],
            "quiet": 1000, "what": ""}
    base.update(over)
    return base


def test_a_page_with_nothing_to_wait_for_is_ready_at_once():
    clock = Clock()
    got = ready.wait(lambda: _page(), quiet_ms=80, limit_ms=5000, sleep=clock.sleep,
                     clock=clock)
    assert got.ok is True
    assert got.ms == 0
    assert got.path == "page"
    assert {s.name for s in got.steps if s.ok is None} == {"network"}


def test_every_step_must_hold_at_the_same_moment():
    clock = Clock()
    seq = iter([_page(loaders=["div.spinner (named)"]),
                _page(loaders=[], quiet=0),           # the data replaced the spinner
                _page(quiet=40), _page(quiet=90)])
    got = ready.wait(lambda: next(seq), quiet_ms=80, limit_ms=5000,
                     sleep=clock.sleep, clock=clock)
    assert got.ok is True
    assert got.ms > 0


def test_requests_in_flight_are_waited_for_when_they_are_counted():
    clock = Clock()
    counts = iter([(1, ["http://x/api"]), (1, ["http://x/api"]), (0, [])])
    got = ready.wait(lambda: _page(), inflight=lambda: next(counts), quiet_ms=80,
                     limit_ms=5000, sleep=clock.sleep, clock=clock)
    assert got.ok is True and got.path == "requests"
    network = next(s for s in got.steps if s.name == "network")
    assert network.ok is True and network.ms > 0


def test_a_step_that_never_holds_is_given_up_at_its_limit_and_said():
    clock = Clock()
    got = ready.wait(lambda: _page(), inflight=lambda: (2, ["http://x/poll", "http://x/sse"]),
                     quiet_ms=80, limit_ms=1000, sleep=clock.sleep, clock=clock)
    assert got.ok is False
    assert 1000 <= got.ms < 1100
    text = got.text()
    assert "not ready" in text and "2 requests" in text and "http://x/poll" in text
    assert "1000 ms" in text


def test_a_page_that_cannot_be_asked_claims_nothing():
    got = ready.wait(lambda: None, limit_ms=5000)
    assert got.ok is None and got.text() == ""
    got = ready.wait(lambda: (_ for _ in ()).throw(RuntimeError("gone")), limit_ms=5000)
    assert got.ok is None and "could not be asked" in got.note


def test_the_wait_can_be_switched_off():
    calls = []
    got = ready.wait(lambda: calls.append(1), limit_ms=0)
    assert got.ok is None and calls == []


def test_the_loader_rule_is_written_down_with_where_it_errs():
    assert "Wrong when" in ready.LOADER_RULE
    for word in ("spin", "skeleton", "shimmer", "progress"):
        assert word in ready.PROBE_JS


# --------------------------------------------------------------------------- #
#  Counting requests
# --------------------------------------------------------------------------- #
class FakeRequest:
    def __init__(self, url, kind="fetch", page=None):
        self.url, self.resource_type = url, kind
        self.frame = type("F", (), {"page": page})()


class FakeContext:
    def __init__(self):
        self.handlers = {}

    def on(self, event, fn):
        self.handlers.setdefault(event, []).append(fn)

    def emit(self, event, request):
        for fn in self.handlers.get(event, []):
            fn(request)


def test_requests_are_counted_per_page_and_streams_and_images_are_not():
    context = FakeContext()
    count = inflight.track(context)
    assert inflight.track(context) is count, "tracking twice counts once"
    page, other = object(), object()
    api, sse, theirs = (FakeRequest("http://x/api", page=page),
                        FakeRequest("http://x/sse", "eventsource", page),
                        FakeRequest("http://x/other", page=other))
    picture = FakeRequest("http://x/below-the-fold.png", "image", page)
    for r in (api, sse, theirs, picture):
        context.emit("request", r)
    assert count.inflight(page) == (1, ["http://x/api"])
    assert count.inflight() == (2, ["http://x/api", "http://x/other"])
    context.emit("requestfinished", api)
    context.emit("requestfailed", theirs)
    assert count.inflight() == (0, [])


def test_something_that_is_not_a_context_is_not_tracked():
    assert inflight.track(None) is None
    assert inflight.track(object()) is None


# --------------------------------------------------------------------------- #
#  The second look
# --------------------------------------------------------------------------- #
H, W = 60, 80


def frame(*, data: int | None = None, spinner: bool = False, tick: int = 0):
    """A page: a header, a slot that shows a spinner or a number, a counter."""
    img = np.full((H, W, 3), 240, dtype=np.uint8)
    img[2:8, 2:70] = 30                                   # header
    if spinner:
        img[20:36, 30:46] = 120                           # the spinner
    if data is not None:
        img[20:36, 10:70] = data                          # the data
    if tick:
        img[50:58, 60:76] = (tick * 37) % 200             # a counter
    return img


def comparer(baseline):
    def compare(img, mask=None):
        a = np.abs(baseline.astype(int) - img.astype(int)).max(axis=2) > 6
        if mask is not None:
            a &= ~mask
        res = CompareResult(name="x", verdict=Verdict.FAIL if a.any() else Verdict.PASS)
        res.notes = []
        res.size_actual = (img.shape[1], img.shape[0])
        return res
    return compare


def test_a_spinner_that_became_the_data_is_compared_as_the_data():
    """Spinner → data after the page looked ready: the later frame is the verdict."""
    baseline = frame(data=90)
    cmp = comparer(baseline)
    first = frame(spinner=True)
    look = second_look(cmp(first), first,
                       lambda: Later([frame(data=90), frame(data=90)], settled=True),
                       lambda f, m: cmp(f, m))
    assert not look.result.failed
    assert look.unstable is None, "nothing is masked"
    assert np.array_equal(look.frame, frame(data=90))
    assert any("changed after it looked ready" in n for n in look.result.notes)


def test_a_change_in_data_that_arrived_late_cannot_be_hidden():
    """The S1 hole: the same page, its late data changed — it must fail."""
    baseline = frame(data=90)
    cmp = comparer(baseline)
    first = frame(spinner=True)
    look = second_look(cmp(first), first,
                       lambda: Later([frame(data=160), frame(data=160)], settled=True),
                       lambda f, m: cmp(f, m))
    assert look.result.failed
    assert look.unstable is None


def test_a_page_that_was_not_ready_gets_no_second_look():
    baseline = frame(data=90)
    cmp = comparer(baseline)
    first = frame(spinner=True)
    taken = []
    look = second_look(cmp(first), first, lambda: taken.append(1),
                       lambda f, m: cmp(f, m), ready=False,
                       not_ready="1 request still in flight after 5000 ms")
    assert look.result.failed and taken == []
    assert any("not ready" in n and "masked nothing" in n and "in flight" in n
               for n in look.result.notes)


def test_what_keeps_changing_is_named_not_masked():
    baseline = frame(data=90, tick=1)
    cmp = comparer(baseline)
    first = frame(data=90, tick=2)
    later = Later([frame(data=90, tick=3), frame(data=90, tick=4),
                   frame(data=90, tick=5)], settled=False)
    look = second_look(cmp(first), first, lambda: later, lambda f, m: cmp(f, m))
    assert look.result.failed, "a live counter fails until it is masked on purpose"
    assert look.live is not None and look.live[52, 66] and not look.live[25, 40]
    assert any("kept changing" in n and "not masked" in n for n in look.result.notes)


def test_a_steady_failure_is_confirmed():
    baseline = frame(data=90)
    cmp = comparer(baseline)
    first = frame(data=160)
    look = second_look(cmp(first), first,
                       lambda: Later([frame(data=160), frame(data=160)], settled=True),
                       lambda f, m: cmp(f, m))
    assert look.result.failed
    assert any("nothing on this page moved" in n for n in look.result.notes)


def test_live_means_changed_in_two_intervals():
    a, b, c = frame(data=90), frame(data=160), frame(data=160)
    assert not live_mask([a, b, c]).any(), "once is a transition"
    assert live_mask([frame(tick=1), frame(tick=2), frame(tick=3)]).any()


@pytest.mark.parametrize("pauses_ms", [(100, 250, 500)])
def test_the_second_look_does_not_confirm_within_one_tick(pauses_ms):
    """Two identical frames 100 ms apart prove nothing about a 200 ms counter."""
    from vistest.library.targets import frames_after

    clock = Clock()
    ticks = iter([b"1", b"2", b"3", b"4", b"5", b"6", b"7", b"8"])  # 100, 350, 850 ms
    frames, settled = frames_after(lambda: next(ticks), b"1", timeout_ms=1200,
                                   pauses=pauses_ms, min_ms=300, clock=clock,
                                   sleep=clock.sleep)
    assert not settled
    assert frames[0] == b"1" and len(frames) >= 3
