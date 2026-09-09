# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Configuration. Presets + YAML + environment variables + per-call override.

The settings objects themselves moved to `vistest.core.settings`, where they
are plain data. What is left here is everything that *reads the world* to fill
them in: the search for `vistest.yaml` up from the working directory, the
environment variables, the platform key. That is the whole reason for the
split — the comparison core has to be importable without any of it.

Every name that used to live here is re-exported below, so `from
vistest.config import DiffConfig` keeps working exactly as before.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, replace
from pathlib import Path

from .core.settings import (
    AIConfig,
    AuthConfig,
    CaptureConfig,
    ConfigError,
    DiffConfig,
    MatrixConfig,
    PathsConfig,
    RenderConfig,
    ServiceConfig,
)

__all__ = [
    "AIConfig", "AuthConfig", "CaptureConfig", "ConfigError", "DiffConfig",
    "MatrixConfig", "PathsConfig", "RenderConfig", "ServiceConfig",
    "VisTestConfig", "env_flag", "env_float", "env_int", "env_text",
    "platform_key",
]


# --------------------------------------------------------------------------- #
#  Reading the environment
#
#  Four functions rather than four inline `try: int(...) except: pass` blocks,
#  and the difference is the whole point of them: garbage in a variable used to
#  leave the default in place without a word. The setting appeared to be
#  ignored, and the only way to find out why was to read this file.
#
#  In the library mode that costs more than an afternoon. The variable is set
#  in somebody else's CI, by somebody who has never seen this repository, and
#  the symptom they get is «vistest does not respect our threshold» — with no
#  error, no warning and nothing in the log.
#
#  So: a variable that is set and unreadable stops the run, and the message
#  names the variable, the value and what was expected. A variable that is not
#  set at all is not a mistake and never was.
# --------------------------------------------------------------------------- #
_TRUE = ("1", "true", "yes", "on")
_FALSE = ("0", "false", "no", "off")


def env_text(name: str, default: str = "") -> str:
    """The variable, stripped. Unset and empty are the same thing here."""
    return (os.getenv(name) or "").strip() or default


def env_flag(name: str, *, default: bool = False) -> bool:
    raw = env_text(name)
    if not raw:
        return default
    if raw.lower() in _TRUE:
        return True
    if raw.lower() in _FALSE:
        return False
    raise ConfigError(
        f"{name}: {raw!r} is not a yes/no value "
        f"({'/'.join(_TRUE)} or {'/'.join(_FALSE)})")


def env_int(name: str, *, default: int | None = None) -> int | None:
    raw = env_text(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"{name}: {raw!r} is not an integer") from None


def env_float(name: str, *, default: float | None = None) -> float | None:
    raw = env_text(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        raise ConfigError(f"{name}: {raw!r} is not a number") from None


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
            #  There is a config file here and we cannot read it. Falling back
            #  to the defaults would run somebody's suite under thresholds they
            #  did not choose and never see — so this stops, and says which
            #  file it stopped over.
            #  PyYAML is a base dependency, so reaching this means it was
            #  removed from the environment on purpose. Falling back to the
            #  defaults would run somebody's suite under thresholds they did
            #  not choose and never see, so this stops and names the file.
            raise ConfigError(
                f"{path}: reading it requires PyYAML, which is installed with "
                "vistest but is missing from this environment. Reinstall it "
                "with `pip install pyyaml`, or remove the file to run on the "
                "defaults.") from e

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
        if v := env_text("VISTEST_API_URL"):
            cfg.service = replace(cfg.service, api_url=v)
        if v := env_text("VISTEST_PROJECT"):
            cfg.service = replace(cfg.service, project=v)
        # Two names, because both occur in the wild: in a pipeline that is
        # already pushing runs the variable is called INGEST.
        if v := (env_text("VISTEST_TOKEN") or env_text("VISTEST_INGEST_TOKEN")):
            cfg.service = replace(cfg.service, token=v)
        if v := env_text("VISTEST_ROOT"):
            cfg.paths = replace(cfg.paths, root=v)
        if env_flag("VISTEST_UPDATE_BASELINES"):
            cfg.update_baselines = True
        if env_flag("VISTEST_PERCEPTUAL"):
            cfg.ai = replace(cfg.ai, perceptual_enabled=True)

        # The engine's memory limit. A `DiffConfig` field like any other, so
        # the only thing that happens here is what happens to every environment
        # variable: it is read once, at load, in the loader — and a value that
        # cannot be read stops the run instead of quietly leaving the default,
        # which is what it used to do.
        if (limit := env_int("VISTEST_ENGINE_MAX_PIXELS")) is not None:
            if limit < 0:
                raise ConfigError(
                    f"VISTEST_ENGINE_MAX_PIXELS: {limit} is negative "
                    "(0 turns the check off)")
            cfg.diff = replace(cfg.diff, max_pixels=limit)

        # Verdict thresholds set from the interface. The service keeps them in
        # its own database, but a run of a connected project is a separate
        # process with its own vistest.yaml that knows nothing about that
        # database. Environment variables are the only way the threshold
        # reaches someone else's pytest; without this the setting would apply
        # to the service's own runs and silently not to project runs — the kind
        # of discrepancy that costs days to find.
        #
        # Validated through the same function the interface validates with, so
        # a number outside its range is refused here too. It used to be dropped
        # in silence, which made «the threshold does not work» a bug report
        # about the engine rather than about the value that was typed.
        from .core.thresholds import ThresholdError, validate

        patch = {}
        for name in ("fail_severity", "max_changed_area_pct"):
            variable = f"VISTEST_{name.upper()}"
            raw = env_float(variable)
            if raw is None:
                continue
            try:
                patch[name] = validate(name, raw)
            except ThresholdError as e:
                raise ConfigError(f"{variable}: {e}") from None
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

    #  Read as a flag rather than compared with "1": an image that sets it to
    #  `true` used to fall through to the host's platform key, and the whole
    #  set of baselines silently moved to another directory.
    if env_flag("VISTEST_IN_DOCKER"):
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
