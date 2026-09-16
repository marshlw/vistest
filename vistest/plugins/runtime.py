# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Where the core calls plugins, and the rules it applies to their answers.

Every function here has the same shape: no implementation — the deterministic
path; an implementation that raised or answered nonsense — a warning, and the
same deterministic path. The deterministic path is not a fallback bolted on
afterwards; it is what the engine does, and a plugin is allowed to add to it.

**What a score does to a test.** The scorer only estimates. The core turns the
estimate into a tier and the tier into an outcome, under ``plugins.fail_on``:

=============  ==========  ==============  ===========
score tier     ``any``     ``likely-real``  ``confirmed``
=============  ==========  ==============  ===========
noise          fails       suppressed      suppressed
likely-real    fails       fails           not failing
confirmed      fails       fails           fails
=============  ==========  ==============  ===========

Two things hold whatever the setting:

* **nothing is swallowed quietly.** A region that does not fail because of a
  score is moved to ``suppressed`` with ``suppressed_by`` naming the scorer,
  the tier and the score. Reports and the pytest summary count those, so
  "1 difference suppressed as rendering noise" is always said out loud;
* **two safety rules outrank any scorer.** A region covering more than a fifth
  of the page, and any change of page geometry, are never suppressed by a
  score. That is what visual testing exists to catch, and a model trained on
  somebody's corpus does not get a vote on it.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from ..models import ChangeKind
from .api import MAX_ANNOTATIONS_PER_REGION, Annotation, AuthResult, RegionContext
from .registry import Registration

log = logging.getLogger("vistest.plugins")

__all__ = [
    "FAIL_ON",
    "MAX_SUPPRESSED_AREA_FRAC",
    "ScoringPolicy",
    "SuppressionReason",
    "annotate",
    "authenticate",
    "guard",
    "reason_of",
    "score",
    "tier_of",
]

FAIL_ON = ("any", "likely-real", "confirmed")
DEFAULT_FAIL_ON = "likely-real"

NOISE, LIKELY_REAL, CONFIRMED = "noise", "likely-real", "confirmed"

MAX_SUPPRESSED_AREA_FRAC = 0.20

#  Prefixes of `suppressed_by`. The text after the colon is for people; the
#  prefix is for code that counts (the pytest summary, the report).
PREFIX_NOISE = "noise"
PREFIX_BELOW = "below-fail-on"
PREFIX_KIND = "ignored-kind"


@dataclass(frozen=True)
class ScoringPolicy:
    fail_on: str = DEFAULT_FAIL_ON
    noise_below: float = 0.5
    confirmed_at: float = 0.9

    @classmethod
    def of(cls, cfg: Any) -> ScoringPolicy:
        """From a `PluginsConfig` (or anything with the same fields)."""
        if cfg is None:
            return cls()
        return cls(fail_on=getattr(cfg, "fail_on", DEFAULT_FAIL_ON),
                   noise_below=float(getattr(cfg, "noise_below", 0.5)),
                   confirmed_at=float(getattr(cfg, "confirmed_at", 0.9)))


def tier_of(value: float, policy: ScoringPolicy) -> str:
    if value < policy.noise_below:
        return NOISE
    if value < policy.confirmed_at:
        return LIKELY_REAL
    return CONFIRMED


def guard(region: Any, *, total_pixels: int, size_changed: bool) -> str:
    """Why this region may never be suppressed by a score. Empty — it may."""
    if size_changed:
        return "page geometry changed"
    if total_pixels > 0 and (region.w * region.h) / total_pixels > MAX_SUPPRESSED_AREA_FRAC:
        return f"region covers over {MAX_SUPPRESSED_AREA_FRAC:.0%} of the page"
    return ""


# --------------------------------------------------------------------------- #
#  Suppression reasons, for whoever counts them
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SuppressionReason:
    key: str
    phrase: str


_REASONS = {
    PREFIX_NOISE: SuppressionReason(PREFIX_NOISE, "suppressed as rendering noise"),
    PREFIX_BELOW: SuppressionReason(
        PREFIX_BELOW, "not failing: scored below the fail_on threshold"),
    PREFIX_KIND: SuppressionReason(
        PREFIX_KIND, "suppressed by diff.ignore_kinds"),
}

#  Classes the engine itself calls noise. Set aside by `ignore_kinds`, they are
#  counted as rendering noise; any other class set aside that way is a choice
#  somebody made in the config, and is counted as that.
_NOISE_KINDS = ("noise", "antialias")


def reason_of(suppressed_by: str | None) -> SuppressionReason:
    """Group a `suppressed_by` string for counting.

    Anything written before these prefixes existed, or by a deterministic
    stage that only names itself, counts as rendering noise: that is what every
    suppression in the engine has always meant.
    """
    head, _, rest = (suppressed_by or "").partition(":")
    head = head.strip()
    if head == PREFIX_KIND:
        kind = rest.strip().split(" ")[0]
        return _REASONS[PREFIX_NOISE] if kind in _NOISE_KINDS else _REASONS[PREFIX_KIND]
    return _REASONS.get(head, _REASONS[PREFIX_NOISE])


def count_suppressed(regions: Sequence[Any]) -> dict[str, int]:
    """phrase -> how many regions. Stable order: noise first."""
    counts: dict[str, int] = {}
    for r in regions:
        phrase = reason_of(getattr(r, "suppressed_by", None)).phrase
        counts[phrase] = counts.get(phrase, 0) + 1
    noise = _REASONS[PREFIX_NOISE].phrase
    return dict(sorted(counts.items(), key=lambda kv: (kv[0] != noise, kv[0])))


def count_suppressed_dicts(regions: Sequence[dict]) -> dict[str, int]:
    """The same count over serialised regions (report parts, stored JSON)."""
    counts: dict[str, int] = {}
    for r in regions:
        phrase = reason_of(r.get("suppressed_by")).phrase
        counts[phrase] = counts.get(phrase, 0) + 1
    noise = _REASONS[PREFIX_NOISE].phrase
    return dict(sorted(counts.items(), key=lambda kv: (kv[0] != noise, kv[0])))


def say_suppressed(counts: dict[str, int]) -> list[str]:
    """``{"suppressed as rendering noise": 1}`` -> the line people read."""
    out = []
    for phrase, n in counts.items():
        noun = "difference" if n == 1 else "differences"
        out.append(f"{n} {noun} {phrase}")
    return out


# --------------------------------------------------------------------------- #
#  Scoring
# --------------------------------------------------------------------------- #
@dataclass
class ScoreOutcome:
    scored: int = 0
    suppressed_noise: int = 0
    suppressed_below: int = 0
    held: int = 0
    error: str = ""
    notes: list[str] = field(default_factory=list)


def _eligible(region: Any) -> bool:
    kind = getattr(getattr(region, "kind", None), "value", getattr(region, "kind", ""))
    return not region.suppressed_by and kind not in ("noise", "antialias")


def _clean_scores(raw: Any, expected: int) -> list[float]:
    if isinstance(raw, (str, bytes, dict)):
        raise ValueError(f"expected {expected} scores, got {type(raw).__name__}")
    values = list(raw)
    if len(values) != expected:
        raise ValueError(f"expected {expected} scores, got {len(values)}")
    out = []
    for v in values:
        if isinstance(v, bool):
            raise ValueError(f"a score is not a number: {v!r}")
        if not isinstance(v, (int, float)):
            try:
                v = float(v)          # numpy scalars
            except (TypeError, ValueError):
                raise ValueError(f"a score is not a number: {v!r}") from None
        v = float(v)
        if not math.isfinite(v):
            raise ValueError(f"a score is not finite: {v!r}")
        out.append(min(1.0, max(0.0, v)))
    return out


def score(registration: Registration | None, regions: list[Any],
          ctx: RegionContext, policy: ScoringPolicy) -> ScoreOutcome:
    """Ask the scorer, then apply the policy. Mutates `regions` in place."""
    outcome = ScoreOutcome()
    if registration is None or not regions:
        return outcome
    if policy.fail_on not in FAIL_ON:            # validated at load; belt and braces
        policy = ScoringPolicy(DEFAULT_FAIL_ON, policy.noise_below, policy.confirmed_at)

    candidates = [r for r in regions if _eligible(r)]
    if not candidates:
        return outcome

    try:
        raw = registration.impl.score(candidates, ctx)
        if raw is None:               # the scorer abstains this time
            return outcome
        values = _clean_scores(raw, len(candidates))
    except Exception as e:
        outcome.error = f"{type(e).__name__}: {e}"
        log.warning("scorer %r failed, comparing without it: %s",
                    registration.name, outcome.error)
        return outcome

    name = registration.name
    for region, value in zip(candidates, values, strict=True):
        region.score = round(value, 4)
        outcome.scored += 1
        tier = tier_of(value, policy)
        if policy.fail_on == "any" or tier == CONFIRMED:
            continue
        if tier == LIKELY_REAL and policy.fail_on != "confirmed":
            continue

        blocked = guard(region, total_pixels=ctx.total_pixels,
                        size_changed=ctx.size_changed)
        if blocked:
            outcome.held += 1
            continue

        if tier == NOISE:
            region.suppressed_by = (f"{PREFIX_NOISE}: {name} scored {value:.2f} "
                                    f"(below {policy.noise_below:g})")
            #  Noise is not a change of any kind: the class and the severity go
            #  the way every other noise suppression in the engine takes them.
            region.kind = ChangeKind.NOISE
            region.severity = 0.0
            outcome.suppressed_noise += 1
        else:
            region.suppressed_by = (
                f"{PREFIX_BELOW}: {name} scored {value:.2f}, likely real but "
                f"below {policy.confirmed_at:g} (fail_on=confirmed)")
            outcome.suppressed_below += 1

    if outcome.held:
        outcome.notes.append(
            f"Scorer {name}: {outcome.held} region(s) would not have failed on "
            "the score alone but were kept — a safety rule outranks it.")
    if outcome.suppressed_noise:
        outcome.notes.append(
            f"Scorer {name}: {outcome.suppressed_noise} region(s) scored as "
            "rendering noise and suppressed. They are listed with the "
            "suppressed regions.")
    if outcome.suppressed_below:
        outcome.notes.append(
            f"Scorer {name}: {outcome.suppressed_below} region(s) are likely "
            "real but below the confirmed threshold; with fail_on=confirmed "
            "they do not fail the check. They are listed with the suppressed "
            "regions.")
    return outcome


# --------------------------------------------------------------------------- #
#  Annotating
# --------------------------------------------------------------------------- #
def annotate(registrations: list[Registration], regions: list[Any],
             ctx: RegionContext, *, result: Any = None) -> int:
    """Run the annotator chain. Returns how many annotations were attached.

    What an annotator may touch is its return value. The verdict fields are
    snapshotted around every call and restored if they moved — a rule written
    in code rather than in a comment.
    """
    attached = 0
    for reg in registrations:
        batch = getattr(reg.impl, "annotate_batch", None)
        try:
            if callable(batch):
                #  The programmatic annotator's older, whole-comparison
                #  contract (`vistest.ai.hooks`). Not part of the plugin API.
                attached += _guarded(reg, regions,
                                     lambda b=batch: b(regions, ctx, result))
                continue
            for region in regions:
                got = _guarded(reg, [region],
                               lambda region=region, impl=reg.impl:
                               impl.annotate(region, ctx))
                attached += got
        except Exception as e:
            log.warning("annotator %r failed, continuing without it: %s: %s",
                        reg.name, type(e).__name__, e)
    return attached


_VERDICT_FIELDS = ("kind", "severity", "suppressed_by", "score")


def _guarded(reg: Registration, regions: list[Any], call) -> int:
    before = [tuple(getattr(r, f) for f in _VERDICT_FIELDS) for r in regions]
    try:
        produced = call()
    finally:
        reverted = 0
        for region, keep in zip(regions, before, strict=True):
            if tuple(getattr(region, f) for f in _VERDICT_FIELDS) != keep:
                for f, value in zip(_VERDICT_FIELDS, keep, strict=True):
                    setattr(region, f, value)
                reverted += 1
        if reverted:
            log.warning("annotator %r tried to change the verdict on %d "
                        "region(s) — reverted", reg.name, reverted)
    if produced is None or len(regions) != 1:
        return 0
    return _attach(reg, regions[0], produced)


def _attach(reg: Registration, region: Any, produced: Any) -> int:
    if isinstance(produced, Annotation):
        produced = [produced]
    count = 0
    for item in produced:
        if len(region.annotations) >= MAX_ANNOTATIONS_PER_REGION:
            break
        try:
            clean = Annotation.checked(item, source=reg.name)
        except ValueError as e:
            log.warning("annotator %r returned an unusable annotation: %s",
                        reg.name, e)
            continue
        region.annotations.append(clean.to_dict())
        count += 1
    return count


# --------------------------------------------------------------------------- #
#  Signing in
# --------------------------------------------------------------------------- #
ROLES = ("viewer", "reviewer", "admin")


class ProviderUnavailable(RuntimeError):
    """The provider raised. Carries its message for the audit log."""


def authenticate(registration: Registration | None, login: str,
                 secret: str) -> AuthResult | None:
    """Ask the external provider. ``None`` when there is none or it said no.

    Raises `ProviderUnavailable` when the provider raised, so the caller can
    record the difference between "wrong password" and "directory down"; the
    answer to the person signing in is the same either way.
    """
    if registration is None or not login or not secret:
        return None
    try:
        result = registration.impl.authenticate(login, secret)
    except Exception as e:
        log.warning("auth provider %r failed: %s: %s", registration.name,
                    type(e).__name__, e)
        raise ProviderUnavailable(f"{type(e).__name__}: {e}") from None
    if result is None:
        return None
    if not isinstance(result, AuthResult):
        log.warning("auth provider %r returned %s instead of AuthResult — "
                    "treated as not recognised", registration.name,
                    type(result).__name__)
        return None
    #  The provider answers for the login it was asked about. A provider that
    #  answers with somebody else's account is not signing this person in.
    if str(result.login).strip().lower() != str(login).strip().lower():
        log.warning("auth provider %r answered for %r when asked about %r — "
                    "treated as not recognised", registration.name,
                    result.login, login)
        return None
    source = (str(result.source or "").strip() or "external")[:40]
    if source == "local":
        source = "external"
    role = result.role if result.role in ROLES else "viewer"
    return AuthResult(login=login, name=str(result.name or "")[:200], role=role,
                      external_id=str(result.external_id or "")[:500],
                      email=str(result.email or "")[:320],
                      groups=tuple(str(g) for g in (result.groups or ())),
                      source=source)
