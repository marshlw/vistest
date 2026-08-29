# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Vision-LLM: человекочитаемое описание регресса. Opt-in.

Вызывается только для самых серьёзных регионов (severity ≥ порога) и только
когда явно включён — это единственная часть системы, которая ходит в сеть и
стоит денег. Всё остальное работает офлайн.

Что даёт: в PR-комментарии вместо «CONTENT sev=68 @(412,1880) 220×48» —
«Кнопка "Оформить заказ" сменила цвет с синего на серый и стала неактивной».
Это то, что реально читают на код-ревью.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os

import numpy as np

from ..models import ChangeKind, DiffRegion

log = logging.getLogger("vistest.ai.captioner")

PROMPT = """You are analysing a visual regression in a web interface.
You are given three crops of the same part of the page:
1) BEFORE — the baseline, 2) AFTER — the current version, 3) HEATMAP — the difference map.

The engine classified the change as "{kind}" (severity {severity:.0f}/100){selector}.

Answer STRICTLY with a single JSON object and no markdown wrapper:
{{
  "summary": "one sentence in English: what exactly changed",
  "component": "what this interface element is called",
  "kind": "added|removed|moved|color|text|content",
  "likely_intentional": true|false,
  "severity_hint": "trivial|minor|major|critical",
  "reasoning": "short justification, up to 20 words"
}}"""


def _to_png_b64(arr: np.ndarray) -> str:
    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii")


class Captioner:
    def __init__(self, cfg):
        self.cfg = cfg
        self._client = None
        self._failed = False

    @property
    def available(self) -> bool:
        if self._failed or not self.cfg.captioner_enabled:
            return False
        if self._client is not None:
            return True
        try:
            import anthropic
        except ImportError:
            log.info("the anthropic package is not installed — the captioner is off")
            self._failed = True
            return False
        if not os.getenv("ANTHROPIC_API_KEY"):
            log.info("ANTHROPIC_API_KEY is not set — the captioner is off")
            self._failed = True
            return False
        self._client = anthropic.Anthropic(timeout=self.cfg.captioner_timeout_s)
        return True

    def caption(
        self,
        region: DiffRegion,
        crop_before: np.ndarray,
        crop_after: np.ndarray,
        crop_heat: np.ndarray,
    ) -> dict | None:
        if not self.available:
            return None
        selector = f', element: {region.selector}' if region.selector else ''
        prompt = PROMPT.format(
            kind=region.kind.value, severity=region.severity, selector=selector
        )
        content = []
        for label, img in (("BEFORE", crop_before), ("AFTER", crop_after),
                           ("HEATMAP", crop_heat)):
            content.append({"type": "text", "text": label})
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png",
                           "data": _to_png_b64(img)},
            })
        content.append({"type": "text", "text": prompt})

        try:
            msg = self._client.messages.create(
                model=self.cfg.captioner_model,
                max_tokens=400,
                messages=[{"role": "user", "content": content}],
            )
            text = "".join(b.text for b in msg.content if b.type == "text").strip()
            if text.startswith("```"):
                text = text.split("```")[1].lstrip("json").strip()
            return json.loads(text)
        except Exception as e:
            log.warning("caption failed: %s", e)
            return None

    def annotate(self, regions: list[DiffRegion], expected, actual, heatmap) -> None:
        if not self.available:
            return
        targets = [
            r for r in sorted(regions, key=lambda x: -x.severity)
            if r.severity >= self.cfg.captioner_min_severity
        ][: self.cfg.captioner_max_regions]

        for r in targets:
            pad = 24
            h, w = expected.shape[:2]
            x0, y0 = max(0, r.x - pad), max(0, r.y - pad)
            x1, y1 = min(w, r.x + r.w + pad), min(h, r.y + r.h + pad)
            data = self.caption(
                r, expected[y0:y1, x0:x1], actual[y0:y1, x0:x1], heatmap[y0:y1, x0:x1]
            )
            if not data:
                continue
            r.caption = data.get("summary")
            if data.get("likely_intentional") is True:
                r.suppressed_by = "llm:likely_intentional"
            kind = data.get("kind")
            if kind in {k.value for k in ChangeKind}:
                r.kind = ChangeKind(kind)
