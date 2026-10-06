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
    def __init__(self, url, kind="fetch", page=None, method="GET"):
        self.url, self.resource_type, self.method = url, kind, method
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
    assert count.inflight(page) == (1, ["GET x/api"])
    assert count.inflight() == (2, ["GET x/api", "GET x/other"])
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
    assert any("the difference from the baseline is real, not motion" in n
               for n in look.result.notes)


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


# --------------------------------------------------------------------------- #
#  S2b
# --------------------------------------------------------------------------- #
def test_a_page_that_switches_between_two_states_does_not_pass_by_luck():
    """Frames A, B, A, B after the failure, A the baseline: a fail, and named.

    A carousel, a blinking banner, a flickering break: the last frame being the
    right one is luck, and the page did not hold still after it.
    """
    baseline = frame(data=90)
    cmp = comparer(baseline)
    a, b = frame(data=90), frame(data=160)
    look = second_look(cmp(b), b, lambda: Later([a, b, a], settled=False),
                       lambda f, m: cmp(f, m))
    assert look.result.failed
    assert look.live is not None and look.live[25, 40]
    assert any("not a result" in n for n in look.result.notes)


def test_a_later_frame_that_never_settled_does_not_pass_either():
    baseline = frame(data=90)
    cmp = comparer(baseline)
    first = frame(spinner=True)
    look = second_look(cmp(first), first,
                       lambda: Later([frame(data=90)], settled=False),
                       lambda f, m: cmp(f, m))
    assert look.result.failed
    assert any("did not hold still" in n for n in look.result.notes)


def test_a_request_is_named_by_method_and_path_never_by_its_query():
    context = FakeContext()
    count = inflight.track(context)
    page = type("P", (), {"url": "https://shop.example/cart"})()
    poll = FakeRequest("https://shop.example/api/poll?token=SECRET&x=1", page=page)
    poll.method = "GET"
    beacon = FakeRequest("https://stats.example/collect?id=7", page=page)
    beacon.method = "POST"
    for r in (poll, beacon):
        context.emit("request", r)
    n, held = count.inflight(page)
    assert n == 2
    assert held == ["GET /api/poll", "POST stats.example/collect"]
    assert not any("SECRET" in h or "?" in h for h in held)


def test_ignore_requests_takes_a_long_poll_out_of_the_wait():
    context = FakeContext()
    count = inflight.track(context)
    page = type("P", (), {"url": "https://shop.example/"})()
    poll = FakeRequest("https://shop.example/api/poll?since=1", page=page)
    poll.method = "GET"
    context.emit("request", poll)
    assert count.inflight(page, ("*/api/poll*",)) == (0, [])
    assert count.inflight(page, ("*/other/*",))[0] == 1


def test_with_requests_counted_a_rotating_decoration_does_not_hold_the_picture():
    """The exact signal beats the guess: nothing in flight, the area quiet."""
    clock = Clock()
    logo = _page(loaders=["div.mark (turning)"], said=0)
    got = ready.wait(lambda: logo, inflight=lambda: (0, []), quiet_ms=40, limit_ms=5000,
                     sleep=clock.sleep, clock=clock)
    assert got.ok is True and got.ms == 0
    assert got.guessed == "div.mark (turning)"


def test_without_the_count_the_guess_still_holds_the_picture():
    clock = Clock()
    logo = _page(loaders=["div.mark (turning)"], said=0)
    got = ready.wait(lambda: logo, quiet_ms=40, limit_ms=1000, sleep=clock.sleep,
                     clock=clock)
    assert got.ok is False and "still showing a loader" in got.text()


def test_what_the_page_says_itself_is_never_overridden():
    clock = Clock()
    busy = _page(loaders=["section#list (busy)"], said=1)
    got = ready.wait(lambda: busy, inflight=lambda: (0, []), quiet_ms=40, limit_ms=1000,
                     sleep=clock.sleep, clock=clock)
    assert got.ok is False and "section#list (busy)" in got.text()


def test_a_request_in_flight_keeps_the_guess_standing():
    clock = Clock()
    spinner = _page(loaders=["div.spinner (named)"], said=0)
    counts = iter([(1, ["GET /api/data"])] * 3 + [(0, [])] * 50)
    got = ready.wait(lambda: spinner, inflight=lambda: next(counts), quiet_ms=40,
                     limit_ms=5000, sleep=clock.sleep, clock=clock)
    loaders = next(s for s in got.steps if s.name == "loaders")
    assert got.ok is True and loaders.ms > 0, "waited while the data was in flight"


def test_the_service_driver_passes_ignore_requests_and_resets_when_asked():
    """`capture.ignore_requests` and `capture.reset_hover_focus` reach the service path too."""
    from vistest.config import CaptureConfig
    from vistest.integrations.driver import Driver

    seen: dict = {}

    class Fake(Driver):
        def inflight(self, ignore=()):
            seen["ignore"] = ignore
            return lambda: (0, [])

        def reset_hover_focus(self):
            seen["reset"] = True

        def evaluate(self, expression, arg=None, *, timeout_ms=None):
            if isinstance(arg, dict) and "selector" in arg:
                return _page()
            return True

        def wait_ready(self, timeout_ms):
            return None

        def sleep_ms(self, ms):
            return None

    cfg = CaptureConfig(ignore_requests=("*/api/poll*",), reset_hover_focus=True,
                        full_page=False, freeze_css=False, determinism=False)
    Fake().settle(cfg)
    assert seen == {"ignore": ("*/api/poll*",), "reset": True}
    seen.clear()
    Fake().settle(CaptureConfig(full_page=False, freeze_css=False, determinism=False))
    assert seen == {"ignore": ()}


def test_a_network_step_at_its_limit_names_the_request_and_the_way_out():
    clock = Clock()
    got = ready.wait(lambda: _page(), inflight=lambda: (1, ["GET /api/poll"]), quiet_ms=40,
                     limit_ms=1000, sleep=clock.sleep, clock=clock)
    text = got.text()
    assert "1 request (GET /api/poll) still in flight after 1000 ms" in text
    assert "capture.ignore_requests" in text


def test_the_service_says_when_a_baseline_was_taken_an_older_way(tmp_path):
    from vistest.config import VisTestConfig
    from vistest.models import Verdict
    from vistest.service import CheckService

    from . import synthetic as syn

    cfg = VisTestConfig.preset_of("balanced")
    cfg.paths.root = str(tmp_path / ".vistest")
    cfg.ai.attribution_enabled = False
    svc = CheckService(cfg, platform="test-chromium-1x", run_dir=tmp_path / "run")
    ours = {"capture_version": ready.CAPTURE_VERSION}
    base = syn.page()
    changed = syn.regress_button_removed(base)

    svc.check("old.png", base, render=False)                    # no record: version 1
    res = svc.check("old.png", changed, render=False, meta=ours)
    assert res.verdict is Verdict.FAIL
    assert any("taken the old way" in n for n in res.notes)
    assert not any("taken the old way" in n
                   for n in svc.check("old.png", changed, render=False).notes), \
        "a picture from elsewhere is not compared by how it was taken"

    svc.check("new.png", base, render=False, meta=ours)
    res = svc.check("new.png", changed, render=False, meta=ours)
    assert res.verdict is Verdict.FAIL
    assert not any("taken the old way" in n for n in res.notes)
    assert not any("taken the old way" in n
                   for n in svc.check("old.png", base, render=False, meta=ours).notes)


# --------------------------------------------------------------------------- #
#  dev2: the quiet window is counted from the end of the last request
# --------------------------------------------------------------------------- #
class _FakeTime:
    """A clock the test moves: the request count reads it instead of the real one."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now


class _World:
    """A page whose spinner is shown, answered, shown again, and answered again.

    t=0     request A goes out (before the check is called)
    t=100   A is answered; the quiet window has been running since t=0
    t=105   the app re-renders — the same spinner markup — ...
    t=106   ... and starts request B
    t=250   B is answered
    t=251   the picture replaces the spinner

    The page is asked through the library's own path (`targets._wait_ready`):
    the requests of its context are counted, `evaluate` answers the probe from
    the world's clock, and `wait_for_timeout` moves that clock instead of
    sleeping.
    """

    EVENTS = ((0.100, "end", "A"), (0.106, "start", "B"), (0.250, "end", "B"))
    MUTATIONS = (0.0, 0.105, 0.251)

    def __init__(self, monkeypatch):
        self.time = _FakeTime()
        monkeypatch.setattr(inflight, "time", self.time)
        self.count = inflight.RequestCount()
        self.requests = {n: _Request(n) for n in "AB"}
        self.count.started(self.requests["A"])
        self.done = 0
        self.context = _Context()
        inflight._COUNTS[self.context] = self.count
        self.url = "http://x/page"
        self.goto = None                      # what makes it a Page to the library

    def screenshot(self, **kw):               # pragma: no cover - not taken here
        raise AssertionError

    def _apply(self):
        while self.done < len(self.EVENTS) and self.EVENTS[self.done][0] <= self.time.now:
            _, what, name = self.EVENTS[self.done]
            (self.count.started if what == "start" else self.count.ended)(self.requests[name])
            self.done += 1

    def evaluate(self, script, arg=None, **kw):
        self._apply()
        now = self.time.now
        last = max(t for t in self.MUTATIONS if t <= now)
        shown = now >= 0.251
        return _page(quiet=int(round((now - last) * 1000)),
                     loaders=[] if shown else ["div.spinner (named)"], said=0)

    def wait_for_timeout(self, ms):
        self.time.now += ms / 1000

    @property
    def picture_is_there(self):
        return self.time.now >= 0.251


class _Context:
    pass


class _Request:
    resource_type = "fetch"
    method = "GET"
    frame = None

    def __init__(self, name):
        self.url = f"http://x/api/{name}"


def test_the_quiet_window_runs_from_the_end_of_the_last_request(monkeypatch):
    """The race: A's answer arrives, the page is «quiet» (the window ran while A was out),
    nothing is in flight — and the app is about to start B behind the same spinner."""
    from vistest.library import targets

    world = _World(monkeypatch)
    got = targets._wait_ready(world, world, [], [], False, 5000, 40, ())
    assert got.ok is True
    assert world.picture_is_there, (
        f"declared ready at {world.time.now * 1000:.0f} ms, with the spinner still shown "
        "(request B was about to start)")
