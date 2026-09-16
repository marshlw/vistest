# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Plugin tables: their own migrations, their own version, their own names.

The core schema has one version number (``PRAGMA user_version``) and it
belongs to the core. A plugin that bumped it, or added a column to a core
table, would make the database depend on which packages happen to be
installed — and the next core migration, or a restore onto a machine without
the plugin, would meet a schema it does not know.

So a plugin gets:

* a row in ``plugin_schema_version`` (plugin name → number of steps applied),
  separate from the core's version;
* tables named ``ext_<plugin>_*`` and nothing else. This is enforced by
  SQLite itself through an authorizer, not by reading the SQL: a statement
  that creates, alters, reads or writes any other table is refused before it
  runs.

A failing step rolls back that plugin's pending steps, is logged as a warning,
and leaves every other plugin and the core untouched.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Sequence
from typing import Any

from .registry import PluginRegistry, table_prefix

log = logging.getLogger("vistest.plugins")

__all__ = ["PluginDatabase", "apply_all", "apply_one", "version_of"]

#  What SQLite does behind the scenes of an allowed statement. Creating a table
#  writes a row into `sqlite_master`; `AUTOINCREMENT` keeps `sqlite_sequence`.
_INTERNAL = {"sqlite_master", "sqlite_temp_master", "sqlite_sequence",
             "sqlite_schema", "sqlite_temp_schema"}

_TABLE_ACTIONS = {
    sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_CREATE_TEMP_TABLE,
    sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_DROP_TEMP_TABLE,
    sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
    sqlite3.SQLITE_READ, sqlite3.SQLITE_ALTER_TABLE,
    sqlite3.SQLITE_CREATE_INDEX, sqlite3.SQLITE_DROP_INDEX,
    sqlite3.SQLITE_CREATE_TEMP_INDEX, sqlite3.SQLITE_DROP_TEMP_INDEX,
    sqlite3.SQLITE_ANALYZE,
}
_REFUSED = {
    sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH, sqlite3.SQLITE_PRAGMA,
    sqlite3.SQLITE_CREATE_TRIGGER, sqlite3.SQLITE_CREATE_TEMP_TRIGGER,
    sqlite3.SQLITE_CREATE_VIEW, sqlite3.SQLITE_CREATE_TEMP_VIEW,
    sqlite3.SQLITE_CREATE_VTABLE, sqlite3.SQLITE_DROP_VTABLE,
    sqlite3.SQLITE_DROP_TRIGGER, sqlite3.SQLITE_DROP_VIEW,
}


def _authorizer(prefix: str):
    def check(action, arg1, arg2, _db, _source):
        if action in _REFUSED:
            return sqlite3.SQLITE_DENY
        if action in _TABLE_ACTIONS:
            #  For index actions arg1 is the index name and arg2 the table;
            #  for ALTER TABLE arg1 is the database and arg2 the table.
            if action in (sqlite3.SQLITE_CREATE_INDEX, sqlite3.SQLITE_DROP_INDEX,
                          sqlite3.SQLITE_CREATE_TEMP_INDEX,
                          sqlite3.SQLITE_DROP_TEMP_INDEX):
                names = [arg1, arg2]
            elif action == sqlite3.SQLITE_ALTER_TABLE:
                names = [arg2]
            else:
                names = [arg1]
            for name in names:
                if name is None or name in _INTERNAL:
                    continue
                if not str(name).lower().startswith(prefix):
                    return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    return check


SCHEMA = """
CREATE TABLE IF NOT EXISTS plugin_schema_version (
  plugin     TEXT PRIMARY KEY,
  version    INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def version_of(path: str, plugin: str) -> int:
    conn = _connect(path)
    try:
        conn.executescript(SCHEMA)
        row = conn.execute("SELECT version FROM plugin_schema_version WHERE plugin=?",
                           (plugin,)).fetchone()
        return int(row["version"]) if row else 0
    finally:
        conn.close()


def apply_one(path: str, plugin: str, steps: Sequence[str]) -> int:
    """Apply the steps not applied yet. Returns the version reached.

    Raises on failure, after rolling back this plugin's pending steps.
    """
    conn = _connect(path)
    try:
        conn.executescript(SCHEMA)
        row = conn.execute("SELECT version FROM plugin_schema_version WHERE plugin=?",
                           (plugin,)).fetchone()
        found = int(row["version"]) if row else 0
        if found > len(steps):
            raise RuntimeError(
                f"its tables are at version {found} and this build of it knows "
                f"{len(steps)} steps — it was downgraded; its migrations are "
                "not run")
        if found == len(steps):
            return found

        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.set_authorizer(_authorizer(table_prefix(plugin)))
            for step in steps[found:]:
                conn.execute(step)
            conn.set_authorizer(None)
            conn.execute(
                "INSERT INTO plugin_schema_version(plugin, version, updated_at)"
                " VALUES(?, ?, datetime('now'))"
                " ON CONFLICT(plugin) DO UPDATE SET version=excluded.version,"
                " updated_at=excluded.updated_at", (plugin, len(steps)))
            conn.execute("COMMIT")
        except BaseException:
            conn.set_authorizer(None)
            conn.execute("ROLLBACK")
            raise
        return len(steps)
    finally:
        conn.close()


def apply_all(path: str, registry: PluginRegistry) -> dict[str, Any]:
    """Every registered plugin's migrations. Never raises."""
    out: dict[str, Any] = {}
    for plugin, steps in sorted(registry.migrations().items()):
        try:
            out[plugin] = apply_one(path, plugin, steps)
        except Exception as e:
            out[plugin] = f"failed: {type(e).__name__}: {e}"
            log.warning("plugin %r: migrations not applied: %s: %s",
                        plugin, type(e).__name__, e)
    return out


class PluginDatabase:
    """A plugin's handle on its own tables, and only on them."""

    def __init__(self, path: str, plugin: str):
        self.path = path
        self.plugin = plugin
        self.prefix = table_prefix(plugin)

    def _run(self, sql: str, params: Sequence[Any] = ()):
        conn = _connect(self.path)
        try:
            conn.set_authorizer(_authorizer(self.prefix))
            cur = conn.execute(sql, tuple(params))
            rows = [dict(r) for r in cur.fetchall()]
            return rows, cur.rowcount
        finally:
            conn.close()

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        return self._run(sql, params)[0]

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        return self._run(sql, params)[1]
