# Бенчмарк движка сравнения

Сгенерировано `python tests/benchmark.py --compare --native docs/benchmark_native.json --no-timing --markdown` 2026-11-23, OpenCV 5.1.0, numpy 2.5.5, Python 3.12.0, Windows AMD64, preset `balanced`.

The corpus is synthetic and frozen in the repository as PNG files (`tests/benchmark_corpus/`); it is not drawn at run time, so the installed OpenCV does not change what is measured — and neither does the engine. `tests/test_corpus_frozen.py` checks each engine verdict, region count and suppression explanation against the frozen table and compares continuous metrics with measured tolerances. Figures published before the freeze were tied to the OpenCV version of whoever ran them and are not comparable with these — see CHANGELOG.

`tests/benchmark_corpus/metrics.json` holds the engine's answer next to the frozen question, and `tests/test_corpus_frozen.py` replays it on every run. Verdicts are strict: every case must agree, on any machine. Region counts are strict too, and so is every rule the engine names when it suppresses a difference — the sentence without its pixel counts, which ride the change mask and move with it. The continuous metrics carry a tolerance that was measured rather than chosen (severity 1e-04, changed area 5e-03, ΔE00 2e-02, SSIM 1e-04): each one sits above the widest spread seen across 25 environments on 2026-09-18, and that measurement is recorded in the file beside the number, so a red line can say whether a drift looks like a new machine or like a changed engine. All 25 environments are x86-64 — the published tolerances are measured there and nowhere else, and `tests/corpus.py` says what to do before widening them.

Every tool runs with the settings a user gets after installing it and changing nothing — VisTest included. The exact values are listed under «Настройки». Nothing was tuned for this table.

## Результат

| Инструмент | Ложные падения | Пропущенные регрессы | Верно | мс |
|---|---|---|---|---|
| VisTest (balanced) | 0/48 (0%) | 0/33 (0%) | 81/81 | — |
| absdiff (самопис) | 45/48 (94%) | 0/33 (0%) | 36/81 | — |
| pixelmatch 7.2.0 | 27/48 (56%) | 3/33 (9%) | 51/81 | — |
| Playwright 1.63.0 toHaveScreenshot() | 20/48 (42%) | 3/33 (9%) | 58/81 | — |

By corpus raster (correct · false failures · misses):

| Tool | `opencv-4.14` | `opencv-5.0` | `opencv-5.1` |
|---|---|---|---|
| VisTest (balanced) | 27/27 · 0/16 · 0/11 | 27/27 · 0/16 · 0/11 | 27/27 · 0/16 · 0/11 |
| absdiff (самопис) | 12/27 · 15/16 · 0/11 | 12/27 · 15/16 · 0/11 | 12/27 · 15/16 · 0/11 |
| pixelmatch 7.2.0 | 17/27 · 9/16 · 1/11 | 17/27 · 9/16 · 1/11 | 17/27 · 9/16 · 1/11 |
| Playwright 1.63.0 toHaveScreenshot() | 19/27 · 7/16 · 1/11 | 19/27 · 7/16 · 1/11 | 20/27 · 6/16 · 1/11 |

Строки pixelmatch и Playwright посчитаны **их собственным кодом** на тех же PNG (`scripts/bench_pixelmatch.mjs`, результат — `docs/benchmark_native.json`, отпечаток корпуса `6fc52d5d6023896f…` сверяется при чтении). Время в них не мерилось: другой процесс, другой язык — цифра была бы не про алгоритм.

## Что означают колонки

**Ложные падения** — доля страниц из группы NOISE, на которых инструмент дал красный тест. Страница по существу не менялась, поэтому каждое такое падение ложное. Это главная метрика: именно от ложных падений команда через месяц ставит `--update-snapshots` в CI, что равносильно выключению визуального тестирования.

**Пропущенные регрессы** — доля группы SIGNAL, где инструмент остался зелёным. Смотреть только на первую колонку нельзя: инструмент, который не падает никогда, идеален по ложным падениям и бесполезен.

## Настройки

Без этого таблица ничего не значит. Всё — значения по умолчанию; ни одна опция не передавалась ни одному инструменту.

**VisTest (balanced)**

- пресет `balanced` — значение по умолчанию, `vistest.yaml` не читается
- `DiffConfig`: `delta_e_threshold=2.3`, `ssim_threshold=0.9`, `require_consensus=True`, `flat_recolour=True`, `flat_recolour_min_area_pct=1.25`, `flat_recolour_max_cv=0.09`, `align_enabled=True`, `max_align_shift_px=8.0`, `antialias_filter=True`, `aa_tolerance=0.1`, `morph_open_px=2`, `morph_close_px=6`, `min_region_px=24`, `min_region_fill=0.06`, `max_regions=200`, `explain_noise=True`, `detect_moved=True`, `move_search_px=64`, `move_match_threshold=0.93`, `fail_severity=25.0`, `max_changed_area_pct=0.15`, `area_requires_region=True`, `area_hard_fail_pct=5.0`, `above_fold_px=900`, `above_fold_weight=1.5`, `ignore_kinds=('noise', 'antialias')`, `moved_severity_scale=0.35`, `size_tolerance_px=0`, `fail_on_size_change=True`, `max_pixels=80000000`, `engine='v2'`, `threshold_source=''`, `area_source=''`, `preset='balanced'`
- `AIPipeline(AIConfig())` — как у `CheckService`: `gate_enabled=False`, `perceptual_enabled=False`, `attribution_enabled=True` (атрибуция ищет селектор по DOM и на вердикт не влияет)
- политика: падение, если есть регион с `severity ≥ fail_severity`, или изменённая площадь выше порога, или изменился размер

**absdiff (самопис)**

- весь алгоритм самописного скрипта, воспроизводить нечего: `tests/baselines.py::absdiff`
- `tolerance=0`: любой отличающийся канал любого пикселя валит тест; разный размер — падение

**pixelmatch 7.2.0**

- вызов: npm package CLI: pixelmatch <expected.png> <actual.png>
- threshold: 0.1 (package default, not passed)
- includeAA: false (package default: anti-aliasing detector on)
- fail when: CLI exit code != 0: any differing pixel (66) or size mismatch (65)

**Playwright 1.63.0 toHaveScreenshot()**

- вызов: playwright-core 1.63.0 playwright-core/lib/coreBundle: getComparator('image/png')(actual, expected, options) — the call Page.expectScreenshot makes for toHaveScreenshot()
- comparator: "pixelmatch" (default; option not set)
- threshold: 0.2 (default; option not set)
- includeAA: false (built into Playwright's pixelmatch copy)
- maxDiffPixels: 0 (default; option not set)
- maxDiffPixelRatio: not set
- fail when: more than 0 differing pixels, or size mismatch
- not reproduced: page capture (animations/caret/stability retries): the corpus is already captured

## Версии

| Что | Версия |
|---|---|
| Python | 3.12.0 |
| numpy | 2.5.5 |
| opencv-python-headless | 5.1.0.95 |
| pillow | 13.0.0 |
| PyYAML | 6.0.4 |
| Node.js | v22.19.0 |
| npm `pixelmatch` | 7.2.0 |
| npm `pngjs (used by pixelmatch)` | 7.0.0 |
| npm `@playwright/test` | 1.63.0 |
| npm `playwright` | 1.63.0 |
| npm `playwright-core` | 1.63.0 |

npm-версии прибиты в `scripts/bench/package.json` и `scripts/bench/package-lock.json`; `npm ci` ставит ровно их.

## Корпус

81 пар: 48 NOISE + 33 SIGNAL. Stored in `tests/benchmark_corpus/` (PNG + `manifest.json`), drawn by `tests/synthetic.py` and frozen: `python tests/benchmark.py --regenerate` redraws them on purpose, and `tests/test_corpus_frozen.py` fails when the code starts drawing something other than what is on disk.

OpenCV 4.14, 5.0 and 5.1 produce different text rasters. The same cases are frozen separately for each raster.

| Raster | Drawn with | Case names | What differs |
|---|---|---|---|
| `opencv-4.14` | `opencv-python-headless==4.14.0.94` | no suffix | original raster: text as OpenCV 4.x draws it |
| `opencv-5.0` | `opencv-python-headless==5.0.0.93` | `…, thin glyphs` | OpenCV 5 raster: the same strings, with thinner, lighter glyph strokes |
| `opencv-5.1` | `opencv-python-headless==5.1.0.95` | `…, OpenCV 5.1` | OpenCV 5.1 raster: revised glyph rendering |

Почему синтетика, а не настоящие скриншоты: на реальных парах правильный ответ приходится размечать человеком, и в спорных случаях человек размечает так, как ему удобно. Здесь ответ задан построением — его можно оспорить, читая код, а не полагаясь на нашу добросовестность. Обратная сторона честная: синтетика не покрывает всё разнообразие реальных страниц, поэтому цифра говорит о движке, а не о вашем проекте. Про свой проект отвечает `vistest doctor`.

| Case | Group | Expected | Why |
|---|---|---|---|
| `identical` | NOISE | pass | byte for byte: any failure here is an engine defect |
| `sensor noise σ=1.6` | NOISE | pass | ±2 brightness levels, ΔE00 < 1: invisible to a human in principle |
| `sensor noise σ=3.0` | NOISE | pass | the upper bound of codec noise |
| `antialias 0.4px` | NOISE | pass | different sub-pixel text rendering: a browser upgrade or hinting |
| `jpeg q=88` | NOISE | pass | re-compression in the screenshot pipeline |
| `jpeg q=75` | NOISE | pass | aggressive re-compression, still not a layout regression |
| `global shift 1px` | NOISE | pass | the whole page moved by a pixel: a scroll bar, rounding |
| `global shift 3px` | NOISE | pass | the same, larger: still a whole-page shift, not a layout change |
| `combined` | NOISE | pass | all of it at once: what a real run in somebody else's CI looks like |
| `font fallback` | NOISE | pass | a fallback font: the same letters, heavier strokes |
| `shadow radius` | NOISE | pass | a shadow recomputed with a different blur radius |
| `gradient dither` | NOISE | pass | different gradient dithering: visible to arithmetic, not to the eye |
| `scrollbar` | NOISE | pass | a scroll bar appeared: that is the environment, not the layout |
| `caret` | NOISE | pass | a blinking caret in an input field caught in the frame |
| `lazy placeholder` | NOISE | pass | an image had not loaded yet: the shot was early, the page is intact |
| `subpixel text` | NOISE | pass | a sub-pixel shift of text lines, not of the whole page |
| `button color` | SIGNAL | fail | the buy button turned grey: the colour changed, the structure did not |
| `button removed` | SIGNAL | fail | the button is gone: the worst visual bug there is |
| `promo removed` | SIGNAL | fail | a block disappeared: a large change, no excuse for missing it |
| `layout +40px` | SIGNAL | fail | content moved by 40px: the layout broke |
| `page taller +160` | SIGNAL | fail | the page grew taller: a common and costly regression |
| `tiny icon 22px` | SIGNAL | fail | a 22×22 icon: checks that small things do not drown in the filters |
| `price changed` | SIGNAL | fail | a different price: a small area, an expensive mistake |
| `button shrunk` | SIGNAL | fail | the button got narrower: the size changed, not the colour |
| `text overflow` | SIGNAL | fail | text overflows its card: the usual result of a font change |
| `header color` | SIGNAL | fail | the header changed colour: a flat fill, the same structure |
| `promo text` | SIGNAL | fail | a different promo code: one line, but a real content error |
| `identical, thin glyphs` | NOISE | pass | byte for byte: any failure here is an engine defect |
| `sensor noise σ=1.6, thin glyphs` | NOISE | pass | ±2 brightness levels, ΔE00 < 1: invisible to a human in principle |
| `sensor noise σ=3.0, thin glyphs` | NOISE | pass | the upper bound of codec noise |
| `antialias 0.4px, thin glyphs` | NOISE | pass | different sub-pixel text rendering: a browser upgrade or hinting |
| `jpeg q=88, thin glyphs` | NOISE | pass | re-compression in the screenshot pipeline |
| `jpeg q=75, thin glyphs` | NOISE | pass | aggressive re-compression, still not a layout regression |
| `global shift 1px, thin glyphs` | NOISE | pass | the whole page moved by a pixel: a scroll bar, rounding |
| `global shift 3px, thin glyphs` | NOISE | pass | the same, larger: still a whole-page shift, not a layout change |
| `combined, thin glyphs` | NOISE | pass | all of it at once: what a real run in somebody else's CI looks like |
| `font fallback, thin glyphs` | NOISE | pass | a fallback font: the same letters, heavier strokes |
| `shadow radius, thin glyphs` | NOISE | pass | a shadow recomputed with a different blur radius |
| `gradient dither, thin glyphs` | NOISE | pass | different gradient dithering: visible to arithmetic, not to the eye |
| `scrollbar, thin glyphs` | NOISE | pass | a scroll bar appeared: that is the environment, not the layout |
| `caret, thin glyphs` | NOISE | pass | a blinking caret in an input field caught in the frame |
| `lazy placeholder, thin glyphs` | NOISE | pass | an image had not loaded yet: the shot was early, the page is intact |
| `subpixel text, thin glyphs` | NOISE | pass | a sub-pixel shift of text lines, not of the whole page |
| `button color, thin glyphs` | SIGNAL | fail | the buy button turned grey: the colour changed, the structure did not |
| `button removed, thin glyphs` | SIGNAL | fail | the button is gone: the worst visual bug there is |
| `promo removed, thin glyphs` | SIGNAL | fail | a block disappeared: a large change, no excuse for missing it |
| `layout +40px, thin glyphs` | SIGNAL | fail | content moved by 40px: the layout broke |
| `page taller +160, thin glyphs` | SIGNAL | fail | the page grew taller: a common and costly regression |
| `tiny icon 22px, thin glyphs` | SIGNAL | fail | a 22×22 icon: checks that small things do not drown in the filters |
| `price changed, thin glyphs` | SIGNAL | fail | a different price: a small area, an expensive mistake |
| `button shrunk, thin glyphs` | SIGNAL | fail | the button got narrower: the size changed, not the colour |
| `text overflow, thin glyphs` | SIGNAL | fail | text overflows its card: the usual result of a font change |
| `header color, thin glyphs` | SIGNAL | fail | the header changed colour: a flat fill, the same structure |
| `promo text, thin glyphs` | SIGNAL | fail | a different promo code: one line, but a real content error |
| `identical, OpenCV 5.1` | NOISE | pass | byte for byte: any failure here is an engine defect |
| `sensor noise σ=1.6, OpenCV 5.1` | NOISE | pass | ±2 brightness levels, ΔE00 < 1: invisible to a human in principle |
| `sensor noise σ=3.0, OpenCV 5.1` | NOISE | pass | the upper bound of codec noise |
| `antialias 0.4px, OpenCV 5.1` | NOISE | pass | different sub-pixel text rendering: a browser upgrade or hinting |
| `jpeg q=88, OpenCV 5.1` | NOISE | pass | re-compression in the screenshot pipeline |
| `jpeg q=75, OpenCV 5.1` | NOISE | pass | aggressive re-compression, still not a layout regression |
| `global shift 1px, OpenCV 5.1` | NOISE | pass | the whole page moved by a pixel: a scroll bar, rounding |
| `global shift 3px, OpenCV 5.1` | NOISE | pass | the same, larger: still a whole-page shift, not a layout change |
| `combined, OpenCV 5.1` | NOISE | pass | all of it at once: what a real run in somebody else's CI looks like |
| `font fallback, OpenCV 5.1` | NOISE | pass | a fallback font: the same letters, heavier strokes |
| `shadow radius, OpenCV 5.1` | NOISE | pass | a shadow recomputed with a different blur radius |
| `gradient dither, OpenCV 5.1` | NOISE | pass | different gradient dithering: visible to arithmetic, not to the eye |
| `scrollbar, OpenCV 5.1` | NOISE | pass | a scroll bar appeared: that is the environment, not the layout |
| `caret, OpenCV 5.1` | NOISE | pass | a blinking caret in an input field caught in the frame |
| `lazy placeholder, OpenCV 5.1` | NOISE | pass | an image had not loaded yet: the shot was early, the page is intact |
| `subpixel text, OpenCV 5.1` | NOISE | pass | a sub-pixel shift of text lines, not of the whole page |
| `button color, OpenCV 5.1` | SIGNAL | fail | the buy button turned grey: the colour changed, the structure did not |
| `button removed, OpenCV 5.1` | SIGNAL | fail | the button is gone: the worst visual bug there is |
| `promo removed, OpenCV 5.1` | SIGNAL | fail | a block disappeared: a large change, no excuse for missing it |
| `layout +40px, OpenCV 5.1` | SIGNAL | fail | content moved by 40px: the layout broke |
| `page taller +160, OpenCV 5.1` | SIGNAL | fail | the page grew taller: a common and costly regression |
| `tiny icon 22px, OpenCV 5.1` | SIGNAL | fail | a 22×22 icon: checks that small things do not drown in the filters |
| `price changed, OpenCV 5.1` | SIGNAL | fail | a different price: a small area, an expensive mistake |
| `button shrunk, OpenCV 5.1` | SIGNAL | fail | the button got narrower: the size changed, not the colour |
| `text overflow, OpenCV 5.1` | SIGNAL | fail | text overflows its card: the usual result of a font change |
| `header color, OpenCV 5.1` | SIGNAL | fail | the header changed colour: a flat fill, the same structure |
| `promo text, OpenCV 5.1` | SIGNAL | fail | a different promo code: one line, but a real content error |

## Кто с чем сравнивается

| Инструмент | Что это | Оговорка |
|---|---|---|
| VisTest (balanced) | ΔE00 ∧ SSIM, консенсус, AI-слой по умолчанию (обучаемый гейт выкл) | наш код |
| absdiff (самопис) | любой отличающийся пиксель валит тест | наш код: это и есть весь алгоритм |
| pixelmatch 7.2.0 | npm package CLI: pixelmatch <expected.png> <actual.png> | оригинальный код |
| Playwright 1.63.0 toHaveScreenshot() | playwright-core 1.63.0 playwright-core/lib/coreBundle: getComparator('image/png')(actual, expected, options) — the call Page.expectScreenshot makes for toHaveScreenshot() | оригинальный код |

BackstopJS в таблице нет намеренно: воспроизводить поведение resemble.js по памяти мы не стали, а нативного раннера пока не написали. Появится — добавим строку.

## Как воспроизвести

```bash
git clone <repo> && cd visual-testing
python run.py setup

# сторонние инструменты: версии прибиты в scripts/bench/package-lock.json
npm ci --prefix scripts/bench
node scripts/bench_pixelmatch.mjs > docs/benchmark_native.json

# таблица: VisTest + нативные pixelmatch и Playwright на тех же PNG
python tests/benchmark.py --compare --native docs/benchmark_native.json \
    --no-timing --markdown docs/benchmark.md
```

Нужны Python ≥ 3.11 и Node.js ≥ 18; браузер для этой таблицы не нужен. `--no-timing` убирает единственное, что меняется от прогона к прогону: консольный вывод воспроизводится побайтно, а в этом файле от машины к машине меняются только дата и строки окружения. Без `--native` строки конкурентов считает порт на numpy — он помечен в таблице как порт и годится только для быстрой проверки без Node.

## Что эта таблица не доказывает

- Корпус синтетический. Он проверяет, что движок отличает известные виды шума от известных видов регресса, а не то, как он поведёт себя на вашем приложении.
- The environment line at the top is there so that the run can be repeated, not because the figures depend on it. The one column that does depend on the machine is the milliseconds, and `--no-timing` leaves it out.
- The tolerances the other columns are checked under were measured on x86-64 and on nothing else. A machine of another architecture is outside that sample, so a red line there is not yet evidence of anything; `tests/corpus.py` says how to tell the two apart and what to measure before widening a number.
- У Playwright сравниваются готовые снимки. Съёмка `toHaveScreenshot()` (отключение анимаций, скрытие каретки, повторные кадры до стабильности) здесь не участвует — как и стабилизация съёмки у VisTest: корпус уже снят.
- Applitools и Percy здесь не участвуют: закрытые SaaS, прогнать их на своём корпусе и опубликовать результат нельзя.
- Пороги VisTest настраивались в том числе по этому корпусу. Это конфликт интересов, и мы о нём говорим прямо — поэтому корпус и код открыты, а не приложены картинкой.

## Browser corpus

The table above is measured on pictures OpenCV drew — the raster the thresholds were tuned on. It can say «not worse», not «better». The other corpus, `tests/browser_corpus/`, is drawn by Chromium from six local templates, through the library's own capture, with labels that come from the change that made each frame (`scripts/browser_corpus.py`). Its table is `docs/benchmark_browser.md`: `python tests/benchmark.py --corpus browser`.

**Its split is by template.** Calibration: table, form, cards, article. Held out: landing, dark. By template, never by pair: held-out templates never take part in choosing a threshold, a preset or any other constant of the engine; they are only measured once a choice has been made on the calibration half. The split is recorded per template and per pair in `tests/browser_corpus/manifest.json` (`split`).

**A third half is sealed: held-out-2** (dashboard, settings — the «same renderer» group only). The held-out half above is spent for the page-shift rule of engine v2: its figures were seen while that rule was being made. The sealed half is for the final acceptance of f5 and the figures of the README; nothing is run on it before then, and `python tests/benchmark.py --corpus browser` prints it only with `--final`.

## Results by case

| Case | Expected | VisTest (balanced) | absdiff (самопис) | pixelmatch 7.2.0 | Playwright 1.63.0 toHaveScreenshot() |
|---|---|---|---|---|---|
| `identical` | pass | pass | pass | pass | pass |
| `sensor noise σ=1.6` | pass | pass | fail ❌ | pass | pass |
| `sensor noise σ=3.0` | pass | pass | fail ❌ | pass | pass |
| `antialias 0.4px` | pass | pass | fail ❌ | fail ❌ | fail ❌ |
| `jpeg q=88` | pass | pass | fail ❌ | fail ❌ | pass |
| `jpeg q=75` | pass | pass | fail ❌ | fail ❌ | fail ❌ |
| `global shift 1px` | pass | pass | fail ❌ | fail ❌ | fail ❌ |
| `global shift 3px` | pass | pass | fail ❌ | fail ❌ | fail ❌ |
| `combined` | pass | pass | fail ❌ | fail ❌ | fail ❌ |
| `font fallback` | pass | pass | fail ❌ | fail ❌ | fail ❌ |
| `shadow radius` | pass | pass | fail ❌ | pass | pass |
| `gradient dither` | pass | pass | fail ❌ | pass | pass |
| `scrollbar` | pass | pass | fail ❌ | fail ❌ | pass |
| `caret` | pass | pass | fail ❌ | fail ❌ | fail ❌ |
| `lazy placeholder` | pass | pass | fail ❌ | pass | pass |
| `subpixel text` | pass | pass | fail ❌ | pass | pass |
| `button color` | fail | fail | fail | fail | fail |
| `button removed` | fail | fail | fail | fail | fail |
| `promo removed` | fail | fail | fail | fail | fail |
| `layout +40px` | fail | fail | fail | fail | fail |
| `page taller +160` | fail | fail | fail | fail | fail |
| `tiny icon 22px` | fail | fail | fail | fail | fail |
| `price changed` | fail | fail | fail | fail | fail |
| `button shrunk` | fail | fail | fail | fail | fail |
| `text overflow` | fail | fail | fail | fail | fail |
| `header color` | fail | fail | fail | pass ❌ | pass ❌ |
| `promo text` | fail | fail | fail | fail | fail |
| `identical, thin glyphs` | pass | pass | pass | pass | pass |
| `sensor noise σ=1.6, thin glyphs` | pass | pass | fail ❌ | pass | pass |
| `sensor noise σ=3.0, thin glyphs` | pass | pass | fail ❌ | pass | pass |
| `antialias 0.4px, thin glyphs` | pass | pass | fail ❌ | fail ❌ | fail ❌ |
| `jpeg q=88, thin glyphs` | pass | pass | fail ❌ | fail ❌ | pass |
| `jpeg q=75, thin glyphs` | pass | pass | fail ❌ | fail ❌ | fail ❌ |
| `global shift 1px, thin glyphs` | pass | pass | fail ❌ | fail ❌ | fail ❌ |
| `global shift 3px, thin glyphs` | pass | pass | fail ❌ | fail ❌ | fail ❌ |
| `combined, thin glyphs` | pass | pass | fail ❌ | fail ❌ | fail ❌ |
| `font fallback, thin glyphs` | pass | pass | fail ❌ | fail ❌ | fail ❌ |
| `shadow radius, thin glyphs` | pass | pass | fail ❌ | pass | pass |
| `gradient dither, thin glyphs` | pass | pass | fail ❌ | pass | pass |
| `scrollbar, thin glyphs` | pass | pass | fail ❌ | fail ❌ | pass |
| `caret, thin glyphs` | pass | pass | fail ❌ | fail ❌ | fail ❌ |
| `lazy placeholder, thin glyphs` | pass | pass | fail ❌ | pass | pass |
| `subpixel text, thin glyphs` | pass | pass | fail ❌ | pass | pass |
| `button color, thin glyphs` | fail | fail | fail | fail | fail |
| `button removed, thin glyphs` | fail | fail | fail | fail | fail |
| `promo removed, thin glyphs` | fail | fail | fail | fail | fail |
| `layout +40px, thin glyphs` | fail | fail | fail | fail | fail |
| `page taller +160, thin glyphs` | fail | fail | fail | fail | fail |
| `tiny icon 22px, thin glyphs` | fail | fail | fail | fail | fail |
| `price changed, thin glyphs` | fail | fail | fail | fail | fail |
| `button shrunk, thin glyphs` | fail | fail | fail | fail | fail |
| `text overflow, thin glyphs` | fail | fail | fail | fail | fail |
| `header color, thin glyphs` | fail | fail | fail | pass ❌ | pass ❌ |
| `promo text, thin glyphs` | fail | fail | fail | fail | fail |
| `identical, OpenCV 5.1` | pass | pass | pass | pass | pass |
| `sensor noise σ=1.6, OpenCV 5.1` | pass | pass | fail ❌ | pass | pass |
| `sensor noise σ=3.0, OpenCV 5.1` | pass | pass | fail ❌ | pass | pass |
| `antialias 0.4px, OpenCV 5.1` | pass | pass | fail ❌ | fail ❌ | fail ❌ |
| `jpeg q=88, OpenCV 5.1` | pass | pass | fail ❌ | fail ❌ | pass |
| `jpeg q=75, OpenCV 5.1` | pass | pass | fail ❌ | fail ❌ | fail ❌ |
| `global shift 1px, OpenCV 5.1` | pass | pass | fail ❌ | fail ❌ | fail ❌ |
| `global shift 3px, OpenCV 5.1` | pass | pass | fail ❌ | fail ❌ | fail ❌ |
| `combined, OpenCV 5.1` | pass | pass | fail ❌ | fail ❌ | fail ❌ |
| `font fallback, OpenCV 5.1` | pass | pass | fail ❌ | fail ❌ | pass |
| `shadow radius, OpenCV 5.1` | pass | pass | fail ❌ | pass | pass |
| `gradient dither, OpenCV 5.1` | pass | pass | fail ❌ | pass | pass |
| `scrollbar, OpenCV 5.1` | pass | pass | fail ❌ | fail ❌ | pass |
| `caret, OpenCV 5.1` | pass | pass | fail ❌ | fail ❌ | fail ❌ |
| `lazy placeholder, OpenCV 5.1` | pass | pass | fail ❌ | pass | pass |
| `subpixel text, OpenCV 5.1` | pass | pass | fail ❌ | pass | pass |
| `button color, OpenCV 5.1` | fail | fail | fail | fail | fail |
| `button removed, OpenCV 5.1` | fail | fail | fail | fail | fail |
| `promo removed, OpenCV 5.1` | fail | fail | fail | fail | fail |
| `layout +40px, OpenCV 5.1` | fail | fail | fail | fail | fail |
| `page taller +160, OpenCV 5.1` | fail | fail | fail | fail | fail |
| `tiny icon 22px, OpenCV 5.1` | fail | fail | fail | fail | fail |
| `price changed, OpenCV 5.1` | fail | fail | fail | fail | fail |
| `button shrunk, OpenCV 5.1` | fail | fail | fail | fail | fail |
| `text overflow, OpenCV 5.1` | fail | fail | fail | fail | fail |
| `header color, OpenCV 5.1` | fail | fail | fail | pass ❌ | pass ❌ |
| `promo text, OpenCV 5.1` | fail | fail | fail | fail | fail |
