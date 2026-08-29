# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Пороги вердикта, которые можно поменять из интерфейса.

Порог падения жил ровно в одном месте — `vistest.yaml`, — а в настройках стоял
ползунок, который не отправлял никуда ни одного запроса. Он показывал число,
двигался, и на этом всё заканчивалось: значение умирало при следующей
перерисовке экрана, а начальное (35) даже не совпадало с настоящим дефолтом
(25). Это хуже, чем отсутствие настройки: отсутствующей функцией человек не
пользуется, а этой пользовался и уходил уверенный, что настроил.

Здесь — то, чего не хватало, чтобы ползунок стал правдой.

**Что можно менять.** Только политику вердикта: при какой severity снимок
считается упавшим и какая доля изменённой площади достаточна сама по себе. Всё
остальное в `DiffConfig` — параметры движка (пороги ΔE00 и SSIM, морфология,
поиск сдвигов); их подбирают один раз под задачу и держат в конфиге рядом с
кодом, а не крутят из веб-интерфейса между прогонами.

**Где живут значения.** В таблице `setting`, а не в `vistest.yaml`. Конфиг
лежит в git и описывает проект; сервис, переписывающий чужой версионируемый
файл, создаёт конфликты на ровном месте и ломается при нескольких репликах на
одном томе. Значение порога принадлежит инсталляции — там и хранится.

**Кто кого перекрывает.** По возрастанию силы:

    дефолт/пресет  →  vistest.yaml  →  глобальный override  →  override проекта

Возвращая эффективное значение, мы всегда говорим и его источник: «35» без
ответа на вопрос «почему 35» — ровно та же непроверяемая обещалка, что и
«принять как эталон» без указания, какой именно эталон.
"""

from __future__ import annotations

from dataclasses import replace

from ..config import VisTestConfig

GLOBAL = "global"
PROJECT = "project"


class ThresholdError(ValueError):
    """Значение не проходит проверку — с текстом, который можно показать."""


# name -> (низ, верх, единица, зачем)
EDITABLE: dict[str, tuple[float, float, str, str]] = {
    "fail_severity": (
        0.0, 100.0, "",
        "Severity at which a snapshot is considered failed. 0 — any visible "
        "difference is a failure; 100 — only gross breakage."),
    "max_changed_area_pct": (
        0.0, 100.0, "%",
        "Share of the frame that is enough on its own, regardless of severity. "
        "Catches a page that shifted as a whole."),
}


def _rows(db, scope: str, project_key: str = "") -> dict[str, float]:
    out: dict[str, float] = {}
    try:
        rows = db.query(
            "SELECT name, value FROM setting WHERE scope=? AND project_key=?",
            (scope, project_key or ""))
    except Exception:
        # Отсутствие таблицы не должно ронять прогон: пороги просто останутся
        # теми, что в конфиге.
        return out
    for r in rows:
        if r["name"] not in EDITABLE:
            continue
        try:
            out[r["name"]] = float(r["value"])
        except (TypeError, ValueError):
            continue
    return out


def overrides(db, project_key: str | None = None) -> dict[str, float]:
    """Что перекрывает конфиг для этого проекта: глобальное + проектное."""
    merged = dict(_rows(db, GLOBAL))
    if project_key:
        merged.update(_rows(db, PROJECT, project_key))
    return merged


def effective(db, cfg: VisTestConfig | None = None,
              project_key: str | None = None) -> dict:
    """Действующие значения и — обязательно — откуда каждое взялось."""
    cfg = cfg or VisTestConfig.load()
    from_yaml = {name: getattr(cfg.diff, name) for name in EDITABLE}
    glob = _rows(db, GLOBAL)
    proj = _rows(db, PROJECT, project_key) if project_key else {}

    values, sources = {}, {}
    for name in EDITABLE:
        if name in proj:
            values[name], sources[name] = proj[name], "project"
        elif name in glob:
            values[name], sources[name] = glob[name], "global"
        else:
            values[name], sources[name] = from_yaml[name], "config"

    return {
        "project": project_key or "",
        "values": values,
        "sources": sources,
        "config": from_yaml,          # что сказал бы vistest.yaml без переопределений
        "global_overrides": glob,
        "project_overrides": proj,
        "preset": cfg.preset,
        "editable": {
            name: {"min": lo, "max": hi, "unit": unit, "hint": hint}
            for name, (lo, hi, unit, hint) in EDITABLE.items()
        },
    }


def validate(name: str, value) -> float:
    if name not in EDITABLE:
        raise ThresholdError(
            f"{name!r} is not editable from the interface. "
            f"Editable: {', '.join(sorted(EDITABLE))}.")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ThresholdError(f"{name}: {value!r} is not a number") from None
    lo, hi, unit, _ = EDITABLE[name]
    if not lo <= number <= hi:
        raise ThresholdError(
            f"{name}: {number:g}{unit} is outside {lo:g}{unit}…{hi:g}{unit}")
    return number


def put(db, values: dict, *, project_key: str | None = None,
        who: str = "") -> dict:
    """Записать или снять переопределения.

    `None` в значении снимает переопределение — это не то же самое, что «ноль».
    Ноль здесь осмысленное значение («падать на любом видимом различии»), и без
    отдельного способа сказать «верни как в конфиге» вернуться было бы нельзя.
    """
    scope = PROJECT if project_key else GLOBAL
    key = project_key or ""
    written, cleared = {}, []

    for name, raw in (values or {}).items():
        if raw is None:
            db.execute(
                "DELETE FROM setting WHERE scope=? AND project_key=? AND name=?",
                (scope, key, name))
            cleared.append(name)
            continue
        number = validate(name, raw)
        db.execute(
            "INSERT INTO setting(scope, project_key, name, value, updated_at,"
            " updated_by) VALUES(?,?,?,?,datetime('now'),?)"
            " ON CONFLICT(scope, project_key, name) DO UPDATE SET"
            " value=excluded.value, updated_at=excluded.updated_at,"
            " updated_by=excluded.updated_by",
            (scope, key, name, repr(number), who))
        written[name] = number

    return {"written": written, "cleared": cleared, "scope": scope,
            "project": key}


def apply(cfg: VisTestConfig, db, project_key: str | None = None
          ) -> VisTestConfig:
    """Конфиг с наложенными переопределениями — для прогонов внутри сервиса.

    Возвращается копия: `VisTestConfig.load()` кешируется в модулях и делится
    между запросами, а править общий объект ради одного прогона — это разослать
    чужой порог всем остальным.
    """
    patch = overrides(db, project_key)
    if not patch:
        return cfg
    return replace(cfg, diff=cfg.diff.merged(**patch))


def env_for(db, project_key: str | None = None) -> dict[str, str]:
    """Переопределения для ЧУЖОГО процесса.

    Прогон подключённого проекта — это отдельный pytest, который читает свой
    `vistest.yaml` и про нашу базу ничего не знает. Без этого порог, выставленный
    в интерфейсе, действовал бы на прогоны сервиса и молча не действовал на
    прогоны проектов — расхождение, которое ищут днями.
    """
    patch = overrides(db, project_key)
    return {f"VISTEST_{name.upper()}": repr(value)
            for name, value in patch.items()}
