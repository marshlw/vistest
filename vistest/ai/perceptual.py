# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Перцептивный фильтр на лёгкой CNN (ONNX Runtime, CPU).

Зачем нужен поверх ΔE00+SSIM: математика меряет сигнал, а не восприятие.
Есть класс изменений, которые математически заметны, а человеком не
воспринимаются как «другая картинка»:
  * перекодирование JPEG/WebP с другим качеством,
  * другой dithering градиента,
  * лёгкая разница в сглаживании иконки после обновления библиотеки,
  * пересчитанная тень с другим радиусом размытия на 0.5px.

Модель даёт эмбеддинг кропа; если косинусное расстояние между «было» и
«стало» ниже порога — регион понижается до NOISE.

Модель НЕОБЯЗАТЕЛЬНА. Без onnxruntime или без файла модели слой молча
выключается, движок работает на классике.

Подходящие модели (все ≤ 30 МБ, ~3–8 мс на кроп 224×224 на CPU):
  * mobilenetv3_small_100  (timm → ONNX)
  * squeezenet1_1          (torchvision → ONNX, база LPIPS)
  * efficientnet_lite0
Экспорт: см. scripts/export_perceptual_model.py
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

log = logging.getLogger("vistest.ai.perceptual")

_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


class PerceptualModel:
    """Ленивая обёртка над ONNX-моделью. Никогда не бросает наружу."""

    def __init__(self, model_path: str, input_size: int = 224):
        self.model_path = Path(model_path)
        self.input_size = input_size
        self._session = None
        self._input_name: str | None = None
        self._failed = False

    @property
    def available(self) -> bool:
        if self._failed:
            return False
        if self._session is not None:
            return True
        return self._load()

    def _load(self) -> bool:
        try:
            import onnxruntime as ort
        except ImportError:
            log.info("onnxruntime is not installed — the perceptual filter is off")
            self._failed = True
            return False

        if not self.model_path.exists():
            log.info("model %s not found — the perceptual filter is off", self.model_path)
            self._failed = True
            return False

        try:
            so = ort.SessionOptions()
            so.intra_op_num_threads = 2
            so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            self._session = ort.InferenceSession(
                str(self.model_path), so, providers=["CPUExecutionProvider"]
            )
            self._input_name = self._session.get_inputs()[0].name
            return True
        except Exception as e:
            log.warning("the model cannot be loaded: %s", e)
            self._failed = True
            return False

    # ------------------------------------------------------------------ #
    def _preprocess(self, crops: list[np.ndarray]) -> np.ndarray:
        import cv2

        batch = np.empty((len(crops), 3, self.input_size, self.input_size), np.float32)
        for i, c in enumerate(crops):
            r = cv2.resize(c, (self.input_size, self.input_size),
                           interpolation=cv2.INTER_AREA)
            x = (r.astype(np.float32) / 255.0 - _MEAN) / _STD
            batch[i] = x.transpose(2, 0, 1)
        return batch

    def embed(self, crops: list[np.ndarray]) -> np.ndarray | None:
        if not crops or not self.available:
            return None
        try:
            out = self._session.run(None, {self._input_name: self._preprocess(crops)})[0]
            feats = out.reshape(len(crops), -1).astype(np.float32)
            norm = np.linalg.norm(feats, axis=1, keepdims=True)
            return feats / np.maximum(norm, 1e-8)
        except Exception as e:
            log.warning("inference failed: %s", e)
            self._failed = True
            return None

    def distances(
        self, pairs: list[tuple[np.ndarray, np.ndarray]]
    ) -> list[float] | None:
        """Косинусные расстояния 0..2 для пар (было, стало)."""
        if not pairs or not self.available:
            return None
        flat = [c for pair in pairs for c in pair]
        emb = self.embed(flat)
        if emb is None:
            return None
        a, b = emb[0::2], emb[1::2]
        return [float(1.0 - float(np.dot(x, y))) for x, y in zip(a, b, strict=False)]
