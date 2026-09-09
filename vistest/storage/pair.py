# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Эталон, который их инструмент положил рядом с фактом.

Playwright, Cypress и jest-image-snapshot при неудачном сравнении оставляют не
одну картинку, а тройку: `login-actual.png`, `login-expected.png`,
`login-diff.png` — в одном каталоге. Второй файл и есть эталон, с которым они
сравнивали, причём выбранный ими самими: с учётом их проекта, их платформы, их
`snapshotPathTemplate`. Вычислить этот выбор со стороны нельзя — правило
живёт в их конфигурации и меняется в ней же.

Поэтому: если снимок есть в нашем хранилище — работаем с ним, как раньше, со
всей историей и апрувом. Если нет, а рядом с фактом лежит их `expected` —
сравниваем с ним и говорим об этом в отчёте. Второе не заменяет первое: апрув
такого снимка означает `--update-snapshots` в их инструменте, а не запись у
нас, и врать про это не нужно.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from ..capture.playwright_capture import read_png
from ..core import noise as _noise
from .base import BaselineRecord, BaselineStore


class SiblingBaselineStore(BaselineStore):
    """Хранилище проекта плюс эталоны, найденные среди артефактов прогона.

    Один объект, а не два, потому что `CheckService` работает ровно с одним
    хранилищем. Разделять их значило бы решать «каким сервисом проверять этот
    снимок» на уровень выше — то есть в коде сбора, который про сравнение не
    знает ничего.
    """

    def __init__(self, primary: BaselineStore, siblings: dict[str, Path]):
        self.primary = primary
        self.siblings = {str(k): Path(v) for k, v in (siblings or {}).items()}

    # ------------------------------------------------------------------ #
    def borrowed(self, name: str) -> bool:
        """Этот снимок сравнивается с их файлом, а не с нашим эталоном."""
        return not self.primary.exists(name) and name in self.siblings

    def source_of(self, name: str) -> Path | None:
        return self.siblings.get(name) if self.borrowed(name) else None

    # ------------------------------------------------------------------ #
    def exists(self, name: str) -> bool:
        return self.primary.exists(name) or name in self.siblings

    def load(self, name: str) -> BaselineRecord | None:
        if self.primary.exists(name):
            return self.primary.load(name)
        png = self.siblings.get(name)
        if png is None or not png.exists():
            return None

        side = self._side(name)
        meta = _read_json(side / "meta.json") if side else None
        meta = meta or {}
        return BaselineRecord(
            name=name,
            image=read_png(png),
            stability_mask=_noise.load_mask(side / "stability.png") if side else None,
            dom=_read_json(side / "dom.json") if side else None,
            ignore_boxes=meta.get("ignore_boxes", []),
            version=int(meta.get("version", 1)),
            meta={**meta, "source": str(png), "borrowed": True},
        )

    def save(self, record: BaselineRecord) -> None:
        """Пишем всегда в наше хранилище.

        Перезаписать их `expected` в `test-results` было бы бессмысленно: этот
        каталог их инструмент чистит на следующем прогоне.
        """
        self.primary.save(record)

    def list_names(self) -> list[str]:
        return sorted(set(self.primary.list_names()) | set(self.siblings))

    def dir_for(self, name: str) -> Path:
        return self.primary.dir_for(name)

    def add_ignore_box(self, name: str, box: dict) -> None:
        self.primary.add_ignore_box(name, box)

    def ignore_mask(self, name: str, shape: tuple[int, int]) -> np.ndarray | None:
        if self.primary.exists(name):
            getter = getattr(self.primary, "ignore_mask", None)
            return getter(name, shape) if getter else None
        record = self.load(name)
        if record is None:
            return None
        mask = record.stability_mask
        if record.ignore_boxes:
            from ..core.regions import mask as zone_mask

            boxes, _ = zone_mask(shape, record.ignore_boxes,
                                 baseline_dom=record.dom)
            mask = boxes if mask is None else _noise.merge_masks(mask, boxes)
        return mask

    # ------------------------------------------------------------------ #
    def _side(self, name: str) -> Path | None:
        getter = getattr(self.primary, "dir_for", None)
        if getter is None:
            return None
        try:
            return getter(name)
        except Exception:                                  # pragma: no cover
            return None


def _read_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return None
