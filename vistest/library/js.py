# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Every question the library asks a live page, with a deadline.

`page.evaluate` in Playwright's Python API has no timeout. On a page whose
main thread is busy — an endless loop, a script that never returns — or whose
renderer crashed, it never returns, and neither did `expect_screenshot`: the
run hung until somebody's CI killed it, with nothing in the log. Playwright's
own `page.screenshot` gives up after its timeout on the same page.

So every script the library runs on a live page goes through `call` here:

* a Playwright **page** is asked through `wait_for_function`, which does take a
  timeout — the script is wrapped so that it runs once and hands back its value
  in an object, which is always truthy, so it is never polled a second time;
* a Playwright **locator** through its own `evaluate(..., timeout=)`;
* anything else — a project's own page wrapper, a test's fake — through its
  `evaluate`, exactly as before: there is no timeout to give it.

Each question waits at most `Budget.cap_ms`, and never past the deadline of
the whole check (`budget_ms`). A question that runs out of time is not yet a
verdict on the page: a slow one (fonts that take their time) is told apart
from a dead one by asking the page the cheapest question there is. A dead
page — closed, crashed, or not answering — raises `CaptureError` with the
reason, and every later question of the same check raises it at once, so the
places that degrade quietly on an ordinary error (naming, putting the scroll
back) do not each wait again.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from .errors import CaptureError, hide_for_verdicts

__all__ = ["Budget", "SlowAnswer", "budget", "budget_ms", "call", "check", "current",
           "diagnose", "screenshot_timeout"]

#: The longest one question may take, unless the readiness limit is longer.
CALL_CAP_MS = 5000
#: How long the page gets to answer «are you there» after a question timed out.
ALIVE_MS = 1000
#: Over the limits the check is made of, for everything that is not waiting:
#: the canary's tab, decoding, comparing, writing.
SLACK_MS = 15000
#: `wait_for_function` polls a falsy answer; the wrapped script never gives one,
#: so this is only how soon a second look would come if it ever did.
_POLL_MS = 100

_ARG_UNSET = object()


def budget_ms(ready_timeout_ms: int, stable_timeout_ms: int) -> int:
    """The deadline of one check, from the limits it is made of.

    Readiness at most twice (once more after the window is set to its
    baseline's scroll), frames for at most three stable windows (the first
    picture, the second look, the canary), and `SLACK_MS` for the rest.
    """
    return 2 * max(0, int(ready_timeout_ms)) + 3 * max(0, int(stable_timeout_ms)) + SLACK_MS


class SlowAnswer(TimeoutError):
    """One question got no answer in time, and the page is otherwise alive."""


class Budget:
    """The time one check may spend asking a page, and whether the page is dead."""

    def __init__(self, total_ms: int, cap_ms: int = CALL_CAP_MS,
                 clock=time.monotonic) -> None:
        self.total_ms = int(total_ms)
        self.cap_ms = max(int(cap_ms), 1)
        self._clock = clock
        self._deadline = clock() + self.total_ms / 1000
        #: The reason the page is dead, once it is known; "" while it answers.
        self.dead = ""

    def remaining_ms(self) -> int:
        return int((self._deadline - self._clock()) * 1000)

    def timeout_for(self, what: str) -> int:
        """How long the next question may take: the cap, never past the deadline."""
        left = self.remaining_ms()
        if left <= 0:
            self.dead = (f"the check ran out of its time ({self.total_ms} ms) before "
                         f"{what}: the page kept it waiting")
            raise CaptureError(self.dead)
        return min(self.cap_ms, left)


_current: ContextVar[Budget | None] = ContextVar("vistest_js_budget", default=None)


def current() -> Budget | None:
    return _current.get()


@contextmanager
def budget(total_ms: int, cap_ms: int = CALL_CAP_MS) -> Iterator[Budget]:
    """Everything asked of a page inside the block shares one deadline."""
    b = Budget(total_ms, cap_ms)
    token = _current.set(b)
    try:
        yield b
    finally:
        _current.reset(token)


def check() -> None:
    """Raise `CaptureError` if the page of this check is already known to be dead."""
    b = _current.get()
    if b is not None and b.dead:
        raise CaptureError(b.dead)


def screenshot_timeout() -> int | None:
    """The `timeout` for `page.screenshot`: what is left of the deadline, or None
    outside a check (Playwright's own default then)."""
    b = _current.get()
    if b is None:
        return None
    if b.dead:
        raise CaptureError(b.dead)
    return max(b.remaining_ms(), 1000)


# --------------------------------------------------------------------------- #
def _playwright(obj: Any) -> bool:
    return type(obj).__module__.startswith("playwright.")


def _is_locator(obj: Any) -> bool:
    return _playwright(obj) and type(obj).__name__ == "Locator"


def _page_of(owner: Any):
    if callable(getattr(owner, "wait_for_function", None)):
        return owner
    return getattr(owner, "page", None)


def _wrap(script: str) -> str:
    """`script` as a predicate that runs once and is always truthy: `{v: value}`."""
    return f"(a) => Promise.resolve(({script.strip()})(a)).then((v) => ({{ v }}))"


def call(owner: Any, script: str, arg: Any = _ARG_UNSET, *, what: str) -> Any:
    """`owner.evaluate(script, arg)`, but never longer than the check allows.

    `what` names the question in words — «waiting for web fonts» — for the
    message when the page does not answer.
    """
    check()
    page_way = _playwright(owner) and callable(getattr(owner, "wait_for_function", None))
    locator_way = _is_locator(owner)
    if not (page_way or locator_way):
        evaluate = owner.evaluate
        return evaluate(script) if arg is _ARG_UNSET else evaluate(script, arg)
    b = _current.get()
    ms = b.timeout_for(what) if b is not None else CALL_CAP_MS
    value = None if arg is _ARG_UNSET else arg
    try:
        if locator_way:
            return owner.evaluate(script, value, timeout=ms)
        handle = owner.wait_for_function(_wrap(script), arg=value, timeout=ms,
                                         polling=_POLL_MS)
        try:
            got = handle.json_value()
        finally:
            try:
                handle.dispose()
            except Exception:  # noqa: BLE001 - the answer is in; the handle is litter
                pass
        return got.get("v") if isinstance(got, dict) else None
    except Exception as error:  # noqa: BLE001 - sorted out right below
        diagnose(owner, error, what, ms)
        raise


def diagnose(owner: Any, error: BaseException, what: str, waited_ms: int | None = None
             ) -> None:
    """Turn an error from a page into `CaptureError` when the page is dead.

    Returns when the page is alive and the error was the question's own; raises
    `SlowAnswer` for a question that only took too long, and `CaptureError` for
    a page that is closed, crashed, or not answering at all.
    """
    name = type(error).__name__
    text = str(error)
    b = _current.get()

    def dead(reason: str):
        if b is not None:
            b.dead = reason
        raise CaptureError(reason) from None

    if not _playwright(error):
        return
    if name == "TargetClosedError" or "has been closed" in text:
        dead(f"the page is closed: {what} could not be asked (it was closed before "
             "or during the check)")
    if "crash" in text.lower():
        dead(f"the page crashed: {what} failed ({_first_line(text)})")
    if name != "TimeoutError":
        return
    page = _page_of(owner)
    waited = f" in {waited_ms} ms" if waited_ms is not None else ""
    if _alive(page):
        raise SlowAnswer(f"{what}: no answer{waited}") from None
    if _crashed(page):
        dead(f"the page crashed: {what} got no answer{waited}")
    dead(f"the page stopped answering: {what} got no answer{waited}, and the page "
         f"does not answer at all — its main thread is busy (an endless loop, or a "
         "script that does not return)")


def _first_line(text: str) -> str:
    return text.strip().splitlines()[0] if text.strip() else ""


def _alive(page: Any) -> bool:
    """Does the page answer the cheapest question there is, within `ALIVE_MS`?"""
    wait = getattr(page, "wait_for_function", None)
    if not callable(wait):
        return True
    try:
        wait("() => true", timeout=ALIVE_MS, polling=_POLL_MS)
        return True
    except Exception:  # noqa: BLE001 - any failure here means «no»
        return False


def _crashed(page: Any) -> bool:
    """A crashed renderer refuses a screenshot at once, saying so; a busy one only
    times out."""
    shot = getattr(page, "screenshot", None)
    if not callable(shot):
        return False
    try:
        shot(timeout=500)
    except Exception as e:  # noqa: BLE001
        return "crash" in str(e).lower()
    return False


#  pytest: the library's own verdicts and refusals are shown without its
#  frames (library/errors.py, hide_for_verdicts).
__tracebackhide__ = hide_for_verdicts
