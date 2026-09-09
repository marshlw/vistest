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

from dataclasses import dataclass, fields, replace
from typing import Any


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
    # coefficients), which is why it is on by default — unlike the ONNX filter,
    # which needs an external model file.
    gate_enabled: bool = True
    gate_model_path: str = ""        # empty — the model from the package
    gate_threshold: float = 0.0      # 0 — the threshold from the model

    perceptual_enabled: bool = False
    perceptual_model_path: str = "models/perceptual.onnx"
    perceptual_tolerance: float = 0.06   # cos-distance below this → region = NOISE
    perceptual_min_region_px: int = 16

    attribution_enabled: bool = True     # DOM → selector; works without models
    attribution_min_iou: float = 0.25


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
