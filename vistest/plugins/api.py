# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The public plugin contract. Version 1.

Everything a plugin may rely on is in this module, and nothing else in the
package is part of the contract. A plugin is a distribution that declares an
entry point in the ``vistest.plugins`` group::

    [project.entry-points."vistest.plugins"]
    my-plugin = "my_package.vistest_plugin"

The target is a module (or any object) with a ``register(registry)`` callable
and an ``API_VERSION`` integer, or the ``register`` function itself, in which
case ``API_VERSION`` is read from the function's module::

    # my_package/vistest_plugin.py
    API_VERSION = 1          # the version this plugin was written against

    def register(registry):
        registry.add_annotator(MyAnnotator(), priority=10)

Write the number, do not import it. ``from vistest.plugins.api import
API_VERSION`` always matches whatever VisTest is installed, which turns the
check into a formality exactly when it matters — after an upgrade.

**Four roles.**

* ``RegionAnnotator`` — says something about a region. Several may be
  active; they run as a chain, highest priority first.
* ``RegionScorer`` — estimates, per region, how likely the change is real.
  One active implementation. What the estimate does to the outcome of a test
  is decided by the core (see `vistest.plugins.runtime`), not by the scorer.
* ``AuthProvider`` — checks a login and a secret against something other than
  the local user table. One active implementation. The local sign-in is always
  tried first and always keeps working.
* ``BaselineSyncBackend`` — moves baselines between installations. One active
  implementation.

**The rules every role shares.**

1. A plugin never takes a run down. Any exception it raises is logged as a
   warning and the core carries on along the path it takes when no plugin is
   installed at all. Raising is therefore a legitimate way to say "I cannot
   answer this time".
2. A plugin never changes the verdict directly. It returns data; the core
   applies it under rules the core owns and reports every consequence.
3. Arrays handed to a plugin are read-only views. Mutating the pictures would
   change what every later stage sees, and nothing downstream could tell.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "API_VERSION",
    "ENTRY_POINT_GROUP",
    "Annotation",
    "AuthProvider",
    "AuthResult",
    "BaselineSelection",
    "BaselineSyncBackend",
    "RegionAnnotator",
    "RegionContext",
    "RegionScorer",
    "RouteProvider",
    "SettingsStore",
]

#  Raised only on an incompatible change to anything in this module. Adding an
#  optional field or an optional method is not one.
API_VERSION = 1

ENTRY_POINT_GROUP = "vistest.plugins"

#  Limits on what an annotator may attach. A region card with forty lines of
#  commentary is not more informative, and the text ends up in the database,
#  in the report and in the JSON handed to CI.
MAX_ANNOTATIONS_PER_REGION = 8
MAX_ANNOTATION_TEXT = 500
MAX_ANNOTATION_KIND = 40


# --------------------------------------------------------------------------- #
#  Data passed to plugins and returned by them
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Annotation:
    """One remark about one region.

    ``text`` is what a person reads. ``kind`` is a short free-form label a
    reader can group by (``caption``, ``metric``, ``note``). ``value`` is an
    optional number or short string that goes with it. ``source`` is filled
    by the core with the name the implementation was registered under; a value
    set by the plugin is overwritten, so a remark can never claim to come from
    somewhere else.
    """

    text: str
    kind: str = "note"
    value: float | int | str | None = None
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "kind": self.kind, "value": self.value,
                "source": self.source}

    @classmethod
    def checked(cls, raw: Any, *, source: str) -> Annotation:
        """Validate what a plugin returned. Raises ``ValueError`` on garbage."""
        if not isinstance(raw, Annotation):
            raise ValueError(f"expected an Annotation, got {type(raw).__name__}")
        if not isinstance(raw.text, str) or not raw.text.strip():
            raise ValueError("an annotation needs non-empty text")
        if not isinstance(raw.kind, str) or not raw.kind.strip():
            raise ValueError("an annotation needs a kind")
        value = raw.value
        if isinstance(value, bool):
            value = int(value)
        if isinstance(value, float) and not math.isfinite(value):
            value = None
        if value is not None and not isinstance(value, (int, float, str)):
            raise ValueError(
                f"an annotation value must be a number or a string, "
                f"got {type(value).__name__}")
        if isinstance(value, str):
            value = value[:MAX_ANNOTATION_TEXT]
        return cls(text=raw.text.strip()[:MAX_ANNOTATION_TEXT],
                   kind=raw.kind.strip()[:MAX_ANNOTATION_KIND],
                   value=value, source=source)


@dataclass(frozen=True)
class RegionContext:
    """What a scorer or an annotator may know about the comparison.

    ``expected`` and ``actual`` are the two frames, RGB ``uint8`` H×W×3,
    already aligned and padded to one size, and read-only. They are ``None``
    when the caller has no pixels (a unit test, a replay of a stored result).

    ``options`` holds the plugin sections of ``vistest.yaml`` — everything under
    ``plugins:`` that is not a core key — keyed by section name.
    ``settings`` is the ``ai:`` section the caller runs with (an ``AIConfig``),
    for plugins that honour one of its existing knobs; it may be ``None``.
    """

    name: str = ""
    expected: Any = None
    actual: Any = None
    total_pixels: int = 0
    page_height: int = 0
    aligned: bool = False
    size_changed: bool = False
    region_count: int = 0
    dom_expected: Mapping[str, Any] | None = None
    dom_actual: Mapping[str, Any] | None = None
    options: Mapping[str, Any] = field(default_factory=dict)
    settings: Any = None

    def options_for(self, section: str) -> Mapping[str, Any]:
        value = self.options.get(section) if self.options else None
        return value if isinstance(value, Mapping) else {}


@dataclass(frozen=True)
class AuthResult:
    """A person the provider recognised.

    ``role`` is a suggestion: the core accepts only ``viewer``, ``reviewer``
    and ``admin``, falls back to ``viewer`` for anything else, and never lowers
    a role raised by hand inside VisTest. ``source`` is stored with the account
    and must not be ``local`` — an account that came from outside cannot be
    opened with a local password.
    """

    login: str
    name: str = ""
    role: str = "viewer"
    external_id: str = ""
    email: str = ""
    groups: tuple[str, ...] = ()
    source: str = "external"


@dataclass(frozen=True)
class BaselineSelection:
    """Which baselines a sync operation is about. Empty everywhere — all.

    ``names`` are shell-style patterns matched against the snapshot name.
    """

    project: str = ""
    platform: str = ""
    names: tuple[str, ...] = ()


# --------------------------------------------------------------------------- #
#  Roles
# --------------------------------------------------------------------------- #
@runtime_checkable
class RegionAnnotator(Protocol):
    """Describes a region. Adds words; cannot change the outcome."""

    def annotate(self, region: Any, ctx: RegionContext) -> Iterable[Annotation]:
        ...


@runtime_checkable
class RegionScorer(Protocol):
    """Estimates how likely each region is a real change. This is the gate.

    Returns one number per region, in order, each in ``[0, 1]``: ``0`` means
    certainly rendering noise, ``1`` certainly a real change. The scale is
    calibrated so that ``0.5`` is the scorer's own decision boundary; the core
    thresholds (``plugins.noise_below``, ``plugins.confirmed_at``) are read
    against that scale. A wrong number of values, or a value that is not a
    finite number, discards the whole answer and the core continues without it.
    ``None`` abstains: the scorer has no opinion on this comparison (it is
    switched off, its model is missing) and nothing is recorded.

    The regions passed in are the ones still standing: anything already
    suppressed by the deterministic path is not offered.
    """

    def score(self, regions: Sequence[Any],
              ctx: RegionContext) -> Sequence[float] | None:
        ...


@runtime_checkable
class AuthProvider(Protocol):
    """Checks a login and a secret against an external identity source.

    ``None`` — the provider does not recognise this pair. An exception — the
    provider could not be asked; the core records it and answers the sign-in
    form as if the pair was not recognised.
    """

    def authenticate(self, login: str, secret: str) -> AuthResult | None:
        ...


@runtime_checkable
class BaselineSyncBackend(Protocol):
    """Moves baselines between installations.

    ``selection`` is a `BaselineSelection`. Every method may raise ``ValueError`` for input
    it refuses and ``FileNotFoundError`` for a source that is not there; the
    core turns those into user-facing messages. ``modes`` lists the merge modes
    ``plan`` and ``apply`` accept.
    """

    modes: Sequence[str]

    def export(self, archive: Any, *, baselines_root: Any, selection: Any,
               with_history: bool = False) -> dict:
        ...

    def inspect(self, archive: Any) -> dict:
        ...

    def plan(self, archive: Any, *, baselines_root: Any, mode: str,
             selection: Any, layout: str = "auto") -> list[dict]:
        ...

    def apply(self, archive: Any, *, baselines_root: Any, mode: str,
              selection: Any, who: str = "", layout: str = "auto") -> dict:
        ...


# --------------------------------------------------------------------------- #
#  Optional extensions
# --------------------------------------------------------------------------- #
@runtime_checkable
class RouteProvider(Protocol):
    """An implementation that also serves its own HTTP routes.

    Checked on registered auth providers and sync backends. The router is a
    FastAPI ``APIRouter``; its routes are mounted only by the server, only when
    the implementation is active, and every route must enforce its own access
    rules. Without the plugin those paths simply do not exist.
    """

    def api_router(self) -> Any:
        ...


@runtime_checkable
class SettingsStore(Protocol):
    """Key-value settings the server keeps in its database.

    Available to plugins as ``registry.host.settings`` once a server has
    started; ``None`` in the library mode. Keys are shared with the core, so a
    plugin prefixes its own.
    """

    def get_text(self, name: str, default: str = "") -> str:
        ...

    def set_text(self, name: str, value: str, who: str = "") -> None:
        ...
