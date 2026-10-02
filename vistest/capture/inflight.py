# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The requests of a browser context that are in flight, counted from its start.

From inside a check there is no way to see a request the page started before
the check was called: the browser keeps no list of them that a script can
read. So the count has to be running already — hung on the context when it is
created. Two places do that: the pytest plugin of the library (for every
context Playwright creates in the test process — `install_hooks`) and the
service, when it opens a context of its own (`capture.stabilize.install`).
`ready.wait` then asks `inflight(page)`: how many of *this page's* requests
are still on their way.

Not counted: WebSocket and EventSource streams, which stay open for as long
as the page lives — counting them would make every chat widget a page that is
never ready; and images, media and fonts, which have steps of their own that
look only at the photographed area (`ready.py`: images, fonts) — a lazy image
far below the fold must not hold a check of the header. A long poll is
counted, and the readiness step that waits for it gives up at its limit and
says which request it was.

Playwright is not imported here: a context is recognised by having `.on`.
"""

from __future__ import annotations

import fnmatch
import time
import weakref
from urllib.parse import urlsplit

__all__ = ["NOT_COUNTED", "RequestCount", "for_page", "install_hooks", "label", "track"]

#: Resource types not counted: streams, which do not end, and what the
#: readiness wait looks at in the area itself (images, fonts) or never waits
#: for (media).
NOT_COUNTED = frozenset({"websocket", "eventsource", "image", "media", "font"})

_COUNTS: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


class RequestCount:
    """Requests of one context in flight, and when the last one ended."""

    def __init__(self) -> None:
        self._open: dict = {}
        self.last_end = time.monotonic()

    def started(self, request) -> None:
        try:
            if request.resource_type in NOT_COUNTED:
                return
        except Exception:  # noqa: BLE001 - a request we cannot read is still counted
            pass
        self._open[request] = time.monotonic()

    def ended(self, request) -> None:
        if self._open.pop(request, None) is not None:
            self.last_end = time.monotonic()

    def inflight(self, page=None, ignore: tuple[str, ...] = ()) -> tuple[int, list[str]]:
        """(how many, a few of them) — `page`'s requests in flight, or all of them.

        Each is named by its method and path — `GET /api/poll` — and never by
        its query string, where tokens live. `ignore` — glob patterns
        (`capture.ignore_requests`) matched against the URL without its query:
        a long poll or an analytics beacon the page never waits for itself.
        """
        held = []
        try:
            home = urlsplit(str(page.url)).netloc if page is not None else ""
        except Exception:  # noqa: BLE001
            home = ""
        for request in list(self._open):
            if page is not None:
                try:
                    frame = request.frame
                    if frame is not None and frame.page is not page:
                        continue
                except Exception:  # noqa: BLE001 - a service worker's: counted
                    pass
            try:
                url = str(request.url)
            except Exception:  # noqa: BLE001
                url = ""
            bare = url.split("?", 1)[0].split("#", 1)[0]
            if ignore and any(fnmatch.fnmatchcase(bare, p) for p in ignore):
                continue
            held.append(label(request, url, home))
        return len(held), held[:3]


def label(request, url: str, home: str = "") -> str:
    """`GET /api/poll` — the method and the path, without the query string.

    The host is added only when it is not the page's own (`home`): an
    analytics beacon is recognised by where it goes.
    """
    try:
        method = str(request.method)
    except Exception:  # noqa: BLE001
        method = "?"
    try:
        parts = urlsplit(url)
        where = parts.path or "/"
        if parts.netloc and parts.netloc != home:
            where = f"{parts.netloc}{where}"
    except ValueError:
        where = "?"
    return f"{method} {where}"


def track(context) -> RequestCount | None:
    """Count the requests of `context` from now on. Idempotent; never raises."""
    if context is None or not callable(getattr(context, "on", None)):
        return None
    try:
        existing = _COUNTS.get(context)
    except TypeError:                     # not weak-referenceable: not a context
        return None
    if existing is not None:
        return existing
    count = RequestCount()
    try:
        context.on("request", count.started)
        context.on("requestfinished", count.ended)
        context.on("requestfailed", count.ended)
        _COUNTS[context] = count
    except Exception:  # noqa: BLE001 - counting is help, not a condition
        return None
    return count


def for_page(page) -> RequestCount | None:
    """The count hung on `page`'s context, or None when nobody hung one."""
    try:
        context = page.context
    except Exception:  # noqa: BLE001
        return None
    try:
        return _COUNTS.get(context)
    except TypeError:
        return None


_HOOKED = False


def install_hooks() -> bool:
    """Count the requests of every context Playwright creates in this process.

    For the pytest plugin: wraps `Browser.new_context`, `Browser.new_page` and
    `BrowserType.launch_persistent_context` of Playwright's sync API so that
    each new context gets a `RequestCount` before any page in it navigates.
    Returns False — and changes nothing — when Playwright is not installed.
    """
    global _HOOKED
    if _HOOKED:
        return True
    try:
        from playwright.sync_api import Browser, BrowserType
    except Exception:  # noqa: BLE001 - no Playwright, nothing to count
        return False

    def wrap(cls, name, context_of):
        original = getattr(cls, name)
        if getattr(original, "__vistest__", False):
            return

        def wrapper(self, *args, **kwargs):
            made = original(self, *args, **kwargs)
            try:
                track(context_of(made))
            except Exception:  # noqa: BLE001 - never in the way of the test
                pass
            return made

        wrapper.__vistest__ = True
        wrapper.__wrapped__ = original
        wrapper.__doc__ = original.__doc__
        setattr(cls, name, wrapper)

    wrap(Browser, "new_context", lambda c: c)
    wrap(Browser, "new_page", lambda p: p.context)
    wrap(BrowserType, "launch_persistent_context", lambda c: c)
    _HOOKED = True
    return True
