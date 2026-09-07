"""FastAPI service: run intake, review flow, metrics, UI serving."""

from __future__ import annotations

import json
import os
import re
import shutil
import threading as _threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import (
    Body,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
)
from fastapi.staticfiles import StaticFiles

from ..config import VisTestConfig
from . import decisions as _decisions
from . import license as _license
from . import logs as _logs
from . import metrics as _metrics
from . import net as _net
from . import notify as _notify
from . import retention as _retention
from . import rights as _rights
from . import rundiff as _rundiff
from .baselines import router as baselines_router
from .check import router as check_router
from .db import Database
from .doctor import router as doctor_router
from .jobs import runner as _jobs_runner
from .license import router as license_router
from .projects import router as projects_router
from .record import router as record_router
from .settings import router as settings_router

ROOT = Path(os.getenv("VISTEST_ROOT", ".vistest")).resolve()
ARTIFACTS = ROOT / "artifacts"


def _find_frontend() -> Path:
    """Review UI directory.

    From source it lives at the repository root, from an installed package it
    lives inside `vistest/`. Without the second option `pip install vistest &&
    vistest serve` starts the API with no interface and silently returns 404 on
    `/ui/`.
    """
    here = Path(__file__).resolve()
    candidates = [
        here.parents[1] / "frontend",   # installed package: vistest/frontend
        here.parents[2] / "frontend",   # repository: <repo>/frontend
    ]
    if v := os.getenv("VISTEST_FRONTEND"):
        candidates.insert(0, Path(v).resolve())
    for c in candidates:
        if (c / "index.html").exists():
            return c
    return candidates[-1]


FRONTEND = _find_frontend()

db = Database(ROOT / "vistest.db")
cfg = VisTestConfig.load()

# Фоновые задачи получают хранилище — и заодно разбираются с тем, что осталось
# от прошлого запуска. Задача жила в памяти процесса, и при перезапуске
# **исчезала**: опрос получал 404, а интерфейс писал «connection to the task
# lost», то есть винил сеть. Воскресить её нельзя (внутри чужой pytest или
# браузер), но пропадать бесследно она не должна.
_jobs_runner.bind(db)

# Единственное, что уходит из сервиса наружу само. Всё остальное здесь работает
# через «человек придёт и посмотрит», и это верно ровно до первого дня, когда он
# не пришёл: двенадцать падений лежат третьи сутки, а никто не знает, что
# смотреть надо было позавчера. По умолчанию выключено и без вебхука молчит.
_notify.ensure_ticker(db, base_url=(os.getenv("VISTEST_PUBLIC_URL") or "").strip())

# Уборка истории по расписанию. Кнопка «почистить старое» была с самого начала,
# но нажимать её надо помнить — то есть не нажимают, и `.vistest` съедает
# десятки гигабайт за пару месяцев. Выключено по умолчанию: это единственное
# фоновое действие, которое удаляет данные.
_retention.ensure_ticker(db, lambda rid, key: _drop_run_files(rid, key),
                         lambda: _storage_kb(),
                         checks_dir=ARTIFACTS / "checks")

# --------------------------------------------------------------------------- #
#  Who may reach the API at all
#
#  Until now only *destructive* routes asked who is calling. Everything that
#  merely reads — the run list, a comparison with its regions, the metrics, the
#  storage size, the ticket report — answered anyone who could open the port.
#  The `viewer` role existed and guarded `/files`, so the pictures were closed
#  while the URLs, CSS selectors, element texts, branches and commit hashes
#  were not. `report.html` made even that moot: it embeds the images inside the
#  HTML, so one unauthenticated GET returned exactly what closing `/files` was
#  meant to protect.
#
#  Hence a gate on the whole application rather than a decorator per route: a
#  route added tomorrow is protected by default, and opening one is a
#  deliberate line in the list below.
# --------------------------------------------------------------------------- #
#  «Локально» считается по адресу СОЕДИНЕНИЯ, а не по заголовку — см.
#  `api/net.py`, там же записано, почему это отдельный модуль.

#  Reachable without a session, and each for a stated reason.
PUBLIC_EXACT = {
    "/",                       # redirect to the UI
    "/api/health",             # ops needs it before anyone can sign in (trimmed
                               # for anonymous callers — see `health()`)
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/me",            # the UI asks it precisely to find out it is 401
    "/api/auth/accept-invite",
    # Три роута первого входа. До входа спросить некому, а без них человек на
    # свежей инсталляции упирался в стену «зайдите на машину сервиса и
    # выполните `vistest user add`» — то есть в состояние, из которого
    # интерфейс выйти не мог вовсе.
    "/api/auth/state",
    "/api/auth/setup",
    "/api/auth/register",
    "/favicon.ico",
    # Скрипт стабилизации. Данных в нём нет вовсе: это тот же код заморозки
    # анимаций и снятия DOM, что уезжает в пакете на PyPI. Клиент на другом
    # языке забирает его ПЕРВЫМ, до всякой проверки, и закрытый роут здесь
    # означал бы, что снимки из чужого набора несопоставимы с нашими — при
    # том что скрывать в нём нечего.
    "/api/stabilize.js",
}
#  Схема API до входа не отдаётся: это карта всех роутов сервиса, и
#  открытой она была по недосмотру, а не по решению. Включается на время
#  разбора: VISTEST_DOCS=on (см. `_docs_enabled`).
PUBLIC_PREFIXES = ("/ui", "/api/auth/invite/")


def _client_host(request: Request) -> str:
    """Адрес клиента для журнала: за доверенным прокси — настоящий."""
    return _net.client_ip(request)


def _is_loopback(request: Request) -> bool:
    return _net.is_loopback(request)


def open_install_allowed(request: Request) -> bool:
    """Обслуживаем ли мы этому вызывающему инсталляцию без администратора.

    Вынесено из проверки отдельно, потому что ответ нужен ещё и экрану входа:
    он решает, показывать форму первого администратора или сразу пускать в
    локальном режиме. Два места, считающие это по своим правилам, разошлись бы
    на первом же изменении — и разошлись бы молча.
    """
    from .auth import any_users, auth_disabled

    if auth_disabled():
        return True
    if (os.getenv("VISTEST_ALLOW_OPEN") or "").strip().lower() in ("1", "true", "yes"):
        return True
    if _is_loopback(request):
        return True
    try:
        return bool(any_users(db))
    except Exception:
        return True


def _open_install(request: Request) -> None:
    """Refuse to serve an installation that has no users to a remote caller.

    `current_user()` hands out the admin role while no user exists — that is
    what keeps solo work on an engineer's machine unbroken, and it is correct
    there. It stops being correct the moment the same process is started with
    `--host 0.0.0.0`, which is exactly what the Dockerfile does: whoever gets
    to the port is an administrator, and the only place that says so is a field
    in `/api/health`.

    So: from the outside, an installation without an administrator does not
    serve data at all. It says what to do instead. `VISTEST_AUTH=off` is a
    deliberate choice and is left alone; `VISTEST_ALLOW_OPEN=1` is the escape
    hatch for someone who really means it.
    """
    if open_install_allowed(request):
        return
    # Данные по-прежнему не отдаём: пока активных пользователей нет,
    # `current_user()` раздаёт роль admin каждому, кто дотянулся до порта, и
    # это ровно то, от чего здесь стоит защита. Но выход из состояния теперь
    # есть, и текст говорит про него, а не про поход на машину сервиса.
    raise HTTPException(
        503,
        "This installation has no administrator yet, so everyone who can reach "
        "the port would be one. Open the interface and create the first "
        "administrator on the sign-in screen. To keep the installation open "
        "deliberately: VISTEST_ALLOW_OPEN=1.",
    )


def _metrics_token_ok(request: Request) -> bool:
    """A Prometheus scraper has no cookie, so it gets a token of its own."""
    import hmac as _hmac

    expected = (os.getenv("VISTEST_METRICS_TOKEN") or "").strip()
    if not expected:
        return False
    supplied = (request.headers.get("x-vistest-token") or "").strip()
    if not supplied:
        header = request.headers.get("authorization") or ""
        if header.lower().startswith("bearer "):
            supplied = header[7:].strip()
    return bool(supplied) and _hmac.compare_digest(supplied, expected)


# --------------------------------------------------------------------------- #
#  Заголовки безопасности и защита от запроса с чужой страницы
# --------------------------------------------------------------------------- #
#  Инлайн-скриптов в интерфейсе нет — только `<script src>`, поэтому
#  `script-src 'self'` ничего не ломает. Инлайн-стили есть (атрибут `style` в
#  разметке), отсюда 'unsafe-inline' именно для стилей: убрать его — отдельная
#  работа по разметке, а не строчка в заголовке.
#
#  `frame-ancestors 'none'` здесь важнее остального: «Принять как эталон» —
#  единственное необратимое действие в интерфейсе, и кликджекинг на нём стоит
#  переписанного эталона.
CSP_UI = ("default-src 'self'; "
          "img-src 'self' data: blob:; "
          "style-src 'self' 'unsafe-inline'; "
          "script-src 'self'; "
          "connect-src 'self'; "
          "font-src 'self' data:; "
          "object-src 'none'; "
          "base-uri 'none'; "
          "form-action 'self'; "
          "frame-ancestors 'none'")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    # Ни одна из этих возможностей интерфейсу не нужна, и объявить это дешевле,
    # чем однажды объяснять, почему страница просит микрофон.
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
}

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


#  Версия в адресе.
#
#  Клиенты для JS и JVM уже прибиты к путям `/api/...`, и первое же изменение
#  контракта сломает их без предупреждения — а обновлять их будет не тот, кто
#  выпускает VisTest, а покупатель лицензии, у которого свой цикл релизов.
#
#  Поэтому появляется `/api/v1/...` — тот же самый набор роутов, доступный по
#  стабильному адресу. Не копия роутов и не второй роутер: префикс снимается
#  до маршрутизации, то есть один обработчик обслуживает оба адреса и разойтись
#  им нечем. Когда контракт однажды изменится, `/api/v2` будет отдельным
#  роутером, а `/api/...` без версии останется тем, чем он был.
API_VERSION_PREFIX = "/api/v1/"


async def _api_version(request: Request, call_next):
    path = request.scope.get("path", "")
    if path.startswith(API_VERSION_PREFIX):
        request.scope["path"] = "/api/" + path[len(API_VERSION_PREFIX):]
        request.scope["raw_path"] = request.scope["path"].encode()
    return await call_next(request)


#  Потолок на тело запроса.
#
#  Пределы стоят у каждой двери, куда что-то грузят (снимок, артефакт, архив
#  проекта), и это правильно: там они знают, что именно принимают. Но у роутов,
#  принимающих JSON, потолка нет вовсе, а `POST /api/runs` разбирает тело
#  целиком, до всякой проверки. Один запрос с телом в гигабайт кладёт процесс,
#  и по логам это выглядит как «сервис молча умер».
#
#  Здесь общий предел на всё остальное — большой, чтобы не мешать нормальной
#  работе, и конечный, чтобы «нормальной» осталась только она. Загрузки его не
#  касаются: у них свой, более строгий.
MAX_BODY_BYTES = int(os.getenv("VISTEST_MAX_BODY_MB", "64")) * 1024 * 1024
_STREAMED_PATHS = ("/api/check", "/api/projects/upload")


async def _body_limit(request: Request, call_next):
    if request.method.upper() in UNSAFE_METHODS:
        path = request.url.path
        streamed = path.endswith("/artifacts") or any(
            path.startswith(p) for p in _STREAMED_PATHS)
        if not streamed:
            declared = request.headers.get("content-length")
            # `Content-Length` пишет клиент, и верить ему нельзя — но отказать
            # по нему можно: честный клиент получит внятный ответ, не отправив
            # тело, а нечестный упрётся в тот же предел ниже, при чтении.
            if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
                return JSONResponse(
                    status_code=413,
                    content={"detail": f"The request body is larger than "
                                       f"{MAX_BODY_BYTES // (1024 * 1024)} MB"})
    return await call_next(request)


async def _security_headers(request: Request, call_next):
    """Заголовки на каждый ответ.

    HSTS сознательно НЕ ставится: сервис не знает, доступен ли он по HTTPS, а
    ошибочный HSTS на инсталляции без TLS — это браузер, который отказывается
    её открывать вовсе, и лечится это очисткой настроек у каждого сотрудника.
    Его ставит тот, кто терминирует TLS.
    """
    response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    if request.url.path == "/" or request.url.path.startswith("/ui"):
        response.headers.setdefault("Content-Security-Policy", CSP_UI)
    return response


def _same_origin(request: Request) -> bool:
    """Пришёл ли изменяющий запрос со страницы самого сервиса.

    Защита от запроса, отправленного чужой страницей на куке пользователя.
    Сейчас от этого спасает `SameSite=lax`, но это свойство КУКИ, а не решение
    сервиса: администратор, задавший `VISTEST_CORS_ORIGINS`, разрешает
    доверенным страницам ходить сюда с учётными данными — и тогда единственная
    преграда исчезает.

    Проверяется `Origin`: браузер шлёт его на любой межсайтовый запрос, и
    подделать его страница не может. Клиенты вне браузера (CI, curl, наши же
    клиенты на других языках) `Origin` не присылают вовсе — их это правило не
    касается и сломать интеграции не может.
    """
    origin = request.headers.get("origin")
    if not origin:
        return True
    if origin.rstrip("/") in {o.rstrip("/") for o in _cors_origins}:
        return True

    from urllib.parse import urlsplit

    host = request.headers.get("host") or ""
    return urlsplit(origin).netloc == host


def _access_gate(request: Request) -> None:
    path = request.url.path
    method = request.method.upper()

    if method == "OPTIONS":
        return

    # Изменяющий запрос со страницы чужого сайта не выполняется никогда — даже
    # на публичных роутах: вход через чужую страницу тоже вход.
    if method in UNSAFE_METHODS and not _same_origin(request):
        raise HTTPException(
            403, "This request came from another origin. If a tool of yours "
                 "needs to reach the API, use a token instead of a browser "
                 "session, or add the origin to VISTEST_CORS_ORIGINS.")
    if path in PUBLIC_EXACT or path.startswith(PUBLIC_PREFIXES):
        return

    # Run intake carries its own authentication: a CI runner has no browser and
    # no cookie, so it presents `VISTEST_INGEST_TOKEN` instead. `_require_ingest`
    # is stricter than this gate, not weaker.
    #
    # `/api/check` is on this list for exactly the same reason, and leaving it
    # off was a plain defect. It is documented as the entry point for suites
    # written in Cypress, Playwright JS, Java, C# or anything else — and it
    # answered 401 to every one of them on any installation that has users,
    # because the only way in was a browser session cookie. The route itself
    # asks `_require_ingest`, so the check is not skipped, only moved to where
    # the project name is readable.
    if method == "POST" and (path in ("/api/runs", "/api/check",
                                     "/api/baselines/seed")
                             or (path.startswith("/api/runs/")
                                 and path.endswith("/artifacts"))):
        _open_install(request)
        return

    guarded = (path.startswith("/api/") or path == "/metrics"
               or path.startswith(("/files/", "/local/")))
    if not guarded:
        return

    if path == "/metrics" and _metrics_token_ok(request):
        return

    _open_install(request)
    _require(request, "viewer")


def _docs_enabled() -> bool:
    """Интерактивная схема. Выключена, пока её не попросили.

    `/docs` и `/openapi.json` — полный перечень роутов, тел запросов и имён
    полей. До входа это подсказка тому, кто изучает сервис снаружи, а внутри
    периметра нужна ровно во время разбора. Включается переменной, а не
    правкой кода.
    """
    return (os.getenv("VISTEST_DOCS") or "").strip().lower() in (
        "1", "true", "yes", "on")


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Что происходит при остановке сервиса.

    До этого — ничего. Часы уборки и уведомлений были daemon-потоками: процесс
    их не ждёт, но и не отменяет, поэтому при остановке они досыпали свой
    интервал и успевали сделать ещё один заход уже на выходе. Соединения с
    базой не закрывались вовсе, а каждое держит открытыми и файл, и его WAL.

    Запуск часов остаётся на импорте модуля намеренно: их заводит и
    `vistest serve`, и `uvicorn ...:app`, и тесты, импортирующие модуль
    напрямую, — перенос в lifespan оставил бы часть этих путей без часов, и
    заметили бы это не сразу.
    """
    yield
    _retention.stop_ticker()
    _notify.stop_ticker()
    db.close()


_docs = _docs_enabled()
app = FastAPI(title="VisTest", version="0.1.0",
              dependencies=[Depends(_access_gate)],
              lifespan=_lifespan,
              docs_url="/docs" if _docs else None,
              redoc_url="/redoc" if _docs else None,
              openapi_url="/openapi.json" if _docs else None)

# CORS. The interface is served from the same origin as the API, so it needs
# nothing from here at all. What does need it is a test runner in another
# language calling `POST /api/check` from a page — that is a token/no-cookie
# path, and for it a wildcard without credentials is correct and safe.
#
# What must NOT happen is a wildcard *with* credentials: the session cookie
# would then be usable from any site on the network. Today `samesite=lax`
# saves us, but that is a property of the cookie, not a decision made here.
# So: an explicit list from VISTEST_CORS_ORIGINS enables credentials; the
# default stays a credential-less wildcard.
_cors_origins = [o.strip() for o in
                 (os.getenv("VISTEST_CORS_ORIGINS") or "").split(",") if o.strip()]
# Строка на запрос и сквозной `X-Request-Id`. До этого про сам сервис не было
# известно ничего: ни какой запрос пришёл, ни сколько он шёл, ни чем кончился, —
# и человек с ошибкой в интерфейсе мог только пересказать события своими
# словами. Тело запроса и строка запроса в лог не попадают: там пароли из формы
# входа, значения со стенда и токен метрик.
app.middleware("http")(_logs.middleware)
app.middleware("http")(_security_headers)
app.middleware("http")(_body_limit)
# Регистрируется последним, то есть выполняется первым: путь должен стать
# каноническим до того, как его увидят и страж доступа, и запись в журнал.
app.middleware("http")(_api_version)


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    """Пятисотка, которую можно процитировать.

    Идентификатор в ответе и в логе — один и тот же, и это вся разница между
    «у меня не сохранилось, ну, недавно» и строчкой, по которой запрос находится
    за секунду. Текст самого исключения наружу не отдаётся: в нём бывают пути,
    SQL и куски чужих данных.
    """
    request_id = _logs.current_request_id.get() or "-"
    _logs.log.exception("%s unhandled %s %s", request_id, request.method,
                        request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": f"internal error (request {request_id})"},
        headers={_logs.HEADER: request_id})


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins or ["*"],
    allow_credentials=bool(_cors_origins),
    allow_methods=["*"],
    allow_headers=["*"],
)

# Order matters: specific routes (/api/baselines/snap) must be registered
# before parametric ones (/api/baselines/{name}/upload).
app.include_router(baselines_router)   # baselines and launching checks from the UI
app.include_router(check_router)       # POST /api/check for tests in any language
app.include_router(settings_router)    # environment variables for scenarios behind login
app.include_router(projects_router)    # externally connected test suites
app.include_router(doctor_router)      # project noise and environment check
app.include_router(record_router)      # recording baselines with the mouse
app.include_router(license_router)     # what this installation is licensed for

# Login and roles. Connected last, because the router needs a ready database.
from .auth import build_router as _build_auth  # noqa: E402

app.include_router(_build_auth(db))


def _require(request: Request, role: str, project: str | None = None) -> dict:
    """Role for destructive operations (deleting runs, cleanup, review).

    In local mode (no users created yet) require lets everyone through as
    admin, so solo work on an engineer's machine is not broken.

    `project` — проект, над которым совершается действие. Тогда роль
    считается как максимум из глобальной и выданной на этот проект: `reviewer`
    без проекта означал «может переписать эталон любого набора», и человек,
    отвечающий за витрину, менял точку отсчёта биллинга одним нажатием. Пока
    прав никому не выдано, поведение совпадает с прежним.
    """
    from . import rights
    from .auth import current_user

    user = current_user(db, request.cookies.get("vistest_session"))
    return rights.check(db, user, role, project)


def _supplied_token(request: Request) -> str:
    """Токен из заголовка. Две записи, потому что обе встречаются в дикой природе.

    `X-VisTest-Token` — наш собственный заголовок; `Authorization: Bearer` —
    то, что умеет любой HTTP-клиент и любая CI-система без дополнительной
    настройки.
    """
    supplied = (request.headers.get("x-vistest-token") or "").strip()
    if supplied:
        return supplied
    header = request.headers.get("authorization") or ""
    return header[7:].strip() if header.lower().startswith("bearer ") else ""


def _require_ingest(request: Request, project: str = "") -> str:
    """Who may write a run into the history.

    Run intake is the one place a session cannot be required: the runner in CI
    has no browser and no cookie. Previously that meant the route was simply
    open — anyone inside the perimeter could inject runs, overwrite artifacts
    and fill the disk, and nothing in the history said who did it.

    So there are two accepted ways in, and both are deliberate:

      * `VISTEST_INGEST_TOKEN` in the `X-VisTest-Token` header (or
        `Authorization: Bearer …`) — this is the CI path;
      * an ordinary reviewer session — this is the «I am pushing a local run
        from my own machine» path.

    With no token configured, intake falls back to the session check — which on
    an engineer's machine, where no user exists yet, still lets everything
    through, so solo work is unaffected. What it no longer does is accept
    anonymous runs from the network by default: «did not configure it» must not
    mean «open». `VISTEST_INGEST_OPEN=1` restores the old behaviour for whoever
    deliberately wants it.
    """
    import hmac as _hmac

    from . import citokens

    supplied = _supplied_token(request)

    # ---- токен проекта ----
    #
    # Проверяется ПЕРВЫМ и независимо от того, задана ли общая переменная: это
    # и есть путь, ради которого всё затевалось. Токен знает свой проект, и
    # прогон чужого проекта им не залить — прежняя общая переменная давала
    # доступ ко всему сразу, а по журналу нельзя было ответить, чей пайплайн
    # писал.
    if supplied:
        token = citokens.resolve(db, supplied)
        if token:
            if not citokens.allows(token, "reviewer", project):
                raise HTTPException(
                    403, f"This CI token is issued for project "
                         f"«{token['project']}» and cannot write runs of "
                         f"«{project or 'default'}».")
            return citokens.who(token)

    # ---- общая переменная окружения ----
    expected = (os.getenv("VISTEST_INGEST_TOKEN") or "").strip()
    if expected and supplied and _hmac.compare_digest(supplied, expected):
        return "ci-token"

    if not expected:
        if (os.getenv("VISTEST_INGEST_OPEN") or "").strip().lower() in (
                "1", "true", "yes"):
            return "anonymous-runner"
        # Ни переменной, ни подошедшего токена проекта. Если токены проекта в
        # базе всё же есть, приём закрыт ими: «не настроил переменную» больше
        # не означает «открыто» ровно так же, как и раньше.
        from .auth import require
        return require(db, request.cookies.get("vistest_session"),
                       "reviewer")["login"]

    # No token — maybe it is a person with a session. Do not leak which of the
    # two failed: the answer is the same either way.
    try:
        from .auth import require
        user = require(db, request.cookies.get("vistest_session"), "reviewer")
        return user["login"]
    except HTTPException:
        raise HTTPException(
            401, "Run intake requires the X-VisTest-Token header "
                 "(a project CI token or VISTEST_INGEST_TOKEN) or a reviewer "
                 "session") from None


def require_ingest(request: Request, project: str = "") -> str:
    """Тот же страж приёма, но для роутеров, лежащих в других модулях.

    Отдельное имя без подчёркивания — потому что это уже не частность
    `main`, а точка расширения: любой роут, который принимает данные из
    чужого CI, обязан спрашивать здесь, а не заводить свою проверку.
    """
    return _require_ingest(request, project)


# --------------------------------------------------------------------------- #
#  Runs
# --------------------------------------------------------------------------- #
MAX_ARTIFACT_BYTES = int(os.getenv("VISTEST_MAX_ARTIFACT_MB", "40")) * 1024 * 1024


@app.post("/api/runs")
def create_run(request: Request, payload: dict = Body(...)):
    # Проект вычисляется ДО проверки доступа: токен выдан на проект, и без его
    # имени проверять нечего. Раньше порядок был обратный, потому что проверять
    # было нечего в принципе — общая переменная пускала всюду.
    project = payload.get("project") or cfg.service.project
    who = _require_ingest(request, project)
    # Единственное, что останавливает истёкшая лицензия, — и только после
    # льготного срока. Эталоны и история остаются читаемыми всегда: это данные
    # заказчика, и держать их в заложниках из-за счёта нельзя.
    _license.check_runs()
    run_id = db.ingest_run(payload, project)

    # Re-sending a run under the same key replaces the row and gives it a new
    # id. The artifacts of the previous id belonged to nothing after that and
    # no cleanup could ever reach them.
    replaced = getattr(db, "replaced_run_id", None)
    if replaced and replaced != run_id:
        _rmtree_safe(ARTIFACTS / str(replaced))

    from .auth import audit
    audit(db, who, "run.ingested", str(payload.get("run_id") or run_id),
          project=project, run_id=run_id)
    return {"id": run_id, "run_key": payload.get("run_id"), "project": project}


@app.post("/api/runs/{run_id}/artifacts")
async def upload_artifact(
    run_id: int,
    request: Request,
    snapshot: str = Form(...),
    kind: str = Form(...),
    file: UploadFile = File(...),
    platform: str = Form(default=""),
):
    """Положить артефакт сравнения.

    `platform` появилась здесь позже остального и по неприятной причине.

    Прогон по матрице — это один прогон и шесть вариантов: chromium при 1440,
    firefox при 390 и так далее. Снимок в них называется одинаково, потому что
    это один и тот же экран, — и артефакты всех шести вариантов ложились в
    `artifacts/<run>/<snapshot>/`, то есть в ОДИН каталог. Последний
    загруженный затирал остальные, и в разборе падения на мобильном размере
    человек смотрел на картинку с десктопа, не зная об этом.

    Тем же самым болела привязка ссылки: сравнение искалось по имени снимка
    внутри прогона, а таких строк там шесть, и `db.one` возвращал случайную.

    Поле необязательное: прогон одного варианта (и клиенты прежних версий) его
    не присылают, и для них всё остаётся ровно как было — каталог без
    подпапки варианта.
    """
    # Проект берётся у самого прогона: токен выдан на проект, и заливать
    # картинки в чужой прогон он не должен. Строка читается ДО проверки, но
    # 404 отдаётся ПОСЛЕ неё — иначе роут отвечал бы на вопрос «а есть ли у вас
    # прогон номер 42» тому, кто не имеет права спрашивать вовсе.
    row = db.one(
        "SELECT p.name AS project FROM run r JOIN project p ON p.id=r.project_id"
        " WHERE r.id=?", (run_id,))
    _require_ingest(request, (row or {}).get("project") or "")
    if not row:
        raise HTTPException(404, "run not found")

    suffix = Path(file.filename or "").suffix.lower() or ".png"
    if suffix not in SERVABLE_SUFFIXES:
        raise HTTPException(400, f"artifact type {suffix} is not accepted")

    # Вариант — отдельный уровень каталога, а не суффикс в имени файла:
    # так `_drop_run_files` и уборка сносят прогон целиком, ничего не зная о
    # матрице, а старые прогоны на диске остаются читаемыми.
    dest_dir = ARTIFACTS / str(run_id) / _safe(snapshot)
    if platform:
        dest_dir = dest_dir / _safe(platform)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{_safe(kind)}{suffix}"

    # Streaming with a hard ceiling. Without it a single request fills the data
    # volume, and the service dies in a way that looks like anything but an
    # upload — Content-Length is not trusted here, it is client-supplied.
    written = 0
    try:
        with dest.open("wb") as fh:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > MAX_ARTIFACT_BYTES:
                    raise HTTPException(
                        413, f"artifact is larger than "
                             f"{MAX_ARTIFACT_BYTES // (1024 * 1024)} MB")
                fh.write(chunk)
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise

    uri = "/files/" + dest.relative_to(ARTIFACTS).as_posix()
    # Сравнение ищется по имени И платформе. По одному имени в прогоне по
    # матрице подходит шесть строк, и ссылка доставалась той, которую вернул
    # запрос, — то есть каждый раз другой.
    if platform:
        row = db.one(
            "SELECT c.id, c.artifacts FROM comparison c JOIN snapshot s"
            " ON s.id=c.snapshot_id WHERE c.run_id=? AND s.name=? AND s.platform=?",
            (run_id, snapshot, platform))
    else:
        row = db.one(
            "SELECT c.id, c.artifacts FROM comparison c JOIN snapshot s"
            " ON s.id=c.snapshot_id WHERE c.run_id=? AND s.name=?",
            (run_id, snapshot))
    if row:
        arts = json.loads(row["artifacts"] or "{}")
        arts[kind] = uri
        db.execute("UPDATE comparison SET artifacts=? WHERE id=?",
                   (json.dumps(arts), row["id"]))
    return {"uri": uri}


@app.get("/api/run-projects")
def run_projects():
    """Projects that have runs.

    Needed by the interface: the run list is filtered by project, and without
    this a run of an externally connected test suite made it into the database
    but was not shown — it is stored under its own name, while the default list
    looked at `default`.
    """
    return db.query(
        "SELECT p.name, COUNT(r.id) AS runs, MAX(r.started_at) AS last_run"
        " FROM project p LEFT JOIN run r ON r.project_id=p.id"
        " GROUP BY p.id ORDER BY last_run DESC")


@app.get("/api/decisions")
def decisions_queue(project: str | None = Query(default=None)):
    """Что от человека требуется — по проекту, а не по прогону.

    Прогон отвечает на «что сломалось здесь». Открывая VisTest утром,
    спрашивают другое: на что ответить. Падение, приехавшее три прогона назад
    и с тех пор повторяющееся, в списке последнего прогона неотличимо от
    свежего, а в списках предыдущих его никто не ищет.

    `project='*'` не поддерживается намеренно: причина объединяет снимки
    одного набора, и смешивать наборы значило бы предлагать одно решение для
    двух чужих друг другу проектов.
    """
    project = project or cfg.service.project
    if project == "*":
        raise HTTPException(
            400, "a queue is built for one project: a cause groups snapshots "
                 "of one set, and one answer cannot cover two")
    return _decisions.queue(db, project)


@app.get("/api/runs/counts")
def run_counts(project: str | None = Query(default=None)):
    """How many runs match each filter — over the whole history, not one page.

    The interface counted these itself, on the array it had just fetched. That
    array is one page of sixty, so «With failures 3» meant «three among the last
    sixty», and was shown as if it meant three in total. A count that is only
    right when the history is short is worse than no count: it is believed.

    `unreviewed` is here for the same reason. The sidebar badge is read as «how
    much is waiting for me», and it was filled with the `failed` field of the
    single most recent run — a number that is zero whenever the last run happened
    to be green, however large the backlog behind it.
    """
    project = project or cfg.service.project
    where, params = ("1=1", ()) if project == "*" else ("p.name = ?", (project,))

    row = db.one(
        "SELECT COUNT(*) AS all_runs,"
        "       SUM(COALESCE(r.failed,0) > 0) AS fail,"
        "       SUM(COALESCE(r.new_baselines,0) > 0) AS new,"
        "       SUM(EXISTS(SELECT 1 FROM comparison c"
        "                   WHERE c.run_id=r.id AND c.verdict='error')) AS error"
        "  FROM run r JOIN project p ON p.id = r.project_id"
        f" WHERE {where}", params) or {}

    clean = db.one(
        "SELECT COUNT(*) AS n FROM run r JOIN project p ON p.id = r.project_id"
        f" WHERE {where} AND COALESCE(r.failed,0) = 0"
        "   AND NOT EXISTS(SELECT 1 FROM comparison c"
        "                   WHERE c.run_id=r.id AND c.verdict='error')",
        params) or {}

    waiting = db.one(
        "SELECT COUNT(*) AS n FROM comparison c"
        "  JOIN run r ON r.id = c.run_id JOIN project p ON p.id = r.project_id"
        f" WHERE {where} AND c.verdict='fail' AND c.review IS NULL",
        params) or {}

    return {
        "all": row.get("all_runs") or 0,
        "fail": row.get("fail") or 0,
        "error": row.get("error") or 0,
        "new": row.get("new") or 0,
        "clean": clean.get("n") or 0,
        "unreviewed": waiting.get("n") or 0,
    }


@app.get("/api/runs")
def list_runs(project: str | None = Query(default=None),
              limit: int = Query(default=50, ge=1, le=500),
              branch: str | None = None):
    """project='*' — runs of all projects.

    `limit` has a ceiling: it went straight into `LIMIT ?`, so one request for a
    million rows pulled the whole history through sqlite, through the JSON
    encoder and into memory.
    """
    project = project or cfg.service.project
    # errored — how many snapshots in the run failed with an error (capture/engine).
    # We count it right in the list so the run card shows not «clean» but the
    # number of errors, without a separate column in the run table.
    # variants — сколько наборов «браузер × размер» участвовало в прогоне.
    # Без этого числа список не отличает «упало на одном браузере» от «упало
    # везде»: у прогона по матрице колонка `platform` одна на всех, а сравнений
    # в нём вшестеро больше, и «5 to decide» читается как пять сломанных
    # страниц вместо одной, сломанной на пяти вариантах.
    sql = ("SELECT r.*, p.name AS project,"
           " (SELECT COUNT(*) FROM comparison c"
           "    WHERE c.run_id=r.id AND c.verdict='error') AS errored,"
           " (SELECT COUNT(DISTINCT s.platform) FROM comparison c"
           "    JOIN snapshot s ON s.id=c.snapshot_id"
           "   WHERE c.run_id=r.id) AS variants"
           " FROM run r"
           " JOIN project p ON p.id=r.project_id")
    params: tuple = ()
    if project != "*":
        sql += " WHERE p.name=?"
        params = (project,)
    if branch:
        sql += (" AND" if params else " WHERE") + " r.branch=?"
        params += (branch,)
    sql += " ORDER BY r.started_at DESC LIMIT ?"
    return db.query(sql, params + (limit,))


@app.get("/api/runs/{run_id}/diff")
def run_diff(run_id: int, base: int | None = Query(default=None)):
    """Что изменилось между этим прогоном и другим.

    `base` не указан — берётся предыдущий прогон того же проекта и той же
    платформы, то есть ответ на «что изменилось с прошлого раза». Указан —
    сравнение ветки с мастером, ради которого всё и написано.

    Разбор — в `rundiff`; здесь только сборка данных и явные 404, потому что
    «сравнить с прогоном, которого нет» и «сравнивать не с чем» — это два
    разных ответа, и первый лечится другой ссылкой, а второй не лечится.
    """
    head = db.one("SELECT * FROM run WHERE id=?", (run_id,))
    if not head:
        raise HTTPException(404, "run not found")

    if base is None:
        other = _rundiff.previous_run(db, head)
        if not other:
            raise HTTPException(
                404, "there is no earlier run of this project on this platform "
                     "to compare with")
    else:
        other = db.one("SELECT * FROM run WHERE id=?", (base,))
        if not other:
            raise HTTPException(404, "base run not found")
        if other["id"] == head["id"]:
            raise HTTPException(400, "a run compared with itself has no diff")

    return _rundiff.diff(other, _rundiff.comparisons_of(db, other["id"]),
                         head, _rundiff.comparisons_of(db, head["id"]))


@app.get("/api/runs/{run_id}/clusters")
def run_clusters(run_id: int, min_size: int = 2):
    """Run discrepancies grouped by a common cause.

    Someone changed the padding in the header — twenty snapshots turned red.
    Reviewing them one by one, a person gives up by the fifth and starts
    approving without looking. Grouping turns twenty diffs into one question.
    """
    from ..cluster import summarize

    comps = db.query(
        "SELECT c.id, c.meta, s.name AS snapshot_name FROM comparison c"
        " JOIN snapshot s ON s.id=c.snapshot_id"
        " WHERE c.run_id=? AND c.verdict='fail'", (run_id,))

    items = []
    for c in comps:
        try:
            item = json.loads(c["meta"] or "{}")
        except Exception:
            item = {}
        item.setdefault("name", c["snapshot_name"])
        if not item.get("regions"):
            item["regions"] = db.query(
                "SELECT * FROM region WHERE comparison_id=?", (c["id"],))
        items.append(item)

    return summarize(items, min_size=max(2, min_size))


def _run_comparisons(run_id: int) -> tuple[dict, list[dict]]:
    run = db.one("SELECT * FROM run WHERE id=?", (run_id,))
    if not run:
        raise HTTPException(404, "run not found")
    comps = db.query(
        "SELECT c.*, s.name AS snapshot_name FROM comparison c"
        " JOIN snapshot s ON s.id=c.snapshot_id WHERE c.run_id=?"
        " ORDER BY c.max_severity DESC", (run_id,))
    for c in comps:
        try:
            meta = json.loads(c["meta"] or "{}")
        except Exception:
            meta = {}
        if c.get("verdict") == "error":
            c["error"] = meta.get("error") or "unknown error"
        c["regions"] = db.query(
            "SELECT * FROM region WHERE comparison_id=? ORDER BY severity DESC",
            (c["id"],))
    return run, comps


@app.get("/api/runs/{run_id}/junit.xml")
def run_junit(run_id: int, honor_review: bool = True):
    """The run as JUnit XML — the one format every CI can read.

    Allure attachments are a separate report someone has to build and publish;
    a pytest exit code exists only where the run went through pytest at all.
    A connected project running in observe mode, and a run started by a button
    in the interface, have no pytest — so for CI those runs were invisible
    entirely.

    `honor_review=false` gives the picture at the moment of the run, ignoring
    who accepted what afterwards.
    """
    from ..report.junit import render_junit

    run, comps = _run_comparisons(run_id)
    project = db.one("SELECT name FROM project WHERE id=?",
                     (run["project_id"],)) or {}
    body = render_junit(
        comps, suite=project.get("name") or "vistest",
        run_key=run.get("run_key") or str(run_id),
        platform=run.get("platform") or "", branch=run.get("branch") or "",
        git_sha=run.get("git_sha") or "",
        honor_review=honor_review)
    return PlainTextResponse(body, media_type="application/xml", headers={
        "Content-Disposition":
            f'attachment; filename="vistest-run-{run_id}.xml"'})


@app.get("/api/runs/{run_id}/comment.md")
def run_comment(run_id: int):
    """Сводка прогона для комментария в PR/MR — markdown, без сети.

    VisTest рендерит, CI публикует. Учить сервис ходить в GitHub и GitLab
    самому значило бы держать по клиенту на площадку и хранить у себя токен с
    правом писать в чужие репозитории — взамен ничего, чего не делает строчка
    `gh pr comment --body-file`.
    """
    from ..report.comment import render_comment

    run, comps = _run_comparisons(run_id)
    clusters = []
    try:
        clusters = run_clusters(run_id).get("clusters") or []
    except Exception:
        pass

    from .prefs import branch_baselines_on, default_branch

    body = render_comment(
        comps, run_key=run.get("run_key") or str(run_id),
        platform=run.get("platform") or "", branch=run.get("branch") or "",
        git_sha=run.get("git_sha") or "",
        base_url=(os.getenv("VISTEST_PUBLIC_URL") or "").strip(),
        run_id=run_id, clusters=clusters,
        baseline_scope=run.get("baseline_scope") or "",
        branch_baselines=branch_baselines_on(db),
        default_branch=default_branch(db))
    return PlainTextResponse(body, media_type="text/markdown")


@app.get("/api/runs/{run_id}/gate")
def run_gate(run_id: int, honor_review: bool = True, allow_new: bool = True):
    """Should the pipeline stop on this run — one answer, fit for an exit code.

    Separate from the JUnit export on purpose: that one answers «what happened»,
    this one answers «go or no go». A capture error stops it — there was no
    comparison, and pretending otherwise is the one thing a gate must never do.
    """
    from ..report.junit import gate

    _, comps = _run_comparisons(run_id)
    return gate(comps, honor_review=honor_review, allow_new=allow_new)


@app.get("/api/runs/{run_id}/report.html")
def run_report(run_id: int):
    """Offline report for a run from history.

    Assembled from what is in the service artifacts directory: `run_id` here is
    the database id, not the runner run key. The file is self-contained and
    meant for attaching to a ticket.
    """
    from fastapi.responses import HTMLResponse

    from ..report.html import render_report

    run = db.one("SELECT * FROM run WHERE id=?", (run_id,))
    if not run:
        raise HTTPException(404, "run not found")

    comps = db.query(
        "SELECT c.*, s.name AS snapshot_name FROM comparison c"
        " JOIN snapshot s ON s.id=c.snapshot_id WHERE c.run_id=?"
        " ORDER BY c.max_severity DESC", (run_id,))

    items = []
    for c in comps:
        # `meta` holds the original comparison dict — with regions and notes.
        # From it the report comes out complete; table columns are a fallback.
        try:
            item = json.loads(c["meta"] or "{}")
        except Exception:
            item = {}
        item.setdefault("name", c["snapshot_name"])
        item.setdefault("verdict", c["verdict"])
        item.setdefault("metrics", {
            "max_severity": c["max_severity"], "ssim_global": c["ssim"],
            "de_mean": c["de_mean"], "changed_area_pct": c["changed_area_pct"],
        })
        # Artifacts in the database are stored as links like /files/... —
        # the report needs paths.
        arts = {}
        for kind, uri in (json.loads(c["artifacts"] or "{}")).items():
            if isinstance(uri, str) and uri.startswith("/files/"):
                arts[kind] = str(ARTIFACTS / uri[len("/files/"):])
            elif isinstance(uri, str):
                arts[kind] = uri
        item["artifacts"] = arts
        items.append(item)

    project = db.one("SELECT name FROM project WHERE id=?",
                     (run["project_id"],)) or {}
    body = render_report(items,
                         title=f"Visual run {run.get('run_key') or run_id}",
                         meta={"Project": project.get("name", ""),
                               "Platform": run.get("platform", ""),
                               "Started": run.get("started_at", ""),
                               "Branch": run.get("branch") or ""})
    return HTMLResponse(body, headers={
        "Content-Disposition":
            f'attachment; filename="vistest-run-{run_id}.html"'})


def _variant_label(platform: str, meta: dict | None = None) -> str:
    """Подпись варианта: сперва то, что сказал сам прогон, потом ключ.

    Ключ базового варианта намеренно не содержит размера окна — под ним лежат
    все эталоны, снятые до появления матрицы, и трогать его нельзя (см.
    `vistest/matrix.py`). Но из-за этого один и тот же прогон подписывал бы
    базовый вариант «chromium», а соседний — «chromium · 390×844»: человек
    видит два разреза одного снимка и не может сказать, чем отличается первый.

    Прогон при этом ЗНАЛ размер: он сам его и задавал. Поэтому подпись берётся
    из того, что записало сравнение, а разбор ключа остаётся запасным путём —
    для прогонов, снятых до этой версии.
    """
    from ..matrix import variant_of_platform

    info = variant_of_platform(platform or "")
    meta = meta or {}
    said = meta.get("variant")
    if said:
        return str(said)
    viewport = meta.get("viewport")
    if viewport and info.get("browser"):
        return f"{info['browser']} · {str(viewport).replace('x', '×')}"
    return info["label"]


@app.get("/api/runs/{run_id}")
def get_run(run_id: int):
    run = db.one("SELECT * FROM run WHERE id=?", (run_id,))
    if not run:
        raise HTTPException(404, "run not found")
    comps = db.query(
        "SELECT c.*, s.name AS snapshot_name, s.platform FROM comparison c"
        " JOIN snapshot s ON s.id=c.snapshot_id WHERE c.run_id=?"
        " ORDER BY c.max_severity DESC", (run_id,))
    labels: dict[str, str] = {}
    for c in comps:
        # The error text is in meta (the engine/capture failed on this snapshot) —
        # we pull it out before dropping meta so the UI shows the cause.
        meta = json.loads(c["meta"] or "{}")
        if c.get("verdict") == "error":
            c["error"] = meta.get("error") or "unknown error"
        c["artifacts"] = _servable(json.loads(c["artifacts"] or "{}"))
        c["regions"] = db.query(
            "SELECT * FROM region WHERE comparison_id=? ORDER BY severity DESC",
            (c["id"],))
        # Подпись варианта считается здесь, а не в интерфейсе. Ключ платформы —
        # это адрес каталога (`linux-chromium-1x-390x844`), а человеку нужен
        # ответ на «какой это браузер и какой размер»; разбирать ключ на
        # клиенте значило бы завести второе место, где это правило живёт, и
        # разойтись с этим при первой же правке формата ключа.
        c["variant"] = _variant_label(c.get("platform") or "", meta)
        labels.setdefault(c.get("platform") or "", c["variant"])
        c.pop("meta", None)
    run["errored"] = sum(1 for c in comps if c.get("verdict") == "error")
    run["variants"] = [label for key, label in labels.items() if key]
    run["comparisons"] = comps
    return run


@app.get("/api/comparisons/{comp_id}")
def get_comparison(comp_id: int):
    c = db.one(
        "SELECT c.*, s.name AS snapshot_name, s.platform, s.browser, s.id AS snapshot_id"
        " FROM comparison c JOIN snapshot s ON s.id=c.snapshot_id WHERE c.id=?",
        (comp_id,))
    if not c:
        raise HTTPException(404, "comparison not found")
    c["artifacts"] = _servable(json.loads(c["artifacts"] or "{}"))
    c["meta"] = json.loads(c["meta"] or "{}")
    # Какой это вариант матрицы. В разборе это первое, что надо знать: один и
    # тот же снимок лежит в очереди шесть раз, и «сдвинулось на 14px» на
    # мобильном размере и на десктопном — разные новости.
    from ..matrix import variant_of_platform

    c["variant"] = _variant_label(c.get("platform") or "", c["meta"])
    c["viewport"] = (c["meta"].get("viewport")
                     or variant_of_platform(c.get("platform") or "")["viewport"])
    c["regions"] = db.query(
        "SELECT * FROM region WHERE comparison_id=? ORDER BY severity DESC", (comp_id,))
    c["history"] = db.query(
        "SELECT c2.id, c2.verdict, c2.review, c2.max_severity, c2.created_at,"
        "       r.branch, r.git_sha"
        "  FROM comparison c2 JOIN run r ON r.id=c2.run_id"
        " WHERE c2.snapshot_id=? ORDER BY c2.created_at DESC LIMIT 20",
        (c["snapshot_id"],))
    # Проект сравнения — чтобы интерфейс знал, спрашивать ли право на него.
    # Без этого кнопка «Принять как эталон» показывается всем и отвечает 403
    # тому, у кого прав нет, — а это не строгий бэкенд, это неправда на экране.
    c["project"] = _rights.of_comparison(db, comp_id)
    return c


# --------------------------------------------------------------------------- #
#  Deletion
# --------------------------------------------------------------------------- #
@app.delete("/api/runs/{run_id}")
def delete_run(run_id: int, request: Request, keep_files: bool = False):
    """Delete a run: its rows in the DB and its artifacts on disk.

    Comparisons and regions go by cascade (ON DELETE CASCADE). Baselines are
    not touched — a run is a history of checks, not the source of truth.
    """
    run = db.one("SELECT id, run_key FROM run WHERE id=?", (run_id,))
    if not run:
        raise HTTPException(404, "run not found")
    # Проверка ПОСЛЕ поиска прогона: проект берётся из него, а до этого
    # неизвестно, чьё право спрашивать.
    _require(request, "reviewer", _rights.of_run(db, run_id))

    freed = 0 if keep_files else _drop_run_files(run_id, run["run_key"])
    db.execute("DELETE FROM run WHERE id=?", (run_id,))
    return {"ok": True, "deleted": run_id, "freed_kb": round(freed / 1024)}


@app.post("/api/runs/cleanup")
def cleanup_runs(request: Request, body: dict = Body(default={})):
    """Bulk cleanup of history.

    Runs pile up fast: each full-page diff is several megabytes of PNG. Without
    retention the `.vistest` directory eats tens of gigabytes in a couple of
    months, and the UI starts to lag on a list of thousands of records.

    body: {days?: 30, keep_last?: 20, only_passed?: false, project?: str}
    """
    _require(request, "admin")
    days = body.get("days")
    keep_last = body.get("keep_last")
    only_passed = bool(body.get("only_passed"))
    project = body.get("project") or cfg.service.project

    if days is None and keep_last is None:
        raise HTTPException(400, "specify days or keep_last")

    # The numbers may arrive as strings from the form — we convert them ahead of
    # time, otherwise int() below would fail the request with a 500 instead of a
    # clear 400.
    try:
        days = None if days is None else int(days)
        keep_last = None if keep_last is None else int(keep_last)
    except (TypeError, ValueError):
        raise HTTPException(400, "days and keep_last must be numbers") from None

    # Одна и та же политика, что и у автоматической уборки: два разных набора
    # правил для «почистить кнопкой» и «почистить по расписанию» разошлись бы на
    # первом же изменении, и разошлись бы молча.
    from . import retention as _retention

    result = _retention.sweep(
        db, _drop_run_files, days=days, keep_last=keep_last,
        only_passed=only_passed, project=project)

    # Артефакты одиночных проверок из `POST /api/check`. Прогона они не
    # создают, в базе про них не написано ничего — и уборка, ходящая по
    # прогонам, до них не доставала вовсе. Чистятся только по возрасту:
    # `keep_last` для них бессмысленно, последовательности прогонов здесь нет.
    if days is not None:
        checks = _retention.sweep_checks(ARTIFACTS / "checks", days)
        result["checks_deleted"] = checks["deleted"]
        result["freed_kb"] = result.get("freed_kb", 0) + checks["freed_kb"]
    return {"ok": True, **result}


@app.delete("/api/comparisons/{comp_id}")
def delete_comparison(comp_id: int, request: Request):
    # Право спрашивается ПЕРВЫМ, 404 отдаётся после — как в `delete_run`.
    # Обратный порядок отвечает на вопрос «а есть ли у вас сравнение номер 42»
    # тому, кто не имеет права спрашивать вовсе: по коду ответа перебором
    # снимается карта чужих проектов. Проект берётся из сравнения, и если его
    # нет, `of_comparison` вернёт пусто — тогда проверяется глобальная роль.
    _require(request, "reviewer", _rights.of_comparison(db, comp_id))
    if not db.one("SELECT id FROM comparison WHERE id=?", (comp_id,)):
        raise HTTPException(404, "comparison not found")
    db.execute("DELETE FROM comparison WHERE id=?", (comp_id,))
    return {"ok": True, "deleted": comp_id}


@app.delete("/api/snapshots/{snapshot_id}/history")
def delete_snapshot_history(snapshot_id: int, request: Request):
    """Wipe a snapshot history while keeping the baseline itself.

    Needed when a snapshot was noisy for a long time and its metrics spoil the
    dashboard: after fixing the mask it is more honest to start the statistics
    over than to drag a distorted fail rate around for years.
    """
    # Сначала право, потом существование — см. `delete_comparison`.
    _require(request, "reviewer", _rights.of_snapshot(db, snapshot_id))
    row = db.one("SELECT name FROM snapshot WHERE id=?", (snapshot_id,))
    if not row:
        raise HTTPException(404, "snapshot not found")
    n = db.execute("DELETE FROM comparison WHERE snapshot_id=?", (snapshot_id,))
    return {"ok": True, "snapshot": row["name"], "deleted_comparisons": n}


@app.get("/api/storage")
def storage_usage():
    """How much the artifacts take up — so cleanup is a deliberate choice."""
    runs_dir = ROOT / "runs"
    return {
        "root": str(ROOT),
        "artifacts_kb": round(_dir_size(ARTIFACTS) / 1024),
        "runs_kb": round(_dir_size(runs_dir) / 1024),
        "baselines_kb": round(_dir_size(ROOT / cfg.paths.baselines) / 1024),
        "runs_in_db": (db.one("SELECT COUNT(*) AS n FROM run") or {}).get("n", 0),
        "comparisons_in_db":
            (db.one("SELECT COUNT(*) AS n FROM comparison") or {}).get("n", 0),
    }


def _drop_run_files(run_db_id: int, run_key: str | None) -> int:
    """Run artifacts: both those uploaded to the service and local ones."""
    freed = 0
    targets = [ARTIFACTS / str(run_db_id)]
    if run_key:
        targets.append(ROOT / "runs" / run_key)
    for path in targets:
        freed += _rmtree_safe(path)
    return freed


def _rmtree_safe(path: Path) -> int:
    """Deletion with a strict boundary check: only inside .vistest.

    Paths in the DB come from external runs, including from other machines.
    Wiping something outside the working directory because of a malformed
    string in the database is unacceptable.
    """
    try:
        p = path.resolve()
        if not p.is_relative_to(ROOT) or p == ROOT or not p.exists():
            return 0
        size = _dir_size(p)
        shutil.rmtree(p, ignore_errors=True)
        # Мы только что изменили то, что кеш описывает.
        _forget_sizes()
        return size
    except Exception:
        return 0


def _storage_kb() -> int:
    """Что занимает место и на что смотрит квота — артефакты и прогоны.

    Эталоны сюда не входят намеренно: они и есть тот источник правды, ради
    которого всё остальное существует, и удалять прогоны, чтобы поместились
    эталоны, — путь в никуда.
    """
    return round((_dir_size(ARTIFACTS) + _dir_size(ROOT / "runs")) / 1024)


#  Размер каталога с коротким кешем.
#
#  `rglob("*")` обходит дерево целиком: на инсталляции с историей это десятки
#  тысяч файлов и секунды под GIL. Вызывается он из `/api/storage` (то есть с
#  каждым открытием экрана настроек) и на каждом тике уборки, а ответ за эти
#  секунды не меняется ни на сколько-нибудь значимую величину.
#
#  Пять секунд — потому что кеш нужен от повторного вызова в пределах одного
#  экрана, а не от изменений за час. После уборки кеш сбрасывается явно: там
#  разница как раз в том, ради чего человек и нажал кнопку.
_SIZE_TTL_S = 5.0
_size_cache: dict[str, tuple[float, int]] = {}
_size_lock = _threading.Lock()


def _forget_sizes() -> None:
    with _size_lock:
        _size_cache.clear()


def _dir_size(path: Path) -> int:
    key = str(path)
    now = time.monotonic()
    with _size_lock:
        hit = _size_cache.get(key)
        if hit and now - hit[0] < _SIZE_TTL_S:
            return hit[1]
    try:
        size = (0 if not path.exists()
                else sum(f.stat().st_size for f in path.rglob("*") if f.is_file()))
    except Exception:
        size = 0
    with _size_lock:
        _size_cache[key] = (now, size)
    return size


# --------------------------------------------------------------------------- #
#  Review
# --------------------------------------------------------------------------- #
def _comparison_target(comp_id: int) -> dict:
    """Comparison + the store its decisions apply to.

    The store is resolved from the RUN (see `api/stores`), not from the global
    config. A comparison that came from a connected project's «Run on VisTest
    baselines» must be approved into that project's VisTest set — approving it
    into the service's own store, as this used to do, changed a file nothing
    ever reads.
    """
    from .stores import ScopeError, store_for_run

    c = db.one(
        "SELECT c.*, s.name AS snapshot_name, s.platform, s.id AS snapshot_id"
        "  FROM comparison c JOIN snapshot s ON s.id=c.snapshot_id"
        " WHERE c.id=?", (comp_id,))
    if not c:
        raise HTTPException(404, "comparison not found")
    try:
        store, directory, info = store_for_run(
            db, c["run_id"], cfg=cfg, platform=c["platform"] or "")
    except ScopeError as e:
        raise HTTPException(409, str(e)) from e
    return {"comparison": c, "store": store, "directory": directory, "scope": info}


def _baseline_state(store, name: str) -> dict:
    """The current baseline version and who set it.

    Needed to guard against concurrent approval: while one person looked at the
    diff, a colleague may have accepted another snapshot, and silently
    overwriting their decision is not allowed.
    """
    meta_path = store.dir_for(name) / "meta.json"
    if not meta_path.exists():
        return {"version": 0, "approved_by": None, "updated_at": None}
    try:
        meta = json.loads(meta_path.read_text("utf-8"))
    except Exception:
        return {"version": 0, "approved_by": None, "updated_at": None}
    return {"version": int(meta.get("version", 0)),
            "approved_by": meta.get("approved_by"),
            "updated_at": meta.get("updated_at")}


@app.get("/api/comparisons/{comp_id}/baseline-state")
def baseline_state(comp_id: int):
    """The baseline version at the moment the diff was opened — returned on approve.

    Also reports *which* store the decision will land in. The interface shows
    it: «accept as baseline» without saying which baseline is a promise that
    cannot be checked.
    """
    t = _comparison_target(comp_id)
    state = _baseline_state(t["store"], t["comparison"]["snapshot_name"])
    state["scope"] = t["scope"]["scope"]
    state["directory"] = str(t["directory"])
    state["project_key"] = t["scope"].get("project_key")
    # Ветка — часть ответа на вопрос «какой именно эталон». Без неё панель
    # решения обещает «собственный набор сервиса», а пишет в наложение ветки,
    # и обещание опять становится непроверяемым.
    state["branch"] = t["scope"].get("branch") or ""
    store = t["store"]
    owner = getattr(store, "owner_of", None)
    state["inherited"] = bool(
        owner and owner(t["comparison"]["snapshot_name"]) == "base")
    return state


@app.post("/api/comparisons/{comp_id}/approve")
def approve(comp_id: int, request: Request, body: dict = Body(default={})):
    """Accept actual as the new baseline.

    Approval is the only irreversible action in the interface: it overwrites the
    baseline against which everything is compared afterwards. That is why there
    are two things here that were absent in single-user mode: a role check and
    protection against concurrent edits.
    """
    from .auth import current_user

    # Роль спрашивается не здесь, а внутри — вместе с проектом сравнения.
    # Требовать глобального `reviewer` на входе значило бы закрыть дверь перед
    # тем, кому право выдано ровно на этот проект.
    user = current_user(db, request.cookies.get("vistest_session"))
    return _approve_one(comp_id, user, body)


def _approve_one(comp_id: int, user: dict, body: dict) -> dict:
    """One approval. Shared by the single and the bulk endpoint."""
    from .auth import audit
    from .stores import record_approval

    t = _comparison_target(comp_id)
    c, store, scope = t["comparison"], t["store"], t["scope"]
    # Право проверяется на КАЖДОЕ сравнение, а не на запрос. Массовое
    # утверждение по группе причин может собрать снимки нескольких проектов —
    # и тогда честный ответ такой: своё принято, чужое отказано с объяснением,
    # а не «403 на всё» и не «принято всё».
    _rights.check(db, user, "reviewer", _rights.of_comparison(db, comp_id))

    state = _baseline_state(store, c["snapshot_name"])
    expected = body.get("expected_version")
    if expected is not None and int(expected) != state["version"]:
        # Silently overwriting someone else's decision is not allowed: the person
        # looked at one diff, but the baseline became different in the meantime,
        # and their «accept» no longer refers to what they saw.
        raise HTTPException(409, {
            "error": "The baseline changed while you were looking",
            "message":
                f"The version was {expected}, became {state['version']}"
                + (f" — accepted by {state['approved_by']}"
                   if state["approved_by"] else "")
                + (f" ({state['updated_at']})" if state["updated_at"] else "")
                + ". Refresh the comparison and decide again.",
            "current": state,
        })

    arts = json.loads(c["artifacts"] or "{}")
    actual_uri = arts.get("actual")
    promoted = False
    if actual_uri:
        src = _resolve(actual_uri)
        if src and src.exists():
            from ..capture.playwright_capture import read_png
            from ..storage import BaselineRecord

            store.save(BaselineRecord(
                name=c["snapshot_name"], image=read_png(src),
                meta={"approved_by": user["login"],
                      "from_comparison": comp_id},
            ))
            promoted = True
            run = db.one("SELECT git_sha, branch FROM run WHERE id=?",
                         (c["run_id"],)) or {}
            record_approval(
                db, scope=scope["scope"], name=c["snapshot_name"],
                platform=c["platform"] or "",
                project_key=scope.get("project_key"),
                version=state["version"] + 1, approved_by=user["login"],
                image_uri=actual_uri, snapshot_id=c["snapshot_id"],
                from_comparison=comp_id, git_sha=run.get("git_sha") or "",
                branch=run.get("branch") or "")

    db.execute(
        "UPDATE comparison SET review='approved', reviewed_by=?,"
        " reviewed_at=datetime('now') WHERE id=?",
        (user["login"], comp_id))
    audit(db, user["login"], "baseline.approved", c["snapshot_name"],
          comparison=comp_id, platform=c["platform"], scope=scope["scope"],
          directory=str(t["directory"]),
          version=state["version"] + 1 if promoted else state["version"])
    db.execute("DELETE FROM review_claim WHERE comparison_id=?", (comp_id,))
    return {"ok": True, "baseline_updated": promoted, "by": user["login"],
            "scope": scope["scope"], "directory": str(t["directory"]),
            "version": state["version"] + 1 if promoted else state["version"]}


@app.post("/api/comparisons/bulk")
def bulk_decision(request: Request, body: dict = Body(...)):
    """Apply one decision to a whole group of comparisons.

    The interface groups failures by common cause and offers «accept the whole
    group». That used to be N separate HTTP requests from the browser — forty
    round trips for a header padding change, each one able to fail on its own
    and leave the group half-decided. And none of them carried
    `expected_version`, so the bulk path quietly bypassed the very protection
    against concurrent edits that the single path has.

    What this does **not** promise is atomicity, and saying otherwise would be
    the wrong kind of comfort: an approval writes a PNG to disk, and no database
    transaction can roll that back. So the guarantee is the honest one — one
    round trip, every item attempted, and the per-item outcome reported in
    `done` / `failed`. A caller that needs all-or-nothing has to look at that
    list; pretending the whole thing succeeded or failed together would hide
    exactly the half-applied case it is worried about.

    body: {ids: [int], action: approve|reject, expected_versions?: {id: int}}
    """
    from .auth import current_user

    user = current_user(db, request.cookies.get("vistest_session"))
    ids = body.get("ids") or []
    action = (body.get("action") or "").lower()
    if action not in ("approve", "reject"):
        raise HTTPException(400, "action must be approve or reject")
    if not isinstance(ids, list) or not ids:
        raise HTTPException(400, "ids must be a non-empty list")
    if len(ids) > 500:
        raise HTTPException(400, "no more than 500 comparisons at a time")

    versions = body.get("expected_versions") or {}
    done, failed = [], []
    for raw_id in ids:
        try:
            comp_id = int(raw_id)
        except (TypeError, ValueError):
            failed.append({"id": raw_id, "error": "not a comparison id"})
            continue
        try:
            if action == "approve":
                expected = versions.get(str(comp_id), versions.get(comp_id))
                _approve_one(comp_id, user,
                             {"expected_version": expected} if expected is not None
                             else {})
            else:
                _reject_one(comp_id, user)
            done.append(comp_id)
        except HTTPException as e:
            failed.append({"id": comp_id, "error": _detail_text(e.detail)})
        except Exception as e:
            failed.append({"id": comp_id, "error": f"{type(e).__name__}: {e}"})

    return {"ok": not failed, "action": action,
            "done": done, "failed": failed,
            "counts": {"done": len(done), "failed": len(failed)}}


def _detail_text(detail) -> str:
    if isinstance(detail, dict):
        return str(detail.get("message") or detail.get("error") or detail)
    return str(detail)


@app.post("/api/comparisons/{comp_id}/reject")
def reject(comp_id: int, request: Request, body: dict = Body(default={})):
    from .auth import current_user

    user = current_user(db, request.cookies.get("vistest_session"))
    return _reject_one(comp_id, user)


def _reject_one(comp_id: int, user: dict) -> dict:
    from .auth import audit

    row = db.one(
        "SELECT s.name AS snapshot_name FROM comparison c"
        " JOIN snapshot s ON s.id=c.snapshot_id WHERE c.id=?", (comp_id,))
    if not row:
        raise HTTPException(404, "comparison not found")
    # «Это баг» — тоже решение о чужом проекте: оно снимает падение с разбора
    # и уводит его из дайджеста, то есть закрывает вопрос за чужую команду.
    _rights.check(db, user, "reviewer", _rights.of_comparison(db, comp_id))

    db.execute(
        "UPDATE comparison SET review='rejected', reviewed_by=?,"
        " reviewed_at=datetime('now') WHERE id=?",
        (user["login"], comp_id))
    audit(db, user["login"], "comparison.rejected",
          row.get("snapshot_name", ""), comparison=comp_id)
    db.execute("DELETE FROM review_claim WHERE comparison_id=?", (comp_id,))
    return {"ok": True, "by": user["login"]}


# --------------------------------------------------------------------------- #
#  Collaboration: presence and «I am reviewing this snapshot»
# --------------------------------------------------------------------------- #
PRESENCE_TTL = 60      # seconds: no update — considered offline
CLAIM_TTL = 120        # seconds: holds the snapshot if refreshed by heartbeat


@app.post("/api/presence")
def presence_beat(request: Request, body: dict = Body(default={})):
    """Heartbeat: refresh that I am online and what I am viewing; return who else is online."""
    from .auth import current_user

    user = current_user(db, request.cookies.get("vistest_session"))
    viewing = str(body.get("viewing") or "")[:120]
    db.execute(
        "INSERT INTO presence(user_id, login, name, viewing, last_seen)"
        " VALUES(?,?,?,?,datetime('now'))"
        " ON CONFLICT(user_id) DO UPDATE SET login=excluded.login,"
        " name=excluded.name, viewing=excluded.viewing,"
        " last_seen=datetime('now')",
        (user["id"], user["login"], user.get("name", ""), viewing))
    return _online(exclude=user["id"])


@app.get("/api/presence")
def presence_list(request: Request):
    from .auth import current_user
    user = current_user(db, request.cookies.get("vistest_session"))
    return _online(exclude=user["id"])


def _online(exclude: int | None = None) -> dict:
    rows = db.query(
        "SELECT login, name, viewing, user_id FROM presence"
        " WHERE last_seen > datetime('now', ?) ORDER BY last_seen DESC",
        (f"-{PRESENCE_TTL} seconds",))
    others = [r for r in rows if r["user_id"] != exclude]
    return {"online": rows, "others": others, "count": len(rows)}


def _claim_row(comp_id: int) -> dict | None:
    row = db.one(
        "SELECT * FROM review_claim WHERE comparison_id=?"
        " AND expires_at > datetime('now')", (comp_id,))
    return row


@app.get("/api/comparisons/{comp_id}/claim")
def claim_get(comp_id: int, request: Request):
    from .auth import current_user
    user = current_user(db, request.cookies.get("vistest_session"))
    row = _claim_row(comp_id)
    if not row:
        return {"held": False}
    return {"held": True, "mine": row["user_id"] == user["id"],
            "by": row["name"] or row["login"], "login": row["login"]}


@app.post("/api/comparisons/{comp_id}/claim")
def claim_take(comp_id: int, request: Request):
    """Softly claim the snapshot for yourself (or extend). Does not forbid
    someone else's review — it only shows a colleague that someone is already busy."""
    from .auth import current_user
    # Захват — заявка «я разбираю»; заявлять её имеет смысл тому, кто может
    # разобрать. Роль спрашивается с проектом сравнения, иначе ревьюер одного
    # проекта не смог бы занять снимок собственного набора.
    user = current_user(db, request.cookies.get("vistest_session"))
    _rights.check(db, user, "reviewer", _rights.of_comparison(db, comp_id))

    row = _claim_row(comp_id)
    if row and row["user_id"] != user["id"]:
        return {"ok": False, "mine": False,
                "by": row["name"] or row["login"], "login": row["login"]}
    db.execute(
        "INSERT INTO review_claim(comparison_id, user_id, login, name, expires_at)"
        " VALUES(?,?,?,?, datetime('now', ?))"
        " ON CONFLICT(comparison_id) DO UPDATE SET user_id=excluded.user_id,"
        " login=excluded.login, name=excluded.name, expires_at=excluded.expires_at",
        (comp_id, user["id"], user["login"], user.get("name", ""),
         f"+{CLAIM_TTL} seconds"))
    return {"ok": True, "mine": True}


@app.delete("/api/comparisons/{comp_id}/claim")
def claim_release(comp_id: int, request: Request):
    from .auth import current_user
    user = current_user(db, request.cookies.get("vistest_session"))
    db.execute(
        "DELETE FROM review_claim WHERE comparison_id=? AND user_id=?",
        (comp_id, user["id"]))
    return {"ok": True}


@app.post("/api/comparisons/{comp_id}/ignore-region")
def ignore_region(comp_id: int, request: Request, body: dict = Body(...)):
    """Mark a region as «always ignore» for this snapshot.

    Written into the store this run actually compared against — same reason as
    for approve. And written only there: the `ignore_box` table used to get a
    copy that the engine never reads (it takes the boxes from the store's
    `meta.json`), so the two drifted apart and «delete the ignore zone» cleaned
    up only one of them.
    """
    _require(request, "reviewer", _rights.of_comparison(db, comp_id))
    t = _comparison_target(comp_id)
    c = t["comparison"]

    from ..zones import held_by, normalize

    # Селектор региона доезжает до зоны, а не теряется по дороге.
    #
    # Кнопка «Ignore area» стоит на карточке региона, а движок этот регион уже
    # назвал: `button[data-testid=submit]`. Записывать после этого голый
    # прямоугольник — значит выбросить единственное, что делает маску
    # переносимой, и заново поставить её на то место, где элемент БЫЛ. Ровно
    # эта потеря и превращала маски в мусор при первом же редизайне.
    payload = dict(body)
    if not payload.get("selector"):
        region = db.one(
            "SELECT selector FROM region WHERE comparison_id=?"
            "   AND x=? AND y=? AND w=? AND h=? LIMIT 1",
            (comp_id, body.get("x"), body.get("y"), body.get("w"), body.get("h")))
        if region and region.get("selector"):
            payload["selector"] = region["selector"]

    try:
        zone = normalize(payload)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None

    t["store"].add_ignore_box(c["snapshot_name"], zone)

    from .auth import audit, current_user
    who = current_user(db, request.cookies.get("vistest_session"))["login"]
    audit(db, who, "baseline.ignore_box", c["snapshot_name"],
          comparison=comp_id, scope=t["scope"]["scope"], box=zone,
          held_by=held_by(zone),
          reason=str(body.get("reason", "ui"))[:120])
    return {"ok": True, "box": zone, "held_by": held_by(zone),
            "scope": t["scope"]["scope"],
            "directory": str(t["directory"])}


@app.get("/api/comparisons/{comp_id}/baseline-versions")
def comparison_baseline_versions(comp_id: int):
    """Versions of the baseline this comparison is judged against, plus who
    approved each one.

    Two sources joined on purpose: the store knows what is on disk and can be
    rolled back to, the `baseline` journal knows who did it and from which run.
    Neither answers the question alone.
    """
    from .stores import approval_history

    t = _comparison_target(comp_id)
    c, scope = t["comparison"], t["scope"]
    name, platform = c["snapshot_name"], c["platform"] or ""

    journal = {row["version"]: row for row in approval_history(
        db, scope=scope["scope"], name=name, platform=platform,
        project_key=scope.get("project_key"))}

    versions = []
    for v in t["store"].versions(name):
        entry = dict(v)
        row = journal.get(v["version"])
        if row:
            entry.update({
                "approved_by": entry.get("approved_by") or row.get("approved_by"),
                "approved_at": row.get("approved_at"),
                "git_sha": row.get("git_sha"),
                "branch": row.get("branch"),
                "from_comparison": row.get("from_comparison"),
                "note": row.get("note"),
            })
        versions.append(entry)

    return {"name": name, "platform": platform, "scope": scope["scope"],
            "project_key": scope.get("project_key"),
            "directory": str(t["directory"]), "versions": versions}


@app.get("/api/snapshots/{snapshot_id}/history")
def snapshot_history(snapshot_id: int, limit: int = Query(default=50, ge=1, le=500)):
    return db.query(
        "SELECT c.id, c.verdict, c.review, c.max_severity, c.changed_area_pct,"
        "       c.created_at, r.branch, r.git_sha, c.artifacts"
        "  FROM comparison c JOIN run r ON r.id=c.run_id"
        " WHERE c.snapshot_id=? ORDER BY c.created_at DESC LIMIT ?",
        (snapshot_id, limit))


# --------------------------------------------------------------------------- #
#  Metrics
# --------------------------------------------------------------------------- #
@app.get("/api/metrics/summary")
def metrics_summary(project: str | None = None,
                    days: int = Query(default=30, ge=1, le=3650)):
    """project='*' — all projects at once (the default value in the interface)."""
    return _metrics.summary(db, project or cfg.service.project, days)


@app.get("/api/metrics/evidence")
def metrics_evidence(project: str = "*",
                     days: int = Query(default=90, ge=1, le=3650),
                     format: str = Query("json", pattern="^(json|html|md)$")):
    """Accumulated quality statistics — something you can present.

    Computed from the labeling a person does anyway in the course of work:
    accepting a failure as the baseline means it was false. The number builds up
    on its own from ordinary runs, no experiment is needed.

    The confidence interval and the share of unlabeled data are computed
    unconditionally: «0% false failures» over eight comparisons means nothing,
    and staying silent about that would be a lie.
    """
    from fastapi.responses import HTMLResponse, PlainTextResponse

    from ..report.evidence import collect_evidence, to_html, to_markdown

    data = collect_evidence(db, project, days)
    if format == "html":
        return HTMLResponse(to_html(data), headers={
            "Content-Disposition": 'attachment; filename="vistest-evidence.html"'})
    if format == "md":
        return PlainTextResponse(to_markdown(data), media_type="text/markdown",
                                 headers={"Content-Disposition":
                                          'attachment; filename="EVIDENCE.md"'})
    return data


@app.get("/api/metrics/flaky")
def metrics_flaky(project: str | None = None,
                  days: int = Query(default=30, ge=1, le=3650)):
    return _metrics.flaky(db, project or cfg.service.project, days)


@app.get("/metrics", response_class=PlainTextResponse)
def prometheus_metrics():
    """Prometheus exposition.

    Reached either with a reviewer's session (the gate) or with
    `VISTEST_METRICS_TOKEN` in `X-VisTest-Token` — a scraper has no cookie. It
    used to be open to anyone who could resolve the host: snapshot names, the
    project list and per-snapshot failure rates are not public data.
    """
    return _metrics.prometheus(db)


def _auth_open() -> bool:
    """True while the installation has no users (or auth is turned off)."""
    from .auth import any_users, auth_disabled
    try:
        return auth_disabled() or not any_users(db)
    except Exception:
        return True


@app.get("/api/health")
def health(request: Request):
    """The service state and what it can actually do.

    `routes` is an exact list of routes, not a retelling. The page updates in
    the browser instantly, while the process lives until a restart, and the
    mismatch between them yields «Not Found» on a button that exists in the
    interface. The list lets the interface state this precisely rather than
    guessing.

    `module` shows where the package was loaded from. If it is a copy in
    `site-packages` rather than the repository directory, then code changes do
    not reach the service at all — and a restart does not help. Diagnosing this
    from the symptoms is almost impossible, so we say it plainly.

    This route is deliberately public — an operator has to be able to ask what
    is wrong before anyone can sign in. So the answer is split in two: an
    anonymous caller learns whether the service is alive and whether it is
    protected, and nothing else. Paths on disk, the module location and the
    full route table are details for whoever is already inside.
    """
    import vistest

    ingest_open = (os.getenv("VISTEST_INGEST_OPEN") or "").strip().lower() in (
        "1", "true", "yes")
    public = {
        "ok": True,
        "version": getattr(vistest, "__version__", "?"),
        # Said out loud on purpose: an installation must not silently believe
        # its run intake is protected when nothing was configured.
        "ingest_protected":
            bool((os.getenv("VISTEST_INGEST_TOKEN") or "").strip()) or not ingest_open,
        "auth_enabled": not _auth_open(),
    }

    try:
        _require(request, "viewer")
    except HTTPException:
        return public

    where = str(Path(vistest.__file__).resolve().parent)
    # `Path.match` compares only the tail of the path, so here we use an honest
    # substring: we care whether the package sits as a copy in site-packages.
    installed_copy = "site-packages" in where.replace("\\", "/").split("/")

    return {
        **public,
        "root": str(ROOT),
        "module": where,
        "editable": not installed_copy,
        "routes": sorted({
            route.path for route in app.routes
            if getattr(route, "path", "").startswith("/api/")
        }),
    }


# --------------------------------------------------------------------------- #
#  Files and UI
# --------------------------------------------------------------------------- #
#  Both routes serve files off the service data volume, and that volume also
#  holds `vistest.db` (password hashes and live session tokens), `secrets.env`
#  and the unpacked sources of uploaded projects. A path check alone is not a
#  security boundary here — it only says «inside .vistest», which is exactly
#  where the secrets are. So there are three locks:
#
#    1. authentication — at minimum the `viewer` role;
#    2. an allow-list of extensions — a run artifact is a picture or a json,
#       nothing else needs to leave over this route;
#    3. an explicit deny-list of subtrees, so that a future artifact kind with
#       an allowed extension cannot reach the database or the secrets.
#
#  Plus `nosniff` and an attachment disposition: a project archive may contain
#  an .html, and serving it inline from our own origin is a stored XSS on top
#  of the session cookie.
# --------------------------------------------------------------------------- #
SERVABLE_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".json", ".txt", ".log",
}
#  Relative to ROOT. Anything under these never leaves over /files or /local.
FORBIDDEN_SUBTREES = ("projects", "external")
FORBIDDEN_NAMES = {"vistest.db", "vistest.db-wal", "vistest.db-shm",
                   "secrets.env", "projects.yaml", "vistest.yaml"}

# Inline types: these the browser must render in place (the diff viewer shows
# them as <img>). Everything else is handed over as a download.
INLINE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


@app.get("/files/{path:path}")
def files(path: str, request: Request):
    """Artifacts uploaded to the service by the runner."""
    _require(request, "viewer")
    return _serve_under(ARTIFACTS, path)


@app.get("/local/{path:path}")
def local_files(path: str, request: Request):
    """Run artifacts stored directly in .vistest/runs/.

    The runner and UI runs write images to disk and put local paths in the DB.
    Serving them to the browser as `D:\\project\\...` is pointless — the image
    will not load. This route serves the same file over HTTP.

    Scoped to `runs/` and `artifacts/` on purpose: the rest of the data volume
    is service state, not something to hand to a browser.
    """
    _require(request, "viewer")
    return _serve_under(ROOT, path)


def _serve_under(base: Path, path: str) -> FileResponse:
    root = base.resolve()
    try:
        p = (root / path).resolve()
    except (OSError, ValueError):
        raise HTTPException(404, "not found") from None
    if not p.is_relative_to(root) or not p.exists() or not p.is_file():
        raise HTTPException(404, "not found")

    if p.suffix.lower() not in SERVABLE_SUFFIXES:
        raise HTTPException(403, "this file type is not served over HTTP")

    # The deny-list is checked relative to the data volume root, not to `base`:
    # /files/ and /local/ have different bases, the protected subtrees are the
    # same ones.
    try:
        rel = p.relative_to(ROOT.resolve()).parts
    except ValueError:
        rel = ()
    if rel and (rel[0] in FORBIDDEN_SUBTREES or p.name in FORBIDDEN_NAMES):
        raise HTTPException(403, "this file is not served over HTTP")

    inline = p.suffix.lower() in INLINE_SUFFIXES
    return FileResponse(p, headers={
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition":
            f'{"inline" if inline else "attachment"}; filename="{_safe(p.name)}"',
        "Content-Security-Policy": "default-src 'none'; sandbox",
    })


def _servable(artifacts: dict) -> dict:
    """Local paths → URLs from which the browser can actually fetch the file."""
    return {k: _artifact_uri(v) for k, v in (artifacts or {}).items()}


def _artifact_uri(value) -> str:
    if not isinstance(value, str) or not value:
        return value
    if value.startswith(("/files/", "/local/", "http://", "https://", "data:")):
        return value
    try:
        p = Path(value).resolve()
    except Exception:
        return value
    for base, prefix in ((ARTIFACTS, "/files/"), (ROOT, "/local/")):
        try:
            base_r = base.resolve()
            if p.is_relative_to(base_r):
                return prefix + p.relative_to(base_r).as_posix()
        except Exception:
            continue
    return value


def frontend_stamp(directory: Path) -> str:
    """Отпечаток интерфейса, лежащего на диске.

    Нужен ровно для одного: чтобы обновление доходило до браузера. Файлы
    подключены обычными `<script src="js/x.js">`, без сборщика и без хэша в
    имени, а отдавались они без `Cache-Control`. Chrome в этом случае считает
    свежесть эвристикой по `Last-Modified` и держит файл часами, не спрашивая
    сервер вовсе.

    Ломается это молча и на редкость убедительно: часть файлов свежая, часть
    из кэша. Экран рисуется, кнопка на месте, а её обработчик зовёт функцию,
    которой в старом файле ещё нет, — и не происходит НИЧЕГО. Ни ошибки, ни
    намёка, что смотришь на позавчерашний интерфейс.

    Считается по именам, размерам и временам файлов, а не по содержимому:
    читать два десятка файлов на каждый заход на страницу незачем, а сборка,
    не изменившая ни одного размера и ни одной секунды, ничего и не изменила.
    """
    import hashlib

    # Здесь стоял кеш на пять секунд — и тесты `test_ui_delivery` показали,
    # почему его тут быть не должно: отпечаток обязан меняться в тот же
    # момент, что и файл на диске. Иначе правка интерфейса доезжает до
    # браузера «через несколько секунд», то есть ровно тот класс поломок,
    # ради которого отпечаток и заведён, возвращается — только теперь с
    # оговоркой «иногда».
    #
    # Кеш и не нужен: каталог интерфейса — это два десятка файлов, а не
    # десятки тысяч, как каталог артефактов (там кеш как раз стоит).
    h = hashlib.sha256()
    for p in sorted(directory.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in (".js", ".css", ".html"):
            continue
        try:
            st = p.stat()
        except OSError:                                     # pragma: no cover
            continue
        h.update(p.name.encode("utf-8"))
        h.update(f"{st.st_size}:{int(st.st_mtime)}".encode())
    return h.hexdigest()[:12]


_ASSET_REF = re.compile(r'(src|href)="((?:js/[\w.\-]+\.js|ui\.css))"')


def stamp_assets(html: str, stamp: str) -> str:
    """Дописать отпечаток к ссылкам на скрипты и стили.

    Версия в адресе — единственное, что действует через любой кэш по дороге, а
    не только через тот, который согласился нас переспросить.
    """
    return _ASSET_REF.sub(rf'\1="\2?v={stamp}"', html)


class FreshStatic(StaticFiles):
    """StaticFiles, который обязан переспросить.

    `no-cache` — это не «не кэшируй», а «кэшируй, но каждый раз спрашивай».
    Вместе с ETag это стоит один 304 на файл и закрывает целый класс поломок,
    где правка есть на диске, а в браузере её нет.
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


if (FRONTEND / "index.html").exists():

    @app.get("/ui/", include_in_schema=False)
    @app.get("/ui/index.html", include_in_schema=False)
    def ui_index():
        html = (FRONTEND / "index.html").read_text("utf-8")
        return HTMLResponse(stamp_assets(html, frontend_stamp(FRONTEND)),
                            headers={"Cache-Control": "no-store"})

    app.mount("/ui", FreshStatic(directory=str(FRONTEND), html=True), name="ui")

    @app.get("/")
    def index():
        return RedirectResponse("/ui/")

else:  # pragma: no cover
    import warnings

    warnings.warn(
        f"Review UI not found ({FRONTEND}). The API works, /ui/ will return 404. "
        "Specify the directory via VISTEST_FRONTEND.",
        stacklevel=1,
    )


def _safe(s: str) -> str:
    """One path segment out of a name that arrived over HTTP.

    The dot has to stay — snapshot names carry `.png` and project prefixes look
    like `shop.example` — which is exactly why `..` used to survive intact:
    every character was allowed, so a snapshot literally named `..` put the
    artifact one directory up. A segment made only of dots is not a name, it is
    a traversal, and it is replaced outright.
    """
    cleaned = "".join(c if c.isalnum() or c in "-_." else "_" for c in str(s))
    return "_" if set(cleaned) <= {"."} else cleaned


def _resolve(uri: str) -> Path | None:
    """Ссылка на артефакт → файл на диске, и только внутри каталога данных.

    Отсюда файл уезжает в `store.save()`, то есть СТАНОВИТСЯ ЭТАЛОНОМ, и
    дальше его можно скачать через `/files`. А `uri` берётся из
    `comparison.artifacts`, куда он попал из тела `POST /api/runs` — то есть
    его пишет чужой CI.

    Проверки не было никакой: `/files/../../etc/passwd` спокойно выходил за
    ARTIFACTS, а третья ветка принимала вообще любой абсолютный путь. Токен
    приёма прогонов — это право писать историю, а не читать диск сервера, и
    разница между этими двумя правами здесь и проводится.
    """
    for prefix, base in (("/files/", ARTIFACTS), ("/local/", ROOT)):
        if uri.startswith(prefix):
            return _inside_root(base / uri[len(prefix):])

    # Абсолютный путь пишет наш же раннер, когда работает на этой машине
    # (`.vistest/runs/...`). Всё, что вне каталога данных, — не наше.
    return _inside_root(Path(uri))


def _inside_root(path: Path) -> Path | None:
    try:
        p = path.resolve()
    except (OSError, ValueError):
        return None
    root = ROOT.resolve()
    if p != root and not p.is_relative_to(root):
        _logs.log.warning("artifact path outside the data volume refused: %s", path)
        return None
    return p if p.exists() else None


def serve(host: str = "127.0.0.1", port: int = 8420, reload: bool = False):
    """Запуск сервиса.

    `proxy_headers=False` — не описка. Разбор `X-Forwarded-*` живёт в
    `api/net.py`, с явным списком доверенных адресов в
    `VISTEST_TRUSTED_PROXIES`. Uvicorn при `proxy_headers=True` ПЕРЕПИСЫВАЕТ
    `scope["client"]` значением из заголовка, и после этого настоящий адрес
    соединения недоступен вообще — а именно по нему решается, можно ли с этого
    запроса запускать процессы на машине сервиса. Одно из двух: либо мы знаем
    настоящий адрес, либо у нас есть удобство uvicorn; выбран первый вариант.
    """
    import uvicorn

    uvicorn.run("vistest.api.main:app", host=host, port=port, reload=reload,
                proxy_headers=False)
