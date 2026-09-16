# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Who is registered for what.

One active implementation per role, except annotators, which form a chain.

**Conflicts are resolved, not refused.** Two installed plugins may both offer
a scorer. The higher ``priority`` wins; on a tie the first one registered
stays, because entry points are loaded in a stable (sorted) order and a
winner that depends on installation order would change between two machines
with the same packages. The loser is kept in ``describe()`` and named in a
warning — a plugin that is installed and silently does nothing is the most
expensive kind of misconfiguration to find.

**Registration is checked.** An object that does not satisfy the protocol is
refused with ``TypeError`` at ``register`` time, which the loader turns into
a warning. Finding out on the first comparison would mean finding out in the
middle of somebody's test run.
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from .api import (
    AuthProvider,
    BaselineSyncBackend,
    RegionAnnotator,
    RegionScorer,
    SettingsStore,
)

log = logging.getLogger("vistest.plugins")

__all__ = ["HostContext", "PluginRegistry", "Registration"]

ROLES = ("scorer", "auth", "sync")
_PROTOCOLS = {"scorer": RegionScorer, "auth": AuthProvider,
              "sync": BaselineSyncBackend, "annotator": RegionAnnotator}

#  Who registered a thing when it was not a plugin loaded from an entry point.
PROGRAMMATIC = "programmatic"


@dataclass(frozen=True)
class Registration:
    role: str
    name: str
    impl: Any
    priority: int
    plugin: str
    order: int

    def describe(self) -> dict:
        return {"role": self.role, "name": self.name, "plugin": self.plugin,
                "priority": self.priority,
                "type": f"{type(self.impl).__module__}.{type(self.impl).__qualname__}"}


@dataclass
class HostContext:
    """What the host process offers to plugins. Filled in by the server.

    ``settings`` — the server's key-value settings (``None`` in the library
    mode). ``plugin_database`` — a callable returning a handle restricted to the
    calling plugin's own tables, see `vistest.plugins.migrations`. ``require``
    and ``audit`` — the server's access check and audit log, for routes a
    plugin serves.
    """

    settings: SettingsStore | None = None
    plugin_database: Callable[[str], Any] | None = None
    mode: str = "library"
    #  `require(request, role) -> user` raises the server's own 401/403.
    require: Callable[..., dict] | None = None
    #  `audit(who, action, target="", **details)` writes to the audit log.
    audit: Callable[..., None] | None = None


@dataclass
class _Migrations:
    plugin: str
    steps: tuple[str, ...]


def slug(name: str) -> str:
    """A plugin name made safe for a table prefix."""
    return re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_") or "plugin"


class PluginRegistry:
    """Registered implementations for one process."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._single: dict[str, list[Registration]] = {r: [] for r in ROLES}
        self._annotators: list[Registration] = []
        self._migrations: dict[str, _Migrations] = {}
        self._order = 0
        self._plugin = PROGRAMMATIC
        self._options: dict[str, Any] = {}
        self.host = HostContext()
        #  Filled by the loader: what was found, what was loaded, what was refused.
        self.loaded: list[dict] = []
        self.refused: list[dict] = []

    # ------------------------------------------------------------------ #
    #  Registration
    # ------------------------------------------------------------------ #
    def set_scorer(self, impl: RegionScorer, *, name: str | None = None,
                   priority: int = 0) -> None:
        self._add("scorer", impl, name, priority)

    def set_auth_provider(self, impl: AuthProvider, *, name: str | None = None,
                          priority: int = 0) -> None:
        self._add("auth", impl, name, priority)

    def set_sync_backend(self, impl: BaselineSyncBackend, *,
                         name: str | None = None, priority: int = 0) -> None:
        self._add("sync", impl, name, priority)

    def add_annotator(self, impl: RegionAnnotator, *, name: str | None = None,
                      priority: int = 0) -> None:
        self._add("annotator", impl, name, priority)

    def add_migrations(self, steps: Sequence[str]) -> None:
        """SQL steps for this plugin's own tables, applied in order, once each.

        The version recorded for the plugin is the number of steps applied, so
        steps are only ever appended. Tables must be named with the prefix from
        ``table_prefix()``; anything else is refused by the database itself.
        """
        if isinstance(steps, str) or not all(isinstance(s, str) for s in steps):
            raise TypeError("migrations are a sequence of SQL strings")
        with self._lock:
            self._migrations[self._plugin] = _Migrations(self._plugin, tuple(steps))

    def remove(self, impl: Any) -> None:
        """Take an implementation out of every role it holds."""
        with self._lock:
            for role in ROLES:
                self._single[role] = [r for r in self._single[role]
                                      if r.impl is not impl]
            self._annotators = [r for r in self._annotators if r.impl is not impl]

    def _add(self, role: str, impl: Any, name: str | None, priority: int) -> None:
        protocol = _PROTOCOLS[role]
        if impl is None or not isinstance(impl, protocol):
            raise TypeError(
                f"{type(impl).__name__} does not implement {protocol.__name__}")
        if isinstance(priority, bool) or not isinstance(priority, int):
            raise TypeError("priority must be an integer")
        with self._lock:
            self._order += 1
            label = str(name or getattr(impl, "name", "") or type(impl).__name__)
            entry = Registration(role=role, name=label, impl=impl,
                                 priority=priority, plugin=self._plugin,
                                 order=self._order)
            if role == "annotator":
                self._annotators.append(entry)
                return
            current = self._winner(role)
            self._single[role].append(entry)
            winner = self._winner(role)
            if current is not None:
                loser = entry if winner is current else current
                log.warning(
                    "two implementations for the %s role: %r from %s is active, "
                    "%r from %s is not (priority %d vs %d)",
                    role, winner.name, winner.plugin, loser.name, loser.plugin,
                    winner.priority, loser.priority)

    def _winner(self, role: str) -> Registration | None:
        entries = self._single[role]
        if not entries:
            return None
        return min(entries, key=lambda r: (-r.priority, r.order))

    # ------------------------------------------------------------------ #
    #  Loader support
    # ------------------------------------------------------------------ #
    @contextmanager
    def registering(self, plugin: str) -> Iterator[None]:
        """Attribute registrations to `plugin`; undo all of them on failure.

        A plugin whose ``register`` raised half way is not half installed: a
        scorer without the annotator it was designed to run beside is a
        configuration nobody tested.
        """
        with self._lock:
            snapshot = ({r: list(v) for r, v in self._single.items()},
                        list(self._annotators), dict(self._migrations),
                        self._order)
            previous, self._plugin = self._plugin, plugin
            try:
                yield
            except BaseException:
                (self._single, self._annotators, self._migrations,
                 self._order) = snapshot
                raise
            finally:
                self._plugin = previous

    def set_options(self, options: dict[str, Any] | None) -> None:
        self._options = dict(options or {})

    def options_for(self, section: str) -> dict[str, Any]:
        """This plugin's section of ``plugins:`` in ``vistest.yaml``."""
        value = self._options.get(section)
        return dict(value) if isinstance(value, dict) else {}

    # ------------------------------------------------------------------ #
    #  Lookup
    # ------------------------------------------------------------------ #
    def scorer(self) -> Registration | None:
        with self._lock:
            return self._winner("scorer")

    def auth_provider(self) -> Registration | None:
        with self._lock:
            return self._winner("auth")

    def sync_backend(self) -> Registration | None:
        with self._lock:
            return self._winner("sync")

    def annotators(self) -> list[Registration]:
        """Highest priority first; registration order breaks ties."""
        with self._lock:
            return sorted(self._annotators, key=lambda r: (-r.priority, r.order))

    def migrations(self) -> dict[str, tuple[str, ...]]:
        with self._lock:
            return {k: v.steps for k, v in self._migrations.items()}

    def is_empty(self) -> bool:
        with self._lock:
            return not (self._annotators or any(self._single.values()))

    def describe(self) -> dict:
        """Everything registered, active or not. For diagnostics, not for logic."""
        with self._lock:
            active = {role: (w.describe() if (w := self._winner(role)) else None)
                      for role in ROLES}
            inactive = [r.describe() for role in ROLES
                        for r in self._single[role] if r is not self._winner(role)]
            return {
                "active": active,
                "annotators": [r.describe() for r in self.annotators()],
                "inactive": inactive,
                "loaded": list(self.loaded),
                "refused": list(self.refused),
            }

    def capabilities(self) -> dict[str, bool]:
        """What the installation can do, in words the interface understands.

        Booleans and nothing else: the interface hides what is ``False`` and
        says nothing about why.
        """
        with self._lock:
            return {
                "region_scores": self._winner("scorer") is not None,
                "region_annotations": bool(self._annotators),
                "external_sign_in": self._winner("auth") is not None,
                "baseline_sync": self._winner("sync") is not None,
            }


def table_prefix(plugin: str) -> str:
    """The only table names a plugin's migrations may create or touch."""
    return f"ext_{slug(plugin)}_"

