# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Plain settings objects. Data and nothing else.

These are the dataclasses the engine is configured with. They live here rather
than in `vistest.config` for one reason: `vistest.config` is a *loader* — it
searches the working directory for `vistest.yaml`, reads a dozen environment
variables and pulls in `matrix` and `scenario` to validate what it found. Every
one of those is a read of global state, and the comparison core must not depend
on any of it.

So the split is by role, not by topic. Here: field names, defaults, and the
reasoning behind each number. There: how a value gets out of a file or an
environment variable and into one of these objects.

Nothing in this module touches the filesystem, the environment or the clock.
`vistest.config` re-exports every name below, so the historical import path
keeps working unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, replace
from typing import Any

__all__ = [
    "AIConfig", "AuthConfig", "CaptureConfig", "ConfigError", "DiffConfig",
    "MatrixConfig", "PathsConfig", "PluginsConfig", "RenderConfig",
    "ServiceConfig",
]


# --------------------------------------------------------------------------- #
#  Configuration errors
#
#  One exception type for «the configuration says something that cannot be
#  obeyed», raised at load time and never later. The rule it serves is the same
#  everywhere in the package: a configuration mistake is loud, and a missing
#  optional part is a warning.
#
#  Loud means the message names three things — the file or variable, the key,
#  and the value that was there. In the library mode this text is the whole
#  bug report: it surfaces in somebody else's CI, in a project we have never
#  seen, and nobody there is going to attach a debugger to find out which of
#  their eleven environment variables we disliked.
#
#  A `ValueError`, deliberately. Config mistakes used to be raised as plain
#  `ValueError` from `VisTestConfig.load`, and callers catch that; narrowing
#  the type without keeping the base would silently stop those handlers from
#  firing.
# --------------------------------------------------------------------------- #
class ConfigError(ValueError):
    """A configuration value is wrong — the message says which one and where."""


# --------------------------------------------------------------------------- #
#  Measured gaps
#
#  A threshold that separates two populations is only as good as the room
#  between them. `Gap` keeps the number together with that room: the worst
#  case on each side, where each was seen, on what and when. The same shape as
#  `Floor` in tests/corpus.py, for the same reason — a number on its own is a
#  number somebody chose; with the measurement beside it, whoever wants to move
#  it can see how far it can go before it lands on one side.
#
#  `tests/test_margins.py` replays the measurement on the frozen corpus and
#  fails, naming the gap, when either side crosses the threshold.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Gap:
    """A threshold between two measured populations, and the measurement."""

    value: float
    noise: float            # the worst case on the side that must stay below/out
    noise_at: str           # where it was seen
    signal: float           # the worst case on the side that must get through
    signal_at: str
    sample: str
    measured: str           # ISO date

    @property
    def ratio(self) -> float:
        lo, hi = sorted((self.noise, self.signal))
        return hi / lo if lo else float("inf")


#: What both gaps below were measured on. The component is a connected region
#: (8-connectivity) of ΔE00 > delta_e_threshold, on the ΔE map the consensus
#: sees — after global alignment. Every component of every pair, not only the
#: largest: the rule acts on each one.
FLAT_RECOLOUR_SAMPLE = (
    "178 pairs, 158 of them with any ΔE00 > 2.3, 903 154 components: frozen "
    "corpus 54 (opencv-4.14 and opencv-5.0 rasters) + "
    "tests/corpus.generate(layout_count=6), 124, 6 layouts; preset balanced "
    "(delta_e_threshold 2.3); "
    "opencv-python-headless 4.14.0.94, numpy 2.4, x86-64")

#: Area of the component, % of the frame. Percent, not pixels: a fixed pixel
#: count means a different thing on a screenshot of another size.
#: Largest noise component that is as uniform as the rule asks (σ/μ <= 0.09):
#: 0.309 % (layout2/scrollbar, a 8x341 bar that appeared). Smallest header
#: recolour: 5.217 % (layout5/header color). 16.9x apart; the geometric middle
#: is 1.27 %, rounded down to 1.25.
FLAT_RECOLOUR_AREA = Gap(
    value=1.25,
    noise=0.309, noise_at="generator layout2/scrollbar (and layout4)",
    signal=5.217, signal_at="generator layout5/header color",
    sample=FLAT_RECOLOUR_SAMPLE, measured="2026-09-25")

#: Coefficient of variation of ΔE00 inside the component (σ/μ).
#: Largest among header recolours: 0.049 (layout5/header color). Smallest among
#: noise components at least as large as the area threshold: 0.178
#: (layout0/gradient dither, 2.7 % of the frame). 3.6x apart; the geometric
#: middle is 0.093, rounded down to 0.09.
#:
#: The two thresholds are not equally backed. Below the area threshold the
#: noise rules of core/explain.py still stand: with the area lowered to 0.05 %
#: the rule fires on JPEG, combined and scroll-bar pairs and every one of them
#: is still explained and passes. Above this one nothing stands: loosened to
#: σ/μ 0.25 the re-dithered gradient (layout0) is taken past consensus and
#: fails — no rule explains a dither. This number is the only thing between
#: that pair and a false failure.
FLAT_RECOLOUR_CV = Gap(
    value=0.09,
    noise=0.178, noise_at="generator layout0/gradient dither",
    signal=0.049, signal_at="generator layout5/header color",
    sample=FLAT_RECOLOUR_SAMPLE, measured="2026-09-25")


# --------------------------------------------------------------------------- #
#  Comparison thresholds
# --------------------------------------------------------------------------- #
@dataclass
class DiffConfig:
    # --- perceptual threshold ---
    # ΔE00 ≈ 1.0 — theoretical limit of discernibility, 2.3 — the commonly accepted JND.
    # Below this value a human cannot tell the colors apart at all.
    delta_e_threshold: float = 2.3
    # Structural threshold: a pixel is considered changed only when BOTH color AND
    # structure have diverged. Consensus instead of OR.
    ssim_threshold: float = 0.90
    require_consensus: bool = True
    # Large flat recolour: the third way past consensus, next to strong colour.
    # See core/recolour.py for why it exists and what it cannot see, and
    # FLAT_RECOLOUR_AREA / FLAT_RECOLOUR_CV above for where the numbers come from.
    flat_recolour: bool = True
    flat_recolour_min_area_pct: float = FLAT_RECOLOUR_AREA.value
    flat_recolour_max_cv: float = FLAT_RECOLOUR_CV.value

    # --- alignment ---
    align_enabled: bool = True
    max_align_shift_px: float = 8.0     # more than this — treated as a real layout shift

    # --- anti-aliasing ---
    antialias_filter: bool = True
    aa_tolerance: float = 0.10          # fraction by which an AA pixel may differ

    # --- segmentation ---
    morph_open_px: int = 2              # eats away isolated noise
    morph_close_px: int = 6             # glues letters into a word
    min_region_px: int = 24             # MINIMUM MASK PIXELS, not bbox area
    min_region_fill: float = 0.06       # bbox with density below this — a thin noise strip
    max_regions: int = 200
    # Deterministic noise explanations after classification: scroll-bar
    # bands, JPEG re-encoding, subpixel re-rendering (core/explain.py).
    # Closing before opening keeps text changes and also keeps rendering
    # residue; this is what removes the residue. Off only for diagnostics.
    explain_noise: bool = True

    # --- MOVED detection ---
    detect_moved: bool = True
    move_search_px: int = 64
    move_match_threshold: float = 0.93

    # --- severity / verdict policy ---
    fail_severity: float = 25.0         # at least one region with this severity → fail
    max_changed_area_pct: float = 0.15  # or the total area of changes

    # Area is measured BEFORE segmentation, so it also includes the pixels that
    # are later discarded as too small or too sparse. If no region remains after
    # filtering, then the engine itself has judged everything it found to be
    # insignificant, and failing on the sum of the discarded pixels means
    # arguing with its own decision.
    #
    # This is seen on a diffuse subpixel difference smeared across the screen:
    # severity 0.0, zero regions, yet the area adds up to the threshold. This is
    # what a comparison of snapshots taken by different capture pipelines looks like.
    area_requires_region: bool = True
    # Exception to the exception: a very large area never comes from subpixel
    # noise. Here we fail even without regions.
    area_hard_fail_pct: float = 5.0
    above_fold_px: int = 900            # what counts as the first screen
    above_fold_weight: float = 1.5
    ignore_kinds: tuple[str, ...] = ("noise", "antialias")
    moved_severity_scale: float = 0.35  # shifts are less critical than changes

    # --- sizes ---
    size_tolerance_px: int = 0          # 0 = any size change is significant
    fail_on_size_change: bool = True

    # --- engine memory limit ---
    #  How many pixels we agree to compare.
    #
    #  The count that matters is not the size of the PNG but this one: a
    #  comparison unfolds a frame into several float32 arrays of H×W (and
    #  H×W×3 for Lab). A full-page shot of 1440×20000 is 28 Mpx, which is about
    #  350 MB per Lab array — and the cascade holds two, plus the ΔE map, plus
    #  the SSIM map. Three such comparisons in parallel (`MAX_PARALLEL` for
    #  background jobs is exactly three) and the machine goes to swap or the
    #  process is killed by the OOM killer.
    #
    #  A process killed on memory is the worst failure available: it writes
    #  nothing to the log, the run simply disappears, and a person looks for
    #  the cause anywhere except the size of the snapshot. So the limit is
    #  explicit, and the message names both the size and the way to raise it.
    #
    #  80 Mpx is 1920×41666: a full page of any sensible height passes, and
    #  «we accidentally captured a 200-megapixel tile map» does not. 0 disables
    #  the check.
    #
    #  `VISTEST_ENGINE_MAX_PIXELS` sets it, and — like every other environment
    #  variable — it is read by the loader in `vistest.config`, not here.
    max_pixels: int = 80_000_000

    def merged(self, **overrides: Any) -> DiffConfig:
        clean = {k: v for k, v in overrides.items() if v is not None}
        known = {f.name for f in fields(self)}
        unknown = set(clean) - known
        if unknown:
            raise TypeError(f"Unknown DiffConfig options: {sorted(unknown)}")
        return replace(self, **clean)


# --------------------------------------------------------------------------- #
#  Capture
# --------------------------------------------------------------------------- #
@dataclass
class CaptureConfig:
    full_page: bool = True
    device_scale_factor: float = 1.0
    # N snapshots in a row → pixels that differ between them are declared unstable
    stability_shots: int = 3
    stability_delay_ms: int = 120
    # mask accumulation across runs: new = old | current (sticky) or EMA
    stability_sticky: bool = True
    max_unstable_ratio: float = 0.25    # above this — a warning «the page is too alive»
    # Recapture on failure. A snapshot failed — take one more frame and see
    # which of the findings does not reproduce: whatever shivers between two
    # frames of the same page is false by construction. That is exactly the
    # logic `doctor` uses to measure the noise of a stand, applied inside a run.
    #
    # On by default, and deliberately so. Visual tests do not die from missing
    # integrations; they die because a month later nobody believes the red any
    # more and clicks «accept» without looking. The price is one extra frame,
    # and only for what has already failed.
    retry_on_fail: bool = True

    freeze_css: bool = True
    hide_scrollbars: bool = True
    hide_caret: bool = True
    disable_smooth_scroll: bool = True
    force_reduced_motion: bool = True

    # Overriding Math.random and the time origin. Enabled everywhere — both when
    # recording and when running: otherwise the baseline is captured with today
    # date and compared against tomorrow, and a diff on the date is guaranteed.
    determinism: bool = True
    # The origin of the page clock. The displayed date will be this one.
    frozen_time: str = "2024-01-01T12:00:00.000Z"
    # The clock RUNS from this point rather than standing still: a stopped clock
    # breaks animations, debounces, and timer-based polling.
    clock_ticks: bool = True

    # Context timezone and locale. Without pinning, the same page will show a
    # different date and a different number format for a developer and in CI.
    timezone: str | None = "Europe/Moscow"
    locale: str | None = "ru-RU"
    color_scheme: str | None = None      # None | "light" | "dark"

    wait_fonts: bool = True
    wait_images: bool = True
    scroll_through_page: bool = True    # warm up lazy-load before full_page
    scroll_step_ratio: float = 0.8
    network_idle_timeout_ms: int = 5000
    settle_timeout_ms: int = 2000

    # Hard limits: without them a heavy public site hangs the capture dead.
    screenshot_timeout_ms: int = 30000
    step_timeout_ms: int = 15000        # for stabilization, DOM, auto-masks
    # Chromium cannot capture pages taller than ~16384 px; an infinite feed
    # after lazy-load warm-up easily exceeds this.
    max_full_page_px: int = 16000

    auto_mask_media: bool = True        # video/canvas/iframe/[data-vistest=ignore]
    auto_mask_animated: bool = True     # elements with a running animation
    mask_selectors: tuple[str, ...] = ()
    mask_color: tuple[int, int, int] = (255, 0, 255)  # magenta: noticeable in artifacts

    capture_dom: bool = True


# --------------------------------------------------------------------------- #
#  Browser and viewport matrix
# --------------------------------------------------------------------------- #
@dataclass
class MatrixConfig:
    """«This page on chromium/firefox/webkit at 1440, 768 and 390» in one line.

    Empty means the behaviour is exactly what it always was: one browser, the
    size from `capture`. Expansion into variants and the storage rules live in
    `vistest.matrix`, which also explains why there must be exactly one base
    size and why it is better stated explicitly.
    """

    browsers: tuple[str, ...] = ()
    viewports: tuple[str, ...] = ()
    # Whose baselines stay under the key with no suffix. Empty — the first of
    # the list. State it explicitly: otherwise reordering the lines in
    # `viewports` moves a whole set of baselines on disk, and it reads as a
    # formatting change.
    base_viewport: str | None = None


# --------------------------------------------------------------------------- #
#  AI
# --------------------------------------------------------------------------- #
@dataclass
class AIConfig:
    # The trained region gate: it suppresses what looks like noise and never
    # raises severity. The model ships with the package (kilobytes of
    # coefficients), so turning it on costs nothing but the decision.
    #
    # Off by default, and the reason is measured, not cautious. The model was
    # trained against the cascade as it was before `core/explain.py`: a wide
    # aperture, a morphological opening that ate small changes, and no
    # deterministic noise explanations. The cascade it now sits behind already
    # suppresses scrollbars, JPEG re-encoding, re-render jitter and morphology
    # by construction, and it does so with a reason a person can argue with.
    # On the corpus the gate no longer adds: without it 0 of 55 regressions
    # over noise are missed, with it 5 of 55 are. A layer that only subtracts
    # must not be on by default.
    #
    # Re-train it against the current cascade and re-measure before flipping
    # this back. If a re-trained gate still does not beat `explain.py`, the
    # honest move is to drop it, not to ship it off by default forever.
    gate_enabled: bool = False
    gate_model_path: str = ""        # empty — the model from the package
    gate_threshold: float = 0.0      # 0 — the threshold from the model

    perceptual_enabled: bool = False
    perceptual_model_path: str = "models/perceptual.onnx"
    perceptual_tolerance: float = 0.06   # cos-distance below this → region = NOISE
    perceptual_min_region_px: int = 16

    attribution_enabled: bool = True     # DOM → selector; works without models
    attribution_min_iou: float = 0.25


# --------------------------------------------------------------------------- #
#  Plugins
# --------------------------------------------------------------------------- #
FAIL_ON_CHOICES = ("any", "likely-real", "confirmed")


@dataclass
class PluginsConfig:
    """The `plugins:` section.

    `fail_on` decides what a scorer's estimate does to a check (see
    `vistest.plugins.runtime` for the table). Without a scorer installed it
    changes nothing at all: there are no estimates to act on.

    `noise_below` and `confirmed_at` cut the score scale into three tiers.
    `disabled` lists entry-point names not to load. `options` holds every other
    key of the section — plugin sections, keyed by name — and is handed to
    plugins as it is.
    """

    enabled: bool = True
    fail_on: str = "likely-real"
    noise_below: float = 0.5
    confirmed_at: float = 0.9
    disabled: tuple[str, ...] = ()
    options: dict[str, Any] = field(default_factory=dict)

    def validated(self, where: str = "plugins") -> PluginsConfig:
        if self.fail_on not in FAIL_ON_CHOICES:
            raise ConfigError(
                f"{where}.fail_on: {self.fail_on!r} is not one of "
                f"{', '.join(FAIL_ON_CHOICES)}")
        for name in ("noise_below", "confirmed_at"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) \
                    or not 0.0 <= float(value) <= 1.0:
                raise ConfigError(
                    f"{where}.{name}: {value!r} is not a number between 0 and 1")
        if float(self.noise_below) > float(self.confirmed_at):
            raise ConfigError(
                f"{where}: noise_below ({self.noise_below}) is above "
                f"confirmed_at ({self.confirmed_at})")
        if not isinstance(self.enabled, bool):
            raise ConfigError(f"{where}.enabled: {self.enabled!r} is not true/false")
        return self


# --------------------------------------------------------------------------- #
#  Artifact rendering
# --------------------------------------------------------------------------- #
@dataclass
class RenderConfig:
    heatmap: bool = True
    boxes: bool = True
    side_by_side: bool = True
    onion_skin: bool = True
    blink_gif: bool = True
    blink_frame_ms: int = 600
    max_artifact_width: int = 2400      # downscale for heavy full-page shots
    label_regions: bool = True
    heatmap_max_de: float = 25.0


# --------------------------------------------------------------------------- #
#  Paths / service
# --------------------------------------------------------------------------- #
@dataclass
class PathsConfig:
    root: str = ".vistest"
    baselines: str = "baselines"
    runs: str = "runs"
    models: str = "models"
    platform_scoped_baselines: bool = True  # baselines/<platform>/<browser>/...


@dataclass
class AuthConfig:
    """Reusing the session between snapshots.

    A run of fifty snapshots behind a login should not mean fifty
    authorizations: that is minutes of time and a sure way to hit a rate-limit.
    """

    flow: str = "login"          # which flow to treat as authorization
    reuse_state: bool = True     # save cookies/localStorage and reuse them
    state_ttl_minutes: int = 60  # 0 = keep indefinitely


@dataclass
class ServiceConfig:
    api_url: str | None = None          # None → offline mode, files only
    project: str = "default"
    push_artifacts: bool = True
    timeout_s: float = 15.0
    fail_open: bool = True              # an unavailable API does not fail the tests
    # Intake token: the project token from «CI tokens», or the shared
    # `VISTEST_INGEST_TOKEN`. It is not kept in git — only the variable name is
    # — so the value arrives from the environment and has no place in
    # `vistest.yaml`. Empty on a single installation: intake is open there.
    token: str = ""
