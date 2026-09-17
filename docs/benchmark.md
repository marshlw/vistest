# Бенчмарк движка сравнения

Сгенерировано `python tests/benchmark.py --compare --native docs/benchmark_native.json --no-timing --markdown` 2026-09-17, OpenCV 4.14.0, numpy 2.5.3, Python 3.13.13, Linux x86_64, preset `balanced`.

The corpus is synthetic and frozen in the repository as PNG files (`tests/benchmark_corpus/`); it is not drawn at run time, so the installed OpenCV does not change what is measured. Figures published before the freeze were tied to the OpenCV version of whoever ran them and are not comparable with these — see CHANGELOG. The environment line above still matters; the caveats at the end say why.

Every tool runs with the settings a user gets after installing it and changing nothing — VisTest included. The exact values are listed under «Настройки». Nothing was tuned for this table.

## Результат

| Инструмент | Ложные падения | Пропущенные регрессы | Верно | мс |
|---|---|---|---|---|
| VisTest (balanced) | 0/32 (0%) | 2/22 (9%) | 52/54 | — |
| absdiff (самопис) | 30/32 (94%) | 0/22 (0%) | 24/54 | — |
| pixelmatch 7.2.0 | 18/32 (56%) | 2/22 (9%) | 34/54 | — |
| Playwright 1.63.0 toHaveScreenshot() | 14/32 (44%) | 2/22 (9%) | 38/54 | — |

By corpus raster (correct · false failures · misses):

| Tool | `opencv-4.14` | `opencv-5.0` |
|---|---|---|
| VisTest (balanced) | 26/27 · 0/16 · 1/11 | 26/27 · 0/16 · 1/11 |
| absdiff (самопис) | 12/27 · 15/16 · 0/11 | 12/27 · 15/16 · 0/11 |
| pixelmatch 7.2.0 | 17/27 · 9/16 · 1/11 | 17/27 · 9/16 · 1/11 |
| Playwright 1.63.0 toHaveScreenshot() | 19/27 · 7/16 · 1/11 | 19/27 · 7/16 · 1/11 |

Строки pixelmatch и Playwright посчитаны **их собственным кодом** на тех же PNG (`scripts/bench_pixelmatch.mjs`, результат — `docs/benchmark_native.json`, отпечаток корпуса `5a0ee31202108e1c…` сверяется при чтении). Время в них не мерилось: другой процесс, другой язык — цифра была бы не про алгоритм.

## Что означают колонки

**Ложные падения** — доля страниц из группы NOISE, на которых инструмент дал красный тест. Страница по существу не менялась, поэтому каждое такое падение ложное. Это главная метрика: именно от ложных падений команда через месяц ставит `--update-snapshots` в CI, что равносильно выключению визуального тестирования.

**Пропущенные регрессы** — доля группы SIGNAL, где инструмент остался зелёным. Смотреть только на первую колонку нельзя: инструмент, который не падает никогда, идеален по ложным падениям и бесполезен.

## Настройки

Без этого таблица ничего не значит. Всё — значения по умолчанию; ни одна опция не передавалась ни одному инструменту.

**VisTest (balanced)**

- пресет `balanced` — значение по умолчанию, `vistest.yaml` не читается
- `DiffConfig`: `delta_e_threshold=2.3`, `ssim_threshold=0.9`, `require_consensus=True`, `align_enabled=True`, `max_align_shift_px=8.0`, `antialias_filter=True`, `aa_tolerance=0.1`, `morph_open_px=2`, `morph_close_px=6`, `min_region_px=24`, `min_region_fill=0.06`, `max_regions=200`, `explain_noise=True`, `detect_moved=True`, `move_search_px=64`, `move_match_threshold=0.93`, `fail_severity=25.0`, `max_changed_area_pct=0.15`, `area_requires_region=True`, `area_hard_fail_pct=5.0`, `above_fold_px=900`, `above_fold_weight=1.5`, `ignore_kinds=('noise', 'antialias')`, `moved_severity_scale=0.35`, `size_tolerance_px=0`, `fail_on_size_change=True`, `max_pixels=80000000`
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
| Python | 3.13.13 |
| numpy | 2.5.3 |
| opencv-python-headless | 4.14.0.94 |
| pillow | 12.3.0 |
| PyYAML | 6.0.3 |
| Node.js | v22.22.2 |
| npm `pixelmatch` | 7.2.0 |
| npm `pngjs (used by pixelmatch)` | 7.0.0 |
| npm `@playwright/test` | 1.63.0 |
| npm `playwright` | 1.63.0 |
| npm `playwright-core` | 1.63.0 |

npm-версии прибиты в `scripts/bench/package.json` и `scripts/bench/package-lock.json`; `npm ci` ставит ровно их.

## Корпус

54 пар: 32 NOISE + 22 SIGNAL. Stored in `tests/benchmark_corpus/` (PNG + `manifest.json`), drawn by `tests/synthetic.py` and frozen: `python tests/benchmark.py --regenerate` redraws them on purpose, and `tests/test_corpus_frozen.py` fails when the code starts drawing something other than what is on disk.

The pages are drawn by OpenCV, and OpenCV 5 rasterises text differently from 4.x: every pair differs. So both rasters are frozen, as separate cases; keeping one would be choosing the convenient picture.

| Raster | Drawn with | Case names | What differs |
|---|---|---|---|
| `opencv-4.14` | `opencv-python-headless==4.14.0.94` | no suffix | original raster: text as OpenCV 4.x draws it |
| `opencv-5.0` | `opencv-python-headless==5.0.0.93` | `…, thin glyphs` | OpenCV 5 raster: the same strings, with thinner, lighter glyph strokes |

Почему синтетика, а не настоящие скриншоты: на реальных парах правильный ответ приходится размечать человеком, и в спорных случаях человек размечает так, как ему удобно. Здесь ответ задан построением — его можно оспорить, читая код, а не полагаясь на нашу добросовестность. Обратная сторона честная: синтетика не покрывает всё разнообразие реальных страниц, поэтому цифра говорит о движке, а не о вашем проекте. Про свой проект отвечает `vistest doctor`.

| Кейс | Группа | Ожидание | Почему |
|---|---|---|---|
| `identical` | NOISE | проход | byte for byte: any failure here is an engine defect |
| `sensor noise σ=1.6` | NOISE | проход | ±2 brightness levels, ΔE00 < 1: invisible to a human in principle |
| `sensor noise σ=3.0` | NOISE | проход | the upper bound of codec noise |
| `antialias 0.4px` | NOISE | проход | different sub-pixel text rendering: a browser upgrade or hinting |
| `jpeg q=88` | NOISE | проход | re-compression in the screenshot pipeline |
| `jpeg q=75` | NOISE | проход | aggressive re-compression, still not a layout regression |
| `global shift 1px` | NOISE | проход | the whole page moved by a pixel: a scroll bar, rounding |
| `global shift 3px` | NOISE | проход | the same, larger: still a whole-page shift, not a layout change |
| `combined` | NOISE | проход | all of it at once: what a real run in somebody else's CI looks like |
| `font fallback` | NOISE | проход | a fallback font: the same letters, heavier strokes |
| `shadow radius` | NOISE | проход | a shadow recomputed with a different blur radius |
| `gradient dither` | NOISE | проход | different gradient dithering: visible to arithmetic, not to the eye |
| `scrollbar` | NOISE | проход | a scroll bar appeared: that is the environment, not the layout |
| `caret` | NOISE | проход | a blinking caret in an input field caught in the frame |
| `lazy placeholder` | NOISE | проход | an image had not loaded yet: the shot was early, the page is intact |
| `subpixel text` | NOISE | проход | a sub-pixel shift of text lines, not of the whole page |
| `button color` | SIGNAL | падение | the buy button turned grey: the colour changed, the structure did not |
| `button removed` | SIGNAL | падение | the button is gone: the worst visual bug there is |
| `promo removed` | SIGNAL | падение | a block disappeared: a large change, no excuse for missing it |
| `layout +40px` | SIGNAL | падение | content moved by 40px: the layout broke |
| `page taller +160` | SIGNAL | падение | the page grew taller: a common and costly regression |
| `tiny icon 22px` | SIGNAL | падение | a 22×22 icon: checks that small things do not drown in the filters |
| `price changed` | SIGNAL | падение | a different price: a small area, an expensive mistake |
| `button shrunk` | SIGNAL | падение | the button got narrower: the size changed, not the colour |
| `text overflow` | SIGNAL | падение | text overflows its card: the usual result of a font change |
| `header color` | SIGNAL | падение | the header changed colour: a flat fill, the same structure |
| `promo text` | SIGNAL | падение | a different promo code: one line, but a real content error |
| `identical, thin glyphs` | NOISE | проход | byte for byte: any failure here is an engine defect |
| `sensor noise σ=1.6, thin glyphs` | NOISE | проход | ±2 brightness levels, ΔE00 < 1: invisible to a human in principle |
| `sensor noise σ=3.0, thin glyphs` | NOISE | проход | the upper bound of codec noise |
| `antialias 0.4px, thin glyphs` | NOISE | проход | different sub-pixel text rendering: a browser upgrade or hinting |
| `jpeg q=88, thin glyphs` | NOISE | проход | re-compression in the screenshot pipeline |
| `jpeg q=75, thin glyphs` | NOISE | проход | aggressive re-compression, still not a layout regression |
| `global shift 1px, thin glyphs` | NOISE | проход | the whole page moved by a pixel: a scroll bar, rounding |
| `global shift 3px, thin glyphs` | NOISE | проход | the same, larger: still a whole-page shift, not a layout change |
| `combined, thin glyphs` | NOISE | проход | all of it at once: what a real run in somebody else's CI looks like |
| `font fallback, thin glyphs` | NOISE | проход | a fallback font: the same letters, heavier strokes |
| `shadow radius, thin glyphs` | NOISE | проход | a shadow recomputed with a different blur radius |
| `gradient dither, thin glyphs` | NOISE | проход | different gradient dithering: visible to arithmetic, not to the eye |
| `scrollbar, thin glyphs` | NOISE | проход | a scroll bar appeared: that is the environment, not the layout |
| `caret, thin glyphs` | NOISE | проход | a blinking caret in an input field caught in the frame |
| `lazy placeholder, thin glyphs` | NOISE | проход | an image had not loaded yet: the shot was early, the page is intact |
| `subpixel text, thin glyphs` | NOISE | проход | a sub-pixel shift of text lines, not of the whole page |
| `button color, thin glyphs` | SIGNAL | падение | the buy button turned grey: the colour changed, the structure did not |
| `button removed, thin glyphs` | SIGNAL | падение | the button is gone: the worst visual bug there is |
| `promo removed, thin glyphs` | SIGNAL | падение | a block disappeared: a large change, no excuse for missing it |
| `layout +40px, thin glyphs` | SIGNAL | падение | content moved by 40px: the layout broke |
| `page taller +160, thin glyphs` | SIGNAL | падение | the page grew taller: a common and costly regression |
| `tiny icon 22px, thin glyphs` | SIGNAL | падение | a 22×22 icon: checks that small things do not drown in the filters |
| `price changed, thin glyphs` | SIGNAL | падение | a different price: a small area, an expensive mistake |
| `button shrunk, thin glyphs` | SIGNAL | падение | the button got narrower: the size changed, not the colour |
| `text overflow, thin glyphs` | SIGNAL | падение | text overflows its card: the usual result of a font change |
| `header color, thin glyphs` | SIGNAL | падение | the header changed colour: a flat fill, the same structure |
| `promo text, thin glyphs` | SIGNAL | падение | a different promo code: one line, but a real content error |

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

Нужны Python ≥ 3.10 и Node.js ≥ 18; браузер для этой таблицы не нужен. `--no-timing` убирает единственное, что меняется от прогона к прогону: консольный вывод воспроизводится побайтно, а в этом файле от машины к машине меняются только дата и строки окружения. Без `--native` строки конкурентов считает порт на numpy — он помечен в таблице как порт и годится только для быстрой проверки без Node.

## Что эта таблица не доказывает

- Корпус синтетический. Он проверяет, что движок отличает известные виды шума от известных видов регресса, а не то, как он поведёт себя на вашем приложении.
- The input is frozen, the engine is not: on the same files OpenCV 4.14 and 5.0 give the same verdicts, but some region metrics differ in the last digits (`cv2.warpAffine` with `INTER_LINEAR` rounds differently). The environment line at the top says which one produced this table.
- У Playwright сравниваются готовые снимки. Съёмка `toHaveScreenshot()` (отключение анимаций, скрытие каретки, повторные кадры до стабильности) здесь не участвует — как и стабилизация съёмки у VisTest: корпус уже снят.
- Applitools и Percy здесь не участвуют: закрытые SaaS, прогнать их на своём корпусе и опубликовать результат нельзя.
- Пороги VisTest настраивались в том числе по этому корпусу. Это конфликт интересов, и мы о нём говорим прямо — поэтому корпус и код открыты, а не приложены картинкой.

## Кейсы по инструментам

| Кейс | Ожидание | VisTest (balanced) | absdiff (самопис) | pixelmatch 7.2.0 | Playwright 1.63.0 toHaveScreenshot() |
|---|---|---|---|---|---|
| `identical` | проход | проход | проход | проход | проход |
| `sensor noise σ=1.6` | проход | проход | падение ❌ | проход | проход |
| `sensor noise σ=3.0` | проход | проход | падение ❌ | проход | проход |
| `antialias 0.4px` | проход | проход | падение ❌ | падение ❌ | падение ❌ |
| `jpeg q=88` | проход | проход | падение ❌ | падение ❌ | проход |
| `jpeg q=75` | проход | проход | падение ❌ | падение ❌ | падение ❌ |
| `global shift 1px` | проход | проход | падение ❌ | падение ❌ | падение ❌ |
| `global shift 3px` | проход | проход | падение ❌ | падение ❌ | падение ❌ |
| `combined` | проход | проход | падение ❌ | падение ❌ | падение ❌ |
| `font fallback` | проход | проход | падение ❌ | падение ❌ | падение ❌ |
| `shadow radius` | проход | проход | падение ❌ | проход | проход |
| `gradient dither` | проход | проход | падение ❌ | проход | проход |
| `scrollbar` | проход | проход | падение ❌ | падение ❌ | проход |
| `caret` | проход | проход | падение ❌ | падение ❌ | падение ❌ |
| `lazy placeholder` | проход | проход | падение ❌ | проход | проход |
| `subpixel text` | проход | проход | падение ❌ | проход | проход |
| `button color` | падение | падение | падение | падение | падение |
| `button removed` | падение | падение | падение | падение | падение |
| `promo removed` | падение | падение | падение | падение | падение |
| `layout +40px` | падение | падение | падение | падение | падение |
| `page taller +160` | падение | падение | падение | падение | падение |
| `tiny icon 22px` | падение | падение | падение | падение | падение |
| `price changed` | падение | падение | падение | падение | падение |
| `button shrunk` | падение | падение | падение | падение | падение |
| `text overflow` | падение | падение | падение | падение | падение |
| `header color` | падение | проход ❌ | падение | проход ❌ | проход ❌ |
| `promo text` | падение | падение | падение | падение | падение |
| `identical, thin glyphs` | проход | проход | проход | проход | проход |
| `sensor noise σ=1.6, thin glyphs` | проход | проход | падение ❌ | проход | проход |
| `sensor noise σ=3.0, thin glyphs` | проход | проход | падение ❌ | проход | проход |
| `antialias 0.4px, thin glyphs` | проход | проход | падение ❌ | падение ❌ | падение ❌ |
| `jpeg q=88, thin glyphs` | проход | проход | падение ❌ | падение ❌ | проход |
| `jpeg q=75, thin glyphs` | проход | проход | падение ❌ | падение ❌ | падение ❌ |
| `global shift 1px, thin glyphs` | проход | проход | падение ❌ | падение ❌ | падение ❌ |
| `global shift 3px, thin glyphs` | проход | проход | падение ❌ | падение ❌ | падение ❌ |
| `combined, thin glyphs` | проход | проход | падение ❌ | падение ❌ | падение ❌ |
| `font fallback, thin glyphs` | проход | проход | падение ❌ | падение ❌ | падение ❌ |
| `shadow radius, thin glyphs` | проход | проход | падение ❌ | проход | проход |
| `gradient dither, thin glyphs` | проход | проход | падение ❌ | проход | проход |
| `scrollbar, thin glyphs` | проход | проход | падение ❌ | падение ❌ | проход |
| `caret, thin glyphs` | проход | проход | падение ❌ | падение ❌ | падение ❌ |
| `lazy placeholder, thin glyphs` | проход | проход | падение ❌ | проход | проход |
| `subpixel text, thin glyphs` | проход | проход | падение ❌ | проход | проход |
| `button color, thin glyphs` | падение | падение | падение | падение | падение |
| `button removed, thin glyphs` | падение | падение | падение | падение | падение |
| `promo removed, thin glyphs` | падение | падение | падение | падение | падение |
| `layout +40px, thin glyphs` | падение | падение | падение | падение | падение |
| `page taller +160, thin glyphs` | падение | падение | падение | падение | падение |
| `tiny icon 22px, thin glyphs` | падение | падение | падение | падение | падение |
| `price changed, thin glyphs` | падение | падение | падение | падение | падение |
| `button shrunk, thin glyphs` | падение | падение | падение | падение | падение |
| `text overflow, thin glyphs` | падение | падение | падение | падение | падение |
| `header color, thin glyphs` | падение | проход ❌ | падение | проход ❌ | проход ❌ |
| `promo text, thin glyphs` | падение | падение | падение | падение | падение |
