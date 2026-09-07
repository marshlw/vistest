# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Резервная копия и восстановление.

Ради чего это существует. Эталон — это ответ на вопрос «как должно
выглядеть», и в инсталляции, работающей полгода, он накоплен решениями
десятков людей: каждое утверждение кто-то посмотрел глазами. Потерять их
означает не «восстановить из git», а «начать смотреть заново», и никакой
пересъёмкой это не лечится: пересъёмка зафиксирует то, что есть сейчас,
включая тот самый регресс, который ищут.

До этого способа сделать копию не было вовсе — только «остановите сервис и
скопируйте том». На него нельзя сослаться в инструкции: он требует доступа к
машине, простоя и знания, что именно копировать (а копировать `vistest.db` на
ходу — верный способ получить файл, который откроется, но не откроется).

Что входит в копию:

* `vistest.db` — через `VACUUM INTO`. Это единственный правильный способ снять
  sqlite под нагрузкой: движок сам делает согласованный снимок, не мешая тем,
  кто пишет, и на выходе получается уже сжатый файл без мусора. Копирование
  файла на ходу даёт битую базу в WAL-режиме — не всегда, а иногда, что хуже.
* эталоны целиком — картинки, паспорта, история версий, маски;
* `projects.yaml` — какие наборы подключены.

Чего в копии НЕТ, и это решение, а не упущение:

* `secrets.env` — пароли от стендов. Копия эталонов уезжает в файловое
  хранилище, в тикет, на флешку; секреты не должны ездить вместе с ней молча.
  Кому нужно — тот добавит `--with-secrets` и будет знать, что везёт.
* артефакты прогонов. Это гигабайты картинок, которые пересоздаются следующим
  прогоном. Копия нужна, чтобы вернуться к работе, а не чтобы вернуть каждый
  дифф двухлетней давности.

Формат — обычный tar.gz с манифестом внутри. Не свой: копию должно быть
возможно распаковать руками, без VisTest, в момент, когда VisTest как раз и не
запускается.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import tarfile
import tempfile
import time
from pathlib import Path

MANIFEST = "vistest-backup.json"


def _root(root: str | Path | None = None) -> Path:
    import os

    return Path(root or os.getenv("VISTEST_ROOT", ".vistest")).resolve()


def _snapshot_db(db_path: Path, dest: Path) -> bool:
    """Согласованный снимок базы. False — базы нет."""
    if not db_path.exists():
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    conn = sqlite3.connect(str(db_path))
    try:
        # Параметр в VACUUM INTO подставить нельзя, поэтому экранируем сами.
        # Путь наш собственный, из аргумента командной строки, а не из сети.
        conn.execute(f"VACUUM INTO '{str(dest)}'".replace("\\\\", "\\\\\\\\"))
    finally:
        conn.close()
    return True


def create(archive: str | Path, *, root: str | Path | None = None,
           with_secrets: bool = False, baselines_dir: str = "baselines") -> dict:
    """Сделать копию. Возвращает манифест."""
    src = _root(root)
    if not src.exists():
        raise FileNotFoundError(f"There is no data volume at {src}")

    archive = Path(archive).resolve()
    archive.parent.mkdir(parents=True, exist_ok=True)

    import vistest

    manifest = {
        "tool": "vistest",
        "version": getattr(vistest, "__version__", "?"),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "root": str(src),
        "contains": [],
        "schema_version": None,
    }

    with tempfile.TemporaryDirectory() as tmp:
        staged = Path(tmp)
        db_copy = staged / "vistest.db"
        if _snapshot_db(src / "vistest.db", db_copy):
            manifest["contains"].append("vistest.db")
            try:
                from .api.db import Database

                manifest["schema_version"] = Database(db_copy).schema_version()
            except Exception:
                pass

        with tarfile.open(archive, "w:gz") as tar:
            if db_copy.exists():
                tar.add(db_copy, arcname="vistest.db")

            baselines = src / baselines_dir
            if baselines.exists():
                tar.add(baselines, arcname="baselines")
                manifest["contains"].append("baselines")

            projects = src / "projects.yaml"
            if projects.exists():
                tar.add(projects, arcname="projects.yaml")
                manifest["contains"].append("projects.yaml")

            secrets = src / "secrets.env"
            if with_secrets and secrets.exists():
                tar.add(secrets, arcname="secrets.env")
                manifest["contains"].append("secrets.env")

            info_path = staged / MANIFEST
            info_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False),
                                 encoding="utf-8")
            tar.add(info_path, arcname=MANIFEST)

    manifest["archive"] = str(archive)
    manifest["size_kb"] = round(archive.stat().st_size / 1024)
    return manifest


def inspect(archive: str | Path) -> dict:
    """Что внутри копии — не распаковывая её."""
    with tarfile.open(Path(archive), "r:gz") as tar:
        member = tar.extractfile(MANIFEST)
        if member is None:
            raise ValueError("This archive has no VisTest manifest")
        return json.loads(member.read().decode("utf-8"))


def _safe_members(tar: tarfile.TarFile, dest: Path):
    """Только то, что ложится внутрь каталога назначения.

    Копию могли передать по почте, а tar умеет пути вида `../../etc`. Проверка
    стоит здесь, а не в вызывающем: восстановление — единственное место, где
    мы распаковываем чужой архив.
    """
    base = dest.resolve()
    for member in tar.getmembers():
        target = (dest / member.name).resolve()
        if target != base and base not in target.parents:
            raise ValueError(f"Unsafe path in the archive: {member.name}")
        if member.issym() or member.islnk():
            raise ValueError(f"Links are not restored: {member.name}")
        yield member


def restore(archive: str | Path, *, root: str | Path | None = None,
            force: bool = False) -> dict:
    """Развернуть копию в каталог данных.

    Отказывается работать поверх непустого каталога без `force`: восстановление
    поверх живой инсталляции — это ровно тот случай, когда «я думал, там пусто»
    стоит дороже всего остального в этом файле.
    """
    dest = _root(root)
    info = inspect(archive)

    if dest.exists() and any(dest.iterdir()) and not force:
        raise FileExistsError(
            f"{dest} is not empty. Restoring over a live installation replaces "
            "the baselines and the database — pass force to say you mean it.")

    # Схема из будущего: см. `api/db.SchemaTooNew`. Развернуть такую копию
    # можно, работать с ней — нет, и сказать об этом надо здесь, а не в момент
    # первого непонятного запроса.
    from .api.db import SCHEMA_VERSION

    found = info.get("schema_version")
    if isinstance(found, int) and found > SCHEMA_VERSION:
        raise RuntimeError(
            f"The backup was made by a newer VisTest (schema {found}, this one "
            f"knows {SCHEMA_VERSION}). Update VisTest before restoring.")

    dest.mkdir(parents=True, exist_ok=True)
    if force:
        for name in ("vistest.db", "vistest.db-wal", "vistest.db-shm"):
            (dest / name).unlink(missing_ok=True)
        if (dest / "baselines").exists():
            shutil.rmtree(dest / "baselines", ignore_errors=True)

    with tarfile.open(Path(archive), "r:gz") as tar:
        members = [m for m in _safe_members(tar, dest) if m.name != MANIFEST]
        tar.extractall(dest, members=members)

    return {**info, "restored_to": str(dest)}
