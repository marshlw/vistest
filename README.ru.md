# VisTest

**Визуальное регресс-тестирование для pytest и Playwright, которое объясняет, почему проверка упала.**

*Read in English: [README.md](https://github.com/marshlw/vistest/blob/main/README.md)*

```bash
pip install "vistest[browser]"
playwright install chromium
```

```python
from vistest import expect_screenshot


def test_home(page):
    page.goto("http://localhost:8000")
    expect_screenshot(page, "home.png")
```

Первый прогон красный, потому что эталона ещё нет: `pytest --vistest-update`
записывает его в `tests/__vistest__/`, вы смотрите на него и коммитите, и
дальше каждый прогон сравнивает с ним.

Когда страница меняется, проверка падает и говорит, что изменилось. Здесь это
[демо-страница](https://github.com/marshlw/vistest/blob/main/examples/demo_page.html)
из репозитория, у которой перекрашена одна кнопка:

![Картинка diff упавшей проверки: страница приглушена, изменённая кнопка в жёлтой рамке с подписью FILL 92, замаскированные области пурпурные](https://raw.githubusercontent.com/marshlw/vistest/main/docs/img/failure.png)

```
vistest: 'landing.png' differs from the baseline (linux-chromium-1x-1280x800)
  1 change not explained as rendering noise · worst 92/100 · no threshold set, so any change fails · 0.50% of the frame
  reason:      fill in 1; worst 112x48 at (522, 418)
  second look: the second picture is the same as the first, so the change is real, not motion
  renderer:    same as the baseline's
  baseline:    tests/__vistest__/linux-chromium-1x-1280x800/landing.png
  actual:      .vistest/actual/linux-chromium-1x-1280x800/landing.png
  changed:     112x48 at (522, 418): fill: #3478f6 → #1d4ed8, color difference 14.5
               (color difference is ΔE00 (CIEDE2000): 1 ≈ barely visible, 10 and more is plainly another color)
  diff:        .vistest/diff/linux-chromium-1x-1280x800/landing.png
  report:      .vistest/report/index.html
  accept it:   pytest --vistest-update
```

(Пути укорочены до корня проекта; в сообщении они полные. Сообщения VisTest
только на английском.)

---

## Что это

Одна функция, `expect_screenshot`, эталоны лежат в вашем репозитории, и
больше ничего нигде не запущено. Упавшая проверка называет, что изменилось —
«fill: #3478f6 → #1d4ed8», «block moved by +1 px along y», — а не печатает
процент. Есть и сервер для команд — коммерческий продукт.

## Установка подробнее

`[browser]` приносит Playwright и `pytest-playwright` — он и даёт тестам
фикстуру `page`; вторая строка скачивает браузер. Нужен Python 3.11 или новее.
Без `[browser]` пакет сравнивает картинки, которые ему передали, и браузер не
открывает.

## Пример подробнее

```python
from pathlib import Path

import pytest

from vistest import expect_screenshot

DEMO = (Path(__file__).parent / "demo_page.html").resolve().as_uri()

#  The line with a counter that ticks every 200 ms and a spinner that never
#  stops: masked, because a moving number is not a regression. A selector is
#  painted over in the picture, the way Playwright's own `mask=` does it.
LIVE = ["p.lead"]


@pytest.fixture
def demo(page):
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(DEMO)
    return page


def test_landing_page(demo):
    """The whole window."""
    expect_screenshot(demo, "landing.png", mask=LIVE)


def test_pricing_cards(demo):
    """One element: a Locator is photographed alone, smaller and steadier."""
    expect_screenshot(demo.locator("#pricing"), "pricing.png")
```

Это [`examples/test_library.py`](https://github.com/marshlw/vistest/blob/main/examples/test_library.py) из репозитория без
третьего теста и строки, которая пропускает файл, когда Playwright не
установлен; страница, которую он открывает, `demo_page.html`, лежит рядом.
Попробовать как есть:

```bash
mkdir -p tests
curl -fsSo tests/test_library.py https://raw.githubusercontent.com/marshlw/vistest/main/examples/test_library.py
curl -fsSo tests/demo_page.html https://raw.githubusercontent.com/marshlw/vistest/main/examples/demo_page.html
```

В своём проекте открывайте `page.goto` свой адрес. `expect_screenshot`
принимает `Page` или `Locator` Playwright, PNG-байты, `PIL.Image`, массив numpy
или путь к PNG.

## Первый прогон и эталоны

```bash
pytest tests/test_library.py
```

Первый прогон красный: эталона ещё нет, а проверка, которая проходит до того,
как картинку кто-то увидел, — не проверка:

```
vistest: no baseline for 'landing.png' (linux-chromium-1x-1280x800)
  expected:    tests/__vistest__/linux-chromium-1x-1280x800/landing.png
  captured:    .vistest/actual/linux-chromium-1x-1280x800/landing.png
  create it:   pytest --vistest-update
               then commit the file — in CI the baseline has to come from the repository, not from the run
```

(Пути укорочены до корня проекта; в сообщении они полные.) Примите картинки,
посмотрите на них и закоммитьте:

```bash
pytest tests/test_library.py --vistest-update
git add tests/__vistest__
git commit -m "visual baselines"
pytest tests/test_library.py                     # зелёный
```

В `.vistest/` лежат картинки последнего прогона и отчёт; в git им не место:

```gitignore
# VisTest artifacts. Baselines live in tests/__vistest__/ and MUST be committed.
.vistest/
```

PNG быстро раздувают репозиторий, поэтому эталонам обычно лучше в LFS:
`git lfs track "tests/__vistest__/**/*.png"`.

## Когда проверка падает

Изменение, которое сдвигает всё под собой, даёт сообщение длиннее — например,
если на той же странице шрифт цены уменьшить с 34 до 30 px. Образец такого
сообщения и разбор каждой его строки — в английском README, раздел
[«When a check fails»](https://github.com/marshlw/vistest/blob/main/README.md#when-a-check-fails).

Если изменение и было задумано, примите его `pytest --vistest-update` и
закоммитьте новый эталон. Отчёт, `.vistest/report/index.html`, — один файл,
картинки внутри, ничего не скачивает: открывается без сети и прикладывается к
тикету.

## В CI

CI сравнивает с эталонами из репозитория и никогда их не пишет. Текст в
Windows, macOS и Linux рисуется по-разному, поэтому у каждой платформы свой
каталог эталонов (ниже), и CI нужен набор, снятый там, где он работает.
Закрепите обе половины — систему раннера и версию Playwright:

```yaml
# .github/workflows/visual.yml
name: visual
on: [push, pull_request]
jobs:
  visual:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v5
        with: { lfs: true }
      - uses: actions/setup-python@v6
        with: { python-version: "3.12" }
      - run: pip install "vistest[browser]" "playwright==1.56.0"
      - run: playwright install --with-deps chromium
      - run: pytest
      - uses: actions/upload-artifact@v4
        if: failure()
        with: { name: vistest, path: .vistest/ }
```

Для GitLab — [`.gitlab-ci.example.yml`](https://github.com/marshlw/vistest/blob/main/.gitlab-ci.example.yml), в образе
Playwright той же версии. Набор для CI в первый раз берётся из самого CI:
прогон падает с «no baseline», вы скачиваете `.vistest/actual/`, смотрите
картинки, кладёте их в `tests/__vistest__/` и коммитите. Новый Playwright
приносит новый Chromium, а он рисует часть пикселей иначе, поэтому версию
меняйте осознанно и после смены снимайте эталоны заново.

`pytest -n auto` поддерживается: каждая запись идёт через временный файл и
атомарное переименование, а отчёт собирается один раз в конце.

## Настройки

Пять флагов pytest:

| Флаг | `pyproject.toml` | Что делает |
| --- | --- | --- |
| `--vistest-update[=MODE]` | — | принять снимки как эталоны: `missing` — только отсутствующие; `changed` (по умолчанию и для голого флага) — отсутствующие и упавшие; `all` — всё, что отличается хоть на байт |
| `--vistest-baselines=PATH` | `vistest_baselines` | где лежат эталоны (по умолчанию `tests/__vistest__`) |
| `--vistest-platform=NAME` | `vistest_platform` | каталог платформы, например `linux-chromium-1x-1440x900`; не задан — берётся со страницы |
| `--vistest-report=PATH` | `vistest_report` | куда писать HTML-отчёт |
| `--vistest-config=PATH` | — | какой vistest.yaml читать, если не тот, что найден от рабочего каталога вверх |

Голый `--vistest-update` считает следующий аргумент режимом, поэтому пути
пишите первыми — `pytest tests/ --vistest-update` — или назовите режим явно:
`pytest --vistest-update=changed tests/`.

Порядок один для всех настроек: **вызов или командная строка, потом
окружение, потом файл — `vistest.yaml`, а для флагов `pyproject.toml`, —
потом умолчание.** У порога есть ещё слой между вызовом и окружением: паспорт
самого снимка (`.json` рядом с эталоном). Таблица всех настроек — в английском
README, раздел [«Settings»](https://github.com/marshlw/vistest/blob/main/README.md#settings).

`VISTEST_UPDATE_BASELINES` — единственное исключение, и намеренное: под pytest
эталоны в репозиторий пишет только командная строка — переменная, которую
выставил CI, не должна принимать картинки прогона.

Сам вызов:

```python
expect_screenshot(
    page, "checkout.png",
    fail_severity=40,                  # 0–100; и max_changed_area_pct=, в %
    mask=["#promo", (0, 0, 320, 64)],  # селекторы закрашиваются, прямоугольники исключаются
    full_page=False,
    scale="css",                       # или "device"; ключ платформы скажет какой
    timeout_ms=10000,                  # вся проверка; дольше — CaptureError
                                       # (зависшая страница: до 1,5 с сверху, чтобы сказать почему)
)
```

`vistest.yaml` необязателен; в [`examples/vistest.yaml`](https://github.com/marshlw/vistest/blob/main/examples/vistest.yaml)
перечислены все ключи, которые читает библиотека, и что каждый делает. Ключ,
который библиотека не читает, называется в одном предупреждении за прогон, а не
молча ничего не меняет. Все параметры, ключи и шаги — в
[гайде](https://github.com/marshlw/vistest/blob/main/docs/GUIDE.md) (на английском).

## Где что лежит

```
tests/__vistest__/                 <- коммитится, смотрится на ревью
  linux-chromium-1x-1440x900/      по каталогу на платформу
    home.png                       эталон
    home.json                      его паспорт: размер, пороги, как снят
    shop/checkout.png
  .renderers/<sha256>.png          канарейки рендера, которыми сняты эталоны

.vistest/                          <- в .gitignore
  actual/…  diff/…  report/index.html
```

Если в проекте нет папки `tests/`, эталоны ложатся в `__vistest__/` в корне.

**Платформа** — это имя каталога: операционная система, браузер, масштаб
картинки и размер окна — `linux-chromium-1x-1440x900`. Эталон, снятый в
Windows, никогда не сравнивается со снятым в Linux: шрифты там рисуются
по-разному. `platform=` и `--vistest-platform` задают имя сами; оставляйте в
нём систему (`linux-chromium-1x-1440x900`) — или знайте, что без неё
(`chromium-1440x900`) у всех машин один набор, и macOS разработчика с Linux в
CI сравниваются с одним и тем же файлом. У цели, которая не живая страница, —
байты, файл, массив — нет ни браузера, ни размера окна: её эталоны ложатся в
корень каталога эталонов, и об этом один раз за прогон говорит
предупреждение.

## Не Python: `vistest check`

Та же проверка для картинки, снятой чем угодно — тестом на Node, набором на
JVM, скриптом:

```bash
vistest check checkout.png shots/checkout.png --platform linux-chromium-1x-1440x900
echo $?          # 0 совпало, 1 отличается, 2 эталона ещё нет (--update его запишет)
vistest check checkout.png shots/checkout.png --json
```

`--json` печатает один объект: `verdict` (`pass`, `fail`, `missing` или
`new_baseline`), `message` и пути `baseline`, `actual` и `diff`. Раскладка и
сообщения — библиотечные. Ещё в библиотеке:

- `vistest compare expected.png actual.png -o diff/` — два PNG, без эталонов;
- `vistest doctor https://your-app.example` — грузит неизменённую страницу
  несколько раз и считает, сколько раз проверка упала бы: шум вашего стенда
  до того, как доверять инструменту;
- `vistest bench test-results` — движок на ваших собственных падениях против
  компаратора самого Playwright; расхождения собраны для человека.

## Дальше

- [docs/GUIDE.md](https://github.com/marshlw/vistest/blob/main/docs/GUIDE.md) — библиотека целиком, на английском: все
  параметры, ожидание готовности и второй взгляд, маски, движок и то, чего он
  не различает.
- [CHANGELOG.md](https://github.com/marshlw/vistest/blob/main/CHANGELOG.md) — что изменилось и что поменять в своих тестах.

---

## Проверка движка

```bash
pytest tests/                           # инварианты: шум не валит, регрессы находятся
python tests/benchmark.py --artifacts   # таблица «шум / сигнал» + картинки
python tests/benchmark.py --regenerate  # перерисовать замороженный корпус (осознанно)

# VisTest против absdiff и настоящих pixelmatch и Playwright (Node.js >= 18)
npm ci --prefix scripts/bench
node scripts/bench_pixelmatch.mjs > docs/benchmark_native.json
python tests/benchmark.py --compare --native docs/benchmark_native.json \
    --no-timing --markdown docs/benchmark.md
```

Без `--native` команда `--compare` считает pixelmatch и Playwright нашим портом
их ядра на numpy (`tests/baselines.py`). Это быстрая проверка без Node, она
помечена как порт и опубликованной цифрой не бывает.

Бенчмарк на синтетике — основной инструмент подстройки порогов: после любой
правки конфигурации сразу видно, не начал ли движок ловить шум. Настоящие
скриншоты без разметки для этого не годятся — в них неизвестно, что «правда».
Настоящие скриншоты с мутациями годятся: берётся реальный рендер, к нему
применяется изменение, метка которого известна заранее (перекрашенная кнопка,
сдвинутый блок, пережатый кадр), и «правда» известна по построению.

Главная метрика — **false-fail rate**: доля неизменённых по существу страниц,
на которых инструмент дал красный тест.

### Цифры

Сравнение с другими инструментами на настоящих рендерах браузера будет
опубликовано в одном из следующих релизов, с условиями прогона, записанными
до него. До тех пор цифр на этой странице нет.

Соответствие ΔE00 контрольным парам из CIE Technical Report (Sharma et al.,
2005) проверяется отдельно.

---

# Лицензия

Apache-2.0. См. [LICENSE](https://github.com/marshlw/vistest/blob/main/LICENSE) и [NOTICE](https://github.com/marshlw/vistest/blob/main/NOTICE).

Используйте, меняйте и распространяйте — в открытом или закрытом коде; LICENSE
и NOTICE должны идти вместе с тем, что вы распространяете. Имя VisTest лицензия
не передаёт: изменённую версию, которую вы распространяете, называйте по-своему.
