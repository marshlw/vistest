# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Verdict thresholds, stored in the service database.

The rules — what is editable, what is a valid value, and which layer beats
which — are `vistest.core.thresholds`. What is here is the half that needs a
database: reading the `setting` table, writing to it, and handing the result to
the core layering.

**Where the values live.** In the `setting` table, not in `vistest.yaml`. The
config is in git and describes the project; a service that rewrites somebody
else's versioned file creates conflicts out of nothing and breaks with several
replicas on one volume. A threshold value belongs to an installation, so that
is where it is kept.

**Who beats whom.** In increasing strength:

    default / preset  →  vistest.yaml  →  global override  →  project override

The history behind this: the failure threshold lived in exactly one place —
`vistest.yaml` — while the settings screen had a slider that sent no request
anywhere. It showed a number, it moved, and that was the end of it: the value
died on the next repaint, and the initial one (35) did not even match the real
default (25). That is worse than a missing setting — a missing feature goes
unused, while this one was used, and people left believing they had configured
something.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import replace

from ..config import VisTestConfig
from ..core.thresholds import (
    EDITABLE,
    ThresholdError,
    ThresholdStore,
    env_patch,
    layer,
    validate,
)

GLOBAL = "global"
PROJECT = "project"

log = logging.getLogger("vistest.thresholds")
#  Said once per process: a fresh database is a normal state, and a line per
#  request would bury the log.
_warned_no_table = False

__all__ = ["EDITABLE", "GLOBAL", "PROJECT", "SettingStore", "ThresholdError",
           "apply", "effective", "env_for", "overrides", "put", "validate"]


def _rows(db, scope: str, project_key: str = "") -> dict[str, float]:
    """The overrides stored for one scope.

    Two failures used to look identical here, and that was the problem. A
    database with no `setting` table yet — a fresh installation, before the
    first migration — genuinely means «no overrides», and a run must not stop
    over it. A table that is there and unreadable, or a row holding something
    that is not a number, means the setting a person made is being ignored;
    answering «no overrides» to that is how a threshold silently stops working
    and stays that way for months.

    So the first case is answered, once, with a line in the log. Everything
    else is raised.
    """
    out: dict[str, float] = {}
    try:
        rows = db.query(
            "SELECT name, value FROM setting WHERE scope=? AND project_key=?",
            (scope, project_key or ""))
    except sqlite3.OperationalError as e:
        if "no such table" not in str(e).lower():
            raise
        global _warned_no_table
        if not _warned_no_table:
            _warned_no_table = True
            log.warning("the `setting` table does not exist yet, so the "
                        "thresholds are the ones in vistest.yaml (%s)", e)
        return out

    for r in rows:
        name = r["name"]
        if name not in EDITABLE:
            #  Rows are written through `validate`, so a name outside the
            #  editable set is either an older version's leftover or a hand
            #  edit. Neither is ours to obey, and neither is worth stopping a
            #  run for; it is worth saying out loud once.
            log.warning("setting %r (scope %s, project %r) is not an editable "
                        "threshold and is ignored", name, scope,
                        project_key or "")
            continue
        try:
            out[name] = float(r["value"])
        except (TypeError, ValueError):
            raise ThresholdError(
                f"the stored threshold {name!r} (scope {scope}, project "
                f"{project_key or '-'}) is {r['value']!r}, which is not a "
                "number. It was written through validation, so the row has "
                "been damaged — fix or delete it in the settings screen."
            ) from None
    return out


class SettingStore:
    """The `ThresholdStore` protocol, implemented over the `setting` table.

    This is the only object the core is given: one method, no sqlite in the
    signature, nothing the engine could reach back through into the service.
    """

    def __init__(self, db):
        self._db = db

    def overrides(self, project_key: str | None = None) -> dict[str, float]:
        merged = dict(_rows(self._db, GLOBAL))
        if project_key:
            merged.update(_rows(self._db, PROJECT, project_key))
        return merged

    def layers(self, project_key: str | None = None
               ) -> tuple[dict[str, float], dict[str, float]]:
        """Global and project rows separately, for a view that names the source."""
        return (_rows(self._db, GLOBAL),
                _rows(self._db, PROJECT, project_key) if project_key else {})


def overrides(db, project_key: str | None = None) -> dict[str, float]:
    """What overrides the config for this project: global plus project."""
    return SettingStore(db).overrides(project_key)


def effective(db, cfg: VisTestConfig | None = None,
              project_key: str | None = None) -> dict:
    """The values in force and — always — where each one came from."""
    cfg = cfg or VisTestConfig.load()
    from_yaml = {name: getattr(cfg.diff, name) for name in EDITABLE}
    glob, proj = SettingStore(db).layers(project_key)
    folded = layer(from_yaml, global_overrides=glob, project_overrides=proj)

    return {
        "project": project_key or "",
        "values": folded["values"],
        "sources": folded["sources"],
        "config": from_yaml,          # what vistest.yaml alone would say
        "global_overrides": glob,
        "project_overrides": proj,
        "preset": cfg.preset,
        "editable": {
            name: {"min": lo, "max": hi, "unit": unit, "hint": hint}
            for name, (lo, hi, unit, hint) in EDITABLE.items()
        },
    }


def put(db, values: dict, *, project_key: str | None = None,
        who: str = "") -> dict:
    """Write or clear overrides.

    `None` as a value clears the override — which is not the same as «zero».
    Zero is a meaningful value here («fail on any visible difference»), and
    without a separate way to say «put it back to what the config says» there
    would be no way back.
    """
    scope = PROJECT if project_key else GLOBAL
    key = project_key or ""
    written, cleared = {}, []

    for name, raw in (values or {}).items():
        if raw is None:
            db.execute(
                "DELETE FROM setting WHERE scope=? AND project_key=? AND name=?",
                (scope, key, name))
            cleared.append(name)
            continue
        number = validate(name, raw)
        #  The number is bound as a number. It used to be written as
        #  `repr(number)`, which puts Python's idea of how a float looks into a
        #  database column — a shape that depends on the type that reached this
        #  line and on the interpreter that ran it. A numpy scalar or an int
        #  would each have written something different, and every one of them
        #  reads back through `float()` on the way out, so nothing would have
        #  complained until the day it did.
        #
        #  `float()` around it although `validate` already returns one: that
        #  is one line of insurance against the day something else fills this
        #  in. A float and its `repr` happen to agree today; an int, a Decimal
        #  or a numpy scalar would each write a different shape, and every one
        #  of them reads back through `float()` on the way out, so nothing
        #  would complain until something failed to parse.
        #
        #  The column keeps its TEXT affinity, so SQLite still stores the value
        #  as text and comparisons in SQL remain lexicographic. Changing that
        #  is a column migration and is deliberately not done here; nothing in
        #  this package compares or orders these values in SQL — `_rows` reads
        #  them out and `float()` parses them, which is also why rows written
        #  by earlier versions keep working untouched.
        db.execute(
            "INSERT INTO setting(scope, project_key, name, value, updated_at,"
            " updated_by) VALUES(?,?,?,?,datetime('now'),?)"
            " ON CONFLICT(scope, project_key, name) DO UPDATE SET"
            " value=excluded.value, updated_at=excluded.updated_at,"
            " updated_by=excluded.updated_by",
            (scope, key, name, float(number), who))
        written[name] = number

    return {"written": written, "cleared": cleared, "scope": scope,
            "project": key}


def apply(cfg: VisTestConfig, db, project_key: str | None = None
          ) -> VisTestConfig:
    """The config with the overrides laid on, for runs inside the service.

    A copy is returned: `VisTestConfig.load()` is cached in modules and shared
    between requests, and editing the shared object for the sake of one run
    means broadcasting somebody's threshold to everyone else.
    """
    patch = overrides(db, project_key)
    if not patch:
        return cfg
    return replace(cfg, diff=cfg.diff.merged(**patch))


def env_for(db, project_key: str | None = None) -> dict[str, str]:
    """Overrides for SOMEBODY ELSE'S process.

    A run of a connected project is a separate pytest that reads its own
    `vistest.yaml` and knows nothing about our database. Without this, a
    threshold set in the interface would apply to the service's own runs and
    silently not to project runs — a discrepancy that costs days to find.
    """
    return env_patch(overrides(db, project_key))


#  A runtime assertion rather than a comment: if the protocol in the core ever
#  grows a method, this line is where it is noticed, not in a caller.
assert isinstance(SettingStore(None), ThresholdStore)
