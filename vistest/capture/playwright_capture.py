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
from ..core import pngio as _pngio
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


#  The two historical names. The decoding itself moved to `vistest.core.pngio`
#  when the library mode needed a PNG reader that does not drag the capture
#  layer — and with it the config loader — into somebody else's test process.
#  Kept here as thin wrappers because they are imported by name from six
#  modules and by tests.
def _write_png(path: Path, rgb: np.ndarray) -> None:
    _pngio.write(path, rgb)


def read_png(path: str | Path) -> np.ndarray:
    return _pngio.read(path)
