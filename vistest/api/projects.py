# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""REST for connected test suites.

    GET    /api/projects                list connected ones
    POST   /api/projects/discover       inspect a directory and propose a spec
    PUT    /api/projects/{key}          save the spec
    DELETE /api/projects/{key}          disconnect (project files are left alone)
    POST   /api/projects/{key}/run      run their tests
    GET    /api/projects/{key}/runs     parsed runs
    POST   /api/projects/{key}/approve  accept a snapshot as a baseline

Running someone else code is a thing to take seriously. That is why
connecting a project, like the variable editor, is by default available only
from the local machine: `POST /api/projects/{key}/run` starts an arbitrary
process on the server, and exposing that outward must be a deliberate choice.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import asdict
from glob import escape as glob_escape
from pathlib import Path

from fastapi import (
    APIRouter,
    Body,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)

from .. import suites
from ..config import VisTestConfig
from ..projects import Project, ProjectRegistry, discover, suggest
from . import net
from .jobs import JobFailure, runner

router = APIRouter()

#  «Локально» живёт в `api/net.py`: считается по адресу соединения,
#  который заголовком не подделать.
# Проходов установки зависимостей. Больше шести — это уже не «слой за слоем»,
# а проект, который проще поставить целиком по requirements.txt.
MAX_DEPS_ROUNDS = 6
MAX_UPLOAD = 200 * 1024 * 1024   # 200 MB per project archive
# Сколько архив имеет право занять в распакованном виде и из скольких файлов
# состоять. Zip сжимает нули в тысячи раз: 40 КБ архива разворачиваются в
# гигабайты, и проверка размера САМОГО архива про это ничего не знает.
MAX_UNPACKED = 2 * 1024 * 1024 * 1024   # 2 GB
MAX_ENTRIES = 50_000


def _uploads_root() -> Path:
    """Where projects uploaded as an archive get unpacked. Inside VISTEST_ROOT
    means on the same data volume: it survives a restart, and there is no need
    to mount the repository separately."""
    return Path(os.getenv("VISTEST_ROOT", ".vistest")).resolve() / "projects"


def _slug(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_-]+", "-", (s or "").strip()).strip("-").lower()
    return s or "project"


def _guard(request: Request, role: str = "admin",
           project: str | None = None) -> None:
    """Running someone else project starts a process on the server.

    That is fundamentally more powerful than everything else in the API, so
    there are two locks here, not one: where the request came from, and the
    role of whoever sent it. Loosening either of them is a deliberate act by
    the administrator.

    The default role is `admin` for connecting and configuring; the actual run
    is allowed for `reviewer` — the person who works with the tests should be
    able to run them, not only the owner of the installation.

    `project` передаётся там, где действие принадлежит одному набору: тогда
    роль считается как максимум из глобальной и выданной на этот проект.
    Подключение и настройка проектов проектным правом не выдаются — это
    действия над инсталляцией, а не над набором.
    """
    from . import rights
    from .auth import current_user

    db = _db()
    rights.check(db, current_user(db, request.cookies.get("vistest_session")),
                 role, project)

    mode = (os.getenv("VISTEST_PROJECTS_UI") or "local").strip().lower()
    if mode == "off":
        raise HTTPException(403, "Connecting projects is turned off "
                                 "(VISTEST_PROJECTS_UI=off)")
    if mode == "all":
        return
    if not net.is_loopback(request):
        raise HTTPException(
            403,
            "Projects can be connected only from the local machine (request "
            f"from {net.client_ip(request)}). This starts an arbitrary process, "
            "so exposing it outward is a deliberate choice: "
            "VISTEST_PROJECTS_UI=all",
        )


def _db():
    from .main import db

    return db


def _registry() -> ProjectRegistry:
    return ProjectRegistry(VisTestConfig.load())


def _need(key: str) -> Project:
    project = _registry().get(key)
    if project is None:
        raise HTTPException(404, f"Project {key!r} is not connected")
    return project


# --------------------------------------------------------------------------- #
@router.get("/api/projects")
def list_projects(request: Request):
    _guard(request)
    from ..external import baseline_status

    cfg = VisTestConfig.load()
    out = []
    for p in _registry().list():
        d = p.to_dict()
        d["problems"] = p.validate()
        d["baseline_counts"] = {
            b.dir: len(list((p.root_path / b.dir).glob("*.png")))
            if (p.root_path / b.dir).exists() else 0
            for b in p.baselines
        }
        d["baseline_status"] = baseline_status(p, cfg)
        # Можно ли этому проекту назвать браузер — и если нет, то почему.
        # Интерфейс обязан знать это ДО того, как показать пункт «во всех
        # браузерах»: пункт, который отвечает отказом, — не строгость, а
        # неправда на экране.
        d["browser_control"] = p.browser_control()
        d["browser_refusal"] = p.browser_refusal()
        out.append(d)
    return {"projects": out}


@router.post("/api/projects/discover")
def discover_project(request: Request, payload: dict = Body(...)):
    """Inspect a directory and propose a spec.

    Propose specifically: everything found is shown to a human and edited
    before saving. Guessing silently in someone else repository is a bad idea.
    """
    _guard(request)
    root = (payload.get("root") or "").strip()
    if not root:
        raise HTTPException(400, "Provide the path to the project directory")

    info = discover(root)
    if not info["exists"]:
        raise HTTPException(404, f"Directory not found: {root}")

    proposal = suggest(root, key=payload.get("key", ""))
    return {"discovered": info, "proposal": proposal.to_dict()}


@router.post("/api/projects/upload")
async def upload_project(request: Request,
                         file: UploadFile = File(...),
                         name: str = Form("")):
    """Connect a project by archive: upload a .zip — we unpack and inspect it.

    More convenient than mounting a volume: the team does not need access to
    the server filesystem, it is enough to hand over the .zip of the test
    repository. The archive is unpacked into VISTEST_ROOT/projects/<slug> (the
    data volume), then comes the same inspection and connection as by path. As
    always, VisTest does not change the project files.
    """
    _guard(request)
    fname = file.filename or ""
    if not fname.lower().endswith(".zip"):
        raise HTTPException(400, "A .zip archive of the project is required")

    # Читаем кусками и во временный файл на диске, а не в память.
    #
    # Здесь стояло `data = await file.read()`, и проверка размера — СТРОКОЙ
    # НИЖЕ. То есть потолок в 200 МБ не защищал ни от чего: к моменту проверки
    # присланное уже лежало в памяти целиком, сколько бы его ни было.
    spool = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
    try:
        written = 0
        while chunk := await file.read(1024 * 1024):
            written += len(chunk)
            if written > MAX_UPLOAD:
                raise HTTPException(
                    413, f"Archive is larger than {MAX_UPLOAD // (1024 * 1024)} MB")
            spool.write(chunk)
        spool.seek(0)
        try:
            zf = zipfile.ZipFile(spool)
        except zipfile.BadZipFile:
            raise HTTPException(400, "File is not a zip archive") from None
        return _unpack_project(zf, name, fname)
    finally:
        spool.close()


def _unpack_project(zf: zipfile.ZipFile, name: str, fname: str) -> dict:
    """Распаковка и осмотр. Вынесено, чтобы временный файл закрывался всегда."""

    slug = _slug(name or Path(fname).stem)
    dest = _uploads_root() / slug
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)

    # Zip-slip: the path of every file must stay inside dest.
    #
    # Плюс объём. Проверка пути отвечает на «куда положат», и не отвечает на
    # «сколько положат»: архив из одних нулей сжимается в тысячи раз, и том
    # данных заполняется файлом, который прошёл проверку размера с большим
    # запасом. Считаем ДО распаковки, по заголовкам архива.
    base = dest.resolve()
    entries = zf.infolist()
    if len(entries) > MAX_ENTRIES:
        shutil.rmtree(dest, ignore_errors=True)
        raise HTTPException(
            400, f"The archive holds more than {MAX_ENTRIES} entries")
    unpacked = 0
    for m in entries:
        target = (dest / m.filename).resolve()
        if target != base and not _inside(target, base):
            shutil.rmtree(dest, ignore_errors=True)
            raise HTTPException(400, "Unsafe path in the archive")
        unpacked += m.file_size
        if unpacked > MAX_UNPACKED:
            shutil.rmtree(dest, ignore_errors=True)
            raise HTTPException(
                413, f"The archive unpacks to more than "
                     f"{MAX_UNPACKED // (1024 * 1024)} MB")
    zf.extractall(dest)

    # People often archive a whole folder, so inside there is one directory. We
    # take it as the root, then the inspection finds conftest/tests instead of
    # an empty wrapper.
    entries = [p for p in dest.iterdir() if not p.name.startswith("__MACOSX")]
    dirs = [p for p in entries if p.is_dir()]
    files = [p for p in entries if p.is_file()]
    root = dirs[0] if (len(dirs) == 1 and not files) else dest

    info = discover(str(root))
    if not info.get("exists"):
        raise HTTPException(400, "No project directory found in the archive")
    proposal = suggest(str(root), key=slug)
    return {"discovered": info, "proposal": proposal.to_dict(), "root": str(root)}


@router.put("/api/projects/{key}")
def save_project(key: str, request: Request, payload: dict = Body(...)):
    _guard(request)
    project = Project.from_dict(key, payload)
    if not project.root:
        raise HTTPException(400, "Project root is not specified")
    # Лимит считается только для НОВОГО проекта: правка уже подключённого не
    # занимает места, и запрещать её из-за лимита значило бы запирать
    # заказчика внутри его же настроек.
    if not _registry().get(key):
        from .license import check_projects

        check_projects(_db())
    _registry().save(project)
    # Инструмент опознаётся по файлам в их корне и запоминается: без сброса
    # проект, только что переехавший на другой корень, ещё сутки считался бы
    # тем, чем был.
    suites.forget(project.root_path)
    return {"ok": True, "project": project.to_dict(),
            "problems": project.validate()}


EDITABLE = ("env", "pytest_args", "tests", "python", "note",
            "runner", "command", "suite", "naming", "search_dirs",
            "keep_dir")
NAMING_KINDS = ("actual", "expected", "diff", "strip")
VAR_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _clean_naming(value) -> dict:
    """Наши правила имён — регулярные выражения, и они приходят из формы.

    Непонятное выражение здесь дороже обычной опечатки: прогон не упадёт, он
    просто ничего не найдёт и скажет «пар не найдено», а причина будет лежать
    в поле, которое человек заполнил час назад. Поэтому проверяем сразу.
    """
    if not isinstance(value, dict):
        raise HTTPException(400, "«naming» must be an object: kind → pattern")
    out: dict[str, str] = {}
    for kind, pattern in value.items():
        kind = str(kind).strip().lower()
        pattern = str(pattern or "").strip()
        if not pattern:
            continue
        if kind not in NAMING_KINDS:
            raise HTTPException(
                400, f"Unknown naming rule: {kind!r} "
                     f"({' | '.join(NAMING_KINDS)})")
        try:
            compiled = re.compile(pattern)
        except re.error as e:
            raise HTTPException(
                400, f"The «{kind}» rule is not a valid expression: {e}") from e
        if kind != "strip" and "name" not in compiled.groupindex:
            raise HTTPException(
                400, f"The «{kind}» rule must say which part of the file name "
                     "is the snapshot name — put it in a (?P<name>…) group, "
                     r"for example (?P<name>.+)-actual\.png")
        out[kind] = pattern
    return out


@router.get("/api/suites")
def list_suites(request: Request):
    """The profiles the interface offers, and what each one implies."""
    _guard(request)
    return {"suites": suites.choices()}


@router.patch("/api/projects/{key}")
def patch_project(key: str, request: Request, payload: dict = Body(...)):
    """Change a few fields of a connected project, keeping the rest.

    Separate from `PUT` on purpose. `PUT` replaces the whole description and is
    right for connecting; for editing it is a trap — a form that knows nothing
    about `adapter` or `baselines` would silently wipe them on save. Here only
    the keys that actually arrived are touched.

    Editing the run environment from the interface exists because the run
    diagnosis points exactly here: it names the variable the project's fixtures
    read, and until now the only way to add it was to edit `projects.yaml` by
    hand on the server.
    """
    _guard(request)
    project = _need(key)
    raw = asdict(project)
    raw.pop("key", None)

    unknown = sorted(set(payload) - set(EDITABLE))
    if unknown:
        raise HTTPException(
            400, f"These fields cannot be edited here: {', '.join(unknown)}. "
                 "Available: " + ", ".join(EDITABLE))

    if "env" in payload:
        raw["env"] = _clean_env(payload["env"])
    if "pytest_args" in payload:
        raw["pytest_args"] = _clean_args(payload["pytest_args"])
    if "command" in payload:
        raw["command"] = _clean_args(payload["command"])
    if "runner" in payload:
        value = str(payload["runner"] or "pytest").lower()
        if value not in ("pytest", "command"):
            raise HTTPException(400, f"Unknown runner: {value!r} (pytest | command)")
        raw["runner"] = value
    if "suite" in payload:
        value = str(payload["suite"] or "auto").strip().lower()
        if value != "auto" and suites.get(value) is None:
            raise HTTPException(
                400, f"Unknown suite profile: {value!r} "
                     f"(auto | {' | '.join(suites.ids())})")
        raw["suite"] = value
    if "naming" in payload:
        raw["naming"] = _clean_naming(payload["naming"])
    if "search_dirs" in payload:
        raw["search_dirs"] = _clean_args(payload["search_dirs"])
    if "keep_dir" in payload:
        raw["keep_dir"] = bool(payload["keep_dir"])
    for field in ("tests", "python", "note"):
        if field in payload:
            raw[field] = str(payload[field] or "")

    updated = Project.from_dict(key, raw)
    _registry().save(updated)
    suites.forget(updated.root_path)

    from .auth import audit, current_user

    who = current_user(_db(), request.cookies.get("vistest_session"))["login"]
    # Values are deliberately not recorded in the audit: a person may paste a
    # password here even after being asked not to. The field names are enough to
    # answer «who changed what».
    audit(_db(), who, "project.edit", key, fields=sorted(payload))

    return {"ok": True, "project": updated.to_dict(),
            "problems": updated.validate()}


def _clean_env(value) -> dict[str, str]:
    if not isinstance(value, dict):
        raise HTTPException(400, "env must be an object {name: value}")
    out: dict[str, str] = {}
    for name, raw_value in value.items():
        name = str(name).strip()
        if not name:
            continue
        if not VAR_NAME.fullmatch(name):
            raise HTTPException(
                400, f"Invalid variable name: {name!r}. Latin letters, digits "
                     "and underscore are allowed, not starting with a digit.")
        text = "" if raw_value is None else str(raw_value)
        if "\n" in text or "\r" in text:
            raise HTTPException(400, f"{name}: a line break in the value")
        out[name] = text
    return out


def _clean_args(value) -> list[str]:
    if not isinstance(value, list):
        raise HTTPException(400, "pytest_args must be a list of strings")
    out: list[str] = []
    for item in value:
        text = str(item).strip()
        if not text:
            continue
        if "\n" in text or "\r" in text:
            raise HTTPException(400, "a line break in a pytest argument")
        out.append(text)
    return out


@router.delete("/api/projects/{key}")
def delete_project(key: str, request: Request):
    """Disconnect a project. Its files are left alone — only the record is removed."""
    _guard(request)
    if not _registry().delete(key):
        raise HTTPException(404, f"Project {key!r} is not connected")
    return {"ok": True}


# --------------------------------------------------------------------------- #
def parse_run_browsers(payload: dict) -> tuple[list[str], str]:
    """Что за прогон просят: список браузеров и его вид.

    Три формы, потому что три разных вопроса у человека:

      `{}`                                  — как было: браузер выбирает их код;
      `{browser: "firefox"}`                — «прогони в firefox»;
      `{browsers: [...], mode: separate}`   — «по одному прогону на браузер»;
      `{browsers: [...], mode: together}`   — «один прогон, все браузеры».

    Разница между двумя последними не косметическая. `separate` — это N
    прогонов в истории: каждый со своим вердиктом, своим статус-чеком в CI и
    своим ключом сериализации, то есть они идут ПАРАЛЛЕЛЬНО. `together` — один
    прогон и один вопрос «эта страница сломалась?», как у матрицы.
    """
    from ..matrix import MatrixError, normalize_browser

    raw = payload.get("browsers")
    if raw is None and payload.get("browser"):
        raw = [payload["browser"]]
    if raw is None:
        return [], ""
    if not isinstance(raw, list):
        raise HTTPException(400, "browsers must be a list")
    try:
        names = [normalize_browser(b) for b in raw if str(b).strip()]
    except MatrixError as e:
        raise HTTPException(400, str(e)) from None
    names = list(dict.fromkeys(names))
    if not names:
        return [], ""

    mode = str(payload.get("mode") or "").strip().lower()
    if mode not in ("separate", "together"):
        # Один браузер — вида нет вовсе, это обычный прогон. Несколько без
        # явного вида — «общий»: человек, который перечислил три браузера и не
        # сказал больше ничего, спрашивает про страницу, а не про три отчёта.
        mode = "together" if len(names) > 1 else ""
    return names, mode


@router.post("/api/projects/{key}/run")
def run_project_endpoint(key: str, request: Request, payload: dict = Body(None)):
    # Running a run should be available to the person who works with the tests,
    # not only the owner of the installation. Configuring the connection is
    # still for the admin.
    _guard(request, role="reviewer", project=key)
    project = _need(key)
    payload = payload or {}

    # `validate` reports only what actually blocks a run: a missing root, a
    # missing tests directory, an unknown baseline store. The filter that used
    # to stand here dropped a message about the virtual environment which
    # `validate` no longer produces — dead code that hid nothing.
    problems = project.validate()
    if problems:
        raise HTTPException(400, "Run is not possible: " + "; ".join(problems))

    lock_key = f"project:{key}"
    cfg = VisTestConfig.load()
    env_overrides = {str(k): str(v) for k, v in (payload.get("env") or {}).items()}
    update = bool(payload.get("update_baselines"))
    extra = list(payload.get("args") or [])
    # `only` — один файл или один тест вместо всего набора. ЗАМЕНЯЕТ цель, а не
    # добавляется к ней: приписанный сбоку идентификатор дал бы pytest две цели,
    # и «прогнать один тест» означало бы прогнать весь набор и вдобавок его.
    only = str(payload.get("only") or "").strip()
    if only and (only.startswith("-") or ".." in only):
        raise HTTPException(400, "only must be a test path inside the project")
    if only:
        # Отказ, а не тихий прогон всего набора. До этого `only` вне pytest
        # просто терялся в сборке команды: человек нажимал «перепроверить этот
        # снимок», ждал двадцать минут и получал прогон всего набора —
        # причём выглядело это как медленная кнопка, а не как операция,
        # которой у этого инструмента нет.
        from ..external import only_refusal

        refusal = only_refusal(project, only)
        if refusal:
            raise HTTPException(400, refusal)
    # Baseline source for this run: "project" (their PNGs) | "vistest" (our
    # captured set) | None (the project setting). ci — the run only compares.
    baseline_source = payload.get("baselines")
    if baseline_source not in ("project", "vistest"):
        baseline_source = None
    ci = bool(payload.get("ci"))

    browsers, mode = parse_run_browsers(payload)

    # Different projects run in parallel — they write to different baselines.
    # The same project is serialized: two runs into one storage would clobber
    # each other. We tell the person about it rather than refusing silently.
    #
    # У прогонов «по одному на браузер» ключ свой на каждый: наборы эталонов
    # разные, и занятость соседнего браузера их не касается. Проверять их одним
    # общим ключом значило бы отвечать «проект уже гоняется» на просьбу
    # запустить ровно то, что и просили запустить одновременно.
    shared_write = update and baseline_source != "vistest"
    if browsers and mode == "separate" and not shared_write:
        keys = [f"{lock_key}:{b}" for b in browsers]
    else:
        keys = [lock_key]
    for candidate in keys:
        busy = runner.busy(candidate)
        if busy:
            raise HTTPException(409, {
                "error": "This project is already running",
                "message": (f"Started by {busy.owner or 'someone'}, "
                            f"status {busy.status}. Two runs of one project "
                            "write to the same baselines, so we wait for it "
                            "to finish."),
                "job_id": busy.id,
            })

    # Про пустые наборы человек узнаёт ДО запуска, а не после полутора минут
    # первого браузера. Прогон это не блокирует — остальные браузеры отработают,
    # и в этом весь смысл, — но начинать полторы минуты ожидания, уже зная, чем
    # кончится второй, значит тратить чужое время молча.
    #
    # Считается только там, где сравнение идёт с НАШИМ набором: у PNG проекта
    # каталог один на все браузеры, и вопроса «а снят ли он для firefox» там нет.
    missing: list[str] = []
    own_set = (baseline_source == "vistest"
               or (baseline_source is None and project.uses_own_baselines()))
    if browsers and own_set and not update:
        from ..external import browsers_with_baselines

        try:
            have = browsers_with_baselines(project, cfg)
            missing = [b for b in browsers if have.get(b) is False]
        except Exception:
            # Не смогли посмотреть — не повод не запускать: это подсказка, а не
            # проверка доступа.
            missing = []

    if browsers:
        # Отказ, а не три одинаковых прогона chromium. Прогон «во всех
        # браузерах», который на деле гоняет один, — худший исход: он пишет
        # три набора эталонов, показывает по ним зелёное и создаёт уверенность
        # в покрытии, которого нет.
        refusal = project.browser_refusal()
        if refusal:
            raise HTTPException(400, refusal)

    def work(job, browser: str = ""):
        from ..external import ExternalRunRefused, run_project

        src_label = {"project": "project baselines", "vistest": "VisTest baselines"}.get(
            baseline_source, "baselines per the project setting")
        job.say(f"project: {project.name} ({project.key}) · {src_label}"
                + (f" · {browser}" if browser else "")
                + (" · CI mode" if ci else ""))
        try:
            run = run_project(
                project, cfg=cfg, env_overrides=env_overrides,
                update_baselines=update, extra_args=extra, only=only,
                baseline_source=baseline_source, browser=browser, ci=ci,
                log=lambda t: job.say(t),
                should_stop=lambda: job.cancelled,
            )
        except ExternalRunRefused as e:
            # Причина уже сформулирована для человека. `JobFailure` отличается
            # от обычного исключения ровно тем, что не тащит за собой
            # питоновский трейс: на объяснённом отказе трейс говорит
            # «инструмент сломался» там, где инструмент отработал как задумано.
            raise JobFailure(str(e)) from None
        s = run.summary()
        # Snapshots and tests are counted separately on purpose. «9 snapshots»
        # for eleven tests is a normal outcome (two tests broke before the
        # comparison), but only if it is said out loud — otherwise the run reads
        # as if two thirds of the suite never started.
        job.say(f"result: snapshots {s['total']} "
                f"(failed {s['failed']}, new baselines {s['new']}, "
                f"errors {s['errored']}) · tests {s['tests']} "
                f"(failed {s['tests_failed']}, "
                f"never started {s['tests_not_started']})")

        # A run without a single result is NOT a success. Previously the job
        # still finished as «done», the person saw a green toast, even though
        # pytest did not collect/run the tests (no dependencies, wrong path,
        # the adapter did not connect), and nothing appeared in history. Now we
        # fail with the real cause — it surfaces as a red toast.
        if s["total"] == 0:
            if run.errors:
                reason = run.errors[0]
            elif s["tests"]:
                reason = (
                    f"{s['tests']} tests ran, but not a single comparison "
                    "happened. Check the comparison point in the project "
                    "settings (adapter.target) and the snapshot-name argument "
                    "(name_arg).")
            else:
                reason = ("pytest did not run a single test. Check the path to "
                          "the tests, the project dependencies "
                          "(pytest/playwright) and the interpreter — the «Check "
                          "dependencies» button.")
            if not run.errors and run.output:
                reason += " · " + str(run.output[-1])[:200]
            job.say("run with no results: " + reason, "error")
            raise JobFailure(reason)

        result = run.to_dict()
        # We put the run into the same history as our own: then it opens with
        # the usual viewer — curtain, onion, blink, region table — and all
        # snapshots are visible in it, not only the failed ones.
        try:
            from .main import ARTIFACTS, db
            from .publish import publish_external_run

            run_id = publish_external_run(
                db, ARTIFACTS, result, project=project.key,
                log=job.say)
            result["history_run_id"] = run_id
            if run_id:
                job.say(f"View with images — the «Runs» tab, "
                        f"project «{project.key}».")
        except Exception as e:
            # History is nice, but the run matters more: we must not fail
            # because of it. Yet we must not stay silent either — otherwise the
            # person looks for a run that is not there.
            result["history_error"] = f"{type(e).__name__}: {e}"
            job.say("could not write to history: " + result["history_error"],
                    "warn")

        # A test that fell at setup is not a result: its body never started, so
        # nothing was compared. Finishing such a run as «done» because two of
        # eleven snapshots happened to go through means calling a broken
        # precondition a successful check. The history entry is already written
        # above — what is left is to say plainly that the run cannot be trusted.
        #
        # The diagnosis itself is already in the log: `run_project` says every
        # remark once, at the moment it is found. Repeating the whole list here
        # buried the answer under three copies of itself, so the toast gets a
        # short line and the log keeps the detail.
        not_started = run.setup_failures
        if not_started:
            raise JobFailure(
                f"{len(not_started)} of {s['tests']} tests never started — they "
                "fell at fixture setup. The cause and what to do about it are "
                "in the log above.")

        return result

    from .auth import audit, current_user

    who = current_user(_db(), request.cookies.get("vistest_session"))["login"]
    audit(_db(), who, "project.run", project.key,
          update_baselines=update, env=env_overrides,
          browsers=browsers or None, mode=mode or None)

    # ------- один прогон на браузер -------
    if browsers and mode == "separate":
        # Ключ сериализации на браузер, а не на проект: наборы эталонов у них
        # разные (браузер входит в ключ платформы), писать друг другу они не
        # могут — и потому идут параллельно, в пределах общего предела задач.
        # Ровно это человек и просит, когда говорит «одновременно во всех».
        #
        # Кроме одного случая: пересъёмка эталонов проекта пишет в ИХ общий
        # каталог PNG, один на все браузеры. Там параллель — это гонка записи,
        # и прогон снова сериализуется по проекту.
        jobs = []
        for name in browsers:
            key_for = lock_key if shared_write else f"{lock_key}:{name}"
            job = runner.submit(
                "project", f"Run {project.name} · {name}",
                (lambda b: lambda j: work(j, b))(name),
                owner=who, lock_key=key_for)
            jobs.append({"browser": name, "job_id": job.id,
                         "queued": job.status == "queued"})
        return {"mode": "separate", "jobs": jobs,
                "missing_baselines": missing,
                # Первый — чтобы старые клиенты, ждущие один `job_id`, всё ещё
                # за чем-то следили, а не считали, что ничего не запустилось.
                "job_id": jobs[0]["job_id"] if jobs else None,
                "parallel": not shared_write}

    # ------- один прогон, все браузеры подряд -------
    if browsers and mode == "together":
        def work_all(job):
            if missing:
                job.say(
                    f"{', '.join(missing)} has no VisTest baseline set yet — "
                    "these browsers will refuse; capture their set with "
                    "«⋯ → Snap VisTest baselines» through the «▾» menu", "warn")
            results, failures = [], []
            for i, name in enumerate(browsers):
                if job.cancelled:
                    break
                job.progress = i / max(len(browsers), 1)
                job.say(f"── {name} ──")
                try:
                    results.append({"browser": name, **work(job, name)})
                except Exception as e:
                    # Один браузер упал — остальные обязаны досчитаться.
                    # Иначе «прогон во всех браузерах» означает «в первом, а
                    # дальше как повезёт», и хуже всего то, что выглядит это
                    # как полный отчёт.
                    #
                    # Ловится ЛЮБОЕ исключение, а не только `JobFailure`.
                    # Здесь стояло `except JobFailure`, и на первом же реальном
                    # прогоне это выстрелило: у firefox не оказалось набора
                    # эталонов, `run_project` бросил `RuntimeError` — задача
                    # умерла целиком, webkit не запустился вовсе, а обещание
                    # «остальные досчитаются» осталось только в комментарии.
                    reason = str(e) if isinstance(e, JobFailure) \
                        else f"{type(e).__name__}: {e}"
                    job.say(f"{name}: {reason}", "error")
                    failures.append({"browser": name, "error": reason})
            job.progress = 1.0
            if failures and not results:
                raise JobFailure(
                    "not a single browser produced a result: "
                    + "; ".join(f"{f['browser']} — {f['error']}" for f in failures))
            if failures:
                # Итог отдельной строкой в конце, а не только ошибками по
                # дороге: лог к этому моменту в сотни строк чужого pytest, и
                # «в скольких браузерах прогон на самом деле состоялся» — это
                # то, ради чего человек в него и полезет.
                job.say(f"the run happened in {len(results)} of "
                        f"{len(browsers)} browsers; did not finish: "
                        + ", ".join(f["browser"] for f in failures), "warn")
            return {"browsers": browsers, "runs": results, "failed": failures}

        job = runner.submit(
            "project", f"Run {project.name} · {len(browsers)} browsers",
            work_all, owner=who, lock_key=lock_key)
        return {"mode": "together", "job_id": job.id, "browsers": browsers,
                "missing_baselines": missing, "queued": job.status == "queued"}

    # ------- как было -------
    one = browsers[0] if browsers else ""
    job = runner.submit("project",
                        f"Run {project.name}" + (f" · {one}" if one else ""),
                        lambda j: work(j, one),
                        owner=who, lock_key=lock_key)
    return {"job_id": job.id, "browser": one or None,
            "queued": job.status == "queued"}


@router.post("/api/projects/{key}/ingest")
def ingest_ready_artifacts(key: str, request: Request, payload: dict = Body(None)):
    """Разобрать картинки, которые их прогон уже оставил. Ничего не запуская.

    Третий способ подключения, и единственный, которому не нужен их инструмент
    в нашем образе. `npx playwright test` внутри контейнера VisTest — это node,
    `npm ci` и вся их сборка у нас; для JVM и .NET такой обмен не окупается
    вовсе. Здесь наоборот: их CI гоняет тесты там, где у него всё стоит, а
    сюда приезжает каталог с картинками.

    Правила разбора те же, что у обычного прогона: тот же профиль набора, те же
    эталоны, тот же сборщик — двух дверей с разным поведением быть не должно.
    """
    _guard(request)
    project = _need(key)
    payload = payload or {}

    problems = project.validate()
    if problems:
        raise HTTPException(400, "Ingest is not possible: " + "; ".join(problems))

    cfg = VisTestConfig.load()
    dirs = _clean_args(payload.get("dirs") or [])
    browser = str(payload.get("browser") or "").strip().lower()
    update = bool(payload.get("update_baselines"))
    baseline_source = payload.get("baselines")
    if baseline_source not in ("project", "vistest"):
        baseline_source = None

    lock_key = f"project:{key}"
    busy = runner.busy(lock_key)
    if busy:
        raise HTTPException(409, {
            "error": "This project is already running",
            "message": (f"Started by {busy.owner or 'someone'}, status "
                        f"{busy.status}. Reading the results while a run "
                        "writes them would judge half a run."),
            "job_id": busy.id,
        })

    from .auth import audit, current_user

    who = current_user(_db(), request.cookies.get("vistest_session"))["login"]
    audit(_db(), who, "project.ingest", key, dirs=dirs or None,
          browser=browser or None)

    def work(job):
        from ..external import ExternalRunRefused, ingest_project, publish

        try:
            run = ingest_project(project, cfg=cfg, dirs=dirs, browser=browser,
                                 baseline_source=baseline_source,
                                 update_baselines=update,
                                 log=lambda t: job.say(t))
        except ExternalRunRefused as e:
            raise JobFailure(str(e)) from None

        s = run.summary()
        job.say(f"result: snapshots {s['total']} (failed {s['failed']}, "
                f"new baselines {s['new']}, errors {s['errored']})")
        try:
            publish(run, project, cfg=cfg, log=lambda t: job.say(t))
        except Exception as e:
            job.say(f"could not write to the history: {e}", "warn")

        if not run.results:
            # Пустой разбор — это не успех. Зелёный тост на нуле найденных пар
            # означает «всё хорошо», хотя на самом деле не проверено ничего, а
            # причина уже названа строкой выше.
            raise JobFailure(
                "Not a single pair was found — the reason is in the log above.")
        return {"run": run.to_dict(), "summary": s,
                "history_run_id": run.history_run_id}

    job = runner.submit("project", f"Ingest {project.name}", work,
                        owner=who, lock_key=lock_key)
    return {"job_id": job.id, "queued": job.status == "queued"}


@router.post("/api/projects/{key}/check")
def check_project(key: str, request: Request, payload: dict = Body(None)):
    """Collect their tests without running anything.

    A cheap way to learn the truth before a run: their conftest is brought up,
    their modules are imported, and if something is missing it is visible right
    away and with their own wording.
    """
    _guard(request)
    project = _need(key)
    payload = payload or {}

    from ..external import preflight

    result = preflight(project, cfg=VisTestConfig.load(),
                       env_overrides={str(k): str(v) for k, v
                                      in (payload.get("env") or {}).items()},
                       log=lambda _t: None)
    result["problems"] = project.validate()
    return result


@router.post("/api/projects/{key}/deps")
def install_deps(key: str, request: Request, payload: dict = Body(None)):
    """Fetch the missing dependencies into the VisTest environment.

    By default only what is missing gets installed: `requirements.txt`
    describes the whole project, while we need the visual tests. Pulling in a
    database driver for their sake that does not build under the current Python
    would break the install over a package these tests do not use.
    """
    _guard(request)
    project = _need(key)
    payload = payload or {}

    if runner.active("deps"):
        raise HTTPException(409, "Installation is already in progress")

    def work(job):
        from ..external import (
            install_dependencies,
            install_requirements,
            preflight,
        )

        cfg = VisTestConfig.load()

        # Чужой набор ставит зависимости своим менеджером, в своём корне.
        # Питоновская ветка ниже — не «общий случай», а частный: она ставит
        # пакеты в окружение VisTest, потому что этим окружением их набор и
        # гоняется. Для Node и JVM это не так, и pip там не поможет ничем.
        if not project.uses_pytest():
            code = install_dependencies(project, log=job.say,
                                        should_stop=lambda: job.cancelled)
            if code != 0:
                raise JobFailure(
                    "Dependencies were not installed — the reason is in the "
                    "log above.")
            return {"code": code, "mode": "suite"}

        if payload.get("all"):
            job.say("installing the whole requirements.txt")
            code = install_requirements(project, log=job.say,
                                        all_requirements=True)
            return {"code": code, "mode": "all"}

        # Missing dependencies are discovered in layers. Even with
        # `--continue-on-collection-errors` a module that fails to import hides
        # everything that would have been imported after it, so one pass sees
        # one layer. Previously that was the person's job: press, wait, press
        # again, four times over. Now the loop is here, and it stops on its own
        # — either everything collects, or a round brings nothing new.
        installed: list[str] = []
        seen: set[tuple] = set()

        for attempt in range(1, MAX_DEPS_ROUNDS + 1):
            job.say(f"checking what is missing… (pass {attempt})")
            pre = preflight(project, cfg=cfg, log=job.say)
            if pre["ok"]:
                done = ("Everything is in place — the tests collect and the "
                        "fixtures resolve.")
                job.say(done if not installed else
                        done + " Installed: " + ", ".join(sorted(set(installed))))
                return {"code": 0, "installed": sorted(set(installed)),
                        "passes": attempt}

            missing = list(pre["missing"])
            plugins = list(pre["plugins"])
            if not missing and not plugins:
                job.say("Collection fails, but no missing packages are visible "
                        "— look at the output: the cause is in their conftest, "
                        "not in the dependencies.", "warn")
                return {"code": 1, "installed": sorted(set(installed)),
                        "passes": attempt}

            batch = tuple(sorted(set(missing) | set(plugins)))
            if batch in seen:
                # The same set as a round ago: pip is not moving, and another
                # attempt would only repeat the same failures.
                job.say("The same set as on the previous pass — installation is "
                        "not moving. What did not install is listed above.",
                        "warn")
                return {"code": 1, "installed": sorted(set(installed)),
                        "passes": attempt, "stuck": list(batch)}
            seen.add(batch)

            if missing:
                job.say("missing modules: " + ", ".join(missing))
            if plugins:
                job.say("missing pytest plugins: " + ", ".join(plugins)
                        + f" (fixtures {', '.join(pre['missing_fixtures'])})")

            install_requirements(project, log=job.say, only=missing,
                                 packages=plugins)
            installed += batch

        job.say(f"Stopped after {MAX_DEPS_ROUNDS} passes — the dependencies "
                "keep revealing new ones. Most likely it is easier to install "
                "the whole requirements.txt at once.", "warn")
        return {"code": 1, "installed": sorted(set(installed)),
                "passes": MAX_DEPS_ROUNDS}

    job = runner.submit("deps", f"Dependencies {project.name}", work)
    return {"job_id": job.id}


@router.get("/api/projects/{key}/runs")
def project_runs(key: str, request: Request, limit: int = Query(20, le=100)):
    """Parsed runs of this project — newest on top."""
    _guard(request)
    _need(key)
    cfg = VisTestConfig.load()
    runs_root = cfg.runs_path()
    if not runs_root.exists():
        return {"runs": []}

    # Ключ проекта попадает прямо в glob-шаблон, а `*` и `?` в нём — метасимволы.
    # Ключ вида `*` показывал прогоны всех проектов сразу.
    safe_key = glob_escape(key)
    out = []
    for d in sorted(runs_root.glob(f"ext-{safe_key}-*"),
                    key=lambda p: p.name, reverse=True)[:limit]:
        path = d / "external.json"
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text("utf-8"))
        except Exception:
            continue
        data["id"] = d.name
        out.append(data)
    return {"runs": out}


@router.post("/api/projects/{key}/baselines/reset")
def reset_baselines_endpoint(key: str, request: Request,
                             payload: dict = Body(None)):
    """Delete the VisTest own baseline set.

    This command never touches the project folder — only what VisTest captured
    itself. It is needed when the set is due for a fresh rebuild: after a
    browser version change or a deliberate redesign it is easier to capture
    again than to sort through twenty diffs one by one.
    """
    _guard(request, project=key)
    project = _need(key)
    payload = payload or {}

    from ..external import reset_baselines

    removed = reset_baselines(project, cfg=VisTestConfig.load(),
                              platform=payload.get("platform", ""),
                              log=lambda _t: None)
    return {
        "ok": True,
        "removed": removed,
        "hint": "The next run will capture the baselines again. The project "
                "folder is untouched.",
    }


@router.get("/api/projects/{key}/file")
def project_file(key: str, request: Request, path: str = Query(...)):
    """Serve a run image by absolute path.

    The path comes from the response of this same API, but it still cannot be
    trusted: the query parameter is editable in the address bar. So we serve
    only what lies inside the directories we created ourselves — VisTest runs
    and the baseline folders of the connected project.
    """
    _guard(request)
    project = _need(key)
    cfg = VisTestConfig.load()

    target = Path(path).resolve()
    allowed = [cfg.runs_path().resolve(), project.sidecar_dir(cfg).resolve()]
    for b in project.baselines:
        allowed.append((project.root_path / b.dir).resolve())

    if not any(_inside(target, root) for root in allowed):
        raise HTTPException(403, "Path is outside the run and baseline directories")
    if not target.exists() or not target.is_file():
        raise HTTPException(404, f"File not found: {target}")

    from fastapi.responses import FileResponse

    return FileResponse(str(target))


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


@router.post("/api/projects/{key}/approve")
def approve_snapshot(key: str, request: Request, payload: dict = Body(...)):
    """Accept a snapshot as a baseline.

    Which store gets overwritten is decided by `scope` — the source of
    baselines the run actually used — not by the project setting. See
    `external.approve`: approving a snapshot from a «Run on VisTest baselines»
    used to land in the project repository, because the choice was made from
    the saved setting rather than from the run.
    """
    _guard(request, role="reviewer", project=key)
    project = _need(key)
    cfg = VisTestConfig.load()

    name = (payload.get("name") or "").strip()
    actual = (payload.get("actual") or "").strip()
    if not name or not actual:
        raise HTTPException(400, "name and actual are required")

    # `actual` is a path that arrives over HTTP. Even though this API produced
    # it a moment ago, the request is editable — so we serve the same rule as
    # `/api/projects/{key}/file`: only inside directories we created ourselves.
    target = _resolve_inside(actual, project, cfg)

    scope = (payload.get("scope") or "").strip().lower() or None
    if scope not in (None, "project", "vistest"):
        raise HTTPException(400, "scope must be 'project' or 'vistest'")

    from ..external import approve

    path = approve(project, name, cfg=cfg, actual_png=target, scope=scope)

    from .auth import audit, current_user
    who = current_user(_db(), request.cookies.get("vistest_session"))["login"]
    audit(_db(), who, "project.baseline.approved", f"{key}/{name}",
          scope=scope or project.baseline_store, path=str(path))

    to_repo = not str(path).startswith(str(cfg.root_path))
    return {
        "ok": True,
        "path": str(path),
        "scope": scope or project.baseline_store,
        "hint": ("The file has been overwritten in the project repository — it "
                 "will land in a normal git diff and a normal review.")
        if to_repo else
        ("Written into the VisTest baseline set for this project — the project "
         "folder is untouched."),
    }


def _resolve_inside(raw: str, project: Project, cfg: VisTestConfig) -> Path:
    """A path from the request → a real file inside our own directories.

    The allowed roots are exactly the ones VisTest creates or is told to read:
    run directories, the project sidecar, and the project's own baseline
    folders. Anything else is a 403 — not a 404, because «not found» would let
    a caller probe the file system for what exists.
    """
    try:
        target = Path(raw).resolve()
    except (OSError, ValueError):
        raise HTTPException(400, "Malformed path") from None

    allowed = [cfg.runs_path().resolve(), project.sidecar_dir(cfg).resolve()]
    for b in project.baselines:
        allowed.append((project.root_path / b.dir).resolve())

    if not any(_inside(target, root) for root in allowed):
        raise HTTPException(403, "Path is outside the run and baseline directories")
    if target.suffix.lower() != ".png":
        raise HTTPException(400, "A .png snapshot is required")
    if not target.is_file():
        raise HTTPException(404, f"Snapshot not found: {target}")
    return target
