# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Очередь решений: не «что красное», а «на что ответить».

Экран прогона отвечает на вопрос «что сломалось в этом прогоне». Человек,
который открывает VisTest утром, задаёт другой: **что от меня требуется**. И
это не список из двадцати трёх картинок.

Разница дорогая, и она уже описана в `cluster.py`: двадцать снимков покраснели
от одной правки line-height, человек разбирает их по одному, к пятому перестаёт
смотреть, к десятому принимает не глядя. Кластеризация внутри прогона это
лечила наполовину — надо было сначала найти нужный прогон, открыть его, увидеть
группы. Если падение приехало три прогона назад и с тех пор повторяется, оно не
видно нигде: в последнем прогоне оно есть, но выглядит как «ещё двадцать
красных».

Здесь очередь строится **по проекту, а не по прогону**, и из неё выкинуто всё,
на что уже ответили.

Три решения, каждое из которых можно было принять неправильно.

**Снимок берётся один раз — по последнему сравнению.** Одно и то же падение,
повторившееся в пяти прогонах, это один вопрос, а не пять. Считать по
сравнениям значило бы показать «115 решений ждут вас» там, где их двадцать три,
и сделать счётчик бесполезным в тот же день.

**Каждый снимок попадает ровно в одну причину.** Регион снимка может подойти
двум кластерам сразу; отдать снимок обоим значит получить сумму причин больше
числа снимков — и человека, который ответил на всё, а счётчик не обнулился.
Снимок достаётся первой причине в порядке полезности, остальные его не видят.

**Причин без объяснения не бывает.** Снимок, для которого общей причины не
нашлось, не исчезает: он становится причиной из одного снимка. Тихо потерять
неразобранное падение — ровно та поломка, ради которой вся эта очередь и
написана.

Отдельно `blocked` — снимки с вердиктом `error`. Их не с чем сравнивать:
картинка не снята. Решать там нечего, и в очередь решений они не идут, но и
молчать о них нельзя: «ноль красных» при пяти неснятых страницах — это не
хорошая новость, а отсутствие проверки.
"""

from __future__ import annotations

import json

from ..cluster import cluster_regions

# Причина показывается, когда её объясняет хотя бы столько снимков. Единица
# означала бы «показывать всё как есть» — это обычный список, он уже есть.
MIN_CAUSE = 2

# Класс изменения → короткая метка на карточке. Метки, а не полные слова:
# в макете это узкий столбец, а «anti-aliasing» в него не помещается.
KIND_TAG = {
    "moved": "SHIFT",
    "resized": "SIZE",
    "color": "COLOR",
    "text": "TEXT",
    "added": "ADDED",
    "removed": "GONE",
    "content": "CONTENT",
}


# --------------------------------------------------------------------------- #
#  Что вообще ждёт ответа
# --------------------------------------------------------------------------- #
def _latest_by_snapshot(db, project: str, verdict: str) -> list[dict]:
    """Последнее сравнение каждого снимка — и только если оно не разобрано.

    Условие на «последнее» стоит внутри подзапроса, а не рядом с ним: снимок,
    который вчера падал и сегодня зелёный, ответа не требует, и попадать в
    очередь по вчерашнему сравнению не должен.
    """
    return db.query(
        "SELECT c.id, c.snapshot_id, c.verdict, c.review, c.max_severity,"
        "       c.changed_area_pct, c.ssim, c.meta, c.created_at,"
        "       s.name AS snapshot_name, s.platform, s.browser,"
        "       r.id AS run_id, r.run_key, r.branch, r.baseline_scope,"
        "       r.project_key"
        "  FROM comparison c"
        "  JOIN snapshot s ON s.id = c.snapshot_id"
        "  JOIN run r ON r.id = c.run_id"
        "  JOIN project p ON p.id = r.project_id"
        " WHERE p.name = ?"
        "   AND c.id = (SELECT c2.id FROM comparison c2"
        "                WHERE c2.snapshot_id = c.snapshot_id"
        "                ORDER BY c2.created_at DESC, c2.id DESC LIMIT 1)"
        "   AND c.verdict = ? AND c.review IS NULL"
        " ORDER BY c.max_severity DESC, s.name", (project, verdict))


def _regions(db, comparison_id: int) -> list[dict]:
    return db.query(
        "SELECT * FROM region WHERE comparison_id=? ORDER BY severity DESC",
        (comparison_id,))


def _error_text(row: dict) -> str:
    try:
        meta = json.loads(row.get("meta") or "{}")
    except Exception:
        meta = {}
    return str(meta.get("error") or "not captured")


# --------------------------------------------------------------------------- #
#  Раскладка по причинам
# --------------------------------------------------------------------------- #
def _chips(names: list[str], limit: int = 4) -> list[str]:
    """«checkout ×4» — где именно эта причина вылезла.

    Первый сегмент имени снимка это раздел приложения, и он отвечает на
    вопрос, который человек задаёт раньше всех остальных: насколько широко
    разъехалось. Список без него — двадцать три имени, которые никто не читает.
    """
    counts: dict[str, int] = {}
    for name in names:
        head = str(name).split("/")[0] or str(name)
        counts[head] = counts.get(head, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    out = [f"{head} ×{n}" for head, n in ordered[:limit]]
    if len(ordered) > limit:
        out.append(f"+{len(ordered) - limit} more")
    return out


def _risk(kind: str, count: int, branch: str) -> str:
    where = f" on branch {branch}" if branch and branch != "main" else ""
    if kind == "removed":
        return ("Disappearances are the most expensive class — make sure it is "
                "intentional before accepting.")
    if count == 1:
        return f"Accepting rewrites one baseline{where}."
    return f"Accepting rewrites {count} baselines{where}."


def _cause(kind: str, items: list[dict], *, description: str = "",
           advice: str = "", common: str = "", selectors: list[str] | None = None,
           index: int = 0) -> dict:
    names = [i["snapshot_name"] for i in items]
    branch = (items[0].get("branch") or "") if items else ""
    severity = max((i.get("max_severity") or 0) for i in items) if items else 0
    return {
        "id": f"c{index}",
        "kind": kind,
        "tag": KIND_TAG.get(kind, kind.upper()[:7]),
        "severity": round(float(severity), 1),
        "count": len(items),
        "title": description or f"{len(names)} snapshots changed",
        "selector": common or (selectors or [""])[0] if (common or selectors) else "",
        "why": advice,
        "chips": _chips(names),
        "risk": _risk(kind, len(items), branch),
        "snapshots": names,
        "comparisons": [i["id"] for i in items],
        # Куда вести по «Inspect» — на самое тяжёлое сравнение группы: если
        # человек посмотрит одно, пусть это будет то, где видно лучше всего.
        "open": items[0]["id"] if items else None,
    }


def causes(db, project: str) -> dict:
    """Очередь причин по всем непросмотренным падениям проекта."""
    pending = _latest_by_snapshot(db, project, "fail")
    by_name: dict[str, dict] = {}
    items: list[dict] = []
    for row in pending:
        row = dict(row)
        row["regions"] = _regions(db, row["id"])
        row["name"] = row["snapshot_name"]
        by_name[row["snapshot_name"]] = row
        items.append(row)

    out: list[dict] = []
    claimed: set[str] = set()
    for index, cluster in enumerate(cluster_regions(items, min_size=MIN_CAUSE)):
        mine = [by_name[n] for n in cluster.snapshots
                if n in by_name and n not in claimed]
        if len(mine) < MIN_CAUSE:
            # После того как снимки разобрали соседние причины, от группы могло
            # остаться меньше, чем нужно, чтобы называться группой. Оставлять
            # её значило бы показать «причину» из одного снимка рядом с честной
            # причиной из одиннадцати и уравнять их в глазах человека.
            continue
        claimed.update(n["snapshot_name"] for n in mine)
        out.append(_cause(cluster.kind, mine,
                          description=cluster.describe(),
                          advice=cluster.advice(),
                          common=cluster.common_ancestor(),
                          selectors=cluster.selectors(),
                          index=index))

    # Всё, что не объяснилось общей причиной, — отдельными вопросами. Не
    # «прочее» одной строкой: за такой строкой падение перестают видеть.
    for offset, row in enumerate(items):
        if row["snapshot_name"] in claimed:
            continue
        top = (row["regions"] or [{}])[0]
        kind = str(top.get("kind") or "content")
        out.append(_cause(kind, [row],
                          description=row["snapshot_name"],
                          advice="On its own — no other snapshot changed the "
                                 "same way.",
                          common=str(top.get("selector") or ""),
                          index=1000 + offset))

    out.sort(key=lambda c: (-c["count"], -c["severity"]))
    for position, cause in enumerate(out, 1):
        cause["position"] = position
    return {"causes": out, "pending": len(items)}


# --------------------------------------------------------------------------- #
#  Сводка экрана
# --------------------------------------------------------------------------- #
def _latest_run(db, project: str) -> dict | None:
    return db.one(
        "SELECT r.* FROM run r JOIN project p ON p.id = r.project_id"
        " WHERE p.name = ? ORDER BY r.started_at DESC, r.id DESC LIMIT 1",
        (project,))


def _snapshot_totals(db, project: str) -> dict:
    """Сколько снимков у проекта и сколько из них сейчас зелёные.

    Считается по последнему сравнению каждого снимка — по тому же правилу, что
    и очередь. Два разных правила счёта на одном экране дали бы «117 из 142»
    рядом с двадцатью тремя красными и двумя неснятыми, где сумма не сходится,
    а объяснить это нечем.
    """
    row = db.one(
        "SELECT COUNT(*) AS total,"
        "       SUM(last.verdict IN ('pass','new_baseline')) AS clean"
        "  FROM (SELECT c.snapshot_id, c.verdict FROM comparison c"
        "          JOIN snapshot s ON s.id = c.snapshot_id"
        "          JOIN run r ON r.id = c.run_id"
        "          JOIN project p ON p.id = r.project_id"
        "         WHERE p.name = ?"
        "           AND c.id = (SELECT c2.id FROM comparison c2"
        "                        WHERE c2.snapshot_id = c.snapshot_id"
        "                        ORDER BY c2.created_at DESC, c2.id DESC"
        "                        LIMIT 1)) AS last", (project,)) or {}
    return {"total": row.get("total") or 0, "clean": row.get("clean") or 0}


def blocked(db, project: str) -> list[dict]:
    rows = _latest_by_snapshot(db, project, "error")
    return [{
        "comparison_id": r["id"],
        "name": r["snapshot_name"],
        "platform": r.get("platform") or "",
        "error": _error_text(r),
        "run_id": r.get("run_id"),
        "project_key": r.get("project_key") or "",
    } for r in rows]


def queue(db, project: str) -> dict:
    """Всё, что нужно экрану «Decisions», одним запросом.

    Одним — потому что три отдельных запроса неизбежно разъезжаются: между
    ними проходит прогон, и человек видит «3 решения» над списком из двух.
    """
    found = causes(db, project)
    stuck = blocked(db, project)
    totals = _snapshot_totals(db, project)
    run = _latest_run(db, project)

    return {
        "project": project,
        "counts": {
            "causes": len(found["causes"]),
            "snapshots": found["pending"],
            "blocked": len(stuck),
            "clean": totals["clean"],
            "total": totals["total"],
        },
        "causes": found["causes"],
        "blocked": stuck,
        "run": {
            "id": run["id"],
            "key": run.get("run_key") or f"#{run['id']}",
            "branch": run.get("branch") or "",
            "git_sha": run.get("git_sha") or "",
            "platform": run.get("platform") or "",
            "started_at": run.get("started_at"),
            "finished_at": run.get("finished_at"),
            "total": run.get("total") or 0,
        } if run else None,
    }
