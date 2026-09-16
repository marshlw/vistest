# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Finding plugins and letting them register.

Three rules, and the order of the day is to never break a run:

* anything a plugin does wrong while loading — fails to import, has no
  ``register``, raises inside it, registers something that is not an
  implementation — is a **warning**, and the process carries on without that
  plugin;
* a plugin written against a different ``API_VERSION`` is **refused** with a
  warning, before its ``register`` is called;
* ``VISTEST_DISABLE_PLUGINS=1`` switches loading off entirely. Nothing from
  the ``vistest.plugins`` entry-point group is imported, and the process runs
  exactly the way it runs with no plugin installed. Implementations registered
  from code are not affected: they were asked for by the code that runs.

Loading happens once per process, on first use, and is idempotent.
"""

from __future__ import annotations

import logging
import sys
import threading
from importlib import metadata
from typing import Any

from .api import API_VERSION, ENTRY_POINT_GROUP
from .registry import PluginRegistry

log = logging.getLogger("vistest.plugins")

__all__ = [
    "ENV_DISABLE",
    "active_registry",
    "discover",
    "ensure_loaded",
    "installed_names",
    "load_plugins",
    "plugins_disabled",
    "reset",
]

ENV_DISABLE = "VISTEST_DISABLE_PLUGINS"

_registry: PluginRegistry | None = None
_loaded = False
_lock = threading.RLock()


def plugins_disabled() -> bool:
    """``VISTEST_DISABLE_PLUGINS`` set to anything truthy.

    Read leniently on purpose. This is the switch someone reaches for while a
    run is on fire; an unreadable value stopping the process would be the worst
    possible answer, so anything that is not clearly "off" means "disabled".
    """
    import os

    raw = (os.getenv(ENV_DISABLE) or "").strip().lower()
    return bool(raw) and raw not in ("0", "false", "no", "off")


def discover() -> list[metadata.EntryPoint]:
    """Entry points in our group, sorted by name. Nothing is imported."""
    try:
        found = metadata.entry_points(group=ENTRY_POINT_GROUP)
    except Exception as e:                                 # pragma: no cover
        log.warning("could not list installed plugins: %s", e)
        return []
    unique: dict[str, metadata.EntryPoint] = {}
    for ep in found:
        #  The same distribution visible twice on sys.path (an editable install
        #  next to a wheel) lists its entry points twice. One is enough.
        unique.setdefault(ep.name, ep)
    return [unique[k] for k in sorted(unique)]


def installed_names() -> set[str]:
    return {ep.name for ep in discover()}


def _declared_version(target: Any) -> Any:
    version = getattr(target, "API_VERSION", None)
    if version is None and callable(target):
        module = sys.modules.get(getattr(target, "__module__", "") or "")
        version = getattr(module, "API_VERSION", None)
    return version


def _register_callable(target: Any):
    register = getattr(target, "register", None)
    if callable(register):
        return register
    if callable(target) and getattr(target, "__name__", "") == "register":
        return target
    return None


def load_plugins(registry: PluginRegistry, *, disabled: set[str] | frozenset = frozenset(),
                 entry_points: list | None = None) -> PluginRegistry:
    """Load every discovered plugin into `registry`. Never raises."""
    if plugins_disabled():
        log.info("%s is set: plugins are not loaded", ENV_DISABLE)
        return registry

    for ep in (discover() if entry_points is None else entry_points):
        name = ep.name
        record = {"name": name, "value": getattr(ep, "value", "")}
        if name in disabled:
            registry.refused.append({**record, "reason": "disabled in vistest.yaml"})
            log.info("plugin %r is disabled in vistest.yaml", name)
            continue
        try:
            target = ep.load()
        except BaseException as e:            # noqa: BLE001 — SystemExit too
            if isinstance(e, KeyboardInterrupt):
                raise
            _refuse(registry, record, f"import failed: {type(e).__name__}: {e}")
            continue

        register = _register_callable(target)
        if register is None:
            _refuse(registry, record, "it has no register(registry) function")
            continue

        version = _declared_version(target)
        if isinstance(version, bool) or not isinstance(version, int):
            _refuse(registry, record,
                    f"it does not declare an integer API_VERSION "
                    f"(this VisTest speaks version {API_VERSION})")
            continue
        if version != API_VERSION:
            _refuse(registry, record,
                    f"it was written for plugin API version {version}, and this "
                    f"VisTest speaks version {API_VERSION}")
            continue

        try:
            with registry.registering(name):
                register(registry)
        except BaseException as e:            # noqa: BLE001
            if isinstance(e, KeyboardInterrupt):
                raise
            _refuse(registry, record,
                    f"register() failed: {type(e).__name__}: {e}")
            continue
        registry.loaded.append({**record, "api_version": version})
        log.info("plugin %r loaded", name)
    return registry


def _refuse(registry: PluginRegistry, record: dict, reason: str) -> None:
    registry.refused.append({**record, "reason": reason})
    log.warning("plugin %r is not loaded: %s", record["name"], reason)


# --------------------------------------------------------------------------- #
#  The process-wide registry
# --------------------------------------------------------------------------- #
def active_registry() -> PluginRegistry:
    """The registry this process uses, with installed plugins loaded."""
    ensure_loaded()
    assert _registry is not None
    return _registry


def _bare() -> PluginRegistry:
    global _registry
    with _lock:
        if _registry is None:
            _registry = PluginRegistry()
        return _registry


def ensure_loaded(options: dict | None = None,
                  disabled: set[str] | frozenset | None = None) -> None:
    """Load plugins once. Later calls are free.

    Options and the disabled list come from ``vistest.yaml``; when the caller
    does not pass them they are read from the config found from the working
    directory, and a config that cannot be read is not our problem here — the
    caller that needs it will say so.
    """
    global _loaded
    registry = _bare()
    if _loaded:
        return
    with _lock:
        if _loaded:
            return
        if options is None or disabled is None:
            conf_options, conf_disabled = _from_config()
            options = conf_options if options is None else options
            disabled = conf_disabled if disabled is None else disabled
        registry.set_options(options)
        #  Marked before loading: a plugin that asks for the registry from
        #  inside its own register() must not start a second load.
        _loaded = True
        load_plugins(registry, disabled=frozenset(disabled or ()))


def _from_config() -> tuple[dict, frozenset]:
    try:
        from ..config import VisTestConfig

        plugins = VisTestConfig.load().plugins
    except Exception:
        return {}, frozenset()
    if not plugins.enabled:
        #  `plugins.enabled: false` is the same switch as the variable, kept in
        #  the file. Every discovered plugin is reported as disabled.
        return dict(plugins.options), frozenset(installed_names())
    return dict(plugins.options), frozenset(plugins.disabled)


def programmatic_registry() -> PluginRegistry:
    """The registry without triggering discovery. For code-only registration."""
    return _bare()


def reset(registry: PluginRegistry | None = None, *, loaded: bool = False) -> None:
    """Replace the process registry. Test hook.

    ``loaded=True`` marks the new registry as already loaded, so nothing is
    discovered — the way to run a test against exactly the registry it built.
    """
    global _registry, _loaded
    with _lock:
        _registry = registry
        _loaded = loaded and registry is not None
