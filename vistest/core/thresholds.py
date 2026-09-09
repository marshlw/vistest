# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Verdict thresholds: what is editable, what is valid, and who wins.

The values themselves live in four different places, and each of them exists
for a reason:

    default / preset  →  vistest.yaml  →  global override  →  project override
                      →  the snapshot's own passport  →  the call

Layering them is arithmetic on dictionaries and does not need a database, a
config file or a web framework — so it is here, in one function, used by
everyone. What *does* need a database is reading the global and project rows,
and that stays in the service: `vistest.api.thresholds` implements the
`ThresholdStore` protocol below over sqlite and passes the result in.

That split is the whole point of this module. Before it, the same layering was
written out four times — in the settings API, in `CheckService`, in the
baselines routes and in the environment variables handed to somebody else's
pytest — and each copy could drift from the others.

Two things are worth saying about the rules, because they are easy to get
subtly wrong on a rewrite:

* `None` never erases anything. It means «not set at this layer», and then the
  layer below is in force. Without that, there would be no way to say «go back
  to what the config says», because zero is a meaningful value here — it means
  «fail on any visible difference».
* An effective value is always returned together with its source. «35» without
  an answer to «why 35» is the same unverifiable promise as «accept as
  baseline» without saying which baseline.

Only the verdict policy is editable: at what severity a snapshot counts as
failed, and what share of changed area is enough on its own. Everything else in
`DiffConfig` is engine tuning (the ΔE00 and SSIM thresholds, the morphology,
the shift search); those are chosen once for a task and kept in the config next
to the code, not turned between runs from a web interface.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

#  Where a value came from, strongest last. These strings reach the interface,
#  so they are part of the API.
CONFIG = "config"
GLOBAL = "global"
PROJECT = "project"
SNAPSHOT = "snapshot"

#  name -> (low, high, unit, why)
EDITABLE: dict[str, tuple[float, float, str, str]] = {
    "fail_severity": (
        0.0, 100.0, "",
        "Severity at which a snapshot is considered failed. 0 — any visible "
        "difference is a failure; 100 — only gross breakage."),
    "max_changed_area_pct": (
        0.0, 100.0, "%",
        "Share of the frame that is enough on its own, regardless of severity. "
        "Catches a page that shifted as a whole."),
}

#  The thresholds a snapshot may carry in its own passport. Deliberately the
#  same short list: a third level must govern the same things the first two do,
#  otherwise it is not a third level but a separate system using the same words.
SNAPSHOT_THRESHOLDS = tuple(EDITABLE)


class ThresholdError(ValueError):
    """The value does not pass validation — with text that can be shown."""


@runtime_checkable
class ThresholdStore(Protocol):
    """Where the global and per-project overrides are kept.

    One method, because that is all the core needs. The service implements it
    over its sqlite table; a library user who has no service at all passes
    nothing and gets the config values.
    """

    def overrides(self, project_key: str | None = None) -> dict[str, float]:
        """Overrides in force for this project: global merged with project."""
        ...


def validate(name: str, value) -> float:
    """A number inside its allowed range, or `ThresholdError` saying why not."""
    if name not in EDITABLE:
        raise ThresholdError(
            f"{name!r} is not editable from the interface. "
            f"Editable: {', '.join(sorted(EDITABLE))}.")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ThresholdError(f"{name}: {value!r} is not a number") from None
    lo, hi, unit, _ = EDITABLE[name]
    if not lo <= number <= hi:
        raise ThresholdError(
            f"{name}: {number:g}{unit} is outside {lo:g}{unit}…{hi:g}{unit}")
    return number


def clean(raw, *, known=None) -> dict[str, float]:
    """Validate a whole dict of thresholds. `None` values drop out.

    Raises `ThresholdError` on the first bad entry, naming it. Used where a
    person has just typed the numbers in and has to be told at once — a
    threshold saved as a string would be ignored silently by the engine, which
    is the worst of the possible outcomes: the interface shows the value and
    the verdicts are computed by the old one.
    """
    if not isinstance(raw, dict):
        raise ThresholdError("thresholds must be an object")
    names = set(known or EDITABLE)
    unknown = set(raw) - names
    if unknown:
        raise ThresholdError(
            f"unknown thresholds: {sorted(unknown)}. "
            f"Editable: {', '.join(sorted(names))}")
    return {name: validate(name, value)
            for name, value in raw.items() if value is not None}


def from_meta(meta: dict | None) -> dict[str, float]:
    """Thresholds out of a baseline's passport. Garbage is ignored silently.

    Failing a comparison because of a string in a threshold field would mean
    one crooked edit of a passport stops a whole run rather than one snapshot.
    Validation belongs at the moment of saving, which is what `clean` is for.
    """
    raw = (meta or {}).get("thresholds") or {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, float] = {}
    for name in SNAPSHOT_THRESHOLDS:
        value = raw.get(name)
        if value is None:
            continue
        try:
            out[name] = float(value)
        except (TypeError, ValueError):
            continue
    return out


def layer(config: dict, *, global_overrides: dict | None = None,
          project_overrides: dict | None = None,
          snapshot: dict | None = None) -> dict:
    """Fold the layers into effective values, each with its source.

    `config` is what the preset and `vistest.yaml` say — the floor, and the
    only layer that is always complete. Above it, in increasing strength: the
    global override, the project override, the snapshot's own passport.

    Returns `{"values": …, "sources": …}`. A `None` anywhere means «not set at
    this layer» and is skipped, so it never erases a lower one.

    The call is not a layer here on purpose: a call may override any engine
    parameter, not only the two editable ones, and it never has to be shown on
    a settings screen with a source next to it. `patch_for` handles that side.
    """
    values = dict(config)
    sources = dict.fromkeys(config, CONFIG)

    def apply(patch: dict | None, source: str) -> None:
        for name, value in (patch or {}).items():
            if value is None or name not in values:
                continue
            values[name] = value
            sources[name] = source

    apply(global_overrides, GLOBAL)
    apply(project_overrides, PROJECT)
    apply(snapshot, SNAPSHOT)
    return {"values": values, "sources": sources}


def patch_for(snapshot_meta: dict | None = None,
              call: dict | None = None) -> dict:
    """The two innermost layers of one comparison, as a `DiffConfig` patch.

    Thresholds go config → project (already folded into the config by then) →
    **snapshot** → call. The last two are what a single comparison adds, and
    this is where they are combined.

    The third level exists because two were not enough: one noisy dashboard
    forced the threshold down for a whole set, that is, fixing one snapshot at
    the cost of every other snapshot's sensitivity. A snapshot's threshold
    lives in its passport rather than in the database, and deliberately so — it
    has to travel with the baseline: into a branch overlay, into a satellite of
    somebody else's project, into an archive.

    The call beats the passport: `assert_screenshot(fail_severity=...)` is
    written next to one particular check and knows more about it than a setting
    made in the interface at some point.

    The passport may only carry the two editable thresholds; a call may set any
    engine parameter, which is why only the first is filtered. The layers are
    folded into one dict rather than expanded as two `**` — they share keys,
    and Python fails such a call.
    """
    out = dict(from_meta(snapshot_meta))
    out.update({name: value for name, value in (call or {}).items()
                if value is not None})
    return out


def env_patch(overrides: dict) -> dict[str, str]:
    """Overrides shaped as environment variables for SOMEBODY ELSE'S process.

    A run of a connected project is a separate pytest that reads its own
    `vistest.yaml` and knows nothing about our database. Without this, a
    threshold set in the interface would apply to the service's own runs and
    silently not to project runs — the kind of discrepancy that costs days to
    find. `vistest.config` reads these names back on the other side.
    """
    return {f"VISTEST_{name.upper()}": repr(float(value))
            for name, value in (overrides or {}).items()
            if name in EDITABLE and value is not None}
