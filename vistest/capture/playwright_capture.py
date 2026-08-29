# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Захват страницы: N снимков + профиль нестабильности + DOM."""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np

from ..config import CaptureConfig
from ..core import noise as _noise
from ..models import Shot
from . import dom as _dom


def _png_bytes_to_rgb(data: bytes) -> np.ndarray:
    from PIL import Image

    return np.array(Image.open(io.BytesIO(data)).convert("RGB"))


def capture(
    page,
    *,
    cfg: CaptureConfig | None = None,
    out_dir: str | Path = ".",
    name: str = "shot",
    clip_selector: str | None = None,
) -> tuple[np.ndarray, np.ndarray, dict, list[str]]:
    """Снять страницу Playwright.

    Возвращает (rgb, unstable_mask, dom_snapshot, notes).

    Кадров делается `cfg.stability_shots`. Первый идёт в сравнение,
    расхождения между кадрами становятся маской нестабильности —
    так динамика вычисляется, а не описывается руками через mask_selectors.

    Реализация делегируется универсальному драйверу, чтобы Playwright и
    Selenium не разъезжались в поведении.
    """
    from ..integrations.driver import PlaywrightDriver

    shot = PlaywrightDriver(page).capture(
        cfg=cfg or CaptureConfig(), clip_selector=clip_selector
    )
    if out_dir:
        Path(out_dir).mkdir(parents=True, exist_ok=True)
    return shot.rgb, shot.unstable, shot.dom, shot.notes


def save_shot(
    out_dir: str | Path,
    name: str,
    rgb: np.ndarray,
    unstable: np.ndarray,
    dom_data: dict,
) -> Shot:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    img_path = out / f"{name}.png"
    _write_png(img_path, rgb)

    mask_path = out / f"{name}.mask.png"
    _noise.save_mask(mask_path, unstable)

    dom_path = out / f"{name}.dom.json"
    _dom.save(dom_path, dom_data)

    return Shot(
        image_path=str(img_path),
        width=int(rgb.shape[1]),
        height=int(rgb.shape[0]),
        stability_mask_path=str(mask_path),
        dom_path=str(dom_path),
        unstable_ratio=_noise.instability_score(unstable),
    )


def _write_png(path: Path, rgb: np.ndarray) -> None:
    try:
        import cv2

        cv2.imwrite(str(path), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    except ImportError:  # pragma: no cover
        from PIL import Image

        Image.fromarray(rgb).save(path)


def read_png(path: str | Path) -> np.ndarray:
    try:
        import cv2

        img = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if img is None:
            raise FileNotFoundError(path)
        return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    except ImportError:  # pragma: no cover
        from PIL import Image

        return np.array(Image.open(path).convert("RGB"))
