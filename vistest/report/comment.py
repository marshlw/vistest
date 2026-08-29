# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Сводка прогона для комментария в PR/MR.

Разделение обязанностей здесь намеренное и стоит того, чтобы назвать его вслух:
**VisTest рендерит, CI публикует.**

Соблазн был противоположный — научить сервис ходить в GitHub и GitLab самому.
Это означало бы держать по клиенту на каждую площадку, просить у человека токен
с правом писать в его репозитории и хранить этот токен у себя. Взамен — ничего,
чего не делает одна строчка `gh pr comment --body-file`. Публиковать умеет CI,
и умеет лучше: он уже аутентифицирован и уже знает номер PR.

Что здесь есть и чего нет:

* есть **markdown**, годный для GitHub, GitLab и Bitbucket без переделок;
* нет сети, токенов и знания о том, куда именно это уедет.

Раньше эту сводку собирал JavaScript прямо внутри `.github/workflows/visual.yml`
— двадцать строк, разбиравших `run.json` руками. Логика отображения жила в
YAML, ничем не проверялась и тихо ломалась при любом изменении полей.

Про содержание. Комментарий читают в ленте PR, между чужими сообщениями, и
читают быстро. Поэтому первой строкой идёт ответ («три снимка из сорока семи
изменились»), а не заголовок; поэтому падения свёрнуты **по общей причине** —
двадцать красных снимков от одного отступа в шапке это один вопрос, а не
двадцать; поэтому таблица обрезается, а не растёт до сотни строк, которые никто
не пролистает.
"""

from __future__ import annotations

from vistest import provenance as _prov

from .junit import normalize

MAX_ROWS = 12
MAX_CLUSTERS = 5


def _esc(text) -> str:
    """Экранирование для таблицы markdown.

    Вертикальная черта в имени снимка или в селекторе разваливает строку
    таблицы, а разваленная таблица выглядит как поломка инструмента, а не как
    имя с необычным символом.
    """
    return (str(text if text is not None else "")
            .replace("|", "\\|").replace("\n", " ").strip())


def _link(base_url: str, path: str) -> str:
    if not base_url:
        return ""
    return base_url.rstrip("/") + path


def render_comment(comparisons: list[dict], *, run_key: str = "",
                   platform: str = "", branch: str = "", git_sha: str = "",
                   base_url: str = "", run_id: int | None = None,
                   clusters: list[dict] | None = None,
                   baseline_scope: str = "", branch_baselines: bool = False,
                   default_branch: str = "") -> str:
    """Markdown-сводка прогона."""
    items = [normalize(c) for c in (comparisons or [])]
    total = len(items)

    failed = [c for c in items if c["verdict"] == "fail" and c["review"] != "approved"]
    accepted = [c for c in items if c["verdict"] == "fail" and c["review"] == "approved"]
    errored = [c for c in items if c["verdict"] == "error"]
    fresh = [c for c in items if c["verdict"] == "new_baseline"]

    lines: list[str] = []

    # ---- первая строка это ответ, а не заголовок ----
    if not total:
        head = "**VisTest: nothing was compared.** The run produced no snapshots at all."
    elif failed:
        head = (f"**VisTest: {len(failed)} of {total} snapshot"
                f"{'s' if total != 1 else ''} changed.**")
    elif errored:
        head = (f"**VisTest: nothing changed, but {len(errored)} snapshot"
                f"{'s' if len(errored) != 1 else ''} could not be checked.**")
    else:
        head = f"**VisTest: no visual changes** ({total} snapshot{'s' if total != 1 else ''})."
    lines.append(head)

    meta = " · ".join(x for x in [
        f"`{_esc(branch)}`" if branch else "",
        f"`{_esc(git_sha)[:8]}`" if git_sha else "",
        _esc(platform),
    ] if x)
    if meta:
        lines.append("")
        lines.append(meta)

    # ---- причины, а не снимки ----
    # Двадцать красных снимков от одного отступа в шапке — это один вопрос.
    # Показать двадцать строк вместо одной значит заставить человека
    # восстанавливать группировку самому.
    groups = [c for c in (clusters or []) if (c.get("snapshot_count") or 0) > 1]
    if failed and groups:
        lines.append("")
        lines.append(f"**Grouped by cause — {len(groups)} question"
                     f"{'s' if len(groups) != 1 else ''}, not {len(failed)} snapshots:**")
        lines.append("")
        for g in groups[:MAX_CLUSTERS]:
            where = g.get("common") or ", ".join((g.get("selectors") or [])[:2])
            lines.append(
                f"- **{_esc(g.get('description') or 'change')}** — "
                f"{g.get('snapshot_count')} snapshots, severity "
                f"{g.get('max_severity')}"
                + (f" · `{_esc(where)}`" if where else ""))
        if len(groups) > MAX_CLUSTERS:
            lines.append(f"- …and {len(groups) - MAX_CLUSTERS} more groups")

    # ---- таблица падений ----
    if failed:
        lines.append("")
        lines.append("| Snapshot | Severity | Area | What changed |")
        lines.append("|---|---:|---:|---|")
        ordered = sorted(failed, key=lambda c: -(float(c["max_severity"] or 0)))
        for c in ordered[:MAX_ROWS]:
            regions = [r for r in (c["regions"] or []) if isinstance(r, dict)]
            what = ", ".join(
                dict.fromkeys(str(r.get("kind") or "change") for r in regions[:3]))
            selector = next((r.get("selector") for r in regions if r.get("selector")), "")
            if selector:
                what = f"{what} `{_esc(selector)}`" if what else f"`{_esc(selector)}`"
            area = (f"{float(c['changed_area_pct']):.3f}%"
                    if c["changed_area_pct"] is not None else "—")
            severity = f"{float(c['max_severity'] or 0):.0f}"
            lines.append(f"| `{_esc(c['name'])}` | {severity} | "
                         f"{area} | {what or '—'} |")
        if len(ordered) > MAX_ROWS:
            lines.append(f"| …and {len(ordered) - MAX_ROWS} more | | | |")

    # ---- то, что проверкой не было ----
    if errored:
        lines.append("")
        lines.append(f"**{len(errored)} not checked** — capture or the engine failed, "
                     "so nothing was compared:")
        for c in errored[:5]:
            lines.append(f"- `{_esc(c['name'])}` — {_esc(c['error'] or 'unknown error')}")
        if len(errored) > 5:
            lines.append(f"- …and {len(errored) - 5} more")

    # ---- то, что уже разобрали ----
    tail = []
    if accepted:
        who = {c["reviewed_by"] for c in accepted if c["reviewed_by"]}
        tail.append(f"{len(accepted)} already accepted as normal"
                    + (f" by {', '.join(sorted(who))}" if who else ""))
    if fresh:
        tail.append(f"{len(fresh)} new baseline{'s' if len(fresh) != 1 else ''} "
                    "(nothing to compare against yet)")
    if tail:
        lines.append("")
        lines.append("_" + "; ".join(tail) + "._")

    # ---- куда уйдёт решение ----
    # Появилось вместе с эталоном на ветку: без этой строки комментарий в MR
    # советовал бы принимать изменения, не говоря, в чей эталон они лягут.
    if failed and branch_baselines and branch and branch != default_branch:
        lines.append("")
        lines.append(
            f"> Accepting these lands on branch `{_esc(branch)}`, not on "
            f"`{_esc(default_branch or 'the base branch')}`. After the merge, "
            "promote the branch in VisTest — otherwise the base set keeps the "
            "old pictures and starts failing again.")

    if base_url and run_id:
        # Ссылка ведёт на КОНКРЕТНЫЙ прогон, а не на список. Разница видна не
        # сразу: список показывает последние шестьдесят, и через день комментарий
        # к позавчерашнему MR открывал бы чужой прогон, выглядящий как свой.
        ui = _link(base_url, f"/ui/#/runs/{run_id}")
        report = _link(base_url, f"/api/runs/{run_id}/report.html")
        lines.append("")
        lines.append(f"[Open the run in VisTest]({ui}) · [report]({report})")
    elif run_key:
        lines.append("")
        lines.append(f"_Run `{_esc(run_key)}`._")

    lines.append("")
    lines.append(_prov.comment_footer())

    return "\n".join(lines).rstrip() + "\n"
