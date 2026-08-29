# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Что изменилось между двумя прогонами.

Зачем. Прогон отвечает на вопрос «что красное сейчас». Вопрос, который на
самом деле задают перед мержем, другой: **что изменил именно этот код**. Их
путают, и путаница дорогая: в ветке двадцать падений, из них восемнадцать
были красными и на мастере — они не про эту ветку, но человек разбирает все
двадцать, устаёт на пятом и принимает остальные не глядя. Ровно те два, ради
которых всё и затевалось, уезжают в мастер вместе с восемнадцатью чужими.

До сих пор это делалось глазами: две вкладки, два списка, поиск глазами по
именам. На двадцати снимках это работает, на двухстах — нет, и не потому что
долго, а потому что человек пропускает.

Здесь два прогона сводятся по имени снимка и раскладываются по тому, **как
изменилось состояние**:

* `broke` — было зелёным, стало красным. Это и есть ответ на вопрос.
* `gone` — было, и больше не проверяется. Самая тихая категория из всех:
  снимок, который перестали снимать, не появится ни в одном списке падений.
  Отсутствие красного читается как «починили», а это может быть удалённый
  тест, переименованный снимок или упавшая до съёмки фикстура.
* `still_failing` — красное в обоих, с разницей в severity: стало хуже или
  осталось как было. Разбирать это в ветке не надо, но и молчать нельзя —
  иначе «ветка чистая» будет означать «ветка не хуже сломанного мастера».
* `fixed` — было красным, стало зелёным.
* `added` — снимка не было, появился.
* `same` — совпало; только счётчик, в список не идёт.

Чего здесь намеренно нет — сравнения картинок между прогонами. Соблазн
очевидный: показать «дифф диффов». Но пиксельная разница между двумя
скриншотами разных прогонов складывается из настоящей разницы и из шума
стенда, и разделить их без эталона нечем — то есть получилась бы картинка,
которая выглядит как ответ, а ответом не является. Сравниваются вердикты,
то есть решения, уже принятые движком против эталона.

Отдельно про честность выводов. Два прогона можно сравнивать не всегда:
если они мерялись против **разных наборов эталонов** или на разных
платформах, то «сломалось» может означать «поменялась точка отсчёта», а не
«поменялся код». Молча выдать в таком случае красивый список — значит
соврать убедительно, поэтому такие случаи возвращаются в `warnings` и
показываются над таблицей.
"""

from __future__ import annotations

# Порядок важен: он же — порядок вывода. Сверху то, ради чего человек сюда
# пришёл, снизу то, что приятно узнать, но действий не требует.
GROUPS = ("broke", "gone", "still_failing", "added", "fixed")

# Насколько должна измениться severity, чтобы называть это «стало хуже».
# Полбалла — это ниже шума любого живого стенда; смысл порога не в точности,
# а в том, чтобы не подсвечивать «хуже» там, где разница в третьем знаке.
WORSE_BY = 0.5


def _key(row: dict) -> tuple:
    """Чем один и тот же снимок опознаётся в двух прогонах.

    Не `snapshot_id`: он привязан к проекту, и сравнение прогонов двух
    проектов (наш набор против набора подключённого) развалилось бы на
    «всё исчезло, всё появилось». Имя со связкой платформа+браузер — это то,
    что человек и считает одним снимком.
    """
    return (row.get("snapshot_name") or "",
            row.get("platform") or "",
            row.get("browser") or "")


def _side(row: dict | None) -> dict | None:
    if row is None:
        return None
    return {
        "comparison_id": row.get("id"),
        "verdict": row.get("verdict"),
        "review": row.get("review"),
        "severity": row.get("max_severity"),
        "changed_area_pct": row.get("changed_area_pct"),
        "ssim": row.get("ssim"),
    }


def _red(row: dict | None) -> bool:
    """Красное — это `fail` и `error`.

    Ошибка съёмки не «нейтральна»: снимок не проверен, и если в прошлый раз
    он проверялся и был зелёным, то это ухудшение, а не отсутствие данных.
    Сложить их в «ни то ни сё» значило бы спрятать сломанную фикстуру —
    самый частый способ незаметно перестать что-либо проверять.
    """
    return bool(row) and row.get("verdict") in ("fail", "error")


def _index(comps: list[dict]) -> tuple[dict, list[str]]:
    """Снимки прогона по ключу — плюс имена, встретившиеся дважды.

    Дубль в одном прогоне возможен: набор может снять одно имя из двух
    тестов. Берём последнее сравнение (у него больший id — значит, оно
    записано позже и описывает итоговое состояние), а сам факт дубля
    возвращаем наверх: молча выбрать одно из двух и не сказать об этом —
    это ровно тот случай, когда человек потом не понимает, почему в списке
    «поехало» нет снимка, который он своими глазами видел красным.
    """
    out: dict = {}
    dupes: list[str] = []
    for row in sorted(comps, key=lambda r: r.get("id") or 0):
        k = _key(row)
        if k in out:
            dupes.append(k[0])
        out[k] = row
    return out, sorted(set(dupes))


def _warnings(base: dict, head: dict, dupes: list[str]) -> list[str]:
    out: list[str] = []

    base_scope = (base.get("baseline_scope") or "global")
    head_scope = (head.get("baseline_scope") or "global")
    if base_scope != head_scope:
        out.append(
            f"The runs were compared against different baseline sets "
            f"({base_scope} and {head_scope}). A snapshot listed as broken "
            f"may mean the reference changed, not the code.")
    elif (base.get("baseline_dir") or "") != (head.get("baseline_dir") or ""):
        out.append(
            "The runs were compared against different baseline directories. "
            "The same set on paper is not the same files on disk.")

    if (base.get("platform") or "") != (head.get("platform") or ""):
        out.append(
            f"Different platforms: {base.get('platform') or '—'} and "
            f"{head.get('platform') or '—'}. Rendering differs between them "
            f"on its own, without any change in the code.")

    if base.get("project_id") != head.get("project_id"):
        out.append("The runs belong to different projects — snapshots are "
                   "matched by name only.")

    started_base = base.get("started_at") or ""
    started_head = head.get("started_at") or ""
    if started_base and started_head and started_base > started_head:
        out.append("The base run is newer than the one being compared: the "
                   "list reads backwards — «broke» means «was fixed later».")

    for name in dupes:
        out.append(f"«{name}» appears more than once in a run; the last "
                   f"comparison is the one used.")
    return out


def diff(base: dict, base_comps: list[dict],
         head: dict, head_comps: list[dict]) -> dict:
    """Разложить два прогона по тому, как изменилось состояние снимков.

    `base` — с чем сравниваем (мастер, прошлый прогон), `head` — что
    сравниваем (ветка, текущий прогон). Направление именно такое и другим
    быть не может: «сломалось» — это про head.
    """
    left, dl = _index(base_comps)
    right, dr = _index(head_comps)

    groups: dict[str, list[dict]] = {name: [] for name in GROUPS}
    same = 0

    for key in sorted(set(left) | set(right)):
        a, b = left.get(key), right.get(key)
        item = {
            "name": key[0],
            "platform": key[1],
            "browser": key[2],
            "base": _side(a),
            "head": _side(b),
            "delta_severity": None,
        }
        if a is not None and b is not None:
            sa, sb = a.get("max_severity"), b.get("max_severity")
            if sa is not None and sb is not None:
                item["delta_severity"] = round(float(sb) - float(sa), 2)

        if b is None:
            groups["gone"].append(item)
        elif a is None:
            # Новый снимок, который сразу красный, — это не «сломалось»: до
            # него ничего не было, ломаться было нечему. Но и молчать о нём
            # нельзя, поэтому он в `added` со своим вердиктом.
            groups["added"].append(item)
        elif _red(b) and not _red(a):
            groups["broke"].append(item)
        elif _red(a) and not _red(b):
            groups["fixed"].append(item)
        elif _red(a) and _red(b):
            d = item["delta_severity"]
            item["worse"] = bool(d is not None and d >= WORSE_BY)
            groups["still_failing"].append(item)
        else:
            same += 1

    groups["broke"].sort(key=lambda i: -((i["head"] or {}).get("severity") or 0))
    groups["still_failing"].sort(key=lambda i: -(i.get("delta_severity") or 0))
    groups["added"].sort(key=lambda i: (not _red(i["head"]), i["name"]))

    # «Требует внимания» — это сломавшееся, по которому ещё нет решения.
    # Считается отдельно от `broke`, потому что после разбора список не
    # укорачивается (решение не отменяет факта поломки), а вопрос — да, и
    # именно на него смотрит человек, решая, можно ли мержить.
    attention = sum(1 for i in groups["broke"]
                    if not (i["head"] or {}).get("review"))

    counts = {name: len(groups[name]) for name in GROUPS}
    counts["same"] = same
    counts["attention"] = attention

    return {
        "base": _run_view(base),
        "head": _run_view(head),
        "counts": counts,
        "groups": groups,
        "order": list(GROUPS),
        "warnings": _warnings(base, head, dl + dr),
        # Вердикт всего сравнения одной строкой — его же показывает и CLI.
        "clean": counts["broke"] == 0 and counts["gone"] == 0,
    }


def _run_view(run: dict) -> dict:
    return {k: run.get(k) for k in
            ("id", "run_key", "branch", "git_sha", "platform", "browser",
             "started_at", "total", "passed", "failed", "new_baselines",
             "baseline_scope", "project_key")}


# --------------------------------------------------------------------------- #
#  Выбор базы по умолчанию
# --------------------------------------------------------------------------- #
def previous_run(db, head: dict) -> dict | None:
    """Прогон, с которым сравнивать, если человек не выбрал сам.

    Предыдущий прогон того же проекта и той же платформы. Платформа в
    условии не для строгости: прогон под другой платформой отличается от
    этого рендерингом, и «сломалось» в таком сравнении будет означать
    «другой шрифт», а не «другой код».

    Возвращает None, если сравнивать не с чем, — и это нормальный ответ,
    а не ошибка: у первого прогона предыдущего не бывает.
    """
    return db.one(
        "SELECT * FROM run"
        " WHERE project_id=? AND COALESCE(platform,'')=COALESCE(?,'')"
        "   AND started_at < ? AND id <> ?"
        " ORDER BY started_at DESC, id DESC LIMIT 1",
        (head.get("project_id"), head.get("platform"),
         head.get("started_at"), head.get("id")))


def comparisons_of(db, run_id: int) -> list[dict]:
    return db.query(
        "SELECT c.id, c.verdict, c.review, c.max_severity, c.changed_area_pct,"
        "       c.ssim, s.name AS snapshot_name, s.platform, s.browser"
        "  FROM comparison c JOIN snapshot s ON s.id=c.snapshot_id"
        " WHERE c.run_id=?", (run_id,))
