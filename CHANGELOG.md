# Changelog

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/),
версии — [семантические](https://semver.org/lang/ru/).

## [Unreleased]

### Competitors in the benchmark are measured with their own code

- **The published pixelmatch and Playwright rows are now native.**
  `scripts/bench_pixelmatch.mjs` runs the pixelmatch CLI from npm and
  Playwright's own `getComparator('image/png')` — the function
  `toHaveScreenshot()` calls — on `tests/benchmark_corpus/`, every tool with
  its defaults. Before, both rows came from our numpy port and the Playwright
  "native" row was pixelmatch with `threshold: 0.2`, i.e. our retelling.
- The figures did not change: on all 54 pairs the native verdicts equal the
  port's (pixel counts differ, the port has no anti-aliasing detector).
  README now names the tool versions instead of "numpy port".
- `scripts/bench/package.json` + `package-lock.json` pin pixelmatch 7.2.0 and
  @playwright/test 1.63.0 (`npm ci --prefix scripts/bench`). The result is
  kept in `docs/benchmark_native.json`, the generated table in
  `docs/benchmark.md` with every setting of every tool and all versions.
- `--native` checks the corpus fingerprint (sha256 of the manifest and every
  PNG) and refuses a JSON computed on other files. A tool missing from the JSON
  stays a port, marked, with a warning. `--with-ports` prints the port next to
  the original in the console for comparison; it never reaches the markdown.
- Wording fix: README and the generated table said the benchmark runs with the
  trained gate "on". It runs with `AIConfig()` as shipped, where
  `gate_enabled` is `False`.

### The benchmark corpus is frozen on disk

- **Benchmark figures published before this change were tied to the installed
  OpenCV version and are not comparable with each other or with the figures
  below.** The curated corpus used to be drawn on every run by
  `tests/synthetic.py` (`cv2.putText`), and OpenCV 5 rasterises text
  differently from 4.x: all 54 images of the corpus differed between the two.
- **`tests/benchmark_corpus/`** now holds the curated corpus as PNG pairs plus
  `manifest.json` (the `export_corpus` format, with `family` and `render`
  added). `corpus.build()` reads it (`corpus.load()`) and draws nothing.
  `tests/test_benchmark.py` and `tests/test_gate.py` run on it;
  `corpus.generate()` for training still draws on the fly, on purpose (its
  docstring says why).
- **Both rasters are frozen, as separate cases**: 27 cases drawn by
  `opencv-python-headless==4.14.0.94` and the same 27 drawn by `==5.0.0.93`,
  named with the suffix `, thin glyphs` (OpenCV 5 draws thinner, lighter
  strokes). 54 pairs; about 16 MB on disk, 12.7 MB of distinct files.
- **Redrawing is deliberate**: `python tests/benchmark.py --regenerate` redraws
  the raster of the installed OpenCV and leaves the other one alone.
  `tests/test_corpus_frozen.py` fails when the files and the generator
  disagree and names the drifted pairs; it also fails under an OpenCV version
  that has no raster in `corpus.RENDERS`.
- `python tests/benchmark.py --no-timing` leaves the timing column blank, so
  the output can be diffed byte for byte; the environment line goes to stderr.
  The output is split by raster.
- Figures on the frozen corpus, preset `balanced`, AI layer on, OpenCV
  4.14.0.94 and 5.0.0.93, numpy 2.5.3, Python 3.13, Linux x86_64: VisTest
  52/54 (26/27 on each raster), 0/32 false failures, 2/22 misses
  (`header color` on both rasters). The comparison table is byte-identical
  under both versions. The detailed table is not: on the same files, six rows
  differ in region metrics (not verdicts), because `cv2.warpAffine` with
  `INTER_LINEAR` rounds differently in 4.14 and 5.0 (`core/align.py`,
  `core/refit.py`, `core/explain.py`).

### The anti-aliasing filter answers for itself

- **The per-pixel anti-aliasing veto no longer erases a region on its own.**
  Most pixels of a glyph that became another glyph pass the per-pixel test,
  and on the benchmark generator that is how four `price changed` regressions
  vanished before segmentation. The mask may still thin a group of changed
  pixels; a group it covers by half or more is taken out only if the
  baseline, re-drawn by what a rasteriser is allowed to do — sub-pixel
  position, fractional stroke weight, softness — reproduces at least 70% of
  it. Otherwise the mask is withdrawn from the whole group
  (`core/explain.py: explain_antialias`, `core/refit.py`).
- **Every erased group is a suppressed region** of kind `antialias`, with
  `suppressed_by` naming the re-drawing and what it left over:
  `antialias: the baseline moved +0.18,-0.07 px reproduces 100% of the
  changed pixels (0 of 412 left)`. `rerender` and `jpeg` now say it the same
  way. `ScreenshotMismatch` spells out up to three of them under `also:`, and
  the pytest summary lists five (all with `-v`).
- **Stroke weight is a continuous search** (±1 px per side, to 1/16 px), not
  one 3×3 morphology step, and neither weight nor softness may change the
  colour of a stroke: on one-pixel text "lighter" and "thinner" look the
  same, and the engine sides with calling it a change.
- **The shift is estimated, not searched** (Lucas–Kanade on the group's
  window). The prototype tried 980 warps per region and cost +30% per
  comparison; this costs ~6 ms per pair, within the noise of the total.
- Measured on `corpus.generate(6)`, 124 pairs, OpenCV 5.0: false failures
  7/70 → 7/70, misses 10/54 → 6/54, 107 → 111 correct, 303–316 → 298–320
  ms/pair. OpenCV 4.14: 7/70 → 7/70, 7/54 → 6/54. Showcase corpus (27): 25 →
  26 on 5.0, 26 → 26 on 4.14, 0/16 false on both.
- `diff.explain_noise: false` restores the unconditional veto.

### Extension points (plugin API v1)

Preparation for the open core. Nothing moves out of the repository yet; the
modules that will are now reached only through a plugin registry, and
everything works without them.

- **`vistest.plugins`.** The public contract is `vistest/plugins/api.py`,
  `API_VERSION = 1`, with four `@runtime_checkable` protocols:
  `RegionAnnotator.annotate(region, ctx)`, `RegionScorer.score(regions, ctx)`,
  `AuthProvider.authenticate(login, secret)` and `BaselineSyncBackend`.
  Plugins are found through the `vistest.plugins` entry-point group and call
  `register(registry)`. One active implementation per role (highest
  `priority`, then first registered; the loser is named in a warning);
  annotators run as a chain.
- **Nothing a plugin does can take a run down.** A plugin that fails to
  import, exits, has no `register`, raises inside it (its registrations are
  rolled back) or registers something that is not an implementation is a
  warning. A different `API_VERSION` is refused before `register` runs. At
  runtime, a scorer or annotator that raises, or answers nonsense, is a
  warning and the deterministic path. `VISTEST_DISABLE_PLUGINS=1` switches
  loading off entirely — nothing from the group is imported.
- **`fail_on: any | likely-real | confirmed`** (`plugins.fail_on`,
  `VISTEST_FAIL_ON`, `--vistest-fail-on`; default `likely-real`) decides what
  a scorer's estimate does to a check. Without a scorer it changes nothing.
  Two safety rules outrank any scorer, as they did the gate: a region over 20%
  of the page and a page-size change are never suppressed by a score.
- **No difference is swallowed quietly.** Every suppressed region carries
  `suppressed_by` (`noise: …`, `below-fail-on: …`, `ignored-kind: …` — the
  class set aside by `diff.ignore_kinds` now says so too). The library report
  lists them in every row, passing ones included, and pytest prints the total:
  `1 difference suppressed as rendering noise`. `ScreenshotMismatch` adds an
  `also:` line. When every region was suppressed, the area note says so
  instead of "not a single region passed filtering".
- **Region fields `score`, `annotations`, `suppressed_by`** are always
  present — `null`, `[]`, `null` without extensions — in the JSON, in the
  database (three new `region` columns; suppressed regions are now stored as
  well, marked, and excluded from every query that lists or counts regions)
  and in the interface (a score and remarks are shown when present; the
  comparison screen lists what was not counted). `gate_probability` and
  `perceptual_distance` are gone from `DiffRegion`: they were one extension's
  fields in the core's model; the score and annotations replace them.
- **Plugin tables.** `registry.add_migrations([...])`, applied once per step,
  versioned in the new `plugin_schema_version` table — separate from
  `PRAGMA user_version`. Tables must be named `ext_<plugin>_*`; an SQLite
  authorizer refuses anything else, core tables included, before it runs.
- **`plugins:` in `vistest.yaml`.** `enabled`, `fail_on`, `noise_below`,
  `confirmed_at`, `disabled`; any other key is kept, handed to plugins as
  their section, and — unless an installed plugin has that name — logged once
  as a warning. It is not refused.
- **`GET /api/capabilities`** — `region_scores`, `region_annotations`,
  `external_sign_in`, `baseline_sync`. The interface hides what is false: no
  directory card, no score column. No messages about it.
- **Moved behind the protocols, still in the repository:** the region gate
  and its feature extractor and the perceptual filter (scorer + annotator),
  LDAP/AD sign-in (`AuthProvider`; `/api/ldap` is now served by it) and
  baseline transfer between installations (`BaselineSyncBackend`;
  `/api/baselines/export|import` and `vistest baselines export|import` exist
  only when it is active). They are registered by the temporary
  `vistest._extensions` package through the entry point in `pyproject.toml`
  — **reinstall (`pip install -e .`) to register it**. Attribution stays in
  the core. `tests/test_plugin_boundary.py` fails if a core module imports any
  of them by name. `set_annotator` keeps its old contract.
- Benchmark unchanged: 24/27, 0/16 false failures with the extensions; 23/27
  with `VISTEST_DISABLE_PLUGINS=1`, the same as `--no-ai`.
- New tests: `test_plugins.py`, `test_degradation.py` (library and server
  scenarios with plugins disabled and with a set of broken plugins installed),
  `test_plugin_boundary.py`.

### Segmentation keeps text; noise is explained, not erased

- **Close before open.** `segment.clean_mask` now closes the change mask
  before opening it. The opening (5×5 ellipse) used to run first and erased
  every stroke thinner than five pixels — text: 13 262 changed pixels of a
  text edit became 374. The benchmark corpus goes from 3 misses to 1
  (`price changed` and `promo text` are found; `header color` is a separate
  cause). False failures stay at 0/16 — with and without the AI layer.
- **Deterministic noise explanations** (`core/explain.py`, open engine, no
  model). The new order also keeps what the old one erased by accident; each
  such region is now suppressed by a named test and says so in
  `suppressed_by` (`scrollbar: …`, `jpeg: …`, `rerender: …`, with the kind it
  had): a scroll-bar band at the right/bottom edge; a frame the baseline
  re-encoded as JPEG reproduces; pixels a ≤1 px quarter-step shift of the
  baseline reproduces (zero-mean noise off the edges); and a box with no
  changed pixel inside (`morphology: …`), which closing can leave behind. The
  AI layer only sees
  regions this stage left. `diff.explain_noise: false` turns it off for
  diagnostics. Without the AI layer the corpus goes from 1/16 false failures
  (the scroll bar, previously hidden by the learned gate) to 0/16.
- **A move is scored by how far it went.** `moved` regions weigh from
  `moved_severity_scale` (0.35) up to 1.0 with the shift measured in the
  element's own size. Before, a 12 px checkbox moved by 16 px scored 17 and
  passed while the same checkbox moved by 32 px scored 35 and failed.
- Tests whose premise was the old order were updated, each with the reason in
  place: the thin-text accounting test now asserts the text is inside a region;
  the per-snapshot-threshold tests tolerate a 3 px block move by severity as
  well as area; the stability-mask clock test changes every digit between
  frames; the gate tests measure the gate with `explain_noise=False`.

### The learned gate is off by default

- **`ai.gate_enabled` now defaults to `false`.** The gate was trained against
  the cascade as it was before `core/explain.py` — wide aperture, opening
  first, no deterministic noise explanations. Behind the cascade it now sits
  in, it subtracts: on the corpus, 0 of 55 regressions over noise are missed
  without it and 5 of 55 are missed with it. A layer that only takes away must
  not be on by default. Nothing is removed — `ai.gate_enabled: true` brings it
  back, and every safety rule around it is unchanged.
- The deterministic explanations do the work the gate was added for, and name
  the reason in `suppressed_by` instead of a probability. Whether a gate
  re-trained against the current cascade beats them is an open question, and
  the honest answer to "it does not" is to drop the layer, not to keep
  shipping it switched off.

### Engine metrics you can reconcile

Found on a live run: `severity 100.0, changed area 1.44%` next to three
regions totalling ~400 px, and two neighbouring radio buttons "moved" by +48
and -58 at once.

- **Where the changed pixels went.** `changed_area_pct` is still measured on
  the change mask before segmentation — that has not changed. New metrics
  split it: `region_pixels`, `suppressed_pixels`, `unassigned_pixels` (they sum
  to `changed_pixels`) and `region_area_pct`. When more than 10% of the change
  is in no region, the one-line reason says so with numbers:
  `these regions hold 0.04% of the 1.44% changed, 1.40% is in no region`.
  On the live pair 97% of the mask was 1–2 px text strokes removed by the
  morphological opening before segmentation.
- The reason line starts with the total region count, never drops kinds
  silently (`other changes in N`), and names the region it points at
  `most severe` — it was chosen by severity and used to be called `largest`.
  The failure headline adds the region count.
- **Severity is a scale again.** Size now multiplies colour/structure
  intensity instead of being added to it, and the result saturates softly
  (`100·(1 − e^(−raw/0.6))`) instead of being clipped. A 27 px speck that
  disappeared scores ~31 (was 100), a moved 360×120 block ~70, a removed
  button ~98. **Stored severities from earlier versions are on the old scale.**
  Verdicts on the benchmark corpus are unchanged (24/27, 0/16 false fails).
- **MOVED no longer invents vectors.** A region whose content fits several
  places equally well (NCC within 0.03) is not given a shift unless the rest
  of the page agrees on one of those places; otherwise it is classified as
  appeared / disappeared / content, and a note says why.
  `DiffRegion.move_alternatives` records the count. Regions too small to
  search (side < 6 px) get the same check against the page's shift.

### Режим библиотеки

VisTest теперь подключается к чужому проекту обычной библиотекой — без сервера,
без интерфейса и без базы. Эталоны лежат файлами в репозитории пользователя и
ревьюятся в пул-реквестах, артефакты и отчёт — в `.vistest/`.

- `vistest.expect_screenshot(target, name, ...)` — публичный API. Принимает
  `Page` и `Locator` из Playwright, байты PNG, `PIL.Image`, массив numpy и путь
  к файлу; Playwright при этом не импортируется, страница распознаётся по
  наличию `screenshot()`. Возвращает `CompareResult`, бросает
  `BaselineMissing` и `ScreenshotMismatch` — оба наследники `AssertionError`,
  оба называют каждый файл путём и команду, которой это чинится.
- Хранилище эталонов файлами: `vistest.storage.file.FileStore`, раскладка
  `tests/__vistest__/<платформа>/<имя>.png` плюс необязательный паспорт
  `<имя>.json`. Протокол — `SnapshotStore` в `vistest.storage.base`, ключ —
  `SnapshotKey`. Повторный `--vistest-update` не трогает совпадающие файлы:
  принять два снимка не должно означать пул-реквест на триста изменённых.
- Флаги pytest: `--vistest-update`, `--vistest-baselines`, `--vistest-platform`,
  `--vistest-report`; у трёх последних есть ini-эквиваленты в `pyproject.toml`.
  Плагин больше не тянет загрузчик конфигурации и раннер на импорте — он
  грузится в каждом прогоне любого проекта, где установлен пакет.
- Отчёт — один самодостаточный HTML-файл: картинки в base64, ни CDN, ни
  шрифтов, ни одного запроса наружу; слайдер «эталон / снимок», причина
  падения словами, светлая и тёмная тема.
- `pytest -n` (xdist): любая запись идёт через временный файл и атомарное
  переименование, общего индекса и блокировок нет, отчёт собирается один раз в
  контроллере из per-test файлов.
- `vistest baselines export|import` понимают обе раскладки и переносят набор
  между ними одним и тем же архивом (`--baselines PATH`, `--layout flat`).
  Это путь миграции: проект, переросший библиотеку, переезжает в инсталляцию
  без переутверждения эталонов.

### Прогон не умирает из-за отличающегося скриншота

Найдено на живой обкатке на чужом проекте: пятый тест из одиннадцати нашёл
настоящее расхождение, хук отчёта упал с `TypeError: Object of type ndarray is
not JSON serializable`, и pytest превратил это в INTERNALERROR — сессия
умерла, оставшиеся шесть тестов не выполнились, отчёта нет.

- **Корень.** Метрики были ни при чём. `compare()` отдаёт рендереру четыре
  полнокадровых массива, и клал он их в `CompareResult.artifacts` — поле,
  объявленное `dict[str, str]` и сериализуемое в каждый отчёт, с
  `# type: ignore[assignment]` на каждой записи. Все прежние вызывающие звали
  `strip_internal` перед сериализацией; добавленный позже library-режим не
  звал, потому что в типе про это ничего не сказано. Массивы переехали в
  отдельное поле `CompareResult.maps`, которое не сериализуется вовсе; туда же
  ушёл `_dom_changes` из AI-слоя. `artifacts` снова значит то, что написано.
- `strip_internal` перестал быть условием сериализуемости и стал тем, чем и
  должен быть: освобождением памяти. Четыре массива размером со скриншот,
  удерживаемые столько, сколько живёт результат, — на наборе из двухсот
  снимков это разница между прогоном и OOM. Library-режим теперь их отпускает.
- **Ни один хук плагина больше не может уронить сессию.** `pytest_configure`,
  `pytest_unconfigure`, `pytest_sessionfinish`, `pytest_terminal_summary` и
  `pytest_runtest_makereport` обёрнуты: отказ необязательной части —
  предупреждение и работа дальше. Единственное намеренное исключение —
  сломанная конфигурация, она по-прежнему падает на загрузке, до первого теста.
- `models._json_default` (сериализатор, кормящий отчёт, не имеет права падать)
  дополнен: большой массив описывается, а не разворачивается в JSON. Иначе
  падение заменялось бы отчётом на сотни мегабайт, который никто не откроет.
- Потолки по мажорной версии в базовых зависимостях: `numpy<3`,
  `opencv-python-headless<6`, `pillow<13`, `pyyaml<7`. Плюс работа `latest-deps`
  в CI, которая ставит самые свежие версии в обход этих потолков и гоняет по
  ним движок и library-режим: следующий мажорный релиз должен ломать наш CI, а
  не чужой прогон. OpenCV 5.0 и numpy 2.x уже приехали именно так.

### Хвосты этапов 1–2

- **Ломающее для флагов:** `--vistest-profile` убран, остался
  `--vistest-platform` (ini `vistest_platform`, переменная `VISTEST_PLATFORM`,
  аргумент `expect_screenshot(platform=...)`). Слово «профиль» в пакете уже
  значит правила имён снимков (`NamingProfile`, `SuiteProfile`); третьего
  значения ему не нужно, а имя в публичной библиотеке меняется только до
  публикации.
- **Коллизия имён.** Два теста, пишущих один эталон, давали одну строку в
  отчёте и зелёный прогон — а под `--vistest-update` перезаписывали эталон
  друг друга в чужом репозитории. Теперь части отчёта группируются по ключу и
  различным `nodeid`: повтор одного теста — ретрай и молчание, два теста —
  коллизия в итоге прогона с ключом и обоими именами и ненулевой код возврата.
  Проверяется под `pytest -n 2`, где тесты живут в разных процессах.
- Нечитаемая часть отчёта по-прежнему пропускается, но теперь считается: «N
  checks could not be read into this report» и в отчёте, и в итоге прогона.
- Цель без живой страницы (байты, файл, массив) кладёт эталоны в корень — это
  не изменилось, но теперь об этом один раз за прогон говорит предупреждение:
  иначе macOS разработчика и linux-CI сравниваются с одним файлом.
- «Эталона нет» теперь называет платформу, на которой он есть. Самый частый и
  самый непонятный отказ таких инструментов.
- `PUT /api/settings/thresholds` писал значение как `repr(number)` —
  питоновское представление числа в колонке БД. Теперь число биндится
  параметром; миграции не нужно, старые строковые значения читаются как
  читались, и это закреплено тестом.
- Обход дерева при определении набора обрывается на 20000 записях, как и
  раньше, но пишет об этом в лог с числом и корнем: «оборван» и «ничего не
  нашлось» перестали выглядеть одинаково.
- `pip install vistest` тянет ещё и PyYAML. «Поставил пакет, написал
  vistest.yaml, получил требование доустановить парсер» — плохие первые пять
  минут ради 700 килобайт; чистая база защищает от веб-сервера, а не от
  разбора конфига. Extra `yaml` убран.
- `PUT /api/settings/thresholds` приводит значение к `float` явно на месте
  записи, а `validate()` закреплён тестом на тип возврата. К схеме `setting`
  дописано, почему колонка остаётся `TEXT`: значения читаются по имени и в SQL
  не сортируются и не сравниваются, поэтому миграция в `REAL` не окупается.
- `tests/test_ui_boot_smoke.py` — восемь одинаковых тел свёрнуты в один хелпер,
  таймаут поднят до 600 с, все проверки помечены одной группой
  (`xdist_group("jsdom")`): под `pytest -n --dist loadgroup` они идут на одном
  воркере и не конкурируют за диск. Импорт jsdom — это ввод-вывод на сотни
  файлов, а не работа процессора, и на сетевом диске он один занимает минуту;
  сообщение при таймауте теперь называет эту причину, а не показывает голый
  `TimeoutExpired`.
- `scripts/check_i18n.py` — храповик по русскому тексту: ищет кириллицу во всех
  файлах репозитория (кроме `*.ru.md` — это переводы, а не долг) и падает, если
  файл вырос сверх записанного в `scripts/i18n_debt.txt` или если кириллица
  появилась в файле, которого в списке нет. Заведён текущим состоянием: 12702
  строки в 220 файлах. Отдельная работа в CI.

### Ошибки конфигурации стали громкими

Правило: **ошибка конфигурации — падение, отказ необязательной части —
предупреждение и работа дальше.** Раньше в нескольких местах было наоборот, и
в чужом CI это выглядело как «настройка не работает», без единой строки в логе.

- Переменные окружения читаются через `env_int` / `env_float` / `env_flag` /
  `env_text` и падают с текстом, называющим переменную и значение.
  `VISTEST_ENGINE_MAX_PIXELS`, `VISTEST_FAIL_SEVERITY`,
  `VISTEST_MAX_CHANGED_AREA_PCT`, `VISTEST_UPDATE_BASELINES`,
  `VISTEST_PERCEPTUAL`, `VISTEST_IN_DOCKER`, `VISTEST_MAX_*` в `/api/check`.
  Пороги дополнительно проверяются по диапазону.
- Невалидное регулярное выражение в профиле имён падает при сборке профиля
  (`NamingError`), а не доживает до `classify` голым `re.error` посреди обхода
  каталога. Неизвестный ключ в `NamingProfile.with_overrides` и в описании
  проекта тоже отвергается по имени — раньше опечатка молча теряла правило.
  Пустое значение по-прежнему означает «не задано»: форма подключения шлёт
  пустую строку для каждого незаполненного поля.
- `vistest.yaml` при отсутствии PyYAML — падение с именем файла, а не тихий
  откат к умолчаниям.
- Сузились три «глотателя исключений»: `api/thresholds._rows` терпит только
  отсутствующую таблицу, `api/baselines._cfg_with_thresholds` — только
  отсутствие сервиса, `external._threshold_env` — только `ImportError`.
  «Переопределений нет» и «таблица сломана» перестали быть одним ответом.

### Упаковка

- **Ломающее:** extra `service` переименован в `server`.
  `pip install "vistest[service]"` больше не разрешается — используйте
  `vistest[server]`. Обновлены CI, `docker/Dockerfile.api` и документация.
- Базовая установка — `numpy`, `opencv-python-headless`, `pillow`, `pyyaml` и
  ничего больше; `tests/test_library_install.py` падает и при появлении лишней
  зависимости в списке, и при появлении лишнего импорта на пути библиотеки.
  PyYAML в базе намеренно: «поставил пакет, написал vistest.yaml, получил
  требование доустановить парсер» — плохие первые пять минут ради 700 килобайт.
  Чистая база защищает от веб-сервера, а не от разбора конфига.

---

Подготовка к продуктивной эксплуатации: аудит кода и закрытие найденного.
Раздел «Безопасность» стоит первым не для порядка — там есть то, что меняет
поведение существующих установок, и прочитать это нужно до обновления.

### Безопасность

- **Заголовки прокси больше не принимаются от кого попало.** Сервис запускался
  с `--proxy-headers --forwarded-allow-ips *`; в этом режиме uvicorn берёт
  адрес клиента из первого элемента `X-Forwarded-For`, то есть из значения,
  которое пишет сам вызывающий (прокси свой адрес дописывает в конец). На этом
  адресе держалась граница «только с машины сервиса»: подключение проекта
  (запуск произвольного процесса), правка и запуск тестов, редактор
  `secrets.env`, запись с мышью — и счётчик неудачных входов. Теперь адрес
  соединения и адрес клиента — разные вещи (`vistest/api/net.py`), а доверие к
  заголовкам включается переменной `VISTEST_TRUSTED_PROXIES`.
  **Требует действия при обновлении:** если у вас перед сервисом стоит nginx,
  Caddy или Traefik — укажите его адрес в этой переменной, иначе кука сессии
  перестанет получать флаг `Secure` (или задайте `VISTEST_COOKIE_SECURE=1`).
- Схема API (`/docs`, `/redoc`, `/openapi.json`) закрыта. Открывается на время
  разбора переменной `VISTEST_DOCS=on`.
- `POST /api/check` получил пределы: размер снимка, число дополнительных
  кадров, число пикселей после декодирования. До этого файл читался в память
  целиком без всякого потолка.
- Загрузка архива проекта читается кусками во временный файл (раньше проверка
  размера стояла ПОСЛЕ чтения в память) и отказывает по распакованному объёму
  и числу записей.
- Путь артефакта больше не выпускает за каталог данных: `_resolve` принимал
  любой абсолютный путь и `../`, а результат становился эталоном.
- Интерфейс отдаётся с `Content-Security-Policy`, `X-Frame-Options: DENY`,
  `Referrer-Policy` и `Permissions-Policy`.
- Изменяющий запрос со страницы чужого сайта отклоняется по `Origin`.
  Клиентов вне браузера это не касается: они `Origin` не присылают.
- Блокировка входа перестала быть оружием против владельца учётной записи:
  жёсткий порог теперь на пару «логин + адрес», а по одному логину со всех
  адресов — высокий.
- Заявки на доступ ограничены по адресу, и у очереди появился потолок. Каждая
  заявка стоит PBKDF2 в 480 000 раундов, то есть открытая регистрация была
  усилителем нагрузки.
- Добавлен `SECURITY.md` с моделью угроз. Главное в нём: **роль `admin`
  равносильна доступу к shell на машине сервиса** — это свойство продукта, а
  не дефект.

### Исправлено

- Артефакты прогона по матрице перетирали друг друга: платформа не участвовала
  ни в пути (`artifacts/<run>/<snapshot>/`), ни в поиске сравнения, которому
  принадлежит ссылка. Шесть вариантов одного снимка складывались в один
  каталог, и в разборе падения показывалась картинка чужого браузера.
- `add_ignore_box` писал паспорт эталона без замка и без атомарной записи.
  Параллельные «Ignore area» теряли зоны, а обрыв записи оставлял битый
  `meta.json`, после чего защита от конкурентного утверждения молча
  выключалась, а нумерация версий начиналась заново.
- Уборка истории и очередь фоновых задач не знали о существовании соседних
  процессов: при нескольких воркерах или репликах на общем томе два прогона
  одного проекта шли параллельно в одни эталоны, а уборка удаляла данные из
  каждого процесса независимо.
- Пороги в `options` проверялись только по имени: строка вместо числа роняла
  сравнение пятисоткой, отрицательная севериность тихо красила всё в красный.

### Добавлено

- **Вход через корпоративный каталог (LDAP / Active Directory).** Настройка и
  проверка соединения в интерфейсе, соответствие групп ролям, появление
  человека при первом входе, пересчёт роли на каждом входе. Локальные учётные
  записи продолжают работать всегда: каталог спрашивается вторым, чтобы
  недоступный сервер не запирал администратора из его же инсталляции. Учётная
  запись из каталога не открывается локальным паролем. Пароль сервисного
  аккаунта живёт в окружении (`VISTEST_LDAP_BIND_PASSWORD`), а не в базе,
  которая уезжает в бэкапы. Ставится как `pip install "vistest[ldap]"`.

- **Перенос эталонов между инсталляциями** — `vistest baselines export|import`
  и те же действия по API. Выборка по проекту, платформе и маске имён; три
  режима слияния (`new`, `update`, `replace`) и `--dry-run`, отвечающий «что
  будет» до того, как это станет необратимым. В режиме `update` присланная
  картинка кладётся новой версией, поэтому импорт можно откатить, а маски
  игнорирования принимающей стороны переживают его.

- **Лицензионный слой.** Ключ с подписью RSA, проверяемый полностью оффлайн
  (`vistest/licensing.py`), экран **Settings → Licence**, `GET/POST
  /api/license`, выпуск ключей — `scripts/issue_license.py`. Бесплатный режим
  без ключа: 2 проекта и 5 активных пользователей, всё остальное без
  ограничений.

  По истечении срока у заказчика ничего не забирается: эталоны, история и
  разбор остаются доступны всегда. Тридцать дней льготного срока с
  предупреждением на каждом экране, после — отказ только на приёме НОВЫХ
  прогонов (402).

- `vistest backup` и `vistest restore` — копия эталонов, базы и списка
  подключённых проектов. База снимается через `VACUUM INTO`, то есть копию
  можно делать на работающем сервисе. `secrets.env` не входит без
  `--with-secrets`.
- Версия схемы базы (`PRAGMA user_version`). Старый код на базе, поработавшей
  под новой версией, теперь отказывается стартовать с внятным текстом, а не
  пишет в чужую схему.
- Плановая уборка подметает и служебные таблицы: журнал (по умолчанию год),
  протухшие сессии, заявки на разбор, завершённые задачи.
- `/metrics` отдаёт метрики самого сервиса: запросы и время по методам и
  классам кода, размер очереди фоновых задач.
- `constraints-service.txt` — зафиксированные версии для образа сервиса,
  включая транзитивные.
- В CI добавлены работы: `pip-audit`, применение миграций к базе прошлой
  версии, e2e-набор в Chromium, сборка и запуск docker-образа.

### Производительность и пределы

- У движка появился потолок по пикселям (`VISTEST_ENGINE_MAX_PIXELS`, 80 Мпикс
  по умолчанию). Сравнение разворачивает кадр в несколько массивов `float32`;
  снимок на 200 Мпикс раньше означал своп или убитый по памяти процесс — то
  есть прогон, исчезнувший без единой строки в логе.
- У тела запроса появился общий потолок (`VISTEST_MAX_BODY_MB`, 64 МБ).
  Загрузок он не касается: у них свой, более строгий.
- Размер каталога артефактов кешируется на несколько секунд: он считался
  полным обходом дерева на каждое открытие экрана настроек и на каждый тик
  уборки.

### Изменено

- **Ядро сравнения вынесено в `vistest/core/` и больше ничего за собой не
  тянет.** Готовим library-режим: пакет, который подключают к чужому проекту,
  как встроенный скриншот-тест Playwright. Поведение сравнения не изменилось
  ни в одной точке — переехали файлы и границы, а не логика.

  Что теперь в ядре: компаратор и каскад (как и раньше), зоны игнорирования
  (`core/regions.py`, бывший `vistest/zones.py`), правила именования снимков
  чужих наборов (`core/naming.py`, класс `NamingProfile`), разрешение порогов
  вердикта (`core/thresholds.py`) и голые дата-классы настроек
  (`core/settings.py`).

  Что осталось снаружи и почему: `vistest/config.py` — это загрузчик, он ищет
  `vistest.yaml` по рабочему каталогу и читает переменные окружения; чтение
  порогов из базы — реализация протокола `ThresholdStore` в
  `vistest/api/thresholds.py`; запуск чужого раннера (`command`, `install`,
  `browser_arg`, `only_arg`, определение набора по маркерам репозитория) —
  `vistest/suites/`, где `SuiteProfile` теперь наследует `NamingProfile`.

  Зависимости на базу и глобальный конфиг внутри ядра заменены явными
  параметрами: `CheckService` больше не собирает слои порогов сам, а вызывает
  `core.thresholds.patch_for`; экран настроек, паспорт снимка и переменные
  окружения для чужого pytest считают эффективное значение одной и той же
  функцией `core.thresholds.layer` — раньше эта арифметика была написана в
  четырёх местах и могла разойтись.

  Старые пути импорта продолжают работать: `vistest.zones`,
  `vistest.config.DiffConfig`, `vistest.service.SNAPSHOT_THRESHOLDS`,
  `vistest.api.thresholds.*` — всё на месте.

- **`import vistest.core` перестал поднимать сервисный слой.** `vistest/__init__.py`
  импортировал `runner` и `service` жадно, поэтому обращение к ядру тянуло за
  собой оркестратор, хранилище эталонов и модуль съёмки Playwright — 25
  модулей вместо 11. Теперь они доступны через `__getattr__` и грузятся при
  первом обращении; `from vistest import CheckService` работает как работал.
- Добавлен `tests/test_core_standalone.py`: в отдельном процессе импортирует
  `vistest.core` **и** `vistest` и падает, если загрузилось что-то за
  пределами явного allowlist'а (`vistest`, `vistest.models`, `vistest.core.*`)
  или один из шести запрещённых пакетов — `fastapi`, `uvicorn`, `starlette`,
  `sqlite3`, `ldap3`, `onnxruntime`. Allowlist, а не список запретов: перечень
  запрещённого молча одобряет всё, о чём не подумали, а тест существует именно
  ради того, о чём не подумали. Расширение ядра теперь видно одной строкой в
  диффе.
- **`DiffConfig.max_pixels`.** Предел движка по памяти был модульной
  константой `comparator.MAX_PIXELS`, читавшейся из `VISTEST_ENGINE_MAX_PIXELS`
  в момент импорта, — единственное чтение глобального состояния, остававшееся
  в ядре, и единственный параметр движка, который тест мог поменять только
  патчем модуля. Стал полем `DiffConfig` со статическим дефолтом; переменную
  окружения читает загрузчик `vistest.config`, как и все остальные. `0`
  выключает проверку. Поведение не изменилось; переменная окружения работает
  как работала.
- **Ленивый корень пакета виден mypy и автодополнению.** У `__getattr__` нет
  имён в исходнике, поэтому отложенные `CheckService`, `VisTestConfig`,
  `VisualTester` и остальные проверялись как `Any`. Добавлен блок
  `if TYPE_CHECKING:` с настоящими импортами (никогда не выполняется) и
  `__dir__`, который перечисляет отложенные имена, ничего не импортируя.
  `tests/test_lazy_exports.py` держит согласованными три списка одних и тех же
  имён: `_LAZY`, блок `TYPE_CHECKING` и `__all__` — разъехаться незаметно они
  больше не могут.
- **Тесты порядка перестали быть удачей.** Двадцать три файла делают
  `importlib.reload(vistest.api.main)`, чтобы собрать сервис на временной базе.
  `monkeypatch` возвращает `VISTEST_ROOT`, а модуль не возвращал никто: reload
  перестраивает модуль на месте, и `vistest.api.main.db` до конца сессии
  смотрел на временную базу теста, где уже заведён администратор. Дальше
  `settings._guard` делал ленивый `from .main import db`, видел
  `any_users(db) == True` и отвечал «Sign in required» — в файле про IP-адреса,
  который аутентификации не касается. В штатном порядке файлов это не всплывало
  и ждало первого `-n`, `--reverse` или запуска одного файла. Восстановление
  теперь одно, в новом `tests/conftest.py`, вместо двадцати фикстур. Набор
  проверен в обратном порядке и на случайных перестановках.
- **`tests/test_naming.py`** — 57 целевых тестов на `NamingProfile`:
  `classify`, `walk`, `roots` и особенно `with_overrides`, у которого все
  правила молчаливые (неизвестный ключ ничего не делает, пустое значение не
  стирает дефолт, голая строка становится кортежем из одного элемента,
  `search_dirs` добавляются, а не заменяются). На этом классе стоит подключение
  наборов на чужих языках, и до сих пор он был покрыт только косвенно.

- Появился `/api/v1/...` — тот же набор роутов по стабильному адресу. Префикс
  снимается до маршрутизации, то есть это не копия роутов и разойтись двум
  адресам нечем. Пути без версии остаются рабочими.
- Удаление сравнения и очистка истории снимка спрашивают право ДО проверки
  существования — как это уже делало удаление прогона. Обратный порядок
  отвечал на «а есть ли у вас объект номер 42» тому, кто не имеет права
  спрашивать.

- Образ сервиса ставит пакет обычной установкой, а не `pip install -e`, и с
  ограничением версий.
- Соединения с базой закрываются при остановке сервиса; выставлены
  `busy_timeout` и `synchronous=NORMAL`.
- Часы уборки и уведомлений останавливаются по событию, а не досыпают свой
  интервал после остановки сервиса.
