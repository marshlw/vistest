# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Корпус для бенчмарка: пары «эталон / прогон» с известным правильным ответом.

Зачем синтетика. На настоящих скриншотах неизвестно, что «правда»: разметка
делается человеком, а человек в спорных случаях размечает так, как ему удобно.
Здесь ответ задан построением: мы сами решили, где регресс, а где шум, и это
решение можно оспорить, глядя на код, а не на нашу добросовестность.

Две группы, и ошибки в них стоят по-разному:

* **NOISE** — страница не менялась по существу. Падение здесь — ложное.
  Именно ложные падения убивают доверие: команда через месяц ставит
  `--update-snapshots` в CI, и визуальное тестирование заканчивается.
* **SIGNAL** — реальный регресс. Пропуск здесь — пропущенный баг.

The curated corpus lives on disk, it is not drawn at run time
------------------------------------------------------------

`tests/benchmark_corpus/` holds PNG pairs and a `manifest.json`, in the format
`export_corpus` writes. `build()` reads from there and draws nothing.

The reason is OpenCV. `synthetic.py` draws the pages with `cv2`, mostly
`cv2.putText`, and OpenCV 5 rasterises text differently from 4.x: every pair
of the corpus differs between the two. While the corpus was drawn on every run,
the benchmark figure was tied to whichever OpenCV the person running it had,
and two figures taken on two machines were not comparable. So the input is
frozen; OpenCV is not pinned.

Both rasters are frozen, as separate cases — `RENDERS` below. Keeping one of
them would be choosing the convenient picture. Cases of the second raster carry
a suffix that says what differs (`, thin glyphs`); `family` is the same for
both, because it is one phenomenon drawn twice.

The generator stays, and redrawing is a deliberate act:

    python tests/benchmark.py --regenerate      # under each version in RENDERS

`tests/test_corpus_frozen.py` fails when the code no longer draws what is on
disk, and names the pairs. Without it the corpus would move silently with the
next OpenCV upgrade.

The corpus can be exported anywhere (`export_corpus`) so that other tools score
exactly the same pairs. Without that, comparing engines means nothing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

from . import synthetic as syn


@dataclass
class Case:
    name: str
    group: str          # NOISE | SIGNAL
    expected_fail: bool
    expected: np.ndarray
    actual: np.ndarray
    why: str = ""       # почему ответ именно такой
    # Маска настоящего изменения — только для SIGNAL. Нужна, чтобы размечать
    # регионы поштучно: рядом с настоящим регрессом на странице попадаются и
    # шумовые регионы, и называть их сигналом значит учить модель неправде.
    change_mask: np.ndarray | None = None
    family: str = ""    # явление, а не случай: «jpeg» для всех вёрсток сразу
    # Which raster the pair was drawn with: a key of RENDERS. Curated corpus
    # only; the training corpus leaves it empty, it is drawn by what is installed.
    render: str = ""


# --------------------------------------------------------------------------- #
#  The frozen corpus
# --------------------------------------------------------------------------- #
FROZEN_DIR = Path(__file__).resolve().parent / "benchmark_corpus"


@dataclass(frozen=True)
class Render:
    """One raster of the curated corpus: what drew it, and how its names show it."""

    key: str            # the `render` field in manifest.json
    opencv: str         # major.minor that draws this raster
    package: str        # the exact version the files were written with
    suffix: str         # appended to the case name; empty for the original raster
    note: str           # what is different about it, for a human


RENDERS: tuple[Render, ...] = (
    Render("opencv-4.14", "4.14", "opencv-python-headless==4.14.0.94", "",
           "original raster: text as OpenCV 4.x draws it"),
    Render("opencv-5.0", "5.0", "opencv-python-headless==5.0.0.93", ", thin glyphs",
           "OpenCV 5 raster: the same strings, with thinner, lighter glyph strokes"),
)


class CorpusError(RuntimeError):
    """The frozen corpus is missing, empty, or cannot be redrawn here."""


def installed_opencv() -> str:
    """major.minor of the installed OpenCV: what selects the raster."""
    import cv2

    return ".".join(cv2.__version__.split(".")[:2])


def render_for(opencv: str | None = None) -> Render | None:
    """The raster the given (by default, the installed) OpenCV draws."""
    opencv = opencv or installed_opencv()
    return next((r for r in RENDERS if r.opencv == opencv), None)


def build(path: str | Path | None = None) -> list[Case]:
    """The curated benchmark corpus, from disk, every raster.

    Draws nothing: the benchmark figure must not depend on which OpenCV the
    person running it has installed. To redraw, `regenerate()`.
    """
    return load(path)


def load(path: str | Path | None = None) -> list[Case]:
    """The inverse of `export_corpus`: PNG pairs and manifest.json -> cases."""
    from vistest.core import pngio

    root = Path(path) if path is not None else FROZEN_DIR
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise CorpusError(
            f"{manifest_path} does not exist. The benchmark corpus is kept in "
            "the repository; if it really has to be drawn again, run "
            "`python tests/benchmark.py --regenerate`")
    manifest = json.loads(manifest_path.read_text("utf-8"))

    cases: list[Case] = []
    for entry in manifest["cases"]:
        cases.append(Case(
            name=entry["name"],
            group=entry["group"],
            expected_fail=bool(entry["expected_fail"]),
            expected=pngio.read(root / entry["expected"]),
            actual=pngio.read(root / entry["actual"]),
            why=entry.get("why", ""),
            family=entry.get("family", entry["name"]),
            render=entry.get("render", ""),
        ))
    if not cases:
        raise CorpusError(f"{manifest_path}: the manifest lists no cases")
    return cases


def generate_curated(render: Render | None = None) -> list[Case]:
    """Draw the curated corpus with the installed OpenCV.

    Names carry the raster suffix, so they can be matched against the files.
    A raster missing from RENDERS is not drawn: which suffix a new raster gets
    is for a person to decide, not the generator.
    """
    render = render or render_for()
    if render is None:
        raise CorpusError(
            f"OpenCV {installed_opencv()} is not described in "
            "tests/corpus.py::RENDERS. It is a new raster: add it there, with "
            "a name suffix that says what is different about it, and only "
            "then redraw.")
    return [replace(c, name=c.name + render.suffix, render=render.key)
            for c in _draw_curated()]


def drift(path: str | Path | None = None) -> tuple[Render | None, list[str]]:
    """Which pairs on disk no longer match what the code draws now.

    Pixels are compared, not PNG bytes: the encoder changes between OpenCV
    versions too, and that is not a change of the corpus. The comparison is
    against the raster of the installed version; if RENDERS has none,
    the result is `(None, every name)`.
    """
    render = render_for()
    frozen = {c.name: c for c in load(path)}
    if render is None:
        return None, sorted(frozen)
    drawn = generate_curated(render)
    mine = {n for n, c in frozen.items() if c.render == render.key}

    drifted: list[str] = []
    for c in drawn:
        f = frozen.get(c.name)
        if f is None or f.render != render.key or not _same(f, c):
            drifted.append(c.name)
    #  A case on disk that the code no longer draws is drift as well.
    drifted += sorted(mine - {c.name for c in drawn})
    return render, drifted


def regenerate(path: str | Path | None = None) -> tuple[Render, list[Case]]:
    """Redraw the raster of the installed OpenCV and write it to disk.

    Cases of the other rasters are left alone, files and manifest entries
    both: only their own OpenCV version can redraw them.
    """
    root = Path(path) if path is not None else FROZEN_DIR
    render = render_for()
    cases = generate_curated(render)

    kept: list[dict] = []
    manifest_path = root / "manifest.json"
    if manifest_path.is_file():
        old = json.loads(manifest_path.read_text("utf-8"))
        kept = [e for e in old["cases"] if e.get("render") != render.key]

    root.mkdir(parents=True, exist_ok=True)
    fresh = [_write_case(root, c) for c in cases]
    order = {r.key: i for i, r in enumerate(RENDERS)}
    entries = sorted(kept + fresh, key=lambda e: order.get(e.get("render"), len(order)))
    _write_manifest(root, entries)
    return render, cases


def _same(a: Case, b: Case) -> bool:
    return (a.group == b.group and a.expected_fail == b.expected_fail
            and a.expected.shape == b.expected.shape
            and a.actual.shape == b.actual.shape
            and np.array_equal(a.expected, b.expected)
            and np.array_equal(a.actual, b.actual))


# --------------------------------------------------------------------------- #
#  What is drawn: sixteen noise pages and eleven regressions, per raster
# --------------------------------------------------------------------------- #
def _draw_curated() -> list[Case]:
    base = syn.page()

    noise = [
        ("identical", base.copy(),
         "byte for byte: any failure here is an engine defect"),
        ("sensor noise σ=1.6", syn.add_sensor_noise(base, 1.6, seed=1),
         "±2 brightness levels, ΔE00 < 1: invisible to a human in principle"),
        ("sensor noise σ=3.0", syn.add_sensor_noise(base, 3.0, seed=2),
         "the upper bound of codec noise"),
        ("antialias 0.4px", syn.resample_antialias(base, 0.4),
         "different sub-pixel text rendering: a browser upgrade or hinting"),
        ("jpeg q=88", syn.recompress(base, 88),
         "re-compression in the screenshot pipeline"),
        ("jpeg q=75", syn.recompress(base, 75),
         "aggressive re-compression, still not a layout regression"),
        ("global shift 1px", syn.shift(base, 1, 1),
         "the whole page moved by a pixel: a scroll bar, rounding"),
        ("global shift 3px", syn.shift(base, 3, 2),
         "the same, larger: still a whole-page shift, not a layout change"),
        ("combined", syn.recompress(
            syn.add_sensor_noise(syn.resample_antialias(syn.shift(base, 1, 0)), 1.2),
            90),
         "all of it at once: what a real run in somebody else's CI looks like"),
        # Ниже — шум, который классические фильтры проходят труднее. Первые
        # девять случаев движок гасит на дефолтных порогах начисто, и это
        # правильно; но тогда на них не видно ни разницы между движками, ни
        # материала для обучающегося гейта.
        ("font fallback", syn.font_fallback(base),
         "a fallback font: the same letters, heavier strokes"),
        ("shadow radius", syn.blur_shadows(base),
         "a shadow recomputed with a different blur radius"),
        ("gradient dither", syn.dither_gradient(base),
         "different gradient dithering: visible to arithmetic, not to the eye"),
        ("scrollbar", syn.scrollbar(base),
         "a scroll bar appeared: that is the environment, not the layout"),
        ("caret", syn.caret(base),
         "a blinking caret in an input field caught in the frame"),
        ("lazy placeholder", syn.lazy_placeholder(base),
         "an image had not loaded yet: the shot was early, the page is intact"),
        ("subpixel text", syn.subpixel_text(base),
         "a sub-pixel shift of text lines, not of the whole page"),
    ]

    signal = [
        ("button color", syn.regress_button_color(base),
         "the buy button turned grey: the colour changed, the structure did not"),
        ("button removed", syn.regress_button_removed(base),
         "the button is gone: the worst visual bug there is"),
        ("promo removed", syn.regress_promo_gone(base),
         "a block disappeared: a large change, no excuse for missing it"),
        ("layout +40px", syn.regress_layout_moved(base, 40),
         "content moved by 40px: the layout broke"),
        ("page taller +160", syn.regress_taller_page(base, 160),
         "the page grew taller: a common and costly regression"),
        ("tiny icon 22px", syn.regress_tiny_icon(base),
         "a 22×22 icon: checks that small things do not drown in the filters"),
        ("price changed", syn.regress_price_changed(base),
         "a different price: a small area, an expensive mistake"),
        ("button shrunk", syn.regress_button_shrunk(base),
         "the button got narrower: the size changed, not the colour"),
        ("text overflow", syn.regress_text_overflow(base),
         "text overflows its card: the usual result of a font change"),
        ("header color", syn.regress_header_color(base),
         "the header changed colour: a flat fill, the same structure"),
        ("promo text", syn.regress_promo_text(base),
         "a different promo code: one line, but a real content error"),
    ]

    cases = [Case(n, "NOISE", False, base, a, w, family=n) for n, a, w in noise]
    cases += [Case(n, "SIGNAL", True, base, a, w, family=n) for n, a, w in signal]
    return cases


# --------------------------------------------------------------------------- #
def export_corpus(cases: list[Case], out: str | Path) -> Path:
    """Разложить корпус на диск: PNG-пары + manifest.json.

    Нужен, чтобы pixelmatch, Playwright и кто угодно ещё считали ровно те же
    изображения. Сравнение движков на разных данных — это не сравнение.
    The inverse is `load()`.
    """
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    _write_manifest(out, [_write_case(out, c) for c in cases])
    return out


def _write_case(out: Path, c: Case) -> dict:
    from vistest.capture.playwright_capture import _write_png

    slug = _slug(c.name)
    d = out / slug
    d.mkdir(exist_ok=True)
    _write_png(d / "expected.png", c.expected)
    _write_png(d / "actual.png", c.actual)
    entry = {
        "name": c.name,
        "slug": slug,
        "group": c.group,
        "expected_fail": c.expected_fail,
        "why": c.why,
        "expected": f"{slug}/expected.png",
        "actual": f"{slug}/actual.png",
    }
    if c.family:
        entry["family"] = c.family
    if c.render:
        entry["render"] = c.render
    return entry


def _write_manifest(out: Path, entries: list[dict]) -> None:
    renders = {e["render"] for e in entries if e.get("render")}
    doc: dict = {}
    if renders:
        doc["renders"] = {r.key: {"opencv": r.package, "suffix": r.suffix,
                                  "note": r.note}
                          for r in RENDERS if r.key in renders}
    doc["cases"] = entries
    (out / "manifest.json").write_text(
        json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8", newline="\n")


def _slug(s: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in s).strip("_")


# --------------------------------------------------------------------------- #
#  Генератор: вёрстки × явления
#
#  Отобранный корпус выше остаётся тем, что показывают человеку: пятнадцать
#  строк, каждую можно прочитать и оспорить. Учить на нём нельзя — по одному
#  примеру на явление и одна-единственная вёрстка. Генератор перемножает
#  параметризованные макеты на те же явления и даёт сотни пар.
# --------------------------------------------------------------------------- #
def _change_mask(expected: np.ndarray, actual: np.ndarray) -> np.ndarray:
    """Где страница изменилась по существу.

    Порог грубый и такой и должен быть: настоящий регресс — это другой цвет,
    другой текст, сдвинутый блок, то есть десятки единиц яркости. Тонкая
    градация здесь не нужна, нужна граница «здесь менялось / здесь нет».
    """
    h = max(expected.shape[0], actual.shape[0])
    w = max(expected.shape[1], actual.shape[1])
    a = np.zeros((h, w, 3), np.int16)
    b = np.zeros((h, w, 3), np.int16)
    a[:expected.shape[0], :expected.shape[1]] = expected
    b[:actual.shape[0], :actual.shape[1]] = actual
    return (np.abs(a - b).max(axis=2) > 8)


def generate(layout_count: int = 6) -> list[Case]:
    """Обучающий корпус: каждое явление на каждой вёрстке.

    Drawn on the fly, by whatever OpenCV is installed, and never frozen —
    unlike `build()`. The rules differ because the jobs differ.

    The curated corpus is a yardstick. Its figure is published and compared
    with figures taken at another time on another machine, so it has to be
    the same yardstick every time; otherwise a change in the figure is a
    change in OpenCV, not in the engine.

    The training corpus is material. The model is better off seeing more
    variants of one phenomenon, and another OpenCV version's raster is one
    more variant, not damage. Reproducibility of training rests elsewhere:
    the model is a file in the repository (`vistest/ai/region_gate.json`),
    and retraining is as deliberate an act as `--regenerate`. Freezing
    hundreds of pairs so that the model learns exactly one raster would be
    a bad trade.
    """
    cases: list[Case] = []
    for lay in syn.layouts(layout_count):
        base = syn.render(lay)

        for family, make in syn.NOISE_TRANSFORMS.items():
            actual = make(lay, base)
            if actual.shape == base.shape and not (actual != base).any():
                continue                      # преобразование ничего не сделало
            cases.append(Case(f"{lay.name}/{family}", "NOISE", False,
                              base, actual, family=family))

        for family, make in syn.SIGNAL_TRANSFORMS.items():
            actual = make(lay, base)
            if actual.shape == base.shape and not (actual != base).any():
                # На этой вёрстке менять нечего: промо-блока нет, карточки нет.
                continue
            cases.append(Case(f"{lay.name}/{family}", "SIGNAL", True,
                              base, actual, family=family,
                              change_mask=_change_mask(base, actual)))
    return cases
