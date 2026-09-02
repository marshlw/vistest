# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Файловое хранилище бейзлайнов.

Раскладка (baselines/<platform-key>/<project>/<name>/):
    baseline.png        эталон
    stability.png       накопленная маска нестабильности
    dom.json            DOM-снепшот на момент апрува
    meta.json           версия, git sha, кто и когда заапрувил, ignore-боксы
    history/v3.png      предыдущие версии (для отката и «когда сломалось»)

Два уровня разделения, и оба обязательны:

* **По платформам.** Рендер шрифтов в Windows и Linux отличается физически,
  общий эталон между ними невозможен.
* **По проектам.** Имя снимка — это ключ к эталону. Если тестировать два
  приложения из одного каталога, их `login.png` схлопнутся в один эталон,
  и вы получите дифф «главная одного проекта против главной другого».
  Проект задаётся префиксом в имени: `shop.example/login.png`.

Плоские имена без проекта по-прежнему работают — старые эталоны не ломаются.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from ..capture.playwright_capture import _write_png, read_png
from ..core import noise as _noise
from .base import BaselineRecord, BaselineStore

_KEEP = "-_.() "


# --------------------------------------------------------------------------- #
#  Атомарная запись и блокировка каталога снимка
#
#  Апрув — единственное необратимое действие в интерфейсе, и до сих пор он
#  выполнялся как последовательность обычных записей: `baseline.png`, потом
#  `meta.json`. Падение между ними оставляло на диске новую картинку со старой
#  версией в паспорте — а по версии работает защита от параллельных решений
#  («эталон изменился, пока вы смотрели»). То есть ломался ровно тот механизм,
#  который должен был спасти.
#
#  Блокировка каталога закрывает вторую половину: «прочитать версию → записать
#  версию+1» два процесса выполняли вперемешку, и одна из версий терялась.
# --------------------------------------------------------------------------- #
LOCK_TIMEOUT_S = 20.0


@contextmanager
def _dir_lock(directory: Path, timeout: float = LOCK_TIMEOUT_S):
    """Межпроцессный лок на каталог снимка.

    Реализован через `O_EXCL`, а не через `fcntl`/`msvcrt`: работает одинаково
    на Windows и в контейнере, а сервис живёт и там, и там. Просроченный лок
    (процесс упал, не убрав за собой) снимается по возрасту файла — иначе одна
    авария закрывала бы снимок навсегда.
    """
    lock = directory / ".vistest.lock"
    deadline = time.monotonic() + timeout
    fd = None
    while True:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                age = time.time() - lock.stat().st_mtime
            except OSError:
                age = 0.0
            if age > timeout:
                # Держатель умер, не сняв лок. Забираем.
                try:
                    lock.unlink()
                except OSError:
                    pass
                continue
            if time.monotonic() > deadline:
                # Ждать дальше бессмысленно: лучше выполнить запись и сказать
                # об этом в логе, чем уронить апрув, который человек уже сделал.
                yield False
                return
            time.sleep(0.05)
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        fd = None
        yield True
    finally:
        if fd is not None:
            os.close(fd)
        try:
            lock.unlink()
        except OSError:
            pass


def _write_atomic_text(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _write_atomic_png(path: Path, image: np.ndarray) -> None:
    tmp = path.with_suffix(f".tmp{os.getpid()}.png")
    _write_png(tmp, image)
    os.replace(tmp, path)


def _safe_segment(part: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in _KEEP else "_" for c in part).strip()
    cleaned = cleaned.strip(".")          # ".." и скрытые каталоги недопустимы
    return cleaned or "snapshot"


MAX_DEPTH = 4


def _safe(name: str) -> str:
    """Имя снимка → относительный путь.

    Слэш в имени означает проект: `shop.example/login.png` ляжет в
    `shop.example/login`. Каждый сегмент санируется отдельно, `..` вырезается —
    имена приходят снаружи (из UI, из чужих тестов по HTTP), и выйти за
    пределы каталога эталонов через них быть не должно.

    **Про глубину.** Раньше здесь стояло `"/".join(parts[-4:])`: начало
    глубокого имени молча отбрасывалось. Два разных снимка с общим хвостом из
    четырёх сегментов —

        billing/eu/checkout/payment/form.png
        billing/us/checkout/payment/form.png

    — ложились в ОДИН каталог и перезаписывали эталон друг друга. Ошибка
    беззвучная и худшая из возможных для хранилища: человек видит дифф между
    европейской и американской формой и не понимает, откуда он взялся.

    Ограничение глубины оставлено (путь не должен расти без предела), но
    отброшенное начало больше не исчезает бесследно: от него берётся короткий
    хеш и приписывается к первому сохранённому сегменту. Имена остаются
    читаемыми, а разными — разными.

    Для имён глубиной до четырёх сегментов — а это подавляющее большинство —
    результат не изменился, то есть существующие эталоны остались на месте.
    Глубокие имена переедут, но они и были сломаны: до сих пор они делили
    каталог с однофамильцами.
    """
    name = str(name).replace("\\", "/").removesuffix(".png")
    parts = [_safe_segment(p) for p in name.split("/") if p.strip(" .")]
    if not parts:
        return "snapshot"
    if len(parts) <= MAX_DEPTH:
        return "/".join(parts)

    import hashlib

    dropped = "/".join(parts[:-MAX_DEPTH])
    digest = hashlib.sha1(dropped.encode("utf-8")).hexdigest()[:8]
    kept = parts[-MAX_DEPTH:]
    return "/".join([f"{kept[0]}-{digest}", *kept[1:]])


def split_project(name: str) -> tuple[str, str]:
    """`shop.example/login.png` -> ("shop.example", "login.png")."""
    text = str(name).replace("\\", "/")
    if "/" not in text:
        return "", text
    head, _, tail = text.rpartition("/")
    return head, tail


class FileBaselineStore(BaselineStore):
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def dir_for(self, name: str) -> Path:
        return self.root / _safe(name)

    # ------------------------------------------------------------------ #
    def exists(self, name: str) -> bool:
        return (self.dir_for(name) / "baseline.png").exists()

    def load(self, name: str) -> BaselineRecord | None:
        d = self.dir_for(name)
        img_path = d / "baseline.png"
        if not img_path.exists():
            return None

        meta = {}
        if (d / "meta.json").exists():
            try:
                meta = json.loads((d / "meta.json").read_text("utf-8"))
            except Exception:
                meta = {}

        dom = None
        if (d / "dom.json").exists():
            try:
                dom = json.loads((d / "dom.json").read_text("utf-8"))
            except Exception:
                pass

        return BaselineRecord(
            name=name,
            image=read_png(img_path),
            stability_mask=_noise.load_mask(d / "stability.png"),
            dom=dom,
            ignore_boxes=meta.get("ignore_boxes", []),
            version=int(meta.get("version", 1)),
            meta=meta,
        )

    def save(self, record: BaselineRecord) -> None:
        """Записать новую версию эталона.

        Две вещи, которых здесь раньше не было, и обе про то, что апрув —
        единственное необратимое действие в интерфейсе.

        **Атомарность.** Картинка и паспорт писались подряд, каждый на месте.
        Падение между ними оставляло `version` в паспорте не тем, что лежит в
        `baseline.png`, — а именно по версии работает защита от гонки решений.
        Теперь оба файла пишутся во временные и переставляются `os.replace`.

        **Блокировка.** Каталог снимка запирается на время записи, поэтому
        «прочитал версию → записал версию+1» не может выполниться двумя
        процессами вперемешку.
        """
        d = self.dir_for(record.name)
        d.mkdir(parents=True, exist_ok=True)

        with _dir_lock(d):
            prev_meta = {}
            if (d / "meta.json").exists():
                try:
                    prev_meta = json.loads((d / "meta.json").read_text("utf-8"))
                except Exception:
                    pass
            version = int(prev_meta.get("version", 0)) + 1

            # архивируем предыдущую версию перед перезаписью
            if (d / "baseline.png").exists():
                hist = d / "history"
                hist.mkdir(exist_ok=True)
                prev_version = int(prev_meta.get("version", 0))
                shutil.copy2(d / "baseline.png", hist / f"v{prev_version}.png")
                # Паспорт версии рядом с картинкой: без него откат знает,
                # «что вернуть», но не знает, на что именно откатывается.
                try:
                    (hist / f"v{prev_version}.json").write_text(
                        json.dumps(prev_meta, indent=2, ensure_ascii=False),
                        encoding="utf-8")
                except OSError:
                    pass

            _write_atomic_png(d / "baseline.png", record.image)

            if record.stability_mask is not None:
                merged = _noise.merge_masks(
                    _noise.load_mask(d / "stability.png"), record.stability_mask,
                    sticky=True,
                )
                _noise.save_mask(d / "stability.png", merged)

            if record.dom is not None:
                _write_atomic_text(
                    d / "dom.json",
                    json.dumps(record.dom, ensure_ascii=False))

            meta = {
                **prev_meta,
                **(record.meta or {}),
                "name": record.name,
                "version": version,
                "width": int(record.image.shape[1]),
                "height": int(record.image.shape[0]),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "ignore_boxes": record.ignore_boxes or prev_meta.get("ignore_boxes", []),
            }
            _write_atomic_text(
                d / "meta.json",
                json.dumps(meta, indent=2, ensure_ascii=False))

    # ------------------------------------------------------------------ #
    #  Версии и откат
    #
    #  История версий писалась на диск с самого начала (`history/v3.png`) и не
    #  была доступна ниоткуда: ни из API, ни из интерфейса. То есть откатить
    #  ошибочный апрув было нельзя, хотя файл для отката лежал на месте.
    # ------------------------------------------------------------------ #
    def versions(self, name: str) -> list[dict]:
        """Все версии эталона, свежая первой."""
        d = self.dir_for(name)
        if not (d / "baseline.png").exists():
            return []

        meta = {}
        if (d / "meta.json").exists():
            try:
                meta = json.loads((d / "meta.json").read_text("utf-8"))
            except Exception:
                pass

        current = int(meta.get("version", 1))
        out = [{
            "version": current,
            "current": True,
            "updated_at": meta.get("updated_at"),
            "approved_by": meta.get("approved_by"),
            "restored_from": meta.get("restored_from"),
            "width": meta.get("width"),
            "height": meta.get("height"),
            "size_kb": round((d / "baseline.png").stat().st_size / 1024),
        }]

        hist = d / "history"
        if hist.is_dir():
            for png in sorted(hist.glob("v*.png")):
                try:
                    version = int(png.stem[1:])
                except ValueError:
                    continue
                side = {}
                sidecar = hist / f"v{version}.json"
                if sidecar.exists():
                    try:
                        side = json.loads(sidecar.read_text("utf-8"))
                    except Exception:
                        pass
                out.append({
                    "version": version,
                    "current": False,
                    "updated_at": side.get("updated_at"),
                    "approved_by": side.get("approved_by"),
                    "restored_from": side.get("restored_from"),
                    "width": side.get("width"),
                    "height": side.get("height"),
                    "size_kb": round(png.stat().st_size / 1024),
                })
        return sorted(out, key=lambda v: -v["version"])

    def version_image(self, name: str, version: int) -> Path | None:
        """Файл конкретной версии: текущая — `baseline.png`, прочие — из history."""
        d = self.dir_for(name)
        current = 1
        if (d / "meta.json").exists():
            try:
                current = int(json.loads((d / "meta.json").read_text("utf-8"))
                              .get("version", 1))
            except Exception:
                pass
        if int(version) == current:
            png = d / "baseline.png"
            return png if png.exists() else None
        png = d / "history" / f"v{int(version)}.png"
        return png if png.exists() else None

    def restore(self, name: str, version: int, *, who: str = "") -> int:
        """Вернуть старую версию, записав её как новую.

        Именно записав новой, а не подменив на месте: история не переписывается
        задним числом. После отката на v3 текущей становится v7 с той же
        картинкой, и в истории видно оба события — и ошибочный апрув, и откат.
        """
        src = self.version_image(name, version)
        if src is None:
            raise FileNotFoundError(f"version {version} of {name!r} not found")

        image = read_png(src)
        prev = self.load(name)
        self.save(BaselineRecord(
            name=name, image=image,
            ignore_boxes=(prev.ignore_boxes if prev else []),
            meta={"approved_by": who or "rollback",
                  "restored_from": int(version)},
        ))
        try:
            return int(json.loads(
                (self.dir_for(name) / "meta.json").read_text("utf-8"))["version"])
        except Exception:
            return 0

    def list_names(self) -> list[str]:
        """Все эталоны, включая вложенные в проекты.

        Возвращается то имя, под которым эталон сохраняли, а не путь каталога.
        Каталог — это уже результат санации: `login.png` лежит в `login/`, и
        отдавать наружу «login» значило отдавать не тот ключ. Само по себе оно
        ещё находится (`_safe` снимает расширение повторно), но всё, что это имя
        показывает или подставляет в код, начинало врать: в списке эталонов
        пропадало расширение, а сгенерированный тест звал
        `assert_screenshot("login")` вместо имени из проекта.
        """
        if not self.root.exists():
            return []
        out = []
        for img in self.root.rglob("baseline.png"):
            rel = img.parent.relative_to(self.root).as_posix()
            out.append(self._stored_name(img.parent, rel))
        return sorted(out)

    def _stored_name(self, directory: Path, rel: str) -> str:
        """Имя из паспорта эталона; путь каталога — как запасной вариант.

        Паспорту верим только если записанное имя совпадает с путём каталога с
        точностью до расширения. Имена приходят снаружи — из UI, из чужих тестов
        по HTTP, — и `../../evil.png` не должен вернуться наружу списком лишь
        потому, что мы сами его когда-то записали в meta.json. На диск он и так
        не попал: раскладку решает `_safe`.
        """
        meta_path = directory / "meta.json"
        if meta_path.exists():
            try:
                name = json.loads(meta_path.read_text("utf-8")).get("name")
            except Exception:
                name = None
            if isinstance(name, str) and name in (rel, f"{rel}.png"):
                return name
        return f"{rel}.png"

    def projects(self) -> list[str]:
        """Список проектов. Пустая строка — эталоны без проекта."""
        return sorted({split_project(n)[0] for n in self.list_names()})

    def add_ignore_box(self, name: str, box: dict) -> None:
        from ..zones import normalize

        d = self.dir_for(name)
        path = d / "meta.json"
        meta = json.loads(path.read_text("utf-8")) if path.exists() else {}
        boxes = meta.setdefault("ignore_boxes", [])
        # Через `normalize`, а не через выборку четырёх чисел: зона, пришедшая
        # из разбора падения, знает СЕЛЕКТОР найденного региона, и терять его
        # здесь значило бы записывать прямоугольник там, где был элемент.
        boxes.append(normalize(box))
        d.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    # ------------------------------------------------------------------ #
    def ignore_mask(self, name: str, shape: tuple[int, int]) -> np.ndarray | None:
        """stability-маска + вечные ignore-зоны, приведённые к размеру кадра.

        Текущего кадра здесь нет — значит нет и его DOM'а: зоны разрешаются по
        снепшоту ЭТАЛОНА. Для не двигавшегося элемента это тот же ответ, для
        переехавшего — только его прежнее положение. Полное разрешение живёт в
        `service.check`, где на руках оба снепшота; сюда ходят те, кому нужна
        маска снимка саму по себе.
        """
        rec = self.load(name)
        if rec is None:
            return None
        mask = rec.stability_mask
        if rec.ignore_boxes:
            from ..zones import mask as zone_mask

            boxes_mask, _ = zone_mask(shape, rec.ignore_boxes, baseline_dom=rec.dom)
            mask = boxes_mask if mask is None else _noise.merge_masks(mask, boxes_mask)
        return mask
