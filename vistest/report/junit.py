# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""JUnit XML — единственный формат, который умеет прочитать любой CI.

Зачем он, если есть Allure-вложения и код возврата pytest. Затем, что ни то,
ни другое не отвечает на вопрос CI «что именно сломалось»:

* Allure — это отдельный отчёт, который надо отдельно собрать и куда-то
  выложить; в интерфейсе сборки он не появляется;
* код возврата pytest есть только там, где прогон **идёт через pytest**. А
  прогон подключённого проекта в режиме наблюдения (мы разбираем оставшиеся
  после чужого прогона PNG) и прогон, запущенный кнопкой в интерфейсе, никакого
  pytest не имеют вовсе. Такие прогоны для CI были невидимы полностью.

JUnit это закрывает: GitHub, GitLab, Jenkins и TeamCity показывают такой файл
как список тестов прямо в сборке, с раскрытием причины падения.

Что во что превращается — решения, а не механика:

| Вердикт | В JUnit | Почему |
|---|---|---|
| `pass` | обычный testcase | |
| `fail` | `<failure>` | регресс; в тексте — severity, площадь, SSIM и список регионов |
| `error` | `<error>` | съёмка или движок упали, сравнения **не было** |
| `new_baseline` | `<skipped>` | сравнивать было не с чем |

`error` отделён от `failure` намеренно: сломанная проверка и найденный дефект —
разные события, и смешать их значит потерять оба. `new_baseline` записать как
«прошло» значило бы заявить проверку, которой не происходило.

Отдельно про **разобранные падения**. Если человек посмотрел на падение и
принял его как новую норму, сборку это ронять не должно: решение уже принято,
и красный CI после него — просто шум. Поэтому при выгрузке прогона из истории
`review='approved'` превращает `fail` в прошедший testcase, а в `system-out`
пишется, кто и когда принял. Пересобрать артефакт после разбора и получить
зелёный — это ровно то, чего от такой выгрузки ждут.
"""

from __future__ import annotations

import re
from xml.sax.saxutils import escape, quoteattr

# XML 1.0 не допускает большинство управляющих символов — а они приезжают в
# текстах ошибок из чужих трейсбеков. Один такой байт делает файл нечитаемым
# целиком, и CI показывает не «упал тест», а «отчёт битый».
_ILLEGAL = re.compile(
    r"[^\x09\x0A\x0D\x20-퟿-�\U00010000-\U0010FFFF]")


def _clean(text) -> str:
    return _ILLEGAL.sub("", str(text if text is not None else ""))


def _attr(value) -> str:
    return quoteattr(_clean(value))


def _text(value) -> str:
    return escape(_clean(value))


def _split_name(name: str) -> tuple[str, str]:
    """`shop.example/login.png` → classname `shop.example`, name `login.png`.

    Префикс проекта в имени снимка — это и есть естественная группировка, и в
    интерфейсе сборки она превращается в дерево вместо плоской простыни.
    """
    text = str(name or "snapshot").replace("\\", "/")
    if "/" not in text:
        return "visual", text
    head, _, tail = text.rpartition("/")
    return head.replace("/", "."), tail


def normalize(comparison: dict) -> dict:
    """Одна форма для двух источников.

    Сравнение приезжает либо из базы (плоские колонки, `snapshot_name`), либо
    из `run.json` (вложенный `metrics`, `name`). Разбирать это в двух местах —
    гарантированный способ получить два разных отчёта об одном прогоне.
    """
    metrics = comparison.get("metrics") or {}
    if not isinstance(metrics, dict):
        metrics = {}
    # `meta` из базы приезжает СТРОКОЙ с JSON — там его никто не разбирал, а
    # здесь оно выглядело как словарь. Один `.get` по строке, и выгрузка падает
    # целиком; поэтому источник данных проверяется, а не подразумевается.
    meta = comparison.get("meta")
    if isinstance(meta, str):
        import json as _json
        try:
            meta = _json.loads(meta or "{}")
        except Exception:
            meta = {}
    if not isinstance(meta, dict):
        meta = {}

    def pick(*keys, default=None):
        for key in keys:
            if comparison.get(key) is not None:
                return comparison[key]
            if metrics.get(key) is not None:
                return metrics[key]
        return default

    return {
        "name": comparison.get("snapshot_name") or comparison.get("name") or "snapshot",
        "verdict": (comparison.get("verdict") or "pass"),
        "review": comparison.get("review"),
        "reviewed_by": comparison.get("reviewed_by"),
        "reviewed_at": comparison.get("reviewed_at"),
        "error": comparison.get("error") or meta.get("error"),
        "max_severity": pick("max_severity", default=0.0),
        "ssim": pick("ssim", "ssim_global", default=None),
        "changed_area_pct": pick("changed_area_pct", default=None),
        "duration_ms": pick("duration_ms", default=None),
        "regions": comparison.get("regions") or [],
    }


def _failure_text(c: dict) -> str:
    lines = [f"severity {float(c['max_severity'] or 0):.1f}"]
    if c["changed_area_pct"] is not None:
        lines[0] += f", changed area {float(c['changed_area_pct']):.3f}%"
    if c["ssim"] is not None:
        lines[0] += f", SSIM {float(c['ssim']):.5f}"

    regions = [r for r in (c["regions"] or []) if isinstance(r, dict)]
    if regions:
        lines.append("")
        lines.append(f"{len(regions)} region(s):")
        for r in regions[:12]:
            where = f"{r.get('w')}×{r.get('h')} @ {r.get('x')},{r.get('y')}"
            bits = [f"  [{round(float(r.get('severity') or 0))}] {r.get('kind') or 'change'}",
                    where]
            if r.get("selector"):
                bits.append(str(r["selector"]))
            lines.append("  ".join(bits))
        if len(regions) > 12:
            lines.append(f"  … and {len(regions) - 12} more")
    return "\n".join(lines)


def render_junit(comparisons: list[dict], *, suite: str = "vistest",
                 run_key: str = "", platform: str = "", branch: str = "",
                 git_sha: str = "", duration_s: float = 0.0,
                 honor_review: bool = True) -> str:
    """Собрать JUnit XML.

    `honor_review=False` — для выгрузки в момент прогона, когда разбирать ещё
    нечего и любое падение обязано быть падением.
    """
    items = [normalize(c) for c in (comparisons or [])]

    cases: list[str] = []
    failures = errors = skipped = 0

    for c in items:
        classname, name = _split_name(c["name"])
        seconds = (float(c["duration_ms"]) / 1000.0) if c["duration_ms"] else 0.0
        head = (f'    <testcase classname={_attr(classname)} name={_attr(name)}'
                f' time="{seconds:.3f}"')

        verdict = c["verdict"]
        accepted = honor_review and verdict == "fail" and c["review"] == "approved"

        if verdict == "error":
            errors += 1
            message = c["error"] or "capture or comparison engine failed"
            cases.append(
                head + ">\n"
                f'      <error type="VisualCheckError" message={_attr(message)}>'
                f"{_text(message)}</error>\n    </testcase>")
        elif verdict == "fail" and not accepted:
            failures += 1
            body = _failure_text(c)
            message = f"visual regression, severity {float(c['max_severity'] or 0):.1f}"
            cases.append(
                head + ">\n"
                f'      <failure type="VisualMismatch" message={_attr(message)}>'
                f"{_text(body)}</failure>\n    </testcase>")
        elif verdict == "new_baseline":
            skipped += 1
            cases.append(
                head + ">\n"
                '      <skipped message="new baseline: there was nothing to '
                'compare against"/>\n    </testcase>')
        elif accepted:
            who = c["reviewed_by"] or "a reviewer"
            when = str(c["reviewed_at"] or "")[:16]
            note = (f"failed, then accepted as the new baseline by {who}"
                    + (f" ({when})" if when else ""))
            cases.append(head + ">\n"
                         f"      <system-out>{_text(note)}</system-out>\n    </testcase>")
        else:
            cases.append(head + "/>")

    props = [("platform", platform), ("branch", branch), ("commit", git_sha),
             ("run", run_key)]
    prop_xml = "".join(
        f'      <property name={_attr(k)} value={_attr(v)}/>\n'
        for k, v in props if v)

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<testsuites tests="{len(items)}" failures="{failures}"'
        f' errors="{errors}" skipped="{skipped}">\n'
        f'  <testsuite name={_attr(suite)} tests="{len(items)}"'
        f' failures="{failures}" errors="{errors}" skipped="{skipped}"'
        f' time="{max(0.0, duration_s):.3f}">\n'
        + (f"    <properties>\n{prop_xml}    </properties>\n" if prop_xml else "")
        + "\n".join(cases) + ("\n" if cases else "")
        + "  </testsuite>\n</testsuites>\n"
    )


def gate(comparisons: list[dict], *, honor_review: bool = True,
         allow_new: bool = True) -> dict:
    """Проходит ли прогон — одним ответом, пригодным для кода возврата.

    Отдельно от JUnit, потому что вопросы разные: JUnit отвечает «что
    случилось», а это — «останавливать ли конвейер». Ошибка съёмки его
    останавливает: проверки не было, и делать вид, что всё хорошо, нельзя.
    """
    items = [normalize(c) for c in (comparisons or [])]
    blocking, accepted, new = [], [], []

    for c in items:
        if c["verdict"] == "error":
            blocking.append({"name": c["name"], "why": c["error"] or "check failed"})
        elif c["verdict"] == "fail":
            if honor_review and c["review"] == "approved":
                accepted.append(c["name"])
            else:
                blocking.append({
                    "name": c["name"],
                    "why": f"severity {float(c['max_severity'] or 0):.1f}"})
        elif c["verdict"] == "new_baseline":
            new.append(c["name"])

    if not allow_new and new:
        blocking.extend({"name": n, "why": "new baseline was captured"} for n in new)

    return {
        "ok": not blocking,
        "total": len(items),
        "blocking": blocking,
        "accepted_as_normal": accepted,
        "new_baselines": new,
        "reason": _gate_reason(blocking, accepted, new, len(items)),
    }


def _gate_reason(blocking, accepted, new, total) -> str:
    if not total:
        return "nothing was compared — the run produced no snapshots at all"
    if not blocking:
        tail = []
        if accepted:
            tail.append(f"{len(accepted)} accepted as normal by a human")
        if new:
            tail.append(f"{len(new)} new baseline(s)")
        return f"{total} snapshot(s) checked" + (f" · {', '.join(tail)}" if tail else "")
    first = blocking[0]
    more = f" and {len(blocking) - 1} more" if len(blocking) > 1 else ""
    return f"{len(blocking)} of {total} blocking: {first['name']} — {first['why']}{more}"
