# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Бенчмарк движка: качество VisTest и сравнение с конкурентами.

    python tests/benchmark.py                       таблица по VisTest
    python tests/benchmark.py --artifacts           + картинки диффов
    python tests/benchmark.py --compare             VisTest против конкурентов
    python tests/benchmark.py --no-ai               голый компаратор, без AI-слоя
    python tests/benchmark.py --compare --markdown bench_out/BENCHMARK.md
    python tests/benchmark.py --export bench_out/corpus   выгрузить корпус

Главная метрика — **false-fail rate**: доля неизменённых по существу страниц,
на которых инструмент упал. Она важнее полноты: пропущенный регресс замечает
следующий прогон, а от ложных падений команда перестаёт верить тестам и
выключает их совсем.

Вторая метрика — **miss rate**: доля настоящих регрессов, которые инструмент
пропустил. Инструмент, который не падает никогда, имеет идеальный false-fail
rate и нулевую пользу, поэтому смотреть надо на обе цифры сразу.

Числа в README получаются прогоном этой команды. Порты
конкурентов помечены как порты; подлинные цифры pixelmatch и Playwright дают
`scripts/bench_pixelmatch.mjs` и флаг `--native`.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests import baselines as bl  # noqa: E402
from tests import corpus as cp  # noqa: E402
from vistest.ai import AIPipeline  # noqa: E402
from vistest.config import VisTestConfig  # noqa: E402
from vistest.core.comparator import compare  # noqa: E402
from vistest.models import Verdict  # noqa: E402
from vistest.render.artifacts import render_all  # noqa: E402


# --------------------------------------------------------------------------- #
#  Счёт
# --------------------------------------------------------------------------- #
@dataclass
class Score:
    title: str
    native: bool = True
    note: str = ""
    false_fails: int = 0      # упал там, где не должен
    misses: int = 0           # не упал там, где должен
    noise_total: int = 0
    signal_total: int = 0
    ms: float = 0.0
    per_case: dict[str, bool] = field(default_factory=dict)   # name -> failed

    @property
    def false_fail_rate(self) -> float:
        return self.false_fails / max(self.noise_total, 1)

    @property
    def miss_rate(self) -> float:
        return self.misses / max(self.signal_total, 1)

    @property
    def correct(self) -> int:
        return (self.noise_total - self.false_fails) + (self.signal_total - self.misses)

    @property
    def total(self) -> int:
        return self.noise_total + self.signal_total

    def add(self, case: cp.Case, failed: bool) -> None:
        self.per_case[case.name] = failed
        if case.group == "NOISE":
            self.noise_total += 1
            self.false_fails += int(failed)
        else:
            self.signal_total += 1
            self.misses += int(not failed)


def _title(cfg: VisTestConfig, ai: AIPipeline | None) -> str:
    return f"VisTest ({cfg.preset})" if ai else f"VisTest ({cfg.preset}, без AI)"


def _note(ai: AIPipeline | None) -> str:
    base = "ΔE00 ∧ SSIM, консенсус"
    return base + (" + обучаемый гейт" if ai else ", AI-слой выключен")


def score_vistest(cases: list[cp.Case], cfg: VisTestConfig,
                  *, artifacts_dir: Path | None = None,
                  ai: AIPipeline | None = None) -> Score:
    s = Score(title=_title(cfg, ai), note=_note(ai))
    for c in cases:
        t0 = time.perf_counter()
        r = compare(c.expected, c.actual, cfg=cfg.diff, name=c.name,
                    ai_hooks=ai)
        s.ms += (time.perf_counter() - t0) * 1000
        s.add(c, r.verdict is Verdict.FAIL)
        wrong = (r.verdict is Verdict.FAIL) != c.expected_fail
        if artifacts_dir and (wrong or c.group == "SIGNAL"):
            render_all(r, artifacts_dir / cp._slug(c.name), cfg=cfg.render)
    return s


def score_engine(cases: list[cp.Case], engine: bl.Engine) -> Score:
    s = Score(title=engine.title, native=engine.native, note=engine.note)
    for c in cases:
        t0 = time.perf_counter()
        res = engine.run(c.expected, c.actual)
        s.ms += (time.perf_counter() - t0) * 1000
        s.add(c, res.failed)
    return s


def score_native(cases: list[cp.Case], native: dict, key: str,
                 title: str, note: str) -> Score | None:
    """Счёт по результатам нативного прогона из bench_pixelmatch.mjs."""
    data = (native.get("results") or {}).get(key)
    if not data:
        return None
    s = Score(title=title, native=True, note=note)
    for c in cases:
        entry = data.get(c.name)
        if entry is None:
            return None
        s.add(c, bool(entry.get("failed")))
    return s


# --------------------------------------------------------------------------- #
#  Вывод
# --------------------------------------------------------------------------- #
def print_detail(cases: list[cp.Case], cfg: VisTestConfig,
                 artifacts_dir: Path | None,
                 ai: AIPipeline | None = None) -> Score:
    header = (f"{'кейс':24s} {'ожид':6s} {'факт':6s} {'sev':>6s} {'изм%':>8s} "
              f"{'ΔE':>6s} {'SSIM':>7s} {'рег':>4s} {'мс':>5s}  итог")
    print(f"\n=== VisTest benchmark (preset={cfg.preset}, "
          f"AI-слой {'вкл' if ai else 'выкл'}) ===")
    print(header)
    print("-" * len(header))

    s = Score(title=_title(cfg, ai), note=_note(ai))
    for group in ("NOISE", "SIGNAL"):
        expected = Verdict.PASS if group == "NOISE" else Verdict.FAIL
        print(f"\n[{group}]  ожидается {expected.value}")
        for c in (x for x in cases if x.group == group):
            t0 = time.perf_counter()
            r = compare(c.expected, c.actual, cfg=cfg.diff, name=c.name,
                        ai_hooks=ai)
            ms = (time.perf_counter() - t0) * 1000
            s.ms += ms
            failed = r.verdict is Verdict.FAIL
            s.add(c, failed)
            ok = failed == c.expected_fail
            print(f"{c.name:24s} {expected.value:6s} {r.verdict.value:6s} "
                  f"{r.max_severity:6.1f} {r.changed_area_pct:8.4f} "
                  f"{r.de_mean:6.2f} {r.ssim_global:7.5f} {len(r.regions):4d} "
                  f"{ms:5.0f}  {'ok' if ok else 'ОШИБКА'}")
            if artifacts_dir and (not ok or group == "SIGNAL"):
                render_all(r, artifacts_dir / cp._slug(c.name), cfg=cfg.render)

    print("\n" + "-" * len(header))
    print(f"Пройдено {s.correct}/{s.total}, "
          f"ложных падений {s.false_fails}/{s.noise_total}, "
          f"пропусков {s.misses}/{s.signal_total}")
    return s


def print_comparison(scores: list[Score]) -> None:
    print("\n=== Сравнение движков ===")
    w = max(len(s.title) for s in scores) + 2
    print(f"{'инструмент':{w}s} {'ложных падений':>16s} {'пропусков':>12s} "
          f"{'верно':>8s} {'мс':>7s}")
    print("-" * (w + 47))
    for s in scores:
        mark = "" if s.native else "  (порт)"
        print(f"{s.title + mark:{w}s} "
              f"{s.false_fails:2d}/{s.noise_total:<2d} "
              f"{s.false_fail_rate * 100:6.0f}%  "
              f"{s.misses:2d}/{s.signal_total:<2d} "
              f"{s.miss_rate * 100:4.0f}%  "
              f"{s.correct:3d}/{s.total:<3d} {s.ms:7.0f}")


def markdown(scores: list[Score], cases: list[cp.Case], cfg: VisTestConfig,
             native_used: bool) -> str:
    import platform
    from datetime import date

    L: list[str] = []
    L.append("# Бенчмарк движка сравнения")
    L.append("")
    L.append(f"Сгенерировано `python tests/benchmark.py --compare --markdown` "
             f"{date.today().isoformat()}, "
             f"{platform.system()} {platform.machine()}, Python "
             f"{platform.python_version()}, пресет `{cfg.preset}`.")
    L.append("")
    L.append("Числа ниже воспроизводятся одной командой на любой машине — "
             "корпус синтетический и генерируется кодом, а не лежит архивом.")
    L.append("")
    L.append("VisTest считается в конфигурации по умолчанию, вместе с "
             "AI-слоем: обучаемый гейт включён в поставке, и прогон у "
             "покупателя идёт именно так. Голый компаратор без него — "
             "`python tests/benchmark.py --compare --no-ai`.")
    L.append("")

    L.append("## Результат")
    L.append("")
    L.append("| Инструмент | Ложные падения | Пропущенные регрессы | Верно | мс |")
    L.append("|---|---|---|---|---|")
    for s in scores:
        mark = "" if s.native else " ⁽ᵖ⁾"
        L.append(
            f"| {s.title}{mark} | "
            f"{s.false_fails}/{s.noise_total} ({s.false_fail_rate * 100:.0f}%) | "
            f"{s.misses}/{s.signal_total} ({s.miss_rate * 100:.0f}%) | "
            f"{s.correct}/{s.total} | {s.ms:.0f} |")
    L.append("")
    if any(not s.native for s in scores):
        L.append("⁽ᵖ⁾ — не оригинальный код, а порт ядра на numpy "
                 "(`tests/baselines.py`), без детектора анти-алиасинга. "
                 "Подлинные цифры: `node scripts/bench_pixelmatch.mjs` "
                 "и флаг `--native`, см. «Как воспроизвести».")
        L.append("")
    if native_used:
        L.append("Строки pixelmatch и Playwright посчитаны **нативным** "
                 "npm-пакетом pixelmatch на тех же PNG.")
        L.append("")

    L.append("## Что означают колонки")
    L.append("")
    L.append("**Ложные падения** — доля страниц из группы NOISE, на которых "
             "инструмент дал красный тест. Страница по существу не менялась, "
             "поэтому каждое такое падение ложное. Это главная метрика: "
             "именно от ложных падений команда через месяц ставит "
             "`--update-snapshots` в CI, что равносильно выключению "
             "визуального тестирования.")
    L.append("")
    L.append("**Пропущенные регрессы** — доля группы SIGNAL, где инструмент "
             "остался зелёным. Смотреть только на первую колонку нельзя: "
             "инструмент, который не падает никогда, идеален по ложным "
             "падениям и бесполезен.")
    L.append("")

    L.append("## Корпус")
    L.append("")
    L.append(f"{len(cases)} пар: "
             f"{sum(1 for c in cases if c.group == 'NOISE')} NOISE + "
             f"{sum(1 for c in cases if c.group == 'SIGNAL')} SIGNAL. "
             "Генерируется `tests/corpus.py`, изображения рисуются "
             "`tests/synthetic.py`.")
    L.append("")
    L.append("Почему синтетика, а не настоящие скриншоты: на реальных парах "
             "правильный ответ приходится размечать человеком, и в спорных "
             "случаях человек размечает так, как ему удобно. Здесь ответ "
             "задан построением — его можно оспорить, читая код, а не "
             "полагаясь на нашу добросовестность. Обратная сторона честная: "
             "синтетика не покрывает всё разнообразие реальных страниц, "
             "поэтому цифра говорит о движке, а не о вашем проекте. Про свой "
             "проект отвечает `vistest doctor`.")
    L.append("")
    L.append("| Кейс | Группа | Ожидание | Почему |")
    L.append("|---|---|---|---|")
    for c in cases:
        L.append(f"| `{c.name}` | {c.group} | "
                 f"{'падение' if c.expected_fail else 'проход'} | {c.why} |")
    L.append("")

    L.append("## Кто с чем сравнивается")
    L.append("")
    L.append("| Инструмент | Что это | Оговорка |")
    L.append("|---|---|---|")
    for s in scores:
        L.append(f"| {s.title} | {s.note or '—'} | "
                 f"{'оригинальный код' if s.native else 'порт на numpy'} |")
    L.append("")
    L.append("BackstopJS в таблице нет намеренно: воспроизводить поведение "
             "resemble.js по памяти мы не стали, а нативного раннера пока не "
             "написали. Появится — добавим строку.")
    L.append("")

    L.append("## Как воспроизвести")
    L.append("")
    L.append("```bash")
    L.append("git clone <repo> && cd visual-testing")
    L.append("python run.py setup")
    L.append("")
    L.append("# быстрый вариант: порты конкурентов на numpy")
    L.append("python tests/benchmark.py --compare")
    L.append("")
    L.append("# честный вариант: настоящий pixelmatch")
    L.append("python tests/benchmark.py --export bench_out/corpus")
    L.append("npm install pixelmatch pngjs")
    L.append("node scripts/bench_pixelmatch.mjs bench_out/corpus > bench_out/native.json")
    L.append("python tests/benchmark.py --compare --native bench_out/native.json")
    L.append("```")
    L.append("")

    L.append("## Что эта таблица не доказывает")
    L.append("")
    L.append("- Корпус синтетический. Он проверяет, что движок отличает "
             "известные виды шума от известных видов регресса, а не то, как "
             "он поведёт себя на вашем приложении.")
    L.append("- Applitools и Percy здесь не участвуют: закрытые SaaS, "
             "прогнать их на своём корпусе и опубликовать результат нельзя.")
    L.append("- Пороги VisTest настраивались в том числе по этому корпусу. "
             "Это конфликт интересов, и мы о нём говорим прямо — поэтому "
             "корпус и код открыты, а не приложены картинкой.")
    L.append("")

    L.append("## Кейсы по инструментам")
    L.append("")
    head = "| Кейс | Ожидание | " + " | ".join(s.title for s in scores) + " |"
    L.append(head)
    L.append("|" + "---|" * (2 + len(scores)))
    for c in cases:
        row = [f"`{c.name}`", "падение" if c.expected_fail else "проход"]
        for s in scores:
            got = s.per_case.get(c.name)
            if got is None:
                row.append("—")
            else:
                row.append(("падение" if got else "проход")
                           + ("" if got == c.expected_fail else " ❌"))
        L.append("| " + " | ".join(row) + " |")
    L.append("")
    return "\n".join(L)


# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="Бенчмарк движка сравнения")
    ap.add_argument("--out", default="bench_out")
    ap.add_argument("--preset", default="balanced",
                    choices=["strict", "balanced", "loose"])
    ap.add_argument("--artifacts", action="store_true", help="сохранить картинки")
    ap.add_argument("--compare", action="store_true",
                    help="сравнить с конкурентами")
    ap.add_argument("--no-ai", action="store_true",
                    help="без AI-слоя: голый компаратор, только для диагностики")
    ap.add_argument("--native", help="JSON нативного прогона pixelmatch")
    ap.add_argument("--export", metavar="DIR",
                    help="выгрузить корпус в PNG для чужих инструментов")
    ap.add_argument("--markdown", metavar="FILE",
                    help="записать таблицу в markdown")
    args = ap.parse_args()

    cfg = VisTestConfig.preset_of(args.preset)
    cases = cp.build()
    # По умолчанию меряем то, что реально уезжает покупателю: гейт включён в
    # конфигурации по умолчанию, и CheckService собирает пайплайн на каждой
    # проверке. Бенчмарк без него мерил подмножество продукта и занижал его.
    ai = None if args.no_ai else AIPipeline(cfg.ai)
    out = Path(args.out)
    artifacts_dir = out if args.artifacts else None

    if args.export:
        path = cp.export_corpus(cases, args.export)
        print(f"Корпус выгружен: {path.resolve()}  ({len(cases)} пар)")
        print("Дальше:  node scripts/bench_pixelmatch.mjs "
              f"{args.export} > bench_out/native.json")
        return 0

    if not args.compare:
        s = print_detail(cases, cfg, artifacts_dir, ai=ai)
        if args.artifacts:
            print(f"Артефакты: {out.resolve()}")
        return 1 if s.correct != s.total else 0

    scores = [score_vistest(cases, cfg, artifacts_dir=artifacts_dir, ai=ai)]

    native: dict = {}
    if args.native:
        native = json.loads(Path(args.native).read_text("utf-8"))

    for engine in bl.ENGINES:
        nat = score_native(
            cases, native, engine.key,
            title=engine.title,
            note=engine.note.replace("порт ядра, без AA-детектора → сверять с node",
                                     "оригинальный npm-пакет"),
        ) if native else None
        scores.append(nat or score_engine(cases, engine))

    print_comparison(scores)

    if args.markdown:
        path = Path(args.markdown)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown(scores, cases, cfg, bool(native)),
                        encoding="utf-8")
        print(f"\nMarkdown: {path.resolve()}")

    vt = scores[0]
    return 1 if vt.correct != vt.total else 0


if __name__ == "__main__":
    raise SystemExit(main())
