# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Which comparison engine runs, and what a result says about the choice.

Two engines stand side by side for one release:

* **v2** (`core/v2/`), the default: every difference a person can see is
  caught first, and only what a named rule explains is taken out. A region
  nothing explains fails the check — unless a threshold was set by a person,
  and then a region below it is listed apart and does not fail.
* **v1** (`core/comparator.py`), the cascade, deprecated: it filters before
  it explains (ΔE00 and SSIM consensus, morphology, per-pixel tolerance) and
  fails on severity. It goes in the next release.

The choice is one field, `DiffConfig.engine`, and `compare()` reads it — no
caller passes the engine on its own. It is set, in increasing strength, by
the default here, `engine:` in vistest.yaml, `VISTEST_ENGINE`, and the call
(`compare(engine=...)`, `expect_screenshot(engine=...)`, an `engine`
override of a check). `ENGINE_SOURCES` names them for the error messages.

Kept apart from `settings` and from both engines so that the default and
the words a result carries about it are written once.
"""

from __future__ import annotations

V1 = "v1"
V2 = "v2"
ENGINES = (V1, V2)

#: The engine a comparison runs when nobody chose one.
DEFAULT = V2

#: Where a choice can come from, weakest first.
ENGINE_SOURCES = ("default", "vistest.yaml `engine:`", "VISTEST_ENGINE",
                  "engine= in the call")

#: The one line a v1 result carries.
V1_DEPRECATED = (
    "engine v1 is deprecated and goes in the next release; v2 is the default "
    "— remove engine: v1 (vistest.yaml), VISTEST_ENGINE=v1 or engine=\"v1\" to "
    "use it")

#: The threshold v2 applies when nobody set one: whatever no rule explains
#: fails. A preset's `fail_severity` is v1's and is not applied to v2.
V2_DEFAULT_THRESHOLD = 0.0


def check(value, where: str = "engine") -> str:
    """`value` as an engine name, or a `ConfigError` saying what is allowed."""
    from .settings import ConfigError

    name = str(value).strip().lower() if value is not None else ""
    if name not in ENGINES:
        raise ConfigError(f"{where} must be "
                          f"{' or '.join(repr(e) for e in ENGINES)}, not {value!r}")
    return name


def preset_note(preset: str) -> str:
    """The line a v2 result carries when a preset other than balanced is chosen."""
    return (f"preset {preset!r} acts on engine v1 only: v2 has no presets — what "
            "no rule explains fails, below your threshold if you set one it is "
            "listed and does not")


#: Regions spelled out in the below-threshold line; the rest are counted.
BELOW_SHOWN = 5


def region_words(region) -> str:
    """The engine's sentence about a region (its «description» annotation),
    or where it is when there is none."""
    def get(key):
        return region.get(key) if isinstance(region, dict) else getattr(region, key, None)

    for a in get("annotations") or ():
        if isinstance(a, dict) and a.get("kind") == "description" and a.get("text"):
            return str(a["text"])
    return f"{get('w')}x{get('h')} at ({get('x')}, {get('y')})"


def below_line(result) -> str:
    """«below the threshold 25 (project override): 2 regions — …», or ''.

    The same words go into the result's notes, the report, the failure
    message and the API answer: a region a threshold let through is said,
    never dropped in silence.
    """
    threshold = getattr(result, "threshold", None)
    below = list(getattr(result, "below_threshold", None) or ())
    if not threshold or not below:
        return ""
    n = len(below)
    #  The same sentence about several regions — a line of text recoloured,
    #  word by word — is said once, with how many regions it is about.
    counts: dict[str, int] = {}
    for r in below:
        words = region_words(r)
        counts[words] = counts.get(words, 0) + 1
    items = [w if k == 1 else f"{w} ×{k}" for w, k in counts.items()]
    said = "; ".join(items[:BELOW_SHOWN])
    if len(items) > BELOW_SHOWN:
        rest = sum(list(counts.values())[BELOW_SHOWN:])
        said += f"; and {rest} more"
    return (f"below the threshold {float(threshold['value']):g} "
            f"({threshold['source']}): {n} region{'' if n == 1 else 's'} — {said}")
