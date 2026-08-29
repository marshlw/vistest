# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Эталон на ветку: слой поверх основного набора.

Зачем. Эталон был один на платформу, а веток у команды много. Дальше по
сценарию: кто-то намеренно переверстал форму в своей ветке, посмотрел дифф,
принял его как новую норму — и main начал падать на том же снимке, потому что
эталон-то один. Обратный случай не лучше: main обновили, а в ветке, где эта
часть страницы ещё старая, всё покраснело. Оба раза виноватым выглядит
инструмент.

Как устроено. Ветка не получает СВОЙ набор целиком — это было бы и дорого, и
бессмысленно: девяносто девять снимков из ста в ветке те же самые. Ветка
получает **наложение**:

* читаем — из ветки, если она этот снимок уже переопределила, иначе из основного
  набора;
* пишем — **всегда в ветку**. Это и есть весь смысл: решение, принятое в ветке,
  не может изменить main.

Отсюда два следствия, которые легко упустить и оба неприятные.

**Каталог для записи.** Движок пишет накопленную маску нестабильности в
`store.dir_for(name)`. Если бы этот метод отдавал каталог основного набора для
унаследованного снимка, прогон ветки правил бы шумовой профиль main — молча и
не имея на это никакого права. Поэтому `dir_for` всегда указывает в ветку, а
чтение основного набора идёт через `load()`, где подмешивается уже накопленное
веткой.

**Материализация.** Добавить ignore-зону к снимку, которого в ветке ещё нет,
некуда: `meta.json` без `baseline.png` никто не прочитает. Поэтому первая же
запись по такому имени сначала копирует картинку из основного набора в ветку —
ветка с этого момента владеет снимком, и так и написано в его паспорте
(`forked_from`).
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from ..core import noise as _noise
from .base import BaselineRecord, BaselineStore
from .fs import FileBaselineStore

# Имя ветки становится именем каталога. `feature/pay-v2` — обычное имя ветки и
# никудышное имя каталога: слэш создал бы вложенность, а `..` увёл бы из
# хранилища вовсе.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_branch(branch: str) -> str:
    """Имя ветки → один сегмент пути.

    Схлопывание разных веток в один сегмент недопустимо ровно по той же
    причине, по которой недопустимо схлопывание имён снимков: `feature/pay` и
    `feature-pay` — разные ветки, и делить эталоны им нельзя. Поэтому к
    очищенному имени приписывается короткий хеш исходного.
    """
    import hashlib

    text = str(branch or "").strip()
    if not text:
        return ""
    cleaned = _UNSAFE.sub("-", text).strip("-.") or "branch"
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:6]
    return f"{cleaned[:48]}-{digest}"


class BranchBaselineStore(BaselineStore):
    """Наложение ветки поверх основного набора."""

    def __init__(self, overlay: FileBaselineStore, base: BaselineStore,
                 *, branch: str = "", base_label: str = ""):
        self.overlay = overlay
        self.base = base
        self.branch = branch
        self.base_label = base_label or "the base branch"

    # ------------------------------------------------------------------ #
    #  Чтение
    # ------------------------------------------------------------------ #
    def owner_of(self, name: str) -> str:
        """`branch` — снимок переопределён веткой, `base` — унаследован."""
        return "branch" if self.overlay.exists(name) else "base"

    def exists(self, name: str) -> bool:
        return self.overlay.exists(name) or self.base.exists(name)

    def load(self, name: str) -> BaselineRecord | None:
        if self.overlay.exists(name):
            return self.overlay.load(name)

        record = self.base.load(name)
        if record is None:
            return None

        # Ветка могла накопить собственный шумовой профиль поверх
        # унаследованной картинки — движок писал его в `dir_for`, то есть сюда.
        # Не подмешать его значило бы копить маску и не пользоваться ею.
        own_mask = _noise.load_mask(self.overlay.dir_for(name) / "stability.png")
        if own_mask is not None:
            record.stability_mask = (
                own_mask if record.stability_mask is None
                else _noise.merge_masks(record.stability_mask, own_mask, sticky=True))
        return record

    def list_names(self) -> list[str]:
        return sorted(set(self.overlay.list_names()) | set(self.base.list_names()))

    def dir_for(self, name: str) -> Path:
        """Всегда каталог ВЕТКИ — сюда пишут.

        Отдавать здесь каталог основного набора для унаследованного снимка
        значило бы позволить прогону ветки править файлы main.
        """
        return self.overlay.dir_for(name)

    def versions(self, name: str) -> list[dict]:
        if self.overlay.exists(name):
            return self.overlay.versions(name)
        getter = getattr(self.base, "versions", None)
        return getter(name) if getter else []

    def version_image(self, name: str, version: int) -> Path | None:
        target = self.overlay if self.overlay.exists(name) else self.base
        getter = getattr(target, "version_image", None)
        return getter(name, version) if getter else None

    def ignore_mask(self, name: str, shape: tuple[int, int]) -> np.ndarray | None:
        record = self.load(name)
        if record is None:
            return None
        mask = record.stability_mask
        if record.ignore_boxes:
            boxes = _noise.mask_from_boxes(shape, record.ignore_boxes)
            mask = boxes if mask is None else _noise.merge_masks(mask, boxes)
        return mask

    def projects(self) -> list[str]:
        from .fs import split_project

        return sorted({split_project(n)[0] for n in self.list_names()})

    # ------------------------------------------------------------------ #
    #  Запись — только в ветку
    # ------------------------------------------------------------------ #
    def _materialize(self, name: str) -> None:
        """Перенести унаследованный снимок в ветку, ничего не меняя в картинке.

        Нужно перед любой правкой паспорта: `meta.json` без `baseline.png`
        рядом — файл, который никто никогда не прочитает.
        """
        if self.overlay.exists(name):
            return
        record = self.base.load(name)
        if record is None:
            return
        meta = dict(record.meta or {})
        meta.pop("version", None)          # версия ветки считается своя
        meta["forked_from"] = self.base_label
        meta["forked_from_version"] = record.version
        self.overlay.save(BaselineRecord(
            name=name, image=record.image,
            stability_mask=record.stability_mask, dom=record.dom,
            ignore_boxes=record.ignore_boxes, meta=meta))

    def save(self, record: BaselineRecord) -> None:
        meta = dict(record.meta or {})
        if not self.overlay.exists(record.name):
            base_record = self.base.load(record.name)
            if base_record is not None:
                meta.setdefault("forked_from", self.base_label)
                meta.setdefault("forked_from_version", base_record.version)
        meta.setdefault("branch", self.branch)
        self.overlay.save(BaselineRecord(
            name=record.name, image=record.image,
            stability_mask=record.stability_mask, dom=record.dom,
            ignore_boxes=record.ignore_boxes, meta=meta))

    def add_ignore_box(self, name: str, box: dict) -> None:
        self._materialize(name)
        self.overlay.add_ignore_box(name, box)

    def restore(self, name: str, version: int, *, who: str = "") -> int:
        if not self.overlay.exists(name):
            raise NotImplementedError(
                f"«{name}» has no baseline of its own on this branch yet — it is "
                f"inherited from {self.base_label}, and rolling it back there "
                "from a branch is not something a branch may do.")
        return self.overlay.restore(name, version, who=who)
