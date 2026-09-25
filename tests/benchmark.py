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
    python tests/benchmark.py --no-timing           no timing column: the output
                                                    is reproducible byte for byte
    python tests/benchmark.py --regenerate          redraw the frozen corpus (on purpose)

Главная метрика — **false-fail rate**: доля неизменённых по существу страниц,
на которых инструмент упал. Она важнее полноты: пропущенный регресс замечает
следующий прогон, а от ложных падений команда перестаёт верить тестам и
выключает их совсем.

Вторая метрика — **miss rate**: доля настоящих регрессов, которые инструмент
пропустил. Инструмент, который не падает никогда, имеет идеальный false-fail
rate и нулевую пользу, поэтому смотреть надо на обе цифры сразу.

The corpus is read from disk (`tests/benchmark_corpus/`), not drawn at run
time: otherwise the figure would depend on the OpenCV version of whoever runs
it. Details in `tests/corpus.py`.

Числа в README получаются прогоном с нативными результатами конкурентов:

    npm ci --prefix scripts/bench
    node scripts/bench_pixelmatch.mjs > docs/benchmark_native.json
    python tests/benchmark.py --compare --native docs/benchmark_native.json \
        --no-timing --markdown docs/benchmark.md

Без `--native` строки pixelmatch и Playwright считает наш порт на numpy
(`tests/baselines.py`); он помечен как порт и в публикацию не идёт.
`--with-ports` рядом с `--native` печатает в консоль обе версии — чтобы
сверить порт с оригиналом, а не чтобы публиковать.
"""

from __future__ import annotations

import argparse
import hashlib
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
    key: str = ""             # bl.Engine.key; empty for VisTest
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
    if ai is None:
        return base + ", AI-слой не подключён (--no-ai)"
    gate = "вкл" if ai_gate_enabled(ai) else "выкл"
    return base + f", AI-слой по умолчанию (обучаемый гейт {gate})"


def ai_gate_enabled(ai: AIPipeline | None) -> bool:
    return bool(ai is not None and ai.cfg.gate_enabled)


def _ms(ms: float, width: int, timing: bool) -> str:
    """Timing is the one thing in the output that changes from run to run."""
    return f"{ms:{width}.0f}" if timing else f"{'—':>{width}s}"


def _render_line(title: str, s: Score) -> str:
    return (f"{title}: {s.correct}/{s.total} correct, "
            f"false failures {s.false_fails}/{s.noise_total}, "
            f"misses {s.misses}/{s.signal_total}")


def by_render(cases: list[cp.Case], s: Score) -> list[tuple[cp.Render, Score]]:
    """The same score, split by the corpus raster."""
    out = []
    for r in cp.RENDERS:
        part = Score(title=r.key)
        for c in cases:
            if c.render == r.key and c.name in s.per_case:
                part.add(c, s.per_case[c.name])
        if part.total:
            out.append((r, part))
    return out


def environment_line() -> str:
    import platform

    import cv2
    import numpy

    return (f"OpenCV {cv2.__version__}, numpy {numpy.__version__}, "
            f"Python {platform.python_version()}, "
            f"{platform.system()} {platform.machine()}")


def python_versions() -> dict[str, str]:
    """Versions of what the VisTest row depends on, for the published table."""
    import platform
    from importlib import metadata

    out = {"Python": platform.python_version()}
    for dist in ("numpy", "opencv-python-headless", "pillow", "PyYAML"):
        try:
            out[dist] = metadata.version(dist)
        except metadata.PackageNotFoundError:
            out[dist] = "не установлен"
    return out


# --------------------------------------------------------------------------- #
#  Нативные результаты конкурентов
# --------------------------------------------------------------------------- #
class NativeError(RuntimeError):
    """The native JSON cannot be used for this corpus."""


def corpus_digest(root: str | Path | None = None) -> str:
    """Отпечаток корпуса — та же формула, что в scripts/bench_pixelmatch.mjs.

    sha256 от строк `"<путь> <sha256 файла>\n"`: manifest.json, затем
    expected/actual каждой пары в порядке манифеста.
    """
    root = Path(root) if root is not None else cp.FROZEN_DIR
    sha = lambda b: hashlib.sha256(b).hexdigest()  # noqa: E731
    manifest_bytes = (root / "manifest.json").read_bytes()
    lines = [f"manifest.json {sha(manifest_bytes)}\n"]
    for entry in json.loads(manifest_bytes.decode("utf-8"))["cases"]:
        for key in ("expected", "actual"):
            rel = entry[key]
            lines.append(f"{rel} {sha((root / rel).read_bytes())}\n")
    return sha("".join(lines).encode("utf-8"))


def load_native(path: str | Path, corpus_root: str | Path | None = None) -> dict:
    """Read the native JSON and refuse it if it was computed on other files.

    Different data is not a comparison: a table that mixes our figures on one
    set of PNGs with theirs on another says nothing.
    """
    doc = json.loads(Path(path).read_text("utf-8"))
    if doc.get("source") != "native":
        raise NativeError(f"{path}: not a native run (source={doc.get('source')!r})")
    want = corpus_digest(corpus_root)
    got = (doc.get("corpus") or {}).get("sha256")
    if got != want:
        raise NativeError(
            f"{path} was computed on a different corpus "
            f"(sha256 {got or 'missing'}, the corpus on disk is {want}). "
            "Re-run: node scripts/bench_pixelmatch.mjs > " + str(path))
    return doc


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
    s = Score(title=engine.title, native=engine.native, note=engine.note,
              key=engine.key)
    for c in cases:
        t0 = time.perf_counter()
        res = engine.run(c.expected, c.actual)
        s.ms += (time.perf_counter() - t0) * 1000
        s.add(c, res.failed)
    return s


def score_native(cases: list[cp.Case], native: dict, key: str,
                 title: str, note: str) -> Score | None:
    """Счёт по результатам нативного прогона из bench_pixelmatch.mjs.

    Название и описание берутся из самого JSON, если он их несёт: там
    записаны версия инструмента и то, как именно он был вызван.
    """
    data = (native.get("results") or {}).get(key)
    if not data:
        return None
    tool = (native.get("tools") or {}).get(key) or {}
    s = Score(title=tool.get("title", title), native=True,
              note=tool.get("invoked", note), key=key)
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
                 ai: AIPipeline | None = None, *, timing: bool = True) -> Score:
    w = max(24, *(len(c.name) for c in cases))
    header = (f"{'кейс':{w}s} {'ожид':6s} {'факт':6s} {'sev':>6s} {'изм%':>8s} "
              f"{'ΔE':>6s} {'SSIM':>7s} {'рег':>4s} {'мс':>5s}  итог")
    print(f"\n=== VisTest benchmark (preset={cfg.preset}, "
          f"AI-слой {'вкл' if ai else 'выкл'}) ===")
    #  The environment goes to stderr, so stdout stays comparable byte for
    #  byte between machines.
    print(f"Run on: {environment_line()}", file=sys.stderr)
    print("Corpus: tests/benchmark_corpus, rasters "
          + ", ".join(f"{r.key} ({r.package})" for r in cp.RENDERS))
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
            #  Printed coarser than `corpus.METRICS_STORED_DECIMALS` records,
            #  and on purpose: the file keeps more digits so that a Δ stays
            #  legible against the tolerance it is compared with, while this
            #  column is read by a person and shows only what reproduces
            #  across machines. Publishing digits we cannot reproduce is the
            #  defect this engine just removed from `suppressed_by`.
            #  `tests/corpus.py` says what was measured, and over what.
            print(f"{c.name:{w}s} {expected.value:6s} {r.verdict.value:6s} "
                  f"{r.max_severity:6.1f} {r.changed_area_pct:8.2f} "
                  f"{r.de_mean:6.1f} {r.ssim_global:7.3f} {len(r.regions):4d} "
                  f"{_ms(ms, 5, timing)}  {'ok' if ok else 'ОШИБКА'}")
            if artifacts_dir and (not ok or group == "SIGNAL"):
                render_all(r, artifacts_dir / cp._slug(c.name), cfg=cfg.render)

    print("\n" + "-" * len(header))
    for r, part in by_render(cases, s):
        print(_render_line(f"raster {r.key}", part))
    print(_render_line("total", s))
    return s


def print_comparison(scores: list[Score], cases: list[cp.Case] | None = None,
                     *, timing: bool = True) -> None:
    print("\n=== Сравнение движков ===")
    print(f"Run on: {environment_line()}", file=sys.stderr)
    w = max(len(s.title) + (0 if s.native else 8) for s in scores) + 2
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
              f"{s.correct:3d}/{s.total:<3d} {_ms(s.ms, 7, timing and s.ms > 0)}")
    if cases:
        for r in cp.RENDERS:
            print(f"\n  raster {r.key}:")
            for s in scores:
                part = dict(by_render(cases, s)).get(r)
                if part:
                    mark = "" if s.native else "  (порт)"
                    print(f"  {s.title + mark:{w}s} "
                          f"{part.false_fails:2d}/{part.noise_total:<2d}"
                          f"{'':9s}{part.misses:2d}/{part.signal_total:<2d}"
                          f"{'':7s}{part.correct:3d}/{part.total}")


REPRO_COMMANDS = [
    "git clone <repo> && cd visual-testing",
    "python run.py setup",
    "",
    "# сторонние инструменты: версии прибиты в scripts/bench/package-lock.json",
    "npm ci --prefix scripts/bench",
    "node scripts/bench_pixelmatch.mjs > docs/benchmark_native.json",
    "",
    "# таблица: VisTest + нативные pixelmatch и Playwright на тех же PNG",
    "python tests/benchmark.py --compare --native docs/benchmark_native.json \\",
    "    --no-timing --markdown docs/benchmark.md",
]


def vistest_settings(cfg: VisTestConfig, ai: AIPipeline | None) -> list[str]:
    """Every knob the VisTest row ran with — all of DiffConfig, not a selection."""
    from dataclasses import fields

    d = cfg.diff
    diff = ", ".join(f"`{f.name}={getattr(d, f.name)!r}`" for f in fields(d))
    if ai is None:
        ai_line = "AI-слой не подключён (`--no-ai`)"
    else:
        a = ai.cfg
        ai_line = (f"`AIPipeline(AIConfig())` — как у `CheckService`: "
                   f"`gate_enabled={a.gate_enabled!r}`, "
                   f"`perceptual_enabled={a.perceptual_enabled!r}`, "
                   f"`attribution_enabled={a.attribution_enabled!r}` "
                   "(атрибуция ищет селектор по DOM и на вердикт не влияет)")
    return [
        f"пресет `{cfg.preset}` — значение по умолчанию, `vistest.yaml` не читается",
        f"`DiffConfig`: {diff}",
        ai_line,
        "политика: падение, если есть регион с `severity ≥ fail_severity`, "
        "или изменённая площадь выше порога, или изменился размер",
    ]


def print_port_check(scores: list[Score], ports: list[Score],
                     cases: list[cp.Case]) -> None:
    """Порт против оригинала, по кейсам. Только консоль: публикуется оригинал."""
    print("\n=== Порт против оригинала ===")
    for port in ports:
        nat = next(s for s in scores if s.native and s.key == port.key)
        differ = [c.name for c in cases
                  if port.per_case.get(c.name) != nat.per_case.get(c.name)]
        print(f"{port.title} (порт) → {nat.title}: "
              f"верно {port.correct} → {nat.correct}, "
              f"ложных {port.false_fails} → {nat.false_fails}, "
              f"пропусков {port.misses} → {nat.misses}; "
              f"вердикты расходятся на {len(differ)} из {len(cases)}"
              + (": " + ", ".join(differ) if differ else ""))


#: Friendly names for the metrics that carry a floor, in the order the table
#: prints them.
_FLOOR_TITLES = {
    "severity": "severity",
    "changed_area_pct": "changed area",
    "de_mean": "ΔE00",
    "ssim": "SSIM",
}


def _frozen_answer_paragraph() -> str:
    """What the recorded answer promises, written from the code that enforces it.

    Generated rather than typed. A published paragraph claiming a tolerance
    the tests do not hold would be the same defect the engine just removed
    from `suppressed_by`: a sentence true everywhere except in its digits.
    """
    floors = cp.METRICS_TOLERANCE
    listed = ", ".join(f"{_FLOOR_TITLES.get(k, k)} {f.tolerance:.0e}"
                       for k, f in floors.items())
    envs = max(f.environments for f in floors.values())
    when = max(f.measured for f in floors.values())
    return (
        "`tests/benchmark_corpus/metrics.json` holds the engine's answer next "
        "to the frozen question, and `tests/test_corpus_frozen.py` replays it "
        "on every run. Verdicts are strict: not one of the 54 may move, on any "
        "machine. Region counts are strict too, and so is every rule the "
        "engine names when it suppresses a difference — the sentence without "
        "its pixel counts, which ride the change mask and move with it. The "
        f"continuous metrics carry a tolerance that was measured rather than "
        f"chosen ({listed}): each one sits above the widest spread seen across "
        f"{envs} environments on {when}, and that measurement is recorded in "
        "the file beside the number, so a red line can say whether a drift "
        "looks like a new machine or like a changed engine. All "
        f"{envs} environments are x86-64 — the published tolerances are "
        "measured there and nowhere else, and `tests/corpus.py` says what to "
        "do before widening them. The verdicts above were additionally "
        "reproduced on Linux and on Windows, under both OpenCV majors."
    )


def markdown(scores: list[Score], cases: list[cp.Case], cfg: VisTestConfig,
             native_used: bool, *, native: dict | None = None,
             ai: AIPipeline | None = None, timing: bool = True) -> str:
    from datetime import date

    native = native or {}
    L: list[str] = []
    L.append("# Бенчмарк движка сравнения")
    L.append("")
    L.append(f"Сгенерировано `python tests/benchmark.py --compare"
             f"{' --native docs/benchmark_native.json' if native_used else ''}"
             f"{'' if timing else ' --no-timing'}"
             f" --markdown` {date.today().isoformat()}, {environment_line()}, "
             f"preset `{cfg.preset}`.")
    L.append("")
    L.append("The corpus is synthetic and frozen in the repository as PNG files "
             "(`tests/benchmark_corpus/`); it is not drawn at run time, so the "
             "installed OpenCV does not change what is measured — and neither "
             "does the engine. Every stage that moves a picture by a fraction of "
             "a pixel goes through `core/warp.py`, which is numpy and not "
             "`cv2.warpAffine`, so the detailed table is identical under "
             "opencv-python-headless 4.14.0.94 and 5.0.0.93: not the verdicts "
             "alone, but every metric of all 54 pairs. Figures "
             "published before the freeze were tied to the OpenCV version of "
             "whoever ran them and are not comparable with these — see "
             "CHANGELOG.")
    L.append("")
    L.append(_frozen_answer_paragraph())
    L.append("")
    L.append("Every tool runs with the settings a user gets after installing it "
             "and changing nothing — VisTest included. The exact values are "
             "listed under «Настройки». Nothing was tuned for this table.")
    L.append("")

    L.append("## Результат")
    L.append("")
    L.append("| Инструмент | Ложные падения | Пропущенные регрессы | Верно | мс |")
    L.append("|---|---|---|---|---|")
    for s in scores:
        mark = "" if s.native else " ⁽ᵖ⁾"
        # A native row is timed in another process, a port row is not the tool.
        ms = f"{s.ms:.0f}" if timing and s.ms and s.native else "—"
        L.append(
            f"| {s.title}{mark} | "
            f"{s.false_fails}/{s.noise_total} ({s.false_fail_rate * 100:.0f}%) | "
            f"{s.misses}/{s.signal_total} ({s.miss_rate * 100:.0f}%) | "
            f"{s.correct}/{s.total} | {ms} |")
    L.append("")
    L.append("By corpus raster (correct · false failures · misses):")
    L.append("")
    L.append("| Tool | " + " | ".join(f"`{r.key}`" for r in cp.RENDERS) + " |")
    L.append("|---|" + "---|" * len(cp.RENDERS))
    for s in scores:
        parts = dict(by_render(cases, s))
        cells = []
        for r in cp.RENDERS:
            x = parts.get(r)
            cells.append("—" if x is None else
                         f"{x.correct}/{x.total} · {x.false_fails}/{x.noise_total}"
                         f" · {x.misses}/{x.signal_total}")
        L.append(f"| {s.title} | " + " | ".join(cells) + " |")
    L.append("")
    if any(not s.native for s in scores):
        L.append("⁽ᵖ⁾ — **не оригинальный код**, а наш порт ядра на numpy "
                 "(`tests/baselines.py`), без детектора анти-алиасинга. Такую "
                 "строку публиковать нельзя: это наше представление о "
                 "конкуренте. Нативный прогон — «Как воспроизвести».")
        L.append("")
    if native_used:
        L.append("Строки pixelmatch и Playwright посчитаны **их собственным "
                 "кодом** на тех же PNG (`scripts/bench_pixelmatch.mjs`, "
                 "результат — `docs/benchmark_native.json`, отпечаток корпуса "
                 f"`{(native.get('corpus') or {}).get('sha256', '?')[:16]}…` "
                 "сверяется при чтении). Время в них не мерилось: другой "
                 "процесс, другой язык — цифра была бы не про алгоритм.")
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

    L.append("## Настройки")
    L.append("")
    L.append("Без этого таблица ничего не значит. Всё — значения по умолчанию; "
             "ни одна опция не передавалась ни одному инструменту.")
    L.append("")
    tools = native.get("tools") or {}
    for s in scores:
        L.append(f"**{s.title}**{'' if s.native else ' ⁽ᵖ⁾'}")
        L.append("")
        if not s.key:
            items = vistest_settings(cfg, ai)
        else:
            if s.native and s.key in tools:
                t = tools[s.key]
                items = [f"вызов: {t['invoked']}"]
                items += [f"{k}: {v}" for k, v in t.get("settings", {}).items()]
            elif s.key == "absdiff":
                items = ["весь алгоритм самописного скрипта, воспроизводить "
                         "нечего: `tests/baselines.py::absdiff`",
                         "`tolerance=0`: любой отличающийся канал любого "
                         "пикселя валит тест; разный размер — падение"]
            else:
                items = [f"порт: {s.note}"]
        L += [f"- {x}" for x in items]
        L.append("")

    L.append("## Версии")
    L.append("")
    L.append("| Что | Версия |")
    L.append("|---|---|")
    for k, v in python_versions().items():
        L.append(f"| {k} | {v} |")
    env = native.get("environment") or {}
    if env:
        L.append(f"| Node.js | {env.get('node', '?')} |")
        for k, v in (env.get("packages") or {}).items():
            L.append(f"| npm `{k}` | {v} |")
    L.append("")
    if env:
        L.append("npm-версии прибиты в `scripts/bench/package.json` и "
                 "`scripts/bench/package-lock.json`; `npm ci` ставит ровно их.")
        L.append("")

    L.append("## Корпус")
    L.append("")
    L.append(f"{len(cases)} пар: "
             f"{sum(1 for c in cases if c.group == 'NOISE')} NOISE + "
             f"{sum(1 for c in cases if c.group == 'SIGNAL')} SIGNAL. "
             "Stored in `tests/benchmark_corpus/` (PNG + `manifest.json`), "
             "drawn by `tests/synthetic.py` and frozen: "
             "`python tests/benchmark.py --regenerate` redraws them on purpose, "
             "and `tests/test_corpus_frozen.py` fails when the code starts "
             "drawing something other than what is on disk.")
    L.append("")
    L.append("The pages are drawn by OpenCV, and OpenCV 5 rasterises text "
             "differently from 4.x: every pair differs. So both rasters are "
             "frozen, as separate cases; keeping one would be choosing the "
             "convenient picture.")
    L.append("")
    L.append("| Raster | Drawn with | Case names | What differs |")
    L.append("|---|---|---|---|")
    for r in cp.RENDERS:
        suffix = f"`…{r.suffix}`" if r.suffix else "no suffix"
        L.append(f"| `{r.key}` | `{r.package}` | {suffix} | {r.note} |")
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
        if not s.key:
            kind = "наш код"
        elif s.key == "absdiff":
            kind = "наш код: это и есть весь алгоритм"
        else:
            kind = "оригинальный код" if s.native else "**порт на numpy**"
        L.append(f"| {s.title} | {s.note or '—'} | {kind} |")
    L.append("")
    L.append("BackstopJS в таблице нет намеренно: воспроизводить поведение "
             "resemble.js по памяти мы не стали, а нативного раннера пока не "
             "написали. Появится — добавим строку.")
    L.append("")

    L.append("## Как воспроизвести")
    L.append("")
    L.append("```bash")
    L += REPRO_COMMANDS
    L.append("```")
    L.append("")
    L.append("Нужны Python ≥ 3.10 и Node.js ≥ 18; браузер для этой таблицы не "
             "нужен. `--no-timing` убирает единственное, что меняется от "
             "прогона к прогону: консольный вывод воспроизводится побайтно, "
             "а в этом файле от машины к машине меняются только дата и "
             "строки окружения. Без `--native` строки конкурентов считает порт на "
             "numpy — он помечен в таблице как порт и годится только для "
             "быстрой проверки без Node.")
    L.append("")

    L.append("## Что эта таблица не доказывает")
    L.append("")
    L.append("- Корпус синтетический. Он проверяет, что движок отличает "
             "известные виды шума от известных видов регресса, а не то, как "
             "он поведёт себя на вашем приложении.")
    L.append("- The environment line at the top is there so that the run can "
             "be repeated, not because the figures depend on it. The one "
             "column that does depend on the machine is the milliseconds, and "
             "`--no-timing` leaves it out.")
    L.append("- The tolerances the other columns are checked under were "
             "measured on x86-64 and on nothing else. A machine of another "
             "architecture is outside that sample, so a red line there is not "
             "yet evidence of anything; `tests/corpus.py` says how to tell the "
             "two apart and what to measure before widening a number.")
    if native_used:
        L.append("- У Playwright сравниваются готовые снимки. Съёмка "
                 "`toHaveScreenshot()` (отключение анимаций, скрытие каретки, "
                 "повторные кадры до стабильности) здесь не участвует — как "
                 "и стабилизация съёмки у VisTest: корпус уже снят.")
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
                    help="без AI-слоя: голый компаратор. С гейтом, выключенным "
                         "по умолчанию, обязан дать те же цифры — расхождение "
                         "значит, что слой включается в обход конфига")
    ap.add_argument("--native", metavar="JSON",
                    help="результаты настоящих pixelmatch и Playwright из "
                         "scripts/bench_pixelmatch.mjs; заменяют порты")
    ap.add_argument("--with-ports", action="store_true",
                    help="вместе с --native: напечатать и строки портов, чтобы "
                         "сверить их с оригиналом. В markdown не попадают")
    ap.add_argument("--export", metavar="DIR",
                    help="выгрузить корпус в PNG для чужих инструментов")
    ap.add_argument("--no-timing", action="store_true",
                    help="do not print timings: without them the output is "
                         "reproducible byte for byte and can be diffed "
                         "between machines")
    ap.add_argument("--record-metrics", action="store_true",
                    help="rewrite tests/benchmark_corpus/metrics.json — the "
                         "detailed table frozen as data, which "
                         "tests/test_corpus_frozen.py checks on every run")
    ap.add_argument("--regenerate", action="store_true",
                    help="redraw the frozen corpus with the installed OpenCV "
                         "(its own raster only) and exit. A deliberate act: "
                         "the benchmark figures have to be re-checked and "
                         "re-published after it")
    ap.add_argument("--markdown", metavar="FILE",
                    help="записать таблицу в markdown")
    args = ap.parse_args()

    cfg = VisTestConfig.preset_of(args.preset)
    timing = not args.no_timing

    if args.record_metrics:
        path, doc = cp.record_metrics()
        print(f"Recorded the detailed table for {len(doc['cases'])} pairs "
              f"(preset={doc['preset']}, AI layer on): {path}")
        print("The numbers are the same on every OpenCV; `git diff` on this "
              "file is the review of what the change did to each pair.")
        return 0

    if args.regenerate:
        render, drawn = cp.regenerate()
        print(f"Redrew raster {render.key} ({len(drawn)} pairs) with "
              f"{environment_line()}: {cp.FROZEN_DIR}")
        others = [r for r in cp.RENDERS if r is not render]
        if others:
            print("Other rasters are untouched; only their own OpenCV can "
                  "redraw them: " + ", ".join(
                      f"{r.key} with {r.package}" for r in others))
        print("Next: git diff --stat tests/benchmark_corpus, a benchmark run, "
              "and the new figures in README.")
        return 0

    # Before the corpus is decoded: a JSON from other files is refused fast.
    native: dict = {}
    if args.native:
        try:
            native = load_native(args.native)
        except NativeError as e:
            print(f"--native: {e}", file=sys.stderr)
            return 2
    elif args.with_ports:
        print("--with-ports имеет смысл только вместе с --native", file=sys.stderr)
        return 2

    cases = cp.build()
    # По умолчанию меряем то, что реально уезжает покупателю: CheckService
    # собирает пайплайн на каждой проверке, и бенчмарк обязан мерить ту же
    # сборку. Гейт при этом выключен (`ai.gate_enabled: false`) — значит
    # `--no-ai` обязан дать те же цифры. Если не даёт, AI-слой включается
    # где-то в обход конфига, и это баг, а не настройка бенчмарка.
    ai = None if args.no_ai else AIPipeline(cfg.ai)
    out = Path(args.out)
    artifacts_dir = out if args.artifacts else None

    if args.export:
        path = cp.export_corpus(cases, args.export)
        print(f"Корпус выгружен: {path.resolve()}  ({len(cases)} пар)")
        print("Нативный прогон читает сам tests/benchmark_corpus; копия "
              "нужна только чужим инструментам. Своим прогоном: "
              f"node scripts/bench_pixelmatch.mjs {args.export} > "
              "bench_out/native.json")
        return 0

    if not args.compare:
        s = print_detail(cases, cfg, artifacts_dir, ai=ai, timing=timing)
        if args.artifacts:
            print(f"Артефакты: {out.resolve()}")
        return 1 if s.correct != s.total else 0

    scores = [score_vistest(cases, cfg, artifacts_dir=artifacts_dir, ai=ai)]
    ports: list[Score] = []

    for engine in bl.ENGINES:
        if not engine.native and native:
            nat = score_native(cases, native, engine.key,
                               title=engine.title, note=engine.note)
            if nat is None:
                # Honest fallback: the row stays a port and says so.
                print(f"--native: no complete results for {engine.key!r}; "
                      "this row is the numpy port", file=sys.stderr)
            else:
                scores.append(nat)
                if args.with_ports:
                    ports.append(score_engine(cases, engine))
                continue
        scores.append(score_engine(cases, engine))

    print_comparison(scores + ports, cases, timing=timing)
    if ports:
        print_port_check(scores, ports, cases)

    if args.markdown:
        path = Path(args.markdown)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown(scores, cases, cfg, bool(native),
                                 native=native, ai=ai, timing=timing),
                        encoding="utf-8", newline="\n")
        print(f"\nMarkdown: {path.resolve()}")

    vt = scores[0]
    return 1 if vt.correct != vt.total else 0


if __name__ == "__main__":
    raise SystemExit(main())
