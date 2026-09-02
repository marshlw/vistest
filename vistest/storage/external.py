# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Эталоны чужого проекта — читаются и пишутся на месте.

Подключая существующий набор тестов, копировать его эталоны к себе нельзя.
Они лежат в git рядом с кодом, их смотрят на ревью, их обновляет CI. Вторая
копия в `.vistest` разошлась бы с первой на первой же неделе, и человек
перестал бы понимать, какая настоящая.

Поэтому источник правды — папка проекта. `snapshots/login_filled.png` читается
оттуда и при апруве перезаписывается там же, то есть попадает в обычный
`git diff` и в обычное ревью. История версий не ведётся намеренно: она уже
есть, и называется git.

Всё, чего в чужой папке быть не должно, живёт в «спутнике» внутри `.vistest`:
маска нестабильности, игнор-зоны, DOM-снепшот, паспорт. Это данные VisTest, и
засорять ими чужой репозиторий было бы наглостью.

Выбор папки эталонов — за вызывающим. У проектов с прогоном в Docker их обычно
две (`snapshots/` для Windows, `snapshots_ci/` для контейнера), и правило
выбора описано в конфигурации проекта, а не угадывается здесь.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from ..capture.playwright_capture import _write_png, read_png
from ..core import noise as _noise
from .base import BaselineRecord, BaselineStore


def _safe(name: str) -> str:
    """Имя снимка → имя файла. Пути наружу вырезаются."""
    stem = str(name).replace("\\", "/").removesuffix(".png")
    parts = [p for p in stem.split("/") if p.strip(" .")]
    cleaned = "_".join(
        "".join(c if c.isalnum() or c in "-_." else "_" for c in p).strip("._")
        for p in parts
    )
    return cleaned or "snapshot"


class ExternalBaselineStore(BaselineStore):
    def __init__(self, snapshots_dir: str | Path, sidecar_dir: str | Path):
        self.root = Path(snapshots_dir)
        self.sidecar = Path(sidecar_dir)

    # ------------------------------------------------------------------ #
    def png_for(self, name: str) -> Path:
        return self.root / f"{_safe(name)}.png"

    def dir_for(self, name: str) -> Path:
        """Каталог служебных данных VisTest. В чужой репозиторий не попадает."""
        d = self.sidecar / _safe(name)
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ------------------------------------------------------------------ #
    def exists(self, name: str) -> bool:
        return self.png_for(name).exists()

    def load(self, name: str) -> BaselineRecord | None:
        png = self.png_for(name)
        if not png.exists():
            return None

        side = self.sidecar / _safe(name)
        meta = _read_json(side / "meta.json") or {}
        dom = _read_json(side / "dom.json")

        return BaselineRecord(
            name=name,
            image=read_png(png),
            stability_mask=_noise.load_mask(side / "stability.png"),
            dom=dom,
            ignore_boxes=meta.get("ignore_boxes", []),
            version=int(meta.get("version", 1)),
            meta={**meta, "source": str(png)},
        )

    def save(self, record: BaselineRecord) -> None:
        png = self.png_for(record.name)
        png.parent.mkdir(parents=True, exist_ok=True)

        side = self.dir_for(record.name)
        prev = _read_json(side / "meta.json") or {}
        _write_png(png, record.image)

        if record.stability_mask is not None:
            _noise.save_mask(
                side / "stability.png",
                _noise.merge_masks(_noise.load_mask(side / "stability.png"),
                                   record.stability_mask, sticky=True),
            )
        if record.dom is not None:
            (side / "dom.json").write_text(
                json.dumps(record.dom, ensure_ascii=False), encoding="utf-8")

        meta = {
            **prev,
            **(record.meta or {}),
            "name": record.name,
            "version": int(prev.get("version", 0)) + 1,
            "width": int(record.image.shape[1]),
            "height": int(record.image.shape[0]),
            "png": str(png),
            "external": True,
            "ignore_boxes": record.ignore_boxes or prev.get("ignore_boxes", []),
        }
        (side / "meta.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    def list_names(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(p.name for p in self.root.glob("*.png") if p.is_file())

    # ------------------------------------------------------------------ #
    #  Версии
    #
    #  Своей истории здесь нет и не должно быть: файл лежит в чужом git, и
    #  вторая история версий рядом с первой — это ровно тот случай, когда через
    #  неделю никто не понимает, какая настоящая. Методы существуют, чтобы у
    #  API был один интерфейс для обоих хранилищ, и честно говорят, где искать
    #  предыдущие версии.
    # ------------------------------------------------------------------ #
    def versions(self, name: str) -> list[dict]:
        png = self.png_for(name)
        if not png.exists():
            return []
        meta = _read_json(self.dir_for(name) / "meta.json") or {}
        return [{
            "version": int(meta.get("version", 1)),
            "current": True,
            "updated_at": meta.get("updated_at"),
            "approved_by": meta.get("approved_by"),
            "width": meta.get("width"),
            "height": meta.get("height"),
            "size_kb": round(png.stat().st_size / 1024),
            "vcs": "git",
            "note": "Previous versions of this file live in the project's git "
                    "history — VisTest keeps no second copy of them.",
        }]

    def version_image(self, name: str, version: int) -> Path | None:
        png = self.png_for(name)
        meta = _read_json(self.dir_for(name) / "meta.json") or {}
        if png.exists() and int(version) == int(meta.get("version", 1)):
            return png
        return None

    def restore(self, name: str, version: int, *, who: str = "") -> int:
        raise NotImplementedError(
            "This baseline lives in the project's repository — roll it back "
            "with git, the same way as any other file under review.")

    def add_ignore_box(self, name: str, box: dict) -> None:
        side = self.dir_for(name)
        path = side / "meta.json"
        meta = _read_json(path) or {}
        from ..zones import normalize

        meta.setdefault("ignore_boxes", []).append(normalize(box))
        path.write_text(json.dumps(meta, indent=2, ensure_ascii=False),
                        encoding="utf-8")

    # ------------------------------------------------------------------ #
    def ignore_mask(self, name: str, shape: tuple[int, int]) -> np.ndarray | None:
        rec = self.load(name)
        if rec is None:
            return None
        mask = rec.stability_mask
        if rec.ignore_boxes:
            from ..zones import mask as zone_mask

            boxes, _ = zone_mask(shape, rec.ignore_boxes, baseline_dom=rec.dom)
            mask = boxes if mask is None else _noise.merge_masks(mask, boxes)
        return mask


def _read_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return None
