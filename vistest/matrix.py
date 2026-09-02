# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Матрица браузеров и разрешений: один снимок — несколько вариантов.

Что здесь решается. Раньше «эта страница на chromium/firefox/webkit при 1440,
768 и 390» означало девять запусков и девять несвязанных наборов эталонов.
Девять раз ответить на один и тот же сдвиг подвала — это не строгость, это
способ приучить человека жать «принять» не глядя.

Здесь снимок снова становится ОДНИМ: у него есть имя и есть варианты. Вариант —
это пара «браузер × размер окна», и у каждого варианта свой эталон, потому что
сравнивать кадр firefox при 390 с кадром chromium при 1440 бессмысленно.
Вердикт при этом один на снимок, а очередь решений группирует варианты одной
причины в один вопрос.

--------------------------------------------------------------------------
Где живёт эталон варианта, и почему именно там

Браузер уже был частью ключа платформы с самого начала: `linux-chromium-1x`.
Отрисовка шрифтов в chromium и firefox физически разная, и общий эталон у них
невозможен — это правило существовало до всякой матрицы.

Размер окна частью ключа НЕ был: он лежал в паспорте снимка. Из-за этого один
снимок мог быть снят ровно в одном размере, а `cli.py` при нескольких размерах
приклеивал размер к ИМЕНИ (`checkout-390x844`). Имя — плохое место для этого:
по нему нельзя собрать варианты обратно, и в интерфейсе они выглядят как разные
снимки, случайно похоже названные.

Поэтому размер переезжает в ключ платформы: `linux-chromium-1x-390x844`.

--------------------------------------------------------------------------
Обратная совместимость, она же самое важное решение в этом файле

Просто добавить суффикс всем — значит в день обновления обесценить все уже
снятые эталоны разом: они лежат под `linux-chromium-1x`, а прогон пойдёт искать
`linux-chromium-1x-1440x900` и запишет всё заново как новые. Человек получит
набор из ста «новых эталонов» и ни одного сравнения — и будет прав, посчитав
это поломкой.

Поэтому один размер в матрице объявляется **базовым**, и его эталоны остаются
там же, где лежали, — под ключом без суффикса. Суффикс получают только
остальные. Базовый по умолчанию первый в списке; задать его явно можно и нужно
(`matrix.base_viewport`), потому что иначе перестановка строк в конфиге меняет
раскладку на диске — а выглядит как правка форматирования.

Матрица не объявлена вовсе — всё работает ровно как раньше, до единого пути.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

BROWSERS = ("chromium", "firefox", "webkit")

# «1440x900». Верхняя граница не косметическая: Chromium не снимает полотно
# выше ~16384 px, и окно в сто тысяч пикселей — это не матрица, а опечатка,
# которая проявится через минуту ожидания и невнятную ошибку драйвера.
_VIEWPORT_RE = re.compile(r"^(\d{2,5})[x×](\d{2,5})$")
MAX_SIDE = 16000


class MatrixError(ValueError):
    """Матрица описана неверно. Отдельный тип, чтобы API отвечал 400, а не 500."""


def parse_viewport(text: str) -> tuple[int, int]:
    """«1440x900» → (1440, 900). Принимает и латинскую `x`, и знак умножения."""
    m = _VIEWPORT_RE.match(str(text or "").strip().lower().replace(" ", ""))
    if not m:
        raise MatrixError(
            f"Размер окна {text!r} не разобран: ожидается «ШИРИНАxВЫСОТА», "
            "например 1440x900.")
    w, h = int(m.group(1)), int(m.group(2))
    if not (1 <= w <= MAX_SIDE and 1 <= h <= MAX_SIDE):
        raise MatrixError(
            f"Размер окна {text!r} вне пределов: сторона до {MAX_SIDE} px "
            "(выше Chromium не снимает полотно вовсе).")
    return w, h


def normalize_viewport(text: str) -> str:
    """Канонический вид размера — он же кусок ключа хранения.

    Приводить к одному виду обязательно: «1440X900», «1440×900» и « 1440x900 »
    описывают один и тот же вариант, а как ключи каталога это три разных
    набора эталонов, и разошлись бы они молча.
    """
    w, h = parse_viewport(text)
    return f"{w}x{h}"


def normalize_browser(text: str) -> str:
    name = str(text or "").strip().lower()
    if name not in BROWSERS:
        raise MatrixError(
            f"Браузер {text!r} неизвестен: {', '.join(BROWSERS)}.")
    return name


@dataclass(frozen=True)
class Variant:
    """Один вариант снимка: браузер, размер окна, масштаб.

    `base` — тот самый вариант, чьи эталоны лежат под ключом без суффикса. Их
    ровно столько же, сколько браузеров: базовый размер один на всю матрицу, а
    браузер в ключе был всегда.
    """

    browser: str
    viewport: str | None      # None — «как настроено в capture», без суффикса
    scale: float = 1.0
    base: bool = False
    # Готовый ключ хранения, если он уже известен и вычислять его не надо.
    #
    # Нужен там, где человек выбрал конкретный набор: «переснять ЭТОТ снимок»,
    # «прогнать ЭТУ платформу». Ключ мог быть снят на другой машине — эталоны
    # docker при сервисе на Windows это обычный случай, — и пересборка ключа из
    # полей дала бы `win-...` вместо `docker-...`, то есть увела бы пересъёмку
    # в пустой набор и записала бы там «новый эталон». Известный ключ всегда
    # правдивее вычисленного.
    key: str | None = None

    @property
    def size(self) -> tuple[int, int] | None:
        return parse_viewport(self.viewport) if self.viewport else None

    @property
    def platform(self) -> str:
        """Ключ хранения эталонов этого варианта."""
        from .config import platform_key

        if self.key:
            return self.key
        suffix = "" if (self.base or not self.viewport) else self.viewport
        return platform_key(self.browser, self.scale, viewport=suffix or None)

    @property
    def label(self) -> str:
        """Как вариант называется для человека."""
        return (f"{self.browser} · {self.viewport.replace('x', '×')}"
                if self.viewport else self.browser)

    @property
    def slug(self) -> str:
        """Имя каталога под артефакты этого варианта внутри прогона.

        Своё на вариант — и это не аккуратность. Артефакты складываются в
        `<прогон>/<slug имени снимка>/`, а имя у вариантов одно на всех: без
        разделения второй вариант затёр бы картинки первого, и разбор показал
        бы кадр firefox под подписью chromium.
        """
        return self.platform

    def as_dict(self) -> dict:
        return {"browser": self.browser, "viewport": self.viewport,
                "scale": self.scale, "platform": self.platform,
                "label": self.label, "base": self.base}


def expand(browsers, viewports, *, base_viewport: str | None = None,
           scale: float = 1.0) -> list[Variant]:
    """Списки → варианты. Порядок предсказуем: браузеры внешний цикл, размеры внутренний.

    Порядок важен по той же причине, по которой важен базовый размер: он
    определяет, в каком порядке человек увидит варианты в разборе, и меняться
    от запуска к запуску не должен.
    """
    names = [normalize_browser(b) for b in (browsers or []) if str(b).strip()]
    sizes = [normalize_viewport(v) for v in (viewports or []) if str(v).strip()]

    # Дубликаты — это не ошибка описания, а обычная невнимательность в списке из
    # шести строк. Молча схлопываем, сохраняя первый порядок: падать здесь
    # значило бы требовать аккуратности там, где цена ошибки нулевая.
    names = list(dict.fromkeys(names)) or ["chromium"]
    sizes = list(dict.fromkeys(sizes))

    if not sizes:
        # Матрица только по браузерам — размер берётся из `capture`, как раньше.
        return [Variant(browser=b, viewport=None, scale=scale, base=True)
                for b in names]

    base = normalize_viewport(base_viewport) if base_viewport else sizes[0]
    if base not in sizes:
        raise MatrixError(
            f"Базовый размер {base} не входит в матрицу ({', '.join(sizes)}). "
            "Базовый — это тот, чьи эталоны уже сняты и остаются на месте; "
            "размер, которого в матрице нет, таким быть не может.")

    return [Variant(browser=b, viewport=v, scale=scale, base=(v == base))
            for b in names for v in sizes]


def from_config(cfg, *, browsers=None, viewports=None,
                base_viewport: str | None = None) -> list[Variant]:
    """Варианты для этого прогона: аргументы вызова сильнее конфига.

    Пустая матрица — это НЕ «нет вариантов», а «один вариант, как раньше».
    Возвращать пустой список отсюда значило бы заставить каждого вызывающего
    писать `or [default]`, и рано или поздно кто-нибудь этого не напишет —
    прогон молча проверит ноль снимков и попадёт в историю зелёным.
    """
    m = getattr(cfg, "matrix", None)
    scale = float(getattr(cfg.capture, "device_scale_factor", 1.0) or 1.0)
    names = browsers if browsers is not None else list(getattr(m, "browsers", ()) or ())
    sizes = viewports if viewports is not None else list(getattr(m, "viewports", ()) or ())
    base = base_viewport if base_viewport is not None \
        else getattr(m, "base_viewport", None)
    return expand(names, sizes, base_viewport=base, scale=scale)


def is_matrix(variants) -> bool:
    """Больше одного варианта — значит прогон матричный, и интерфейс говорит об этом иначе."""
    return len(variants or []) > 1


def variant_of_platform(platform: str) -> dict:
    """Ключ платформы → что за вариант, для показа человеку.

    Обратный разбор нужен интерфейсу и отчётам: в базе у сравнения лежит именно
    ключ, а подпись «chromium · 390×844» человеку понятнее, чем
    `linux-chromium-1x-390x844`.

    Разбирается по форме, а не по словарю известных браузеров: ключ мог быть
    записан версией, которая знала другой их список, и отвечать «не знаю» на
    собственные данные — худший из вариантов.
    """
    text = str(platform or "")
    m = re.match(r"^(?P<system>[^-]+)-(?P<browser>[^-]+)-(?P<scale>[\d.]+)x"
                 r"(?:-(?P<viewport>\d{2,5}x\d{2,5}))?$", text)
    if not m:
        return {"platform": text, "label": text, "browser": "", "viewport": None,
                "system": "", "scale": None}
    vp = m.group("viewport")
    browser = m.group("browser")
    return {
        "platform": text,
        "system": m.group("system"),
        "browser": browser,
        "scale": float(m.group("scale")),
        "viewport": vp,
        "label": f"{browser} · {vp.replace('x', '×')}" if vp else browser,
    }


# --------------------------------------------------------------------------- #
#  Какие движки на этой машине вообще есть
# --------------------------------------------------------------------------- #
#
# Список `BROWSERS` — это то, что УМЕЕТ Playwright, а не то, что установлено
# здесь. Разница между ними стоила целого дня отладки, и вот как она выглядела
# снаружи.
#
# Меню «▾» предлагало три движка. Человек выбирал firefox, снятие набора
# уходило в работу, задача краснела где-то в глубине лога, а на карточке
# появлялся `docker-firefox-1x (0)` — каталог есть, снимков нет. Следующий
# прогон отвечал «набор для docker-firefox-1x ещё не снят — снимите его через
# ⋯ → Snap VisTest baselines», человек делал ровно это, и круг замыкался.
#
# Настоящая причина всё это время была одной строкой глубоко в выводе
# Playwright: «Executable doesn't exist at …/firefox-1495/firefox/firefox».
# Движок не установлен. `pip install playwright` кладёт БИБЛИОТЕКУ; сами
# браузеры скачиваются отдельной командой, и образ, собранный из
# `python:3.12-slim`, не содержит ни одного из них.
#
# Ни один экран об этом не говорил, потому что никто не спрашивал. Спрашиваем.
#
# Три состояния, а не два. «Нет пакета playwright» и «есть пакет, нет движка» —
# разные диагнозы с разными командами, и склеивать их в «не установлен» значит
# отправлять половину людей выполнять команду, которая им не поможет.
NO_PLAYWRIGHT = "no-playwright"
NOT_INSTALLED = "not-installed"
INSTALLED = "installed"

_STATE: dict[str, str] = {}

# Код-зонд отдельной строкой: он выполняется и здесь, и ЧУЖИМ интерпретатором
# (см. `external._project_browser_state`) — у проекта свой playwright, и его
# набор движков к нашему отношения не имеет.
PROBE = (
    "import json,os,sys\n"
    "try:\n"
    "    from playwright.sync_api import sync_playwright\n"
    "except Exception:\n"
    "    print(json.dumps({}));sys.exit(0)\n"
    "out={}\n"
    "try:\n"
    "    with sync_playwright() as p:\n"
    "        for n in ('chromium','firefox','webkit'):\n"
    "            try:\n"
    "                out[n]=bool(os.path.exists(str(getattr(p,n).executable_path)))\n"
    "            except Exception:\n"
    "                out[n]=False\n"
    "except Exception:\n"
    "    out={}\n"
    "print(json.dumps(out))\n"
)


def _probe_here() -> dict[str, bool]:
    """Тот же зонд, но про СВОЁ окружение — и обязательно в отдельном процессе.

    Своя съёмка (`record/snap.py`, `doctor`, «Capture a screenshot») поднимает
    Playwright прямо здесь, поэтому для неё вопрос «установлен ли webkit»
    решается нашим окружением и никаким другим.

    А вот спрашивать это ВНУТРИ процесса нельзя, и цена ошибки здесь особенно
    неприятная. `sync_playwright()` отказывается работать, если его позвали из
    потока с петлёй asyncio, — а сервис живёт под uvicorn. Роут синхронный и
    уезжает в пул потоков, так что сегодня повезло бы; завтра кто-нибудь
    напишет `async def`, зонд бросит исключение, оно попадёт в `except` — и все
    три движка молча станут «не установлены». То есть интерфейс начнёт врать
    ровно в ту сторону, ради которой этот код и написан.

    Подпроцесс от этого не зависит вовсе: там своя петля и своё всё.
    """
    import json
    import subprocess
    import sys

    try:
        done = subprocess.run([sys.executable, "-c", PROBE],
                              capture_output=True, timeout=30, text=True)
        found = json.loads((done.stdout or "{}").strip().splitlines()[-1])
    except Exception:
        return {}
    return found if isinstance(found, dict) else {}


def browser_state(name: str, *, recheck: bool = False) -> str:
    """`installed` | `not-installed` | `no-playwright` — для СВОЕГО окружения.

    Ответ запоминается: путь к бинарю за время жизни процесса не меняется, а
    поднимать Playwright на каждую отрисовку карточки — треть секунды на
    ровном месте. `recheck` — когда движок доустановили и ответ нужен сейчас, а
    не после перезапуска сервиса.
    """
    if recheck:
        _STATE.clear()
    if not _STATE:
        found = _probe_here()
        if not found:
            for b in BROWSERS:
                _STATE[b] = NO_PLAYWRIGHT
        else:
            for b in BROWSERS:
                _STATE[b] = INSTALLED if found.get(b) else NOT_INSTALLED
    return _STATE.get(name, NOT_INSTALLED)


def installed_browsers(*, recheck: bool = False) -> dict[str, str]:
    """Все известные движки и их состояние. Ровно это уезжает в интерфейс."""
    return {name: browser_state(name, recheck=recheck) for name in BROWSERS}


def install_hint(name: str, state: str = NOT_INSTALLED, *, where: str = "") -> str:
    """Что ответить человеку, выбравшему движок, которого здесь нет.

    Ответ обязан содержать команду. «Движок не установлен» без неё — это та же
    загадка, только сформулированная вежливее: `pip install playwright` уже
    выполнен, пакет на месте, и догадаться, что браузеры ставятся ОТДЕЛЬНОЙ
    командой, можно только зная это заранее.
    """
    at = f" in {where}" if where else ""
    if state == NO_PLAYWRIGHT:
        return (f"Playwright is not installed{at}, so there is no {name} to run "
                f"in. Install it and the engine: «pip install playwright && "
                f"playwright install {name}».")
    return (f"The {name} engine is not installed{at}. Playwright ships the "
            f"library, the browsers are downloaded separately — run "
            f"«playwright install {name}» (in Docker: «docker compose exec "
            f"<service> playwright install {name}»), then try again.")


class BrowserMissing(RuntimeError):
    """Движок, в котором просили гнать, не установлен.

    Отдельный тип, потому что это ОБЪЯСНЁННЫЙ отказ, а не поломка: наверху он
    превращается в `JobFailure` и доезжает до человека одной строкой с
    командой, без питоновского трейса. Трейс здесь врал бы — он говорит
    «инструмент сломался» ровно там, где инструмент отработал правильно и
    честно сказал, чего не хватает.
    """


def launch(playwright, name: str, **kwargs):
    """Поднять движок — или объяснить, почему нельзя.

    Здесь везде стояло голое `getattr(p, browser).launch(headless=True)`, и
    отсутствие движка выглядело как двадцать строк рамочки из символов
    Playwright посреди лога задачи. Из неё следовало «run playwright install»,
    но не следовало НИ КАКОЙ движок, ни то, что VisTest при этом ничего не
    снял: человек видел красную задачу и пустой набор.
    """
    try:
        return getattr(playwright, name).launch(**kwargs)
    except Exception as e:
        text = str(e)
        if "Executable doesn't exist" in text or "playwright install" in text:
            raise BrowserMissing(install_hint(name, NOT_INSTALLED)) from None
        raise
