# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Configuration. Presets + YAML + environment variables + per-call override."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
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
    # Пересъёмка при падении. Снимок упал — снимаем ещё один кадр и смотрим, что
    # из найденного не воспроизводится: то, что дрожит между двумя кадрами одной
    # и той же страницы, ложное по построению — ровно та логика, которой
    # `doctor` меряет шум стенда, только применённая внутри прогона.
    #
    # Включено по умолчанию, и это осознанно. Визуальные тесты умирают не от
    # отсутствия интеграций, а от того, что через месяц красному перестают
    # верить и жмут «принять» не глядя. Цена — один лишний кадр, и только у
    # того, что и так упало.
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
#  Матрица браузеров и разрешений
# --------------------------------------------------------------------------- #
@dataclass
class MatrixConfig:
    """«Эта страница на chromium/firefox/webkit при 1440, 768 и 390» — одной строкой.

    Пусто — поведение ровно как раньше: один браузер, размер из `capture`.
    Раскрытие в варианты и правила хранения — в `vistest.matrix`, там же
    объяснено, почему базовый размер обязан быть один и почему его лучше
    задать явно.
    """

    browsers: tuple[str, ...] = ()
    viewports: tuple[str, ...] = ()
    # Чьи эталоны остаются под ключом без суффикса. Пусто — первый из списка.
    # Задавайте явно: иначе перестановка строк в `viewports` переносит набор
    # эталонов на диске, а выглядит это как правка форматирования.
    base_viewport: str | None = None


# --------------------------------------------------------------------------- #
#  AI
# --------------------------------------------------------------------------- #
@dataclass
class AIConfig:
    # Обучаемый гейт регионов: подавляет то, что похоже на шум, и никогда не
    # поднимает severity. Модель уезжает вместе с пакетом (килобайты
    # коэффициентов), поэтому включён по умолчанию — в отличие от ONNX-фильтра,
    # которому нужен внешний файл модели.
    gate_enabled: bool = True
    gate_model_path: str = ""        # пусто — модель из пакета
    gate_threshold: float = 0.0      # 0 — порог из модели

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
    # Токен приёма: токен проекта из «CI-токенов» или общий
    # `VISTEST_INGEST_TOKEN`. В git не хранится — только имя переменной, —
    # поэтому значение приезжает из окружения, а в `vistest.yaml` его быть не
    # должно. Пусто на одиночной установке: там приём и так открыт.
    token: str = ""


@dataclass
class VisTestConfig:
    diff: DiffConfig = field(default_factory=DiffConfig)
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    matrix: MatrixConfig = field(default_factory=MatrixConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    render: RenderConfig = field(default_factory=RenderConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    service: ServiceConfig = field(default_factory=ServiceConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
    # Named sequences of steps: login, accept_cookies, open_cart…
    # Attached via the step {action: flow, name: login}.
    flows: dict[str, list] = field(default_factory=dict)
    update_baselines: bool = False
    preset: str = "balanced"

    # ---------------- factories ----------------
    @classmethod
    def preset_of(cls, name: str) -> VisTestConfig:
        cfg = cls(preset=name)
        if name == "strict":
            cfg.diff = replace(
                cfg.diff,
                delta_e_threshold=1.2,
                ssim_threshold=0.95,
                min_region_px=8,
                fail_severity=10.0,
                max_changed_area_pct=0.02,
                max_align_shift_px=2.0,
            )
        elif name == "loose":
            cfg.diff = replace(
                cfg.diff,
                delta_e_threshold=4.0,
                ssim_threshold=0.82,
                min_region_px=120,
                fail_severity=45.0,
                max_changed_area_pct=0.8,
                max_align_shift_px=16.0,
                moved_severity_scale=0.15,
            )
        elif name != "balanced":
            raise ValueError(f"Unknown preset: {name!r} (strict|balanced|loose)")
        return cfg

    @classmethod
    def load(cls, path: str | Path | None = None) -> VisTestConfig:
        """vistest.yaml → preset → sections. A missing file is not an error."""
        path = Path(path) if path else _find_config()
        if path is None or not path.exists():
            return cls._apply_env(cls())

        try:
            import yaml
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "Reading vistest.yaml requires PyYAML: pip install pyyaml") from e

        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        cfg = cls.preset_of(raw.get("preset", "balanced"))

        sections = {
            "diff": DiffConfig, "capture": CaptureConfig, "matrix": MatrixConfig,
            "ai": AIConfig, "render": RenderConfig, "paths": PathsConfig,
            "service": ServiceConfig, "auth": AuthConfig,
        }
        for key, klass in sections.items():
            if key not in raw or raw[key] is None:
                continue
            current = getattr(cfg, key)
            known = {f.name for f in fields(klass)}
            patch = {}
            for k, v in raw[key].items():
                if k not in known:
                    raise ValueError(f"vistest.yaml: unknown key {key}.{k}")
                # tuple fields come from YAML as lists
                patch[k] = tuple(v) if isinstance(getattr(current, k), tuple) else v
            setattr(cfg, key, replace(current, **patch))

        if "update_baselines" in raw:
            cfg.update_baselines = bool(raw["update_baselines"])

        # Матрица проверяется здесь, а не при первом прогоне. Опечатка в
        # размере окна («1440*900») иначе всплывёт через минуту ожидания
        # браузера и невнятной ошибкой драйвера — то есть максимально далеко
        # от строки, которую надо исправить.
        if cfg.matrix.browsers or cfg.matrix.viewports:
            from .matrix import from_config

            try:
                from_config(cfg)
            except Exception as e:
                raise ValueError(f"vistest.yaml: matrix: {e}") from None

        if raw.get("flows"):
            from .scenario import normalize

            for name, steps in raw["flows"].items():
                try:
                    cfg.flows[name] = normalize(steps)
                except Exception as e:
                    raise ValueError(f"vistest.yaml: flows.{name}: {e}") from e

        return cls._apply_env(cfg)

    @staticmethod
    def _apply_env(cfg: VisTestConfig) -> VisTestConfig:
        if v := os.getenv("VISTEST_API_URL"):
            cfg.service = replace(cfg.service, api_url=v)
        if v := os.getenv("VISTEST_PROJECT"):
            cfg.service = replace(cfg.service, project=v)
        # Два имени, потому что оба встречаются в дикой природе: у пайплайна,
        # который уже льёт прогоны, переменная называется INGEST.
        if v := (os.getenv("VISTEST_TOKEN")
                 or os.getenv("VISTEST_INGEST_TOKEN")):
            cfg.service = replace(cfg.service, token=v)
        if v := os.getenv("VISTEST_ROOT"):
            cfg.paths = replace(cfg.paths, root=v)
        if os.getenv("VISTEST_UPDATE_BASELINES", "").lower() in ("1", "true", "yes"):
            cfg.update_baselines = True
        if os.getenv("VISTEST_PERCEPTUAL", "").lower() in ("1", "true", "yes"):
            cfg.ai = replace(cfg.ai, perceptual_enabled=True)

        # Verdict thresholds set from the interface. The service keeps them in
        # its own database, but a run of a connected project is a separate
        # process with its own vistest.yaml that knows nothing about that
        # database. Environment variables are the only way the threshold
        # reaches someone else's pytest; without this the setting would apply
        # to the service's own runs and silently not to project runs — the kind
        # of discrepancy that costs days to find.
        patch = {}
        for name in ("fail_severity", "max_changed_area_pct"):
            raw = os.getenv(f"VISTEST_{name.upper()}")
            if not (raw or "").strip():
                continue
            try:
                patch[name] = float(raw)
            except ValueError:
                # Garbage in the variable must not fail a run: the threshold
                # stays what the config says.
                continue
        if patch:
            cfg.diff = replace(cfg.diff, **patch)

        # Logins and passwords are not stored in git: only the variable names.
        try:
            from .scenario import load_secrets

            load_secrets(cfg.root_path)
        except Exception:
            pass
        return cfg

    # ---------------- paths ----------------
    @property
    def root_path(self) -> Path:
        return Path(self.paths.root).resolve()

    def baselines_path(self, platform_key: str = "") -> Path:
        p = self.root_path / self.paths.baselines
        if self.paths.platform_scoped_baselines and platform_key:
            p = p / platform_key
        return p

    def runs_path(self) -> Path:
        return self.root_path / self.paths.runs


def _find_config() -> Path | None:
    for d in [Path.cwd(), *Path.cwd().parents]:
        for name in ("vistest.yaml", "vistest.yml"):
            if (d / name).exists():
                return d / name
        if (d / ".git").exists():
            break
    return None


def platform_key(browser: str = "chromium", scale: float = 1.0,
                 viewport: str | None = None) -> str:
    """Environment key. Baselines captured on Windows are not compared with Linux ones.

    `viewport` — размер окна в ключе, для матрицы разрешений. Пусто — ключ
    ровно такой же, каким был всегда, и это не забота о красоте: под ним лежат
    все уже снятые эталоны. Подробности и правило базового размера — в
    `vistest.matrix`.
    """
    import platform as _p

    if os.getenv("VISTEST_IN_DOCKER") == "1":
        system = "docker"
    else:
        system = {"Windows": "win", "Linux": "linux", "Darwin": "mac"}.get(
            _p.system(), _p.system().lower()
        )
    key = f"{system}-{browser}-{scale:g}x"
    if viewport:
        from .matrix import normalize_viewport

        key += "-" + normalize_viewport(viewport)
    return key
