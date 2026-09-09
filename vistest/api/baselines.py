"""Baseline management from the UI: listing, URL binding, capture, running checks.

Why this lives in the interface and not only in the CLI: creating and maintaining
a set of snapshots is the most frequent operation, and it should be visible. When
the list of baselines sits in the file system while the binding of «which snapshot
came from which address» lives only in the test author head, the set goes stale
fast: nobody knows what was captured from where or whether it is still current.

That is why a baseline has a spec (`meta.json`): address, window size, selector,
pause. With it, a snapshot can be recreated and checked with a single button —
without a single line of code.
"""

from __future__ import annotations

import dataclasses
import fnmatch
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import Response

from .. import source as _source
from ..config import VisTestConfig, platform_key
from ..models import Verdict
from ..storage import FileBaselineStore, split_project
from . import net
from .jobs import Job, JobFailure, runner

router = APIRouter()
_cfg = VisTestConfig.load()
ROOT = Path(os.getenv("VISTEST_ROOT", ".vistest")).resolve()

#  «Локально» живёт в `api/net.py`: считается по адресу соединения,
#  который заголовком не подделать.


# --------------------------------------------------------------------------- #
#  Generated tests: viewing and editing from the UI
#
#  Recording with the mouse assembles a pytest file, but a person almost always
#  wants to look at it and adjust it — rename a snapshot, drop an extra step.
#  Previously this required digging into the file system; now the same file is
#  available from the interface. Editing tests runs code on the next run, so it
#  falls under the same restrictions as recording: the reviewer role and, by
#  default, only from the service machine.
# --------------------------------------------------------------------------- #
def _tests_dir() -> Path:
    """Directory with tests. The same one codegen writes to (default ./tests)."""
    return Path(os.getenv("VISTEST_TESTS_DIR") or "tests").resolve()


def _resolve_test(name: str) -> Path:
    """File name -> a safe path strictly inside the tests directory."""
    if not name or not re.fullmatch(r"[A-Za-z0-9_.\-]+\.py", name):
        raise HTTPException(400, "Invalid test file name")
    root = _tests_dir()
    p = (root / name).resolve()
    if p != root and root not in p.parents:
        raise HTTPException(400, "Path outside the tests directory")
    return p


def _guard_tests(request: Request, role: str = "reviewer") -> None:
    from .auth import require
    from .main import db

    require(db, request.cookies.get("vistest_session"), role)
    if role == "viewer":
        return  # reading a test source runs nothing
    mode = (os.getenv("VISTEST_TESTS_UI") or "local").strip().lower()
    if mode == "off":
        raise HTTPException(403, "Editing tests from the interface is turned off "
                                 "(VISTEST_TESTS_UI=off)")
    if mode == "all":
        return
    if not net.is_loopback(request):
        raise HTTPException(
            403,
            "Tests can be edited and run only from the service machine "
            f"(request from {net.client_ip(request)}). To allow deliberately: "
            "VISTEST_TESTS_UI=all",
        )


def _require(request: Request, role: str, project: str | None = None) -> dict:
    """Role check for baseline-mutating endpoints.

    Unlike mouse recording, these actions (capture/recapture/check/delete) are
    also performed by a remote reviewer, so loopback is not required here — only
    the role. In local mode require lets everyone through as admin, so solo work
    is not broken.

    `project` — набор, над которым совершается действие. Без него `reviewer`
    означал «может переписать эталон любого подключённого проекта», и это самая
    дорогая из возможных ошибок в интерфейсе: утверждение необратимо, а свой и
    чужой набор выглядят на экране одинаково.
    """
    from . import rights
    from .auth import current_user
    from .main import db
    return rights.check(
        db, current_user(db, request.cookies.get("vistest_session")),
        role, project)


def _rights_key(scope: str | None) -> str:
    """Проект набора эталонов — так, как его понимают права.

    Собственный набор сервиса тоже чей-то: его проект — тот, под именем
    которого сервис ведёт свою историю. Иначе «global» был бы единственным
    местом, где право не спрашивают вовсе, и изоляция ломалась бы ровно там,
    где у большинства инсталляций и лежит основной набор.
    """
    from . import rights

    _kind, key = parse_scope(scope)
    return rights.of_scope(_kind, key, own=_cfg_fresh().service.project)


def _cfg_fresh() -> VisTestConfig:
    """Config is re-read: the user may have edited vistest.yaml on the fly."""
    try:
        return VisTestConfig.load()
    except Exception:
        return _cfg


def _cfg_with_thresholds(project_key: str | None = None) -> VisTestConfig:
    """Config plus the verdict thresholds set from the interface."""
    cfg = _cfg_fresh()
    try:
        from .main import db
        from .thresholds import apply
        return apply(cfg, db, project_key)
    except Exception:
        # A threshold that could not be read must not cost a run: the config
        # value is a perfectly good answer.
        return cfg


# --------------------------------------------------------------------------- #
#  Scope: which set of baselines these routes are talking about
#
#  Every route here used to be hard-wired to one directory —
#  `.vistest/baselines/<platform>/`. That is the service's own set, and it is
#  the only one the «Baselines» tab could ever show.
#
#  Meanwhile a connected project can have a whole second set, captured by
#  VisTest itself and living at `.vistest/external/<key>/baselines/<platform>/`.
#  It was invisible: not in the list, not in the previews, and — the part that
#  actually hurt — not reachable by `resnap` or `/api/suite/run`. So a snapshot
#  VisTest had captured for a connected project could not be looked at and
#  could not be re-run. There was no scenario to launch, because the route that
#  launches scenarios enumerated a different folder.
#
#  `scope` fixes that with one parameter, understood everywhere:
#      scope=global            the service's own set (default, old behaviour)
#      scope=project:<key>     the VisTest set captured for a connected project
# --------------------------------------------------------------------------- #
GLOBAL_SCOPE = "global"


def parse_scope(scope: str | None) -> tuple[str, str | None]:
    """`"project:aeron"` → `("vistest", "aeron")`; anything empty → global."""
    text = (scope or "").strip()
    if not text or text == GLOBAL_SCOPE:
        return GLOBAL_SCOPE, None
    if text.startswith("project:"):
        key = text.split(":", 1)[1].strip()
        if not key:
            raise HTTPException(400, "scope=project: requires a project key")
        return "vistest", key
    raise HTTPException(
        400, f"Unknown scope {scope!r}. Use 'global' or 'project:<key>'.")


def _project_of(key: str):
    from ..projects import ProjectRegistry

    project = ProjectRegistry(_cfg_fresh()).get(key)
    if project is None:
        raise HTTPException(404, f"Project {key!r} is not connected")
    return project


def _baselines_root(scope: str | None = None) -> Path:
    """Directory holding the per-platform subdirectories of this scope."""
    kind, key = parse_scope(scope)
    cfg = _cfg_fresh()
    if kind == GLOBAL_SCOPE:
        return cfg.root_path / cfg.paths.baselines
    return _project_of(key).vistest_baselines_path(cfg)


# A platform key is a directory name — `win32-chromium-1x` — and it arrives as
# a query parameter. The snapshot *name* has been sanitised since day one
# (`storage.fs._safe`); the platform never was. It went into the path verbatim,
# so `?platform=../../..` read a `baseline.png` and wrote a `meta.json` outside
# the baselines directory entirely. One regexp, in the one place every route
# goes through.
_PLATFORM_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}")


def _platform_segment(platform: str) -> str:
    text = str(platform or "").strip()
    if not _PLATFORM_RE.fullmatch(text):
        raise HTTPException(
            400, f"Invalid platform key {platform!r}: letters, digits, dot, "
                 "dash and underscore only, up to 64 characters.")
    return text


def _store(platform: str, scope: str | None = None) -> FileBaselineStore:
    return FileBaselineStore(_baselines_root(scope) / _platform_segment(platform))


def _branch_store(store, directory, *, platform: str, scope: str | None,
                  project_key: str | None, branch: str):
    """Наложение ветки поверх набора — общая точка с `stores.branch_layer`.

    Отдельная обёртка нужна ровно затем, чтобы прогон и апрув после него
    вычисляли наложение ОДНИМ кодом. Две реализации одного правила — это то,
    с чего начиналась история про «апрув уходит не в тот каталог».
    """
    from .stores import branch_layer

    try:
        from .main import db
    except Exception:
        return store, directory, ""
    kind, _ = parse_scope(scope)
    return branch_layer(store, directory, db=db, cfg=_cfg_fresh(),
                        platform=platform, branch=branch,
                        scope="vistest" if kind != GLOBAL_SCOPE else "global",
                        project_key=project_key)


def _scope_label(scope: str | None) -> str:
    kind, key = parse_scope(scope)
    return "the service's own set" if kind == GLOBAL_SCOPE \
        else f"the VisTest set of project «{key}»"


def _meta(platform: str, name: str, scope: str | None = None) -> dict:
    p = _store(platform, scope).dir_for(name) / "meta.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text("utf-8"))
    except Exception:
        return {}


def _write_meta(platform: str, name: str, patch: dict,
                scope: str | None = None) -> dict:
    """Патч спеки снимка. `None` в значении СТИРАЕТ поле.

    Раньше `None` просто игнорировался, и снять однажды заданный селектор или
    паузу было нечем: спека умела только расти. Пустая строка для этого не
    годится — «селектор равен пустой строке» и «селектора нет» это разные
    вещи, и первая ломает захват молча.
    """
    d = _store(platform, scope).dir_for(name)
    d.mkdir(parents=True, exist_ok=True)
    meta = _meta(platform, name, scope)
    for key, value in patch.items():
        if value is None:
            meta.pop(key, None)
        else:
            meta[key] = value
    (d / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False),
                                encoding="utf-8")
    return meta


def _platforms_of(root: Path) -> list[dict]:
    """Платформы одного набора — с числом эталонов в каждой."""
    if not root.exists():
        return []
    out = []
    for d in sorted(root.iterdir()):
        if d.is_dir():
            out.append({"platform": d.name,
                        "count": sum(1 for _ in d.rglob("baseline.png"))})
    return out


def _scope_inventory() -> list[dict]:
    """Все наборы эталонов этой установки — с платформами и числами.

    Один список на две задачи. Первая — переключатель наборов в интерфейсе:
    узнать, что у подключённого проекта вообще есть набор VisTest, больше
    неоткуда. Вторая — ответ на вопрос «а где они тогда лежат», который
    возникает каждый раз, когда экран или сборка теста отвечают «0». Пустой
    ответ полезен ровно настолько, насколько он называет непустое место.
    """
    cfg = _cfg_fresh()
    scopes = [{
        "scope": GLOBAL_SCOPE,
        "label": "VisTest — own set",
        "kind": "global",
        "platforms": _platforms_of(cfg.root_path / cfg.paths.baselines),
    }]

    try:
        from ..projects import ProjectRegistry

        for p in ProjectRegistry(cfg).list():
            found = _platforms_of(p.vistest_baselines_path(cfg))
            scopes.append({
                "scope": f"project:{p.key}",
                "label": f"{p.name or p.key} — VisTest set",
                "kind": "project",
                "project_key": p.key,
                "platforms": found,
                "empty_hint": (
                    "Not captured yet — «Snap VisTest baselines» in the «⋯» "
                    "menu of the project card.") if not found else "",
            })
    except Exception as e:                                  # pragma: no cover
        scopes.append({"scope": "", "error": f"{type(e).__name__}: {e}"})
    return scopes


@router.get("/api/baselines/scopes")
def list_scopes(request: Request):
    """Every set of baselines this installation has, with counts.

    The interface needs this to offer a store picker at all: before it existed
    there was no way to even learn that a connected project has a VisTest set.
    """
    _require(request, "viewer")
    return {"scopes": _scope_inventory(), "current_platform": platform_key()}


# --------------------------------------------------------------------------- #
#  Listing and preview
# --------------------------------------------------------------------------- #
@router.get("/api/baselines/detail")
def baselines_detail(request: Request,
                     platform: str | None = Query(default=None),
                     scope: str | None = Query(default=None)):
    _require(request, "viewer")
    root = _baselines_root(scope)
    if not root.exists():
        return {"platforms": [], "current": platform_key(),
                "scope": scope or GLOBAL_SCOPE,
                "scope_label": _scope_label(scope),
                "root": str(root), "flows": [], "auth_flow": ""}

    # Проект набора: снимок без записанного источника ищет свой тест в коде
    # ЭТОГО проекта, а не во всех подряд.
    scope_project = parse_scope(scope)[1] or ""
    out = []
    for pdir in sorted(root.iterdir()):
        if not pdir.is_dir() or (platform and pdir.name != platform):
            continue
        store = FileBaselineStore(pdir)
        items = []
        for name in store.list_names():
            meta = _meta(pdir.name, name, scope)
            img = store.dir_for(name) / "baseline.png"
            mask = store.dir_for(name) / "stability.png"
            proj, short = split_project(name)
            items.append({
                "name": name,
                "short": short,
                "project": meta.get("project") or proj,
                "platform": pdir.name,
                "url": meta.get("url"),
                "selector": meta.get("selector"),
                "viewport": meta.get("viewport"),
                "area": meta.get("area"),
                "wait": meta.get("wait"),
                "steps": meta.get("steps") or [],
                "steps_summary": _steps_summary(meta.get("steps")),
                "width": meta.get("width"),
                "height": meta.get("height"),
                "version": meta.get("version", 1),
                "updated_at": meta.get("updated_at"),
                "approved_by": meta.get("approved_by"),
                "ignore_boxes": meta.get("ignore_boxes", []),
                "has_mask": mask.exists(),
                "size_kb": round(img.stat().st_size / 1024) if img.exists() else 0,
                "thumb": f"/api/baselines/image?platform={quote(pdir.name)}"
                         f"&name={quote(name)}&w=320{_scope_q(scope)}",
                "full": f"/api/baselines/image?platform={quote(pdir.name)}"
                        f"&name={quote(name)}{_scope_q(scope)}",
                "source": _source_view(meta, name, scope_project),
                "runnable": bool(meta.get("url")) or _source.runnable_by_test(
                    _source_view(meta, name, scope_project)),
                "scope": scope or GLOBAL_SCOPE,
            })
        projects = sorted({i["project"] for i in items})
        out.append({"platform": pdir.name, "count": len(items),
                    "projects": projects, "items": items})

    cfg = _cfg_fresh()
    return {
        "platforms": out,
        "current": platform_key(),
        "scope": scope or GLOBAL_SCOPE,
        "scope_label": _scope_label(scope),
        "root": str(root),
        "flows": sorted(cfg.flows.keys()),
        "auth_flow": cfg.auth.flow,
    }


def _scope_q(scope: str | None) -> str:
    return f"&scope={quote(scope)}" if scope and scope != GLOBAL_SCOPE else ""


def _steps_summary(steps) -> str:
    if not steps:
        return ""
    try:
        from .. import scenario

        return scenario.summarize(steps)
    except Exception:
        return f"{len(steps)} step(s)"


# The snapshot name contains the project via a slash (`shop.example/login.png`),
# so it is passed as a parameter rather than a path segment: otherwise routing
# breaks it into parts and fails on every other baseline.
@router.get("/api/baselines/image")
def baseline_image(request: Request, platform: str, name: str,
                   w: int | None = None, scope: str | None = None):
    _require(request, "viewer")
    path = _store(platform, scope).dir_for(name) / "baseline.png"
    if not path.exists():
        raise HTTPException(404, "baseline not found")

    return _image_response(path.read_bytes(), w)


def _image_response(data: bytes, w: int | None) -> Response:
    if w and w > 0:
        try:
            from PIL import Image

            img = Image.open(io.BytesIO(data))
            if img.width > w:
                # Preview: heavy full-page PNGs cannot be served whole into the
                # grid — the tab would grind to a halt on the first dozen.
                ratio = w / img.width
                img = img.resize((w, max(1, int(img.height * ratio))), Image.LANCZOS)
                buf = io.BytesIO()
                img.convert("RGB").save(buf, format="WEBP", quality=80)
                return Response(buf.getvalue(), media_type="image/webp",
                                headers={"Cache-Control": "max-age=30",
                                         "X-Content-Type-Options": "nosniff"})
        except Exception:
            pass
    return Response(data, media_type="image/png",
                    headers={"Cache-Control": "max-age=30",
                             "X-Content-Type-Options": "nosniff"})


# --------------------------------------------------------------------------- #
#  Версии эталона и откат
#
#  Апрув — единственное необратимое действие в интерфейсе, и до сих пор оно
#  было необратимо буквально: `FileBaselineStore` аккуратно складывал
#  предыдущие версии в `history/v3.png` с самого начала, но ни одного роута,
#  чтобы их увидеть или вернуть, не существовало. Первое, что просят после
#  первого «ой, приняли не то», — это откат.
# --------------------------------------------------------------------------- #
@router.get("/api/baselines/versions")
def baseline_versions(request: Request, platform: str, name: str,
                      scope: str | None = None):
    _require(request, "viewer")
    store = _store(platform, scope)
    if not store.exists(name):
        raise HTTPException(404, "baseline not found")
    return {
        "name": name, "platform": platform, "scope": scope or GLOBAL_SCOPE,
        "versions": store.versions(name),
    }


@router.get("/api/baselines/version-image")
def baseline_version_image(request: Request, platform: str, name: str,
                           version: int, scope: str | None = None,
                           w: int | None = None):
    """Картинка конкретной версии — чтобы сравнить перед откатом."""
    _require(request, "viewer")
    path = _store(platform, scope).version_image(name, version)
    if path is None:
        raise HTTPException(404, f"version {version} not found")
    return _image_response(path.read_bytes(), w)


@router.post("/api/baselines/restore")
def restore_baseline(request: Request, body: dict = Body(...)):
    """Вернуть предыдущую версию эталона.

    body: {platform, name, version, scope?}

    Возврат записывается НОВОЙ версией, а не подменяет текущую на месте:
    история не должна переписываться задним числом. После отката на v3 текущей
    становится v7 с той же картинкой, и в журнале видно оба события — и
    ошибочный апрув, и его отмену.
    """
    scope = body.get("scope")
    user = _require(request, "reviewer", _rights_key(scope))
    platform = body.get("platform")
    name = body.get("name")
    version = body.get("version")
    if not platform or not name or version is None:
        raise HTTPException(400, "platform, name and version are required")
    try:
        version = int(version)
    except (TypeError, ValueError):
        raise HTTPException(400, "version must be a number") from None

    store = _store(platform, scope)
    if not store.exists(name):
        raise HTTPException(404, "baseline not found")

    try:
        new_version = store.restore(name, version, who=user["login"])
    except NotImplementedError as e:
        raise HTTPException(409, str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e

    kind, project_key = parse_scope(scope)
    try:
        from .auth import audit
        from .main import db
        from .stores import record_approval

        audit(db, user["login"], "baseline.restored", name,
              platform=platform, scope=scope or GLOBAL_SCOPE,
              restored_from=version, version=new_version)
        record_approval(
            db, scope="global" if kind == GLOBAL_SCOPE else "vistest",
            name=name, platform=platform, project_key=project_key,
            version=new_version, approved_by=user["login"],
            note=f"rolled back to v{version}")
    except Exception:
        pass

    return {"ok": True, "name": name, "restored_from": version,
            "version": new_version, "scope": scope or GLOBAL_SCOPE}


def _clean_thresholds(raw):
    """A snapshot's thresholds: validated and turned into numbers.

    A threshold written as a string, or as -5, will not bring a comparison
    down — the engine ignores it silently — but that is the worst of the
    possible outcomes: a person set a value, the interface shows it, and the
    verdicts are still computed by the old one. The error has to be named here,
    at the moment of saving. The rules are `core.thresholds`; what this adds is
    the HTTP shape of the refusal.
    """
    from ..core.thresholds import ThresholdError, clean

    if raw in (None, {}):
        # An empty dict means «drop the override», and `_write_meta` reads
        # `None` as deleting the field.
        return None
    try:
        out = clean(raw)
    except ThresholdError as e:
        raise HTTPException(400, str(e)) from None
    return out or None


@router.post("/api/baselines/meta")
def update_baseline_meta(request: Request, body: dict = Body(...)):
    """Bind an address, window size, selector, pause and steps to a baseline.

    body: {platform, name, scope?, url?, selector?, viewport?, wait?, steps?, note?}

    Binding an address is what makes a snapshot runnable — `/api/suite/run` and
    `resnap` both work off this spec. Without `scope` here, a snapshot in a
    project's VisTest set could never be given an address, and therefore could
    never be re-run.
    """
    platform = body.pop("platform", None)
    name = body.pop("name", None)
    scope = body.pop("scope", None)
    _require(request, "reviewer", _rights_key(scope))
    if not platform or not name:
        raise HTTPException(400, "platform and name are required")
    if not _store(platform, scope).exists(name):
        raise HTTPException(404, "baseline not found")
    allowed = {"url", "selector", "viewport", "wait", "note", "steps",
               "thresholds"}
    unknown = set(body) - allowed
    if unknown:
        raise HTTPException(400, f"unknown fields: {sorted(unknown)}")

    if "thresholds" in body:
        body["thresholds"] = _clean_thresholds(body["thresholds"])

    if "steps" in body:
        from .. import scenario

        try:
            body["steps"] = scenario.normalize(body["steps"])
        except Exception as e:
            raise HTTPException(400, f"steps: {e}") from e
    return _write_meta(platform, name, body, scope)


@router.post("/api/baselines/delete")
def delete_baselines(request: Request, body: dict = Body(...)):
    """Batch deletion.

    body: {platform, names: [...]} — specific snapshots
          {platform, all: true}    — the whole platform at once
          {..., purge_history: true} — also wipe the run history in the DB

    Deleting a whole platform requires an explicit `all`, not an empty name list:
    an empty list meaning «delete everything» is the classic way to lose data to
    a typo on the client.
    """
    scope = body.get("scope")
    _require(request, "admin", _rights_key(scope))
    platform = body.get("platform")
    if not platform:
        raise HTTPException(400, "platform is required")

    store = _store(platform, scope)
    if body.get("all"):
        names = store.list_names()
    else:
        names = body.get("names") or []
        if not names:
            raise HTTPException(400, "specify names or all: true")

    deleted, freed, missing = [], 0, []
    for name in names:
        d = store.dir_for(name)
        if not d.exists():
            missing.append(name)
            continue
        freed += _rmtree_safe(d)
        deleted.append(name)

    purged = 0
    if body.get("purge_history") and deleted:
        _, project_key = parse_scope(scope)
        purged = _purge_history(platform, deleted, project_key)

    # The platform directory may have been left empty — clean it up so it does
    # not clutter the view.
    root = _baselines_root(scope) / _platform_segment(platform)
    if root.exists() and not any(root.iterdir()):
        _rmtree_safe(root)

    return {"ok": True, "deleted": deleted, "missing": missing,
            "freed_kb": round(freed / 1024), "purged_comparisons": purged}


@router.delete("/api/baselines/ignore-boxes")
def clear_boxes(request: Request, platform: str, name: str,
                scope: str | None = None):
    _require(request, "reviewer", _rights_key(scope))
    _write_meta(platform, name, {"ignore_boxes": []}, scope)
    return {"ok": True}


@router.put("/api/baselines/ignore-boxes")
def set_boxes(request: Request, body: dict = Body(...)):
    """Задать ignore-зоны снимка целиком.

    body: {platform, name, scope?, boxes: [{x, y, w, h, reason?}]}

    Зона привязана к СНИМКУ, а не к сравнению. Добавить её раньше можно было
    только из разбора конкретного падения — то есть сначала дождись, пока
    снимок упадёт, и только потом получишь право сказать «вот эта область
    всегда разная». Здесь тот же список правится напрямую, вместе со всей
    остальной спекой.

    Список передаётся целиком, а не по одной зоне: интерфейс рисует их мышью,
    и «удалить вторую из четырёх» через добавление не выражается.

    Зона может держаться за ЭЛЕМЕНТ (`selector` + `match`), а не за
    прямоугольник, — и тогда она переезжает вместе с ним. Координаты при этом
    обязательны всё равно: элемент может исчезнуть, и тогда прямоугольник —
    единственное, что от зоны останется. Разбор формата и опознание — в
    `vistest/zones.py`.
    """
    platform = body.get("platform")
    name = body.get("name")
    scope = body.get("scope")
    _require(request, "reviewer", _rights_key(scope))
    if not platform or not name:
        raise HTTPException(400, "platform and name are required")
    if not _store(platform, scope).exists(name):
        raise HTTPException(404, "baseline not found")

    from ..core.regions import normalize_all

    try:
        boxes = normalize_all(body.get("boxes"))
    except ValueError as e:
        # Разбор формата живёт в одном месте, а не копией на каждый роут: копия
        # разошлась бы с движком, и зона, принятая интерфейсом, оказалась бы
        # для сравнения невидимой.
        raise HTTPException(400, str(e)) from None

    _write_meta(platform, name, {"ignore_boxes": boxes}, scope)

    from .auth import audit, current_user
    from .main import db

    who = current_user(db, request.cookies.get("vistest_session"))["login"]
    from ..core.regions import held_by

    by_element = sum(1 for z in boxes if held_by(z) == "element")
    audit(db, who, "baseline.ignore_boxes", name,
          platform=platform, scope=scope or GLOBAL_SCOPE, count=len(boxes),
          by_element=by_element)
    return {"ok": True, "boxes": boxes, "by_element": by_element}


@router.get("/api/baselines/card")
def baseline_card(request: Request, platform: str, name: str,
                  scope: str | None = None):
    """Всё об одном снимке в одном месте.

    До сих пор снимок был строкой-ключом, размазанной по трём экранам: спека —
    в карточке на вкладке «Baselines», история — только если повезёт наткнуться
    на конкретное сравнение, версии эталона — в модалке из меню «⋯», а
    ignore-зоны видны лишь числом на чипе. Ответить на вопрос «что это за
    снимок и что с ним происходило» было негде.

    Здесь всё это собрано: спека, текущий эталон с версией и автором, зоны,
    и история проверок по всем прогонам. История ищется по имени и платформе —
    ключ, по которому снимок и опознаётся во всей системе.
    """
    _require(request, "viewer")
    store = _store(platform, scope)
    if not store.exists(name):
        raise HTTPException(404, "baseline not found")

    meta = _meta(platform, name, scope)
    directory = store.dir_for(name)
    image = directory / "baseline.png"
    proj, short = split_project(name)
    q = f"platform={quote(platform)}&name={quote(name)}{_scope_q(scope)}"

    width = height = None
    if image.exists():
        try:
            from PIL import Image

            with Image.open(image) as im:
                width, height = im.size
        except Exception:
            pass

    return {
        "name": name, "short": short, "platform": platform,
        "project": meta.get("project") or proj,
        "scope": scope or GLOBAL_SCOPE,
        "scope_label": _scope_label(scope),
        "directory": str(directory),
        "thresholds": _threshold_view(meta, proj),
        "spec": {
            "url": meta.get("url"),
            "selector": meta.get("selector"),
            "viewport": meta.get("viewport"),
            "wait": meta.get("wait"),
            "steps": meta.get("steps") or [],
            "steps_summary": _steps_summary(meta.get("steps")),
            "note": meta.get("note"),
        },
        "baseline": {
            "version": meta.get("version", 1),
            "approved_by": meta.get("approved_by"),
            "updated_at": meta.get("updated_at"),
            "git_sha": meta.get("git_sha"),
            "width": width, "height": height,
            "size_kb": round(image.stat().st_size / 1024) if image.exists() else 0,
            "full": f"/api/baselines/image?{q}",
            "thumb": f"/api/baselines/image?{q}&w=480",
            "has_mask": (directory / "stability.png").exists(),
        },
        "ignore_boxes": _zone_view(meta.get("ignore_boxes", []), directory),
        # Чем снят снимок и чем его можно повторить. `runnable` раньше означал
        # «есть адрес» — и ровно поэтому кнопки появлялись у страниц за входом,
        # где адрес сам по себе даёт форму логина.
        "source": _source_view(meta, name, parse_scope(scope)[1] or ""),
        "runnable": bool(meta.get("url")) or _source.runnable_by_test(
            _source_view(meta, name, parse_scope(scope)[1] or "")),
        "flows": sorted(_cfg_fresh().flows.keys()),
        "history": _snapshot_history(name, platform),
    }


def _zone_view(zones: list, directory) -> list[dict]:
    """Зоны снимка + чем каждая держится и находит ли ещё свою цель.

    Считается на сервере по тем же правилам, что и при сравнении. Разбирать
    лесенку опознания на клиенте значило бы завести второе место, где живёт
    ответ «маска работает или уже нет», — и разойтись с первым молча, а именно
    молчания здесь и надо избежать.
    """
    from ..capture import dom as domcap
    from ..core.regions import find_nodes, held_by

    snapshot = domcap.load(Path(directory) / "dom.json") or {}
    out = []
    for zone in (zones or []):
        if not isinstance(zone, dict):
            continue
        row = dict(zone)
        row["held_by"] = held_by(zone)
        if row["held_by"] == "element":
            hits = find_nodes(snapshot, zone) if snapshot.get("nodes") else []
            # Снепшота нет вовсе — это не «цель потеряна». Про такую зону мы
            # просто ничего не знаем: старый эталон снят без DOM, и врать про
            # него «маска сломана» значило бы отправить человека чинить
            # работающее.
            row["matches"] = len(hits) if snapshot.get("nodes") else None
            row["lost"] = bool(snapshot.get("nodes")) and not hits
        else:
            row["matches"] = None
            row["lost"] = False
        out.append(row)
    return out


@router.post("/api/baselines/element-at")
def element_at(request: Request, body: dict = Body(...)):
    """Какой элемент человек обвёл мышью — и чем за него можно зацепиться.

    body: {platform, name, scope?, x, y, w, h}

    Без этого зона по селектору осталась бы функцией для тех, кто готов сам
    открыть DevTools и списать путь. Человек в этот момент делает ровно одно
    движение — обводит блок на картинке, — и ответ должен приехать оттуда же.

    Возвращается не один узел, а лесенка: сам элемент и его родители. Обвести
    ровно нужный уровень мышью почти невозможно — промахнулся на два пикселя и
    попал в `<span>` внутри кнопки, — а выбрать из списка «span → button →
    form» человек может осмысленно.
    """
    platform = body.get("platform")
    name = body.get("name")
    scope = body.get("scope")
    _require(request, "viewer", _rights_key(scope))
    if not platform or not name:
        raise HTTPException(400, "platform and name are required")

    store = _store(platform, scope)
    if not store.exists(name):
        raise HTTPException(404, "baseline not found")

    from ..capture import dom as domcap

    snapshot = domcap.load(store.dir_for(name) / "dom.json") or {}
    nodes = snapshot.get("nodes") or []
    if not nodes:
        # Честный ответ вместо пустого списка: снимок снят без DOM, и это
        # состояние, а не сбой. Интерфейсу надо сказать «зацепиться не за что,
        # остаются координаты», а не показать пустое меню.
        return {"nodes": [], "reason": "no DOM snapshot was captured with this baseline"}

    try:
        box = {k: int(body.get(k) or 0) for k in ("x", "y", "w", "h")}
    except (TypeError, ValueError):
        raise HTTPException(400, "x, y, w and h must be integers") from None

    ranked = _rank_nodes(nodes, box)
    return {"nodes": ranked[:8], "total": len(nodes)}


def _rank_nodes(nodes: list[dict], box: dict) -> list[dict]:
    """Узлы по тому, насколько они похожи на обведённое.

    Мера — пересечение к объединению (IoU) прямоугольников, та же, что у
    attribution: она одинаково наказывает и за промах мимо, и за «обвёл кнопку,
    а предложили body». Ноль площади у выделения (одиночный клик) означает
    «самый маленький узел под точкой» — и это отдельная ветка, потому что IoU
    там вырождается в ноль для всех.
    """
    x0, y0 = box["x"], box["y"]
    x1, y1 = x0 + max(box["w"], 0), y0 + max(box["h"], 0)
    area = max(0, x1 - x0) * max(0, y1 - y0)

    scored = []
    for n in nodes:
        try:
            nx, ny = int(n["x"]), int(n["y"])
            nw, nh = int(n["w"]), int(n["h"])
        except (KeyError, TypeError, ValueError):
            continue
        if nw <= 0 or nh <= 0:
            continue
        ix0, iy0 = max(x0, nx), max(y0, ny)
        ix1, iy1 = min(x1, nx + nw), min(y1, ny + nh)
        inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
        if area:
            union = area + nw * nh - inter
            score = inter / union if union else 0.0
            if score <= 0:
                continue
        else:
            # Клик без протяжки: подходит всё, что накрывает точку, а лучший —
            # самый маленький из них.
            if not (nx <= x0 <= nx + nw and ny <= y0 <= ny + nh):
                continue
            score = 1.0 / (nw * nh)
        scored.append((score, n))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [{
        "selector": n.get("selector"),
        "testid": n.get("testid"),
        "id": n.get("id"),
        "tag": n.get("tag"),
        "cls": n.get("cls"),
        "text": n.get("text"),
        "x": int(n["x"]), "y": int(n["y"]), "w": int(n["w"]), "h": int(n["h"]),
        "score": round(score, 4),
        # Чем зона за него зацепится, если выбрать именно этот узел. Показывать
        # это надо до выбора: `data-testid` переживёт перестройку вёрстки, а
        # путь из шести тегов — нет, и человек имеет право знать разницу.
        "holds_by": ("data-testid" if n.get("testid") else
                     "id" if n.get("id") else
                     "css path"),
    } for score, n in scored]


def _threshold_view(meta: dict, project_key: str = "") -> dict:
    """A snapshot's thresholds in force and — always — where each came from.

    Without the source, the number on the screen does not answer the question
    a person is actually asking: «did I set this here, or is it like that
    everywhere». And the answer decides where to fix it — in the snapshot or
    in the set.
    """
    from ..core.thresholds import EDITABLE, from_meta, layer
    from .main import db
    from .thresholds import effective

    base = effective(db, _cfg_fresh(), project_key or None)
    own = (meta or {}).get("thresholds") or {}
    folded = layer(base["values"], snapshot=from_meta(meta))
    #  `layer` labels everything it did not touch as «config»; the sources
    #  below it were already resolved by `effective`, so they are kept.
    sources = dict(base["sources"])
    sources.update({name: src for name, src in folded["sources"].items()
                    if src == "snapshot"})
    return {"values": folded["values"], "sources": sources,
            "own": {k: v for k, v in own.items() if k in EDITABLE},
            "inherited": base["values"],
            "editable": base["editable"]}


def _source_view(meta: dict, name: str = "", project_key: str = "") -> dict:
    """Источник снимка для интерфейса — с подписью, а не голыми полями.

    Если записи нет, ответ ищется в коде: тест, который этот снимок объявляет,
    и есть его источник. Раньше здесь честно стояло «not recorded», и для
    целого набора, снятого чужими тестами до появления записи, это означало
    отсутствие любой связи — при том что связь написана в их же файлах прямым
    текстом.
    """
    src = dict((meta or {}).get("source") or {})
    if not src:
        src = declared_source(name, project_key)
    if not src:
        # Снимки, снятые до появления этой записи, о которых и код молчит.
        # Врать про них «captured by URL» нельзя: половина из них снята
        # тестами, и предложить человеку проверить их адресом значит
        # предложить снять форму входа.
        return {"kind": "unknown", "label": "not recorded",
                "test": "", "project_key": "", "runnable": False}
    src["label"] = _source.label(src)
    src["runnable"] = _source.runnable_by_test(src)
    src.setdefault("test", "")
    src.setdefault("project_key", "")
    return src


def _snapshot_history(name: str, platform: str, limit: int = 30) -> list[dict]:
    """Проверки этого снимка по всем прогонам.

    Снимок опознаётся по имени и платформе — тем же ключом, которым он живёт
    везде. Строк без сравнений не бывает: если снимок ни разу не прогонялся,
    список честно пуст, и это само по себе ответ («мёртвый груз»).
    """
    try:
        from .main import db

        return db.query(
            "SELECT c.id, c.verdict, c.review, c.reviewed_by, c.max_severity,"
            "       c.changed_area_pct, c.ssim, c.created_at,"
            "       r.id AS run_id, r.run_key, r.branch, r.git_sha,"
            "       p.name AS project"
            "  FROM comparison c"
            "  JOIN snapshot s ON s.id = c.snapshot_id"
            "  JOIN run r ON r.id = c.run_id"
            "  JOIN project p ON p.id = r.project_id"
            " WHERE s.name = ? AND s.platform = ?"
            " ORDER BY c.created_at DESC LIMIT ?", (name, platform, limit))
    except Exception:
        return []


def _purge_history(platform: str, names: list[str],
                   project_key: str | None = None) -> int:
    """Remove comparisons for deleted snapshots from the DB.

    Otherwise the dashboard keeps counting metrics for snapshots that no longer
    exist, and «dead» rows stay forever at the top of the unstable list.

    Scoped by project when we know it. `name + platform` alone is not an
    identity: two connected suites can each have a `login.png` on the same
    platform, and deleting one project's baseline used to wipe the other
    project's history along with it — silently, since deletion is exactly the
    place nobody looks for collateral damage.
    """
    try:
        from .main import db

        total = 0
        for name in names:
            if project_key:
                rows = db.query(
                    "SELECT s.id FROM snapshot s JOIN project p ON p.id=s.project_id"
                    " WHERE s.name=? AND s.platform=? AND p.name=?",
                    (name, platform, project_key))
            else:
                rows = db.query(
                    "SELECT id FROM snapshot WHERE name=? AND platform=?",
                    (name, platform))
            for r in rows:
                total += db.execute("DELETE FROM snapshot WHERE id=?", (r["id"],))
        return total
    except Exception:
        return 0


def _rmtree_safe(path: Path) -> int:
    """Deletion strictly inside the working directory."""
    try:
        p = Path(path).resolve()
        root = _cfg_fresh().root_path.resolve()
        if not p.is_relative_to(root) or p == root or not p.exists():
            return 0
        size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
        shutil.rmtree(p, ignore_errors=True)
        return size
    except Exception:
        return 0




# --------------------------------------------------------------------------- #
#  Capturing baselines by URL
# --------------------------------------------------------------------------- #
# Что именно попадает в кадр. Три ответа, и ни один не выводится из остальных:
# страница целиком, только окно, один элемент.
AREAS = ("full", "viewport", "element")


def normalize_area(target: dict) -> dict:
    """`area` → то, что понимает драйвер съёмки.

    Раньше «что попадает в кадр» решала одна настройка на всю установку
    (`capture.full_page`), и на снимок её было не поменять. Между тем это и
    есть формат снимка: страница целиком, экран и элемент — три разные вещи,
    и выбирают между ними на каждой странице по-своему. Длинный отчёт снимают
    целиком, шапку — элементом, а бесконечную ленту вообще только экраном,
    иначе Chromium отдаёт мусор.

    Правило перевода одно и жёсткое: `element` без селектора — это не съёмка
    элемента, а съёмка неизвестно чего, и молча превращать её в съёмку
    страницы нельзя.
    """
    area = str(target.get("area") or "").strip().lower()
    if not area:
        return target
    if area not in AREAS:
        raise HTTPException(400, f"area must be one of: {', '.join(AREAS)}")
    if area == "element" and not target.get("selector"):
        raise HTTPException(400, "area=element needs a selector")
    out = {**target, "area": area}
    if area != "element":
        out["full_page"] = area == "full"
        out.pop("selector", None)
    return out


def snapshot_meta(target: dict, previous_source: dict | None = None) -> dict:
    """Паспорт снимка: всё, чем он воспроизводится.

    Смысл паспорта в том, что снимок можно снять заново одной кнопкой. Значит,
    в нём обязано лежать ВСЁ, что влияет на кадр: адрес, размер окна, шаги,
    пауза, селектор — и область. Область попала сюда последней и по той же
    причине: пока она была одной настройкой на всю установку, пересъёмка
    возвращала другой кадр того же адреса, а разница читалась как изменение
    страницы.

    `source` не затирается: снимок, снятый когда-то тестом, при пересъёмке по
    адресу терял связь с тестом — и «перепроверить» для него снова начинало
    означать «снять форму входа». Сохранённая связь важнее свежей записи о
    способе. Источник при этом указывается явно, а не угадывается: сервис
    живёт в своём процессе, и если он сам запущен под pytest, автоопределение
    приписало бы снимку совершенно посторонний тест — убедительно и неверно.
    """
    return {
        "url": target["url"],
        "selector": target.get("selector"),
        "viewport": target.get("viewport"),
        "wait": target.get("wait"),
        "steps": target.get("steps") or [],
        "area": target.get("area"),
        "full_page": target.get("full_page"),
        "source": previous_source or _source.detect(
            "recorded" if target.get("steps") else "url"),
    }


def capture_config_for(capture, target: dict):
    """Настройки съёмки для ОДНОГО снимка.

    Настройка установки — умолчание: она отвечает на «как обычно», а не на
    «как этот». Снимок, у которого область задана, диктует её сам; снимок без
    неё ведёт себя ровно как раньше — иначе появление поля молча переснимало
    бы весь накопленный набор по-другому.

    При съёмке элемента `full_page` не значит ничего: в кадре узел, а не
    страница. Трогать настройку в этом случае — заводить состояние, которое ни
    на что не влияет и потому обязательно кого-нибудь однажды запутает.
    """
    if target.get("full_page") is None or target.get("selector"):
        return capture
    return dataclasses.replace(capture, full_page=bool(target["full_page"]))


def _variants_from(body: dict, *, browser: str | None = None):
    """Варианты для этого запроса: тело сильнее конфига, конфиг сильнее умолчания.

    `browser` отдельным полем остаётся ради совместимости: интерфейс и клиенты
    посылали его с самого начала, и ломать это ради красоты нельзя. Он значит
    «матрица из одного браузера» и проигрывает явному `matrix.browsers`.
    """
    from ..matrix import MatrixError, from_config

    raw = body.get("matrix")
    if raw is True:
        # «Матрица как в конфиге» — самая частая просьба, и писать ради неё
        # копию списков в теле запроса значит завести второе место, где они
        # живут, и разойтись с ним при первой же правке `vistest.yaml`.
        try:
            return from_config(_cfg_fresh())
        except MatrixError as e:
            raise HTTPException(400, str(e)) from None
    raw = raw or {}
    if not isinstance(raw, dict):
        raise HTTPException(400, "matrix must be true or an object "
                                 "{browsers: [...], viewports: [...]}")
    browsers = raw.get("browsers")
    if browsers is None and browser:
        browsers = [browser]
    try:
        return from_config(_cfg_fresh(), browsers=browsers,
                           viewports=raw.get("viewports"),
                           base_viewport=raw.get("base_viewport"))
    except MatrixError as e:
        # Ошибка описания матрицы — это 400, а не 500: человек опечатался в
        # размере окна, а не сломал сервис.
        raise HTTPException(400, str(e)) from None


@router.get("/api/matrix")
def matrix_preview(request: Request):
    """Во что раскрывается матрица этой инсталляции — до того, как её запустят.

    Список из шести строк в конфиге превращается в восемнадцать прогонов и
    восемнадцать наборов эталонов, и увидеть это заранее дешевле, чем узнать
    из счёта за время. Заодно отвечает на «а где лежат эталоны варианта»:
    ключ хранения виден прямо здесь.
    """
    _require(request, "viewer")
    from ..matrix import BROWSERS, INSTALLED, from_config, installed_browsers

    cfg = _cfg_fresh()
    variants = from_config(cfg)
    # Что из этого здесь ЕСТЬ, а не только поддерживается.
    #
    # `known_browsers` — список того, что умеет Playwright. Интерфейс показывал
    # его как список того, во что можно прогнать, и человек выбирал движок,
    # которого в образе нет. Дальше всё выглядело как поломка VisTest: пустой
    # набор эталонов, отказ «набор ещё не снят», круг.
    #
    # Отвечаем обоими списками сразу. Прятать неустановленные было бы хуже:
    # «почему у меня только chromium» — вопрос, на который экран обязан
    # отвечать сам, а не молчанием.
    state = installed_browsers()
    return {
        # Браузеры, которые вообще можно назвать. Список приезжает с сервера, а
        # не зашит в интерфейс: движки поддерживает Playwright, и знать про них
        # обязан тот, кто его запускает.
        "known_browsers": list(BROWSERS),
        "browser_state": state,
        "installed_browsers": [b for b in BROWSERS if state.get(b) == INSTALLED],
        "declared": bool(cfg.matrix.browsers or cfg.matrix.viewports),
        "browsers": list(cfg.matrix.browsers or ()),
        "viewports": list(cfg.matrix.viewports or ()),
        "base_viewport": next((v.viewport for v in variants if v.base), None),
        "variants": [v.as_dict() for v in variants],
    }


@router.post("/api/baselines/snap")
def snap(request: Request, body: dict = Body(...)):
    """Capture baseline(s) by address. Returns the id of a background job.

    body: {targets: [{url, name, viewport, selector, wait, area}], update: bool,
           browser: "chromium", scope?: "global" | "project:<key>"}

    `area` is «full» (the whole page), «viewport» (the window only) or
    «element» (one node, needs `selector`). Left out, the installation default
    applies — the way it always did.
    """
    user = _require(request, "reviewer", _rights_key(body.get("scope")))
    from .. import scenario

    targets = body.get("targets") or []
    if not targets:
        raise HTTPException(400, "at least one target with a url is required")
    targets = [normalize_area(t) for t in targets]
    for t in targets:
        if not t.get("url"):
            raise HTTPException(400, "every target must have a url")
        if t.get("steps"):
            try:
                t["steps"] = scenario.normalize(t["steps"])
            except Exception as e:
                raise HTTPException(400, f"steps: {e}") from e

    browser = body.get("browser", "chromium")
    update = bool(body.get("update"))
    scope = body.get("scope")
    parse_scope(scope)                      # validate before starting a job
    variants = _variants_from(body, browser=browser)
    title = (targets[0].get("name") or targets[0]["url"]) if len(targets) == 1 \
        else f"{len(targets)} pages"
    if len(variants) > 1:
        title += f" · {len(variants)} variants"

    job = runner.submit("snap", f"Baseline capture: {title}",
                        lambda j: _run_snap(j, targets, variants, update, scope),
                        owner=user["login"], lock_key=f"snap:{scope or 'global'}")
    return {"job_id": job.id, "scope": scope or GLOBAL_SCOPE,
            "variants": [v.as_dict() for v in variants]}


@router.post("/api/baselines/resnap")
def resnap(request: Request, body: dict = Body(...)):
    """Recapture from the baseline spec — «the page changed on purpose».

    body: {platform, name | names: [...], scope?, browser?}

    `names` is accepted alongside `name` on purpose: the interface has always
    sent a list here, the route has always required a single value, and the
    «Re-capture» button therefore answered 400 every single time. Both spellings
    work now, and there is a test for it.
    """
    scope = body.get("scope")
    user = _require(request, "reviewer", _rights_key(scope))
    platform = body.get("platform")
    browser = body.get("browser", "chromium")

    names = body.get("names")
    if isinstance(names, str):
        names = [names]
    if not names:
        one = body.get("name")
        names = [one] if one else []
    names = [n for n in names if n]

    if not platform or not names:
        raise HTTPException(400, "platform and name (or names) are required")

    targets, without_url = [], []
    for name in names:
        meta = _meta(platform, name, scope)
        if not meta.get("url"):
            without_url.append(name)
            continue
        targets.append({"url": meta["url"], "name": name,
                        "viewport": meta.get("viewport"),
                        "area": meta.get("area"),
                        "full_page": meta.get("full_page"),
                        "selector": meta.get("selector"),
                        "wait": meta.get("wait"),
                        "steps": meta.get("steps") or []})

    if not targets:
        raise HTTPException(
            400, f"no address bound to {', '.join(without_url) or 'the baseline'}"
                 " — bind one first, then re-capture")

    title = names[0] if len(targets) == 1 else f"{len(targets)} baselines"
    # Пересъёмка идёт ровно в тот вариант, из которого её попросили: `platform`
    # уже назвал и браузер, и размер окна. Раскрывать здесь матрицу значило бы
    # по кнопке «переснять» у одного снимка переписать эталоны ещё пяти
    # вариантов, о которых человек не просил.
    variants = _variants_of_platform(platform, browser)
    job = runner.submit("snap", f"Recapture: {title}",
                        lambda j: _run_snap(j, targets, variants, True, scope),
                        owner=user["login"], lock_key=f"snap:{scope or 'global'}")
    return {"job_id": job.id, "count": len(targets), "skipped": without_url,
            "scope": scope or GLOBAL_SCOPE}


def _variants_of_platform(platform: str, browser: str = "chromium"):
    """Один вариант, восстановленный из ключа платформы.

    Нужен там, где человек уже выбрал конкретный набор («переснять ЭТОТ
    снимок», «прогнать ЭТУ платформу»): ключ содержит и браузер, и размер, и
    подменять их матрицей нельзя.
    """
    from ..matrix import Variant, variant_of_platform

    info = variant_of_platform(platform)
    return [Variant(browser=info.get("browser") or browser,
                    viewport=info.get("viewport"),
                    scale=info.get("scale") or 1.0,
                    # Ключ уже готов и правдивее вычисленного: эталоны могли
                    # быть сняты в docker, а сервис работает на Windows.
                    key=platform)]


def _target_for(t: dict, variant) -> dict:
    """Цель под конкретный вариант матрицы.

    Размер окна варианта ПЕРЕКРЫВАЕТ размер из паспорта снимка, и это осознанно.
    Матрица — заявление про весь набор («снимаем на 1440, 768 и 390»), а
    паспорт — про один снимок. Если бы паспорт побеждал, половина набора
    молча снималась бы не в тех размерах, а человек считал бы, что проверил
    мобильную вёрстку. Расхождения при этом называются вслух — см.
    `_viewport_conflicts`.
    """
    if not variant.viewport:
        return t
    out = dict(t)
    out["viewport"] = variant.viewport
    return out


def _viewport_conflicts(targets: list[dict], variants) -> list[str]:
    """Снимки, у которых в паспорте свой размер, не совпадающий с базовым.

    Это единственное место, где матрица меняет смысл уже снятого эталона:
    базовый вариант хранится под прежним ключом, но снимается теперь в размере
    матрицы. Промолчать нельзя — человек получит пачку падений и решит, что
    сломался движок. Назвать вслух достаточно: набор всё равно надо привести к
    одному размеру, и это его решение, а не наше.
    """
    from ..matrix import normalize_viewport

    base = next((v.viewport for v in variants if v.base and v.viewport), None)
    if not base:
        return []
    out = []
    for t in targets:
        own = t.get("viewport")
        if not own:
            continue
        try:
            if normalize_viewport(own) != base:
                out.append(f"{t.get('name') or t.get('url')} ({normalize_viewport(own)})")
        except Exception:
            continue
    return out


def _run_snap(job: Job, targets: list[dict], variants, update: bool,
              scope: str | None = None) -> dict:
    """Обёртка ради одного: отсутствующий движок — это отказ, а не поломка.

    `BrowserMissing` уже несёт в себе готовый ответ с командой установки.
    Пропустить его наверх как есть значило бы показать питоновский трейс —
    двадцать строк, из которых человек должен сам выудить, что не хватает
    браузера, и догадаться, какого именно.
    """
    from ..matrix import BrowserMissing

    try:
        return _snap_all(job, targets, variants, update, scope)
    except BrowserMissing as e:
        job.say(str(e), "error")
        raise JobFailure(str(e)) from None


def _snap_all(job: Job, targets: list[dict], variants, update: bool,
              scope: str | None = None) -> dict:
    from playwright.sync_api import sync_playwright

    from ..matrix import is_matrix, launch
    from ..service import CheckService

    cfg = _cfg_fresh()
    created, skipped, failed = [], [], []
    total = len(targets) * max(len(variants), 1)
    done = 0

    if is_matrix(variants):
        job.say(f"matrix: {len(variants)} "
                f"{'variant' if len(variants) == 1 else 'variants'} × "
                f"{len(targets)} pages · {_scope_label(scope)}")
        for v in variants:
            job.say(f"  · {v.label} → {v.platform}")
    else:
        job.say(f"platform {variants[0].platform}, pages: {len(targets)} · "
                f"{_scope_label(scope)}")

    for conflict in _viewport_conflicts(targets, variants)[:5]:
        job.say(f"у снимка задан свой размер окна, матрица его перекроет: {conflict}",
                "warn")

    with sync_playwright() as p:
        for v in variants:
            if job.cancelled:
                break
            svc = CheckService(cfg, platform=v.platform, browser=v.browser,
                               run_dir=cfg.runs_path() / "ui-snap" / v.slug,
                               store=_store(v.platform, scope))
            shots = [_target_for(t, v) for t in targets]
            br = launch(p, v.browser, headless=True)
            state = _auth_state(br, cfg, shots, job)
            try:
                for t in shots:
                    if job.cancelled:
                        break
                    done += 1
                    job.progress = done / max(total, 1)
                    name = _norm_name(t.get("name") or t["url"])
                    tag = f"{name} [{v.label}]" if is_matrix(variants) else name

                    if svc.store.exists(name) and not update:
                        job.say(f"{tag}: baseline already exists, skipping", "warn")
                        skipped.append(name)
                        continue

                    job.say(f"{tag}: opening {t['url']}")
                    try:
                        rgb, dom, unstable = _shoot(
                            br, cfg, _strip_auth_flow(t, cfg, bool(state)), job, state)
                    except Exception as e:
                        job.say(f"{tag}: {type(e).__name__}: {e}", "error")
                        failed.append({"name": name, "variant": v.label,
                                       "error": str(e)})
                        continue

                    # Источник указывается явно, а не угадывается: сервис живёт
                    # в своём процессе, и если он сам запущен под pytest (а в
                    # нашем прогоне так и есть), автоопределение приписало бы
                    # снимку совершенно посторонний тест — и выглядело бы это
                    # убедительно.
                    #
                    # `source` не затирает уже записанный: снимок, снятый
                    # когда-то тестом, при пересъёмке по адресу теряет связь с
                    # тестом, и «перепроверить» для него снова начинает
                    # означать «снять форму входа». Сохранённая связь важнее
                    # свежей записи о способе.
                    previous = (_meta(v.platform, name, scope).get("source")
                                if svc.store.exists(name) else None)
                    svc.check(name, rgb, unstable=unstable, dom=dom,
                              update_baseline=True, render=False,
                              meta=snapshot_meta(t, previous))
                    h, w = rgb.shape[:2]
                    job.say(f"{tag}: saved {w}×{h}", "ok")
                    created.append(name)
            finally:
                br.close()

    job.progress = 1.0
    job.say(f"done: created {len(created)}, skipped {len(skipped)}, "
            f"errors {len(failed)}", "ok" if not failed else "warn")
    return {"created": created, "skipped": skipped, "failed": failed,
            "platform": variants[0].platform,
            "variants": [v.as_dict() for v in variants],
            "scope": scope or GLOBAL_SCOPE}


def _shoot(browser, cfg, target: dict, job: Job, storage_state=None):
    from .. import scenario
    from ..capture.stabilize import context_options, install
    from ..integrations.driver import PlaywrightDriver

    w, h = _viewport(target.get("viewport"))
    ctx_kwargs = context_options(cfg, w, h)
    if storage_state:
        ctx_kwargs["storage_state"] = str(storage_state)

    context = browser.new_context(**ctx_kwargs)
    install(context, determinism=cfg.capture.determinism, cfg=cfg)
    page = context.new_page()
    page.set_default_timeout(cfg.capture.step_timeout_ms)
    try:
        page.goto(target["url"], wait_until="domcontentloaded",
                  timeout=cfg.capture.step_timeout_ms * 2)

        steps = target.get("steps") or []
        if steps:
            job.say(f"  running {len(steps)} step(s)")
            scenario.execute(page, steps, flows=cfg.flows,
                             timeout_ms=cfg.capture.step_timeout_ms,
                             log=lambda t: job.say(f"    {t}"))

        if target.get("wait"):
            page.wait_for_timeout(int(target["wait"]))

        shot = PlaywrightDriver(page).capture(
            cfg=capture_config_for(cfg.capture, target),
            clip_selector=target.get("selector"),
            progress=lambda t: job.say(f"  … {t}"),
        )
        for n in shot.notes:
            job.say(f"  {n}", "warn")
        return shot.rgb, shot.dom, shot.unstable
    finally:
        context.close()


def _auth_state(browser, cfg, targets: list[dict], job: Job):
    """Authenticate once, if at least one snapshot needs it."""
    from .. import scenario

    if not cfg.auth.reuse_state or not cfg.flows:
        return None
    flow = cfg.auth.flow
    if flow not in cfg.flows:
        return None

    needs = any(
        any(s.get("action") == "flow" and s.get("name") == flow
            for s in (t.get("steps") or []))
        for t in targets
    )
    if not needs:
        return None

    try:
        return scenario.establish_auth(browser, cfg, flow,
                                       log=lambda t: job.say(f"  {t}"))
    except Exception as e:
        job.say(f"authentication failed: {e}", "error")
        return None


def _strip_auth_flow(target: dict, cfg, has_state: bool) -> dict:
    """If the session is already up, re-login on every page is not needed."""
    if not has_state:
        return target
    steps = [s for s in (target.get("steps") or [])
             if not (s.get("action") == "flow" and s.get("name") == cfg.auth.flow)]
    return {**target, "steps": steps}


# --------------------------------------------------------------------------- #
#  Running checks
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
#  Перепроверить и переснять — через то, чем снимок сняли
#
#  Кнопки «Check» и «Re-capture» появлялись, как только у снимка был адрес, и
#  делали ровно одно: грузили этот адрес. Для страницы за входом это означало
#  снять форму логина — то есть прогон, который «ничего не нашёл», или эталон,
#  переписанный страницей входа. Проверить это глазами трудно: картинка есть,
#  вердикт есть, а что на картинке — видно только если открыть.
#
#  Теперь решает источник. Снимок, снятый тестом, перепроверяется ТЕМ ЖЕ
#  ТЕСТОМ: он знает и про логин, и про переходы, и про подготовку данных, а мы
#  про это не знаем ничего и повторять своими силами не должны.
# --------------------------------------------------------------------------- #
def _source_of(platform: str, name: str, scope: str | None) -> dict:
    return (_meta(platform, name, scope) or {}).get("source") or {}


def _project_for(key: str):
    from ..projects import ProjectRegistry

    if not key:
        return None
    try:
        return ProjectRegistry(_cfg_fresh()).get(key)
    except Exception:
        return None


def _run_one_test(job: Job, node: str, project_key: str, update: bool) -> dict:
    """Прогнать один тест — чужой через его проект, свой через наш pytest."""
    project = _project_for(project_key)
    if project_key and project is None:
        raise JobFailure(
            f"the snapshot was captured by project «{project_key}», and that "
            "project is no longer connected. Reconnect it, or bind a url to the "
            "snapshot and check it by address.")

    if project is not None:
        from ..external import publish, run_project

        job.say(f"project {project.key}: running {node}"
                + (" and accepting the result as the baseline" if update else ""))
        run = run_project(project, cfg=_cfg_fresh(), only=node,
                          update_baselines=update,
                          log=lambda t: job.say(t),
                          should_stop=lambda: job.cancelled)
        summary = run.summary()
        # Прогон одного теста тоже попадает в историю: иначе «перепроверил и
        # всё хорошо» не остаётся нигде, а именно этот ответ и был нужен.
        try:
            publish(run, project, cfg=_cfg_fresh(), log=lambda t: job.say(t))
        except Exception as e:
            job.say(f"could not write to the history: {e}", "warn")
        if summary["total"] == 0:
            raise JobFailure(
                f"«{node}» produced no snapshots. Either the test did not reach "
                "the comparison, or the name no longer exists — the output above "
                "says which.")
        return {"kind": "test", "test": node, "project": project.key, **summary}

    path = _pytest_path(node)
    args = ["--vistest-update"] if update else []
    job.say(f"pytest {path}" + (" --vistest-update" if update else ""))
    result = _run_pytest(job, path, args)
    if result.get("exit_code") not in (0, 1):
        raise JobFailure(
            f"pytest exited with code {result['exit_code']} — «{node}» did not "
            "run. The output above says why.")
    return {"kind": "test", "test": node, **result}


@router.post("/api/baselines/run")
def run_baseline(request: Request, body: dict = Body(...)):
    """Перепроверить снимок тем способом, которым он снят.

    body: {platform, name, scope?, update?: bool, browser?}

    Один роут на обе кнопки, потому что решение у них общее и ошибиться в нём
    можно одинаково: разница между «проверить» и «переснять» — один флаг, а
    разница между «через тест» и «по адресу» — вопрос, на который у снимка есть
    ответ.
    """
    scope = body.get("scope")
    user = _require(request, "reviewer", _rights_key(scope))
    platform = body.get("platform") or platform_key()
    name = body.get("name")
    update = bool(body.get("update"))
    if not name:
        raise HTTPException(400, "name is required")

    store = _store(platform, scope)
    if not store.exists(name):
        raise HTTPException(404, "baseline not found")

    meta = _meta(platform, name, scope)
    source = meta.get("source") or {}
    node = source.get("test") if source.get("kind") == "test" else ""

    if node:
        project_key = source.get("project_key") or ""
        title = ("Re-capturing " if update else "Checking ") + f"{name} · {node}"
        job = runner.submit(
            "check", title,
            lambda j: _run_one_test(j, node, project_key, update),
            owner=user["login"],
            # Ключ тот же, что у обычного прогона проекта: два прогона одного
            # набора пишут в одни эталоны, и неважно, один в нём тест или все.
            lock_key=f"project:{project_key}" if project_key else "pytest")
        return {"job_id": job.id, "via": "test", "test": node,
                "project": project_key or None}

    if not meta.get("url"):
        # Раньше на это место молча не приходили: кнопок у такого снимка просто
        # не было, и почему — не говорилось. «Ни адреса, ни теста» — это ответ,
        # а пустое место рядом с карточкой ответом не является.
        raise HTTPException(
            400,
            f"«{name}» remembers neither a test nor an address, so there is "
            "nothing to repeat. Run the test that captures it — the link "
            "appears by itself — or bind a url in the snapshot's spec.")

    if update:
        return resnap(request, {"platform": platform, "names": [name],
                                "scope": scope,
                                "browser": body.get("browser", "chromium")})
    return run_suite(request, {"platform": platform, "names": [name],
                               "scope": scope,
                               "browser": body.get("browser", "chromium")})


@router.post("/api/suite/run")
def run_suite(request: Request, body: dict = Body(default={})):
    """Run checks over baselines that have a url set.

    body: {platform?, names?: [...], browser?, scope?,
           matrix?: {browsers: [...], viewports: [...], base_viewport?}}

    This is «run the tests» without a single line of code: addresses are already
    bound to snapshots, the comparison engine is the same one, and the result
    goes into the run history and shows up on the «Runs» tab.

    With `scope=project:<key>` this becomes the answer to «I cannot launch a
    scenario for a snapshot VisTest captured for my project»: the snapshots of
    that set are now enumerable here, and a run over them lands in the history
    tagged with the store it compared against, so an approval afterwards goes
    back to the same place.

    Матрица. `matrix` (или матрица из `vistest.yaml`) прогоняет каждый снимок
    по всем вариантам «браузер × размер окна» в ОДНОМ прогоне. Список снимков
    при этом берётся из набора БАЗОВОГО варианта — там лежат уже снятые
    эталоны, и они же отвечают, что вообще входит в набор. У остальных
    вариантов на первом прогоне эталонов ещё нет, и они запишутся как новые;
    это ожидаемо и видно в прогоне как `new`, а не как падение.
    """
    user = _require(request, "reviewer")
    names = body.get("names")
    browser = body.get("browser", "chromium")
    scope = body.get("scope")
    _, project_key = parse_scope(scope)

    variants = _variants_from(body, browser=browser)
    # Явно названная платформа сильнее — но только если матрицу не просили.
    # Человек выбрал набор на экране и жмёт «Check all» именно по нему;
    # подменять этот выбор матрицей значило бы запустить восемнадцать прогонов
    # вместо одного. А вот если матрицу попросили явно, она и есть просьба.
    if body.get("platform") and not body.get("matrix"):
        variants = _variants_of_platform(body["platform"], browser)

    platform = variants[0].platform
    store = _store(platform, scope)
    targets = []
    for name in (names or store.list_names()):
        meta = _meta(platform, name, scope)
        if not meta.get("url"):
            continue
        targets.append({"name": name, "url": meta["url"],
                        "viewport": meta.get("viewport"),
                        "selector": meta.get("selector"),
                        "wait": meta.get("wait"),
                        "steps": meta.get("steps") or []})

    if not targets:
        raise HTTPException(
            400,
            f"no baselines with a bound address in {_scope_label(scope)}. "
            "Set a url on a snapshot — or capture a new baseline by address."
        )

    title = f"Checking {len(targets)} snapshots"
    if len(variants) > 1:
        title += f" × {len(variants)} variants"
    job = runner.submit("suite", title,
                        lambda j: _run_suite(j, targets, variants,
                                             scope, project_key),
                        owner=user["login"])
    return {"job_id": job.id, "count": len(targets),
            "variants": [v.as_dict() for v in variants],
            "checks": len(targets) * len(variants),
            "scope": scope or GLOBAL_SCOPE}


def _run_suite(job: Job, targets: list[dict], variants,
               scope: str | None = None, project_key: str | None = None) -> dict:
    """Прогон по всем вариантам матрицы — ОДИН прогон и один вердикт.

    Вариант — это пара «браузер × размер окна», и у каждого свой эталон: кадр
    firefox при 390 не с чем сравнивать в наборе chromium при 1440. А вот
    вопрос человеку один: «эта страница сломалась?» — поэтому все варианты
    попадают в один прогон, в одну историю и в одну очередь решений, где
    причина соберёт варианты одного сдвига в один вопрос.

    Артефакты каждого варианта лежат в своём подкаталоге прогона. Иначе
    второй вариант затирал бы картинки первого: каталог берётся по имени
    снимка, а имя у вариантов общее — и разбор показал бы кадр firefox под
    подписью chromium.
    """
    from playwright.sync_api import sync_playwright

    from ..matrix import is_matrix, launch
    from ..runner import git_info
    from ..service import CheckService

    # Пороги, выставленные в интерфейсе, обязаны действовать на прогон, который
    # интерфейс же и запустил. Иначе гистограмма на дашборде отвечает на вопрос
    # «куда ставить порог», ползунок его ставит, а вердикты остаются прежними —
    # и человек делает вывод, что настройка не работает вовсе.
    cfg = _cfg_with_thresholds(project_key)
    # Секунды не хватает на идентификатор прогона.
    #
    # `run_key` уникален, и приём прогона сделан на `INSERT OR REPLACE`:
    # перезапуск упавшей задачи в CI — нормальное дело, и второй раз тот же
    # прогон должен заменить первый, а не удвоиться. Обратная сторона: два
    # РАЗНЫХ прогона, начатых в одну и ту же секунду, тоже считаются одним, и
    # второй молча стирает первый вместе со всеми его сравнениями.
    #
    # Раньше в это было трудно попасть: прогон занимал секунды. Матрица делает
    # набор мельче и быстрее — «прогнать один снимок» стало обычным действием,
    # — и попадание из теоретического становится будничным. Четыре шестнадцатеричных
    # знака стоят ничего и закрывают вопрос.
    run_id = time.strftime("ui-%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    git = git_info()
    base_run_dir = cfg.runs_path() / run_id
    matrix = is_matrix(variants)

    results, comparisons, errored = [], [], []
    total = len(targets) * max(len(variants), 1)
    done = 0

    if matrix:
        job.say(f"run {run_id} · матрица из {len(variants)} вариантов × "
                f"{len(targets)} снимков · {_scope_label(scope)} · "
                f"fail at severity {cfg.diff.fail_severity:g}")
    for conflict in _viewport_conflicts(targets, variants)[:5]:
        job.say(f"у снимка задан свой размер окна, матрица его перекроет: {conflict}",
                "warn")

    with sync_playwright() as p:
        for v in variants:
            if job.cancelled:
                break
            # Ветка накладывается и на сам прогон, а не только на апрув после
            # него. Сравнить с main, а принять в ветку — это два разных эталона
            # в одном действии; человек увидел бы дифф с одной картинкой, а
            # заменил бы другую. Наложение считается на КАЖДЫЙ вариант: у них
            # разные наборы, и общее наложение увело бы половину не туда.
            store, _store_dir, branch = _branch_store(
                _store(v.platform, scope), _baselines_root(scope) / v.platform,
                platform=v.platform, scope=scope, project_key=project_key,
                branch=git.get("branch") or "")

            svc = CheckService(cfg, platform=v.platform, browser=v.browser,
                               run_dir=base_run_dir / v.slug, store=store)
            shots = [_target_for(t, v) for t in targets]

            job.say(f"{v.label} → {v.platform}"
                    + (f" · branch «{branch}»" if branch else ""))

            br = launch(p, v.browser, headless=True)
            state = _auth_state(br, cfg, shots, job)
            try:
                for t in shots:
                    if job.cancelled:
                        break
                    done += 1
                    job.progress = done / max(total, 1)
                    name = t["name"]
                    tag = f"{name} [{v.label}]" if matrix else name
                    job.say(f"{tag}: {t['url']}")
                    try:
                        rgb, dom, unstable = _shoot(
                            br, cfg, _strip_auth_flow(t, cfg, bool(state)), job, state)
                    except Exception as e:
                        job.say(f"{tag}: could not capture — {e}", "error")
                        row = {"name": name, "verdict": "error", "error": str(e)}
                        results.append({**row, "variant": v.label})
                        errored.append({**row, "platform": v.platform,
                                        "browser": v.browser,
                                        "viewport": v.viewport})
                        continue

                    try:
                        # Пересъёмка при падении: страница загружается заново
                        # тем же способом. Здесь это дороже, чем у теста
                        # (браузер уже увёл страницу дальше), но платится
                        # только за упавшее — и взамен прогон по адресам
                        # перестаёт краснеть от анимации.
                        # `br` и `state` привязываются значением: теперь это
                        # переменные ВНЕШНЕГО цикла по вариантам, и замыкание
                        # по имени взяло бы браузер следующего варианта — то
                        # есть пересняло бы кадр не тем браузером, молча и
                        # правдоподобно.
                        res = svc.check(
                            name, rgb, unstable=unstable, dom=dom,
                            recapture=lambda t=t, br=br, state=state: _shoot(
                                br, cfg, _strip_auth_flow(t, cfg, bool(state)),
                                job, state)[0])
                    except Exception as e:
                        # Движок упал на ОДНОМ снимке — раньше это роняло весь
                        # прогон, и остальные снимки не проверялись. Теперь
                        # снимок помечается ошибкой с текстом, а прогон идёт
                        # дальше и доезжает до истории.
                        job.say(f"{tag}: comparison engine crashed — {e}", "error")
                        row = {"name": name, "verdict": "error",
                               "error": f"{type(e).__name__}: {e}"}
                        results.append({**row, "variant": v.label})
                        errored.append({**row, "platform": v.platform,
                                        "browser": v.browser,
                                        "viewport": v.viewport})
                        continue

                    level = {"pass": "ok", "fail": "error"}.get(res.verdict.value, "warn")
                    detail = (f"severity {res.max_severity:.0f}, "
                              f"changed {res.changed_area_pct:.3f}%"
                              if res.verdict is Verdict.FAIL else "")
                    job.say(f"{tag}: {res.verdict.value} {detail}", level)
                    results.append({
                        "name": name,
                        "variant": v.label,
                        "platform": v.platform,
                        "verdict": res.verdict.value,
                        "max_severity": round(res.max_severity, 1),
                        "changed_area_pct": round(res.changed_area_pct, 4),
                        "regions": len(res.regions),
                        "artifacts": res.artifacts,
                    })
            finally:
                br.close()

            # Результаты варианта забираются сразу и помечаются его платформой.
            # Собирать их в конце общим обходом каталога значило бы гадать,
            # какому варианту принадлежит найденный `result.json`, — а от
            # этого зависит, с каким эталоном сравнят при следующем прогоне и
            # куда уедет «принять как эталон».
            comparisons.extend(_collect_comparisons(svc.run_dir, v))

    checked = [r for r in results if r["verdict"] != "error"]
    bad = [r for r in results if r["verdict"] == "error"]

    # Не сняли ни одного снимка, но ошибки были — это не «чисто», это провал.
    # Раньше такой прогон уходил в историю как «clean · 0 snapshots», а причина
    # оставалась только в логе задачи: человек видел зелёный ноль и не понимал,
    # почему «прогон не запускается».
    if not checked and bad:
        first = bad[0].get("error") or "unknown error"
        raise RuntimeError(
            f"could not capture a single baseline out of {len(bad)}. "
            f"Reason: {first}"
        )

    base = variants[0]
    payload = {
        "run_id": run_id,
        "platform": base.platform,
        "browser": base.browser,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "git": git,
        "totals": {
            "total": len(checked),
            "passed": sum(r["verdict"] == "pass" for r in checked),
            "failed": sum(r["verdict"] == "fail" for r in checked),
            "new": sum(r["verdict"] == "new_baseline" for r in checked),
        },
        "comparisons": comparisons,
        # Варианты записываются в сам прогон: список прогонов обязан отличать
        # «упало на одном браузере» от «упало везде», а по одному ключу
        # платформы этого не видно.
        "variants": [v.as_dict() for v in variants],
        # Recorded so an approval on this run finds the same store again.
        "project_key": project_key,
        "baseline_scope": "vistest" if project_key else "global",
        # Каталог БАЗОВОГО набора, а не наложения ветки: наложение
        # пересобирается из ветки прогона (`store_for_run`), и записывать сюда
        # его путь значило бы зафиксировать ветку дважды и в двух видах.
        "baseline_dir": str(_baselines_root(scope) / base.platform),
    }
    _ingest(payload, run_id, errored=errored, project_key=project_key)

    failed = [r["name"] for r in results if r["verdict"] == "fail"]
    job.progress = 1.0
    tail = f", not captured {len(bad)}" if bad else ""
    job.say(f"done: failed {len(failed)} of {len(checked)}{tail}",
            "error" if failed or bad else "ok")
    return {"run_id": run_id, "results": results,
            "variants": [v.as_dict() for v in variants],
            "failed": failed, "errored": [r["name"] for r in bad]}


def _collect_comparisons(run_dir, variant) -> list[dict]:
    """Результаты одного варианта — с его платформой в каждой строке.

    Платформа кладётся здесь, а не при записи в базу: только здесь ещё известно,
    какой вариант это снимал. Дальше по дороге остаётся один прогон и общий
    ключ на всех, и восстановить принадлежность будет неоткуда.
    """
    out = []
    for result_file in sorted(run_dir.glob("*/result.json")):
        try:
            comp = json.loads(result_file.read_text("utf-8"))
        except Exception:
            continue
        comp["platform"] = variant.platform
        comp["browser"] = variant.browser
        comp["viewport"] = variant.viewport
        comp["variant"] = variant.label
        out.append(comp)
    return out


def _ingest(payload: dict, run_id: str, errored: list | None = None,
            project_key: str | None = None) -> None:
    """Положить прогон в базу, чтобы он появился на вкладке «Runs».

    Сравнения приходят уже собранными и уже помеченными платформой варианта.
    Раньше они собирались здесь обходом каталога прогона — и это работало ровно
    до матрицы: варианты кладут `result.json` под одним и тем же именем снимка,
    и по найденному файлу нельзя сказать, чей он.

    errored — снимки, на которых упал захват или движок. Их тоже кладём в
    прогон (verdict=error с текстом причины), чтобы на странице прогона стояло
    не «чисто», а конкретная ошибка по каждому.
    """
    try:
        comparisons = list(payload.get("comparisons") or [])
        for e in (errored or []):
            comparisons.append({
                "name": e.get("name", "?"),
                "verdict": "error",
                "error": e.get("error") or "unknown error",
                "platform": e.get("platform") or payload.get("platform"),
                "browser": e.get("browser") or payload.get("browser"),
                "viewport": e.get("viewport"),
            })
        payload["comparisons"] = comparisons

        from .main import db

        # A run over a project's VisTest set belongs to that project in the
        # history too — otherwise it lands under `default` and disappears from
        # the project's own run list.
        db.ingest_run(payload, project_key or _cfg_fresh().service.project)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
#  Running the pytest suite
# --------------------------------------------------------------------------- #
def pick_platform(scope: str | None, platform: str | None = None) -> str:
    """Платформа, с которой имеет смысл работать в этом наборе.

    По умолчанию бралась `platform_key()` — платформа МАШИНЫ СЕРВИСА. Но
    эталоны снимает не она: у подключённого проекта они лежат под
    `docker-chromium-1x`, а сервис стоит на windows. Совпадения нет, и сборка
    честно отвечала «baselines on the platform: 0» при одиннадцати эталонах в
    соседнем каталоге. Пустая платформа по умолчанию — это ответ на вопрос,
    которого никто не задавал.

    Выбранная человеком платформа приоритетнее всего: если он смотрит на
    пустую, значит хочет посмотреть именно на неё.
    """
    if platform:
        return platform
    default = platform_key()
    found = [p for p in _platforms_of(_baselines_root(scope)) if p["count"]]
    if not found or any(p["platform"] == default for p in found):
        return default
    return max(found, key=lambda p: p["count"])["platform"]


def _baselines_elsewhere(scope: str | None, platform: str) -> list[str]:
    """Где в этой установке эталоны есть — кроме того места, куда мы смотрели.

    Нужно ровно для одного: пустой ответ должен называть непустое место.
    Иначе «нечего собирать» неотличимо от «ты смотришь не туда», а это две
    разные проблемы с двумя разными действиями.
    """
    here = scope or GLOBAL_SCOPE
    out: list[str] = []
    for sc in _scope_inventory():
        for p in sc.get("platforms") or []:
            if not p.get("count"):
                continue
            if sc.get("scope") == here and p.get("platform") == platform:
                continue
            out.append(f"{sc.get('label') or sc.get('scope')} · "
                       f"{p['platform']} · {p['count']}")
    return out


@router.post("/api/tests/codegen")
def codegen(request: Request, body: dict = Body(default={})):
    """Assemble pytest files from baseline specs — one file per project."""
    _require(request, "reviewer")

    scope = body.get("scope") or None
    platform = pick_platform(scope, body.get("platform"))
    project = body.get("project")
    out_dir = body.get("out") or "tests"

    job = runner.submit("codegen", f"Building tests: {platform}",
                        lambda j: _run_codegen(j, platform, project, out_dir,
                                               scope))
    return {"job_id": job.id}


def _run_codegen(job: Job, platform: str, project, out_dir: str,
                 scope: str | None = None) -> dict:
    from ..record.codegen import write_test_files

    store = _store(platform, scope)
    names = store.list_names()
    # Где именно мы смотрели — первой строкой. Число «0» без адреса, по
    # которому его получили, ничего не сообщает.
    job.say(f"set: {_scope_label(scope)} · platform: {platform}")
    job.say(f"baselines on the platform: {len(names)}")

    linked = [0]

    def _link(path: str, links: list[dict]) -> None:
        """Связь «этот эталон снимает вот этот тест» — в паспорт эталона.

        Без неё сборка оставляет две половины, которые друг о друге не знают:
        карточка снимка говорит «теста нет», страница теста — «снимков нет», и
        человек, только что нажавший «Assemble», прав, когда говорит, что тест
        со скриншотом не связался.

        Пишется отдельным полем, а не в `source`. `source` отвечает на вопрос
        «чем этот файл БЫЛ СНЯТ», и переписать его сборкой значило бы соврать:
        собранный тест ещё ни разу не выполнялся. `generated_by` отвечает на
        другой вопрос — «какой тест теперь его снимает», — и это честно уже в
        момент записи.
        """
        rel = f"{Path(out_dir).name}/{Path(path).name}"
        for link in links:
            try:
                _write_meta(platform, link["name"],
                            {"generated_by": {"file": rel,
                                              "test": link["test"]}}, scope)
                linked[0] += 1
            except Exception as e:                          # pragma: no cover
                job.say(f"{link['name']}: link not written — {e}", "warn")

    written = write_test_files(
        store, out_dir, platform=platform, project=project, on_link=_link,
        on_skip=lambda p: job.say(
            f"{p} — edited by hand after building, left as is "
            "(the previous auto-version in the adjacent .py.bak is not touched)", "warn"))
    if not written:
        if not names:
            job.say(f"There are no baselines in {_scope_label(scope)} "
                    f"on «{platform}» — nothing to build.", "warn")
            for line in _baselines_elsewhere(scope, platform):
                job.say(f"but there are some in: {line}", "warn")
        else:
            job.say("None of the baselines has an address set — nothing to build. "
                    "Set a url with the «Address» button.", "warn")
        return {"files": []}

    total = 0
    for path, count in written:
        job.say(f"{path} — {count} snapshot(s)", "ok")
        total += count
    skipped = len(names) - total
    if skipped > 0:
        job.say(f"skipped without an address: {skipped}", "warn")
    if linked[0]:
        job.say(f"linked to their baselines: {linked[0]} — the snapshot card "
                "now names this test, and the test page lists its snapshots",
                "ok")
    job.progress = 1.0
    return {"files": [p for p, _ in written], "snapshots": total,
            "linked": linked[0]}


# Сколько файлов теста показывать из чужого репозитория. Набор на тысячу файлов
# в списке нечитаем ровно так же, как в файловом менеджере, а вкладка «Tests» —
# не файловый менеджер.
MAX_PROJECT_TESTS = 300


# Как pytest сам решает, что файл — тестовый. По умолчанию это ДВА шаблона, а
# не один: `test_*.py` и `*_test.py`. Проект может переопределить их в своей
# конфигурации, и делают это чаще, чем кажется — особенно там, где набор рос из
# другого языка или из другого раннера.
PYTEST_DEFAULT_FILES = ("test_*.py", "*_test.py")

# Как pytest решает, что ФУНКЦИЯ — тестовая. Тоже настраивается, тем же
# способом и в тех же файлах.
PYTEST_DEFAULT_FUNCS = ("test*",)

# Где pytest ищет `python_files`. Порядок тот же, что у него самого.
PYTEST_CONFIGS = ("pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg")

# Каталоги, в которые незачем заходить никогда. Без этого обход упирается в
# `node_modules` и `.venv` чужого репозитория — это секунды на каждый запрос
# и тысячи файлов чужих библиотек в списке, где ждут шесть своих.
SKIP_DIRS = frozenset({
    "node_modules", "__pycache__", "venv", "env", "site-packages",
    "dist", "build", "target", "allure-results", "allure-report",
    "htmlcov", "migrations",
})


def _pytest_option(root: Path, option: str,
                   default: tuple[str, ...]) -> tuple[list[str], str]:
    """Значение опции pytest ЭТОГО проекта — и откуда оно взято.

    Раньше здесь стоял один жёсткий `test_*.py`, и это была не мелочь: набор,
    названный по второму стандартному шаблону (`login_test.py`) или по своему
    (`screenshot_*.py`, `ui_*.py`), давал на вкладке «Tests» пустоту. Пустота
    выглядела как «у проекта нет тестов» — при том что прогон их находил и
    снимал по ним эталоны, потому что запускает их pytest проекта, а не мы.

    Догадываться о настройках не надо: их пишут в конфигурации, и прочитать её
    дешевле, чем ошибиться.
    """
    import configparser
    import re as _re

    for name in PYTEST_CONFIGS:
        path = root / name
        if not path.exists():
            continue
        try:
            text = path.read_text("utf-8", errors="replace")
        except OSError:
            continue

        if name == "pyproject.toml":
            m = _re.search(rf"^\s*{_re.escape(option)}\s*=\s*(\[[^\]]*\]|\S.*)$",
                           text, _re.M)
            if m:
                raw = m.group(1)
                found = _re.findall(r"[\"']([^\"']+)[\"']", raw) or raw.split()
                if found:
                    return list(found), name
            continue

        parser = configparser.ConfigParser(strict=False, interpolation=None)
        try:
            parser.read_string(text)
        except configparser.Error:
            continue
        for section in ("pytest", "tool:pytest"):
            if parser.has_option(section, option):
                found = parser.get(section, option).split()
                if found:
                    return found, name
    return list(default), "pytest defaults"


def _python_files(root: Path) -> tuple[list[str], str]:
    """Шаблоны имён тестовых ФАЙЛОВ этого проекта."""
    return _pytest_option(root, "python_files", PYTEST_DEFAULT_FILES)


def _python_functions(root: Path) -> tuple[list[str], str]:
    """Шаблоны имён тестовых ФУНКЦИЙ этого проекта."""
    return _pytest_option(root, "python_functions", PYTEST_DEFAULT_FUNCS)


# Читать ради счёта тестов файл на мегабайт незачем: это не тест, а данные,
# случайно названные тестом.
MAX_TEST_BYTES = 1_000_000

_TEST_DEF = re.compile(r"^[ \t]*(?:async[ \t]+)?def[ \t]+(\w+)[ \t]*\(", re.M)


def _count_tests(path: Path, funcs: list[str], decl: str = "") -> int:
    """Сколько тестов в файле.

    Человек считает свой набор ТЕСТАМИ, а список показывает ФАЙЛЫ. Шесть строк
    против одиннадцати эталонов выглядят как «нашлось не всё», хотя это просто
    разные единицы измерения. Число тестов рядом с файлом снимает вопрос без
    единого клика.

    Считается по объявлениям, а не запуском pytest: запускать чужой набор ради
    списка имён — значит выполнять чужой код на каждый заход на вкладку.
    Параметризация здесь не разворачивается, и это честно: `def` в файле
    ровно столько, сколько видно глазами.
    """
    try:
        if path.stat().st_size > MAX_TEST_BYTES:
            return 0
        text = path.read_text("utf-8", errors="replace")
    except OSError:
        return 0
    if decl:
        # Чужой язык: считать нечего, кроме объявлений, которые видно глазами
        # — `test(` и `it(` в JS, `@Test` в Java. Тот же стандарт честности,
        # что и у питоновской ветки: параметризация не разворачивается.
        try:
            return len(re.findall(decl, text, re.M))
        except re.error:
            return 0
    return sum(1 for name in _TEST_DEF.findall(text)
               if any(fnmatch.fnmatchcase(name, p) for p in funcs))


def _suite_id(project) -> str:
    try:
        return project.profile().id
    except Exception:                                      # pragma: no cover
        return ""


def _scan_rules(project) -> tuple[list[str], str, list[str], str]:
    """По каким шаблонам искать тесты этого набора и чем считать объявления.

    У pytest ответ даёт ИХ конфигурация — `python_files` и `python_functions`
    в `pytest.ini`, `pyproject.toml` или `setup.cfg`. Это авторитет, и
    статический список рядом с ним рано или поздно с ним разойдётся.

    У всех остальных авторитета в таком виде нет: `playwright.config.ts` — это
    код, и `testMatch` из него не вычитать разбором. Значит, шаблоны берутся
    из профиля набора. До этого не брались ниоткуда: скан искал только
    `*.py`, и подключённый Playwright показывал «0 тестов» при одиннадцати
    снимках — то есть человек видел, что снимки чем-то сняты, а чем именно, в
    интерфейсе не было нигде.
    """
    root = project.root_path
    if project.uses_pytest():
        patterns, source = _python_files(root)
        funcs, funcs_from = _python_functions(root)
        return patterns, source, funcs, funcs_from

    profile = project.profile()
    if not profile.test_files:
        return [], f"the «{profile.id}» profile does not describe test files", \
            [], ""
    return (list(profile.test_files), f"the «{profile.id}» suite profile",
            [profile.test_decl] if profile.test_decl else [],
            f"the «{profile.id}» suite profile")


def _walk_tests(base: Path, patterns: list[str], *,
                skip: Path | None = None, limit: int = MAX_PROJECT_TESTS,
                ) -> tuple[list[Path], bool]:
    """Тестовые файлы под каталогом — с обрезанием заведомо чужих веток.

    `skip` исключает поддерево целиком: так каталог тестов, который уже
    показан, не попадает во второй список — «а что ещё есть в репозитории».
    """
    found: list[Path] = []
    truncated = False
    for dirpath, dirnames, filenames in os.walk(base):
        here = Path(dirpath)
        dirnames[:] = sorted(d for d in dirnames
                             if d not in SKIP_DIRS and not d.startswith("."))
        if skip is not None:
            dirnames[:] = [d for d in dirnames if (here / d) != skip]
        for filename in sorted(filenames):
            if not any(fnmatch.fnmatchcase(filename, p) for p in patterns):
                continue
            if len(found) >= limit:
                return found, True
            found.append(here / filename)
    return found, truncated


def _project_test_scan(key: str = "") -> tuple[list[dict], list[dict]]:
    """Файлы тестов подключённых проектов — и отчёт о том, где мы их искали.

    Вкладка «Tests» показывала ровно то, что сгенерировали мы сами, и для
    подключённого набора отвечала «0» при одиннадцати снимках. Человек видел,
    что снимки чем-то сняты, но чем именно — в интерфейсе не было нигде.

    Второй возвращаемый список — не диагностика ради диагностики. Список,
    который сошёлся не с тем числом, что человек держит в голове, имеет ровно
    четыре причины: каталог не тот, шаблоны имён не те, файлов правда нет — и
    четвёртая, самая обидная: считали разное. Человек считает ТЕСТЫ, список
    показывает ФАЙЛЫ, и шесть строк против одиннадцати эталонов выглядят как
    потеря, хотя это одиннадцать тестов в шести файлах.

    Поэтому наверх уходит и число тестов в каждом файле, и то, что нашлось в
    репозитории ЗА пределами настроенного каталога: «мы смотрели только сюда»
    — это ответ, а «не нашлось» — нет.

    Правим чужие файлы мы принципиально не отсюда: это чужой репозиторий, у
    него свой git и своё ревью. Кнопки «сохранить» здесь нет и не будет.
    """
    from ..projects import ProjectRegistry

    out: list[dict] = []
    notes: list[dict] = []
    try:
        projects = ProjectRegistry(_cfg_fresh()).list()
    except Exception:
        return out, notes

    for project in projects:
        if key and project.key != key:
            continue
        root = project.root_path
        base = (root / project.tests) if project.tests else root
        patterns, source, funcs, funcs_from = _scan_rules(project)
        decl = funcs[0] if (funcs and not project.uses_pytest()) else ""
        note = {"project": project.key, "project_name": project.name,
                "dir": str(base), "root": str(root), "exists": base.exists(),
                "patterns": patterns, "patterns_from": source,
                "functions": funcs, "functions_from": funcs_from,
                "runner": project.runner, "suite": _suite_id(project),
                "found": 0, "tests": 0, "outside": 0, "outside_sample": []}
        notes.append(note)
        if not base.exists():
            continue

        if not patterns:
            # Шаблонов нет — обходить чужое дерево незачем, а «0 файлов» без
            # причины читается как «тестов нет». Причина уже в `patterns_from`.
            continue
        room = max(0, MAX_PROJECT_TESTS - len(out))
        files, truncated = _walk_tests(base, patterns, limit=room)
        if truncated:
            note["truncated"] = True
        for path in files:
            try:
                st = path.stat()
                # Путь ОТ КОРНЯ проекта, а не от каталога тестов: именно в
                # таком виде его понимает pytest этого проекта, и именно он
                # стоит в паспорте снимка. Два разных написания одного файла
                # означали бы, что связь снимка с тестом не сходится по строке.
                rel = path.relative_to(root).as_posix()
            except (OSError, ValueError):
                continue
            count = _count_tests(path, funcs, decl)
            note["tests"] += count
            out.append({
                "name": rel, "short": path.name,
                "project": project.key, "project_name": project.name,
                "path": str(path), "size": st.st_size,
                "mtime": int(st.st_mtime * 1000), "tests": count,
                "generated": False, "editable": False,
                "has_backup": False,
                # Чем это запускается — чтобы интерфейс не подписывал
                # `npx playwright test` словом «pytest».
                "runner": project.runner,
                "suite": note["suite"],
            })
        note["found"] = len(files)

        # Что лежит в репозитории мимо настроенного каталога. В список это не
        # добавляется: подключение говорит, где тесты, и подменять его догадкой
        # нельзя. Но промолчать об этом — значит оставить человека наедине с
        # разницей между тем, что он знает, и тем, что видит.
        if base != root and root.exists():
            others, _ = _walk_tests(root, patterns, skip=base,
                                    limit=MAX_PROJECT_TESTS)
            note["outside"] = len(others)
            note["outside_sample"] = [
                p.relative_to(root).as_posix() for p in others[:5]]
    return out, notes


def _project_tests(key: str = "") -> list[dict]:
    return _project_test_scan(key)[0]


# Как снимок называют в коде теста. Оба написания встречаются в одном наборе:
# метод фикстуры и голая функция из адаптера.
_ASSERT_SHOT = re.compile(
    r"assert_screenshot\s*\(\s*(?:f?)([\"'])(.+?)\1", re.S)
_DEF_LINE = re.compile(r"^[ \t]*(?:async[ \t]+)?def[ \t]+(\w+)[ \t]*\(")


def declaration_key(name: str) -> str:
    """Ключ, по которому имя из кода сходится с именем эталона.

    В коде пишут `assert_screenshot("login")`, а в хранилище снимок лежит как
    `shop.example/login.png`: расширение дописывает рантайм, префикс проекта —
    фикстура. Сравнивать это как строки бессмысленно, поэтому сравниваются
    последний сегмент без расширения и без регистра.
    """
    text = str(name or "").replace("\\", "/").strip()
    tail = text.rsplit("/", 1)[-1]
    if tail.lower().endswith(".png"):
        tail = tail[:-4]
    return tail.lower()


def declared_in(text: str) -> list[dict]:
    """Снимки, которые ОБЪЯВЛЯЕТ этот файл теста, и функции, которые их снимают.

    Связь снимка с тестом до сих пор существовала только в двух видах: её
    записывал прогон (в момент съёмки) или сборка (для собственных тестов).
    Оба вида требуют события. А набор, который уже снят чужими тестами, стоит
    и молчит: код прямым текстом говорит, какой тест какой снимок снимает, —
    и никто этого текста не читает.

    Читается он ровно так, как написан: имя снимка берётся первым строковым
    аргументом `assert_screenshot`, а тест — ближайшим `def` выше. Имя, которое
    собирают из переменной, здесь не разбирается и не угадывается: догадка
    привязала бы снимок не к тому тесту, а это хуже, чем не привязать.
    """
    out: list[dict] = []
    func = ""
    for line in text.splitlines():
        m = _DEF_LINE.match(line)
        if m:
            func = m.group(1)
        for shot in _ASSERT_SHOT.finditer(line):
            name = shot.group(2)
            if not name or "{" in name:
                continue                    # f-строка: имя собирается на лету
            out.append({"name": name, "test": func})
    return out


_DECL_CACHE: dict[str, tuple[tuple, dict]] = {}


def project_declarations(key: str = "") -> dict[str, list[dict]]:
    """Карта «снимок → тесты, которые его снимают» по коду подключённых проектов.

    Считается по файлам, а не по прогону: прогона могло не быть ни разу, а
    вопрос «какой тест снимает этот экран» человек задаёт до него, а не после.

    Результат кэшируется по временам файлов — правка теста видна сразу,
    а двадцать чтений на каждый заход на экран не делаются.
    """
    files, _ = _project_test_scan(key)
    signature = tuple(sorted((f["path"], f["mtime"]) for f in files))
    cached = _DECL_CACHE.get(key)
    if cached and cached[0] == signature:
        return cached[1]

    found: dict[str, list[dict]] = {}
    for item in files:
        try:
            text = Path(item["path"]).read_text("utf-8", errors="replace")
        except OSError:                                     # pragma: no cover
            continue
        for decl in declared_in(text):
            found.setdefault(declaration_key(decl["name"]), []).append(
                {"file": item["name"], "test": decl["test"],
                 "project_key": item["project"], "declared": decl["name"]})
    _DECL_CACHE[key] = (signature, found)
    return found


def declared_source(name: str, project_key: str = "") -> dict:
    """Паспорт источника, собранный по коду, — если код о снимке говорит.

    Возвращается в том же виде, в каком его записал бы прогон: снимок,
    объявленный тестом, СНИМАЕТСЯ этим тестом, и «перепроверить» для него
    означает «прогнать этот тест», а не «сходить по адресу». Разница не
    косметическая: для страницы за входом второе означает снять форму логина.
    """
    if not project_key:
        return {}
    hits = project_declarations(project_key).get(declaration_key(name)) or []
    hits = [h for h in hits if h.get("test")]
    # Два теста на один снимок — это не связь, а вопрос, какой из них главный.
    # Отвечать на него догадкой нельзя.
    if len(hits) != 1:
        return {}
    hit = hits[0]
    return {"kind": "test", "file": hit["file"],
            "test": f"{hit['file']}::{hit['test']}",
            "case": hit["test"], "project_key": hit["project_key"],
            "from_code": True}


def same_test_file(recorded: str, wanted: str) -> bool:
    """Один ли это файл теста — при том что записан он мог быть иначе.

    В паспорте снимка лежит путь, каким его видел pytest ТОГО прогона: он
    относителен корня, из которого pytest запускали. Мы же спрашиваем путём от
    корня проекта. Для набора, который гоняют из подкаталога, это
    `tests/screenshot_tests/login_test.py` против
    `UiTests/tests/screenshot_tests/login_test.py` — один и тот же файл,
    записанный двумя способами.

    Сравнение строк на равенство отвечало «нет» на всех таких парах, и
    выглядело это как «связи нет»: страница теста говорила «снимков нет», хотя
    снимки сняты именно им. Поэтому сравниваются ХВОСТЫ путей — по сегментам, а
    не по символам: `a/login_test.py` и `b/login_test.py` разными остаются, а
    один и тот же файл с более длинным префиксом — сходится.
    """
    def parts(value: str) -> list[str]:
        text = str(value or "").replace("\\", "/").strip()
        return [p for p in text.split("/") if p not in ("", ".")]

    a, b = parts(recorded), parts(wanted)
    if not a or not b:
        return False
    n = min(len(a), len(b))
    return a[-n:] == b[-n:]


def _snapshots_of(test_file: str, project_key: str = "") -> list[dict]:
    """Снимки, снятые этим файлом тестов, — связь в обратную сторону.

    Карточка снимка называет тест; без обратной связи ответить на «а что вообще
    снимает этот тест» было бы можно только перебором всех карточек.

    Ищется по ФАЙЛУ, а не по конкретному тесту: человек открыл файл и хочет
    видеть всё, что он снимает, — параметризация даёт десяток идентификаторов на
    одну функцию, и разбивать их на десяток списков незачем.
    """
    cfg = _cfg_fresh()
    roots: list[tuple[str, Path]] = [
        (GLOBAL_SCOPE, cfg.root_path / cfg.paths.baselines)]
    try:
        from ..projects import ProjectRegistry

        for project in ProjectRegistry(cfg).list():
            roots.append((f"project:{project.key}",
                          project.vistest_baselines_path(cfg)))
    except Exception:
        pass

    found: list[dict] = []
    for scope, root in roots:
        if not root.exists():
            continue
        for platform_dir in sorted(d for d in root.iterdir() if d.is_dir()):
            platform = platform_dir.name
            # Имена берём у хранилища, а не собираем из путей: каталог — это
            # слаг имени, и обратно из него `login.png` не восстанавливается.
            # Собранное имя разошлось бы с настоящим на точке в расширении, и
            # ссылка «открыть снимок» вела бы в 404.
            try:
                names = FileBaselineStore(platform_dir).list_names()
            except Exception:
                continue
            for name in names:
                meta = _meta(platform, name,
                             None if scope == GLOBAL_SCOPE else scope) or {}
                src = meta.get("source") or {}
                gen = meta.get("generated_by") or {}
                # Два разных ответа на два разных вопроса: чем снимок СНЯТ и
                # какой тест его теперь снимает. Показываются оба — собранный
                # тест связан со своими эталонами с момента сборки, а не с
                # первого прогона.
                # Третий ответ на тот же вопрос — код. Снимок, о котором
                # прогон ничего не записал, всё равно назван в тексте теста;
                # не прочитать этого значило бы отвечать «связи нет» там, где
                # она написана прямым текстом.
                dec = declared_source(name, project_key) if project_key else {}
                if same_test_file(src.get("file"), test_file):
                    via, case = "captured", src.get("test") or ""
                elif same_test_file(gen.get("file"), test_file):
                    via, case = "generated", gen.get("test") or ""
                elif same_test_file(dec.get("file"), test_file):
                    via, case = "declared", dec.get("test") or ""
                else:
                    continue
                if via == "captured" and project_key \
                        and src.get("project_key") not in ("", project_key):
                    continue
                found.append({"name": name, "platform": platform,
                              "scope": scope, "test": case, "via": via})
    return sorted(found, key=lambda i: (i["platform"], i["name"]))


@router.get("/api/tests")
def list_tests(request: Request, project: str = Query(default="")):
    """Tests shown in the «Tests» section — ours and the connected projects'."""
    _guard_tests(request, "viewer")
    root = _tests_dir()
    tests = []
    if root.exists():
        for p in sorted(root.glob("test_*.py")):
            try:
                st = p.stat()
            except OSError:
                continue
            tests.append({
                "name": p.name,
                "short": p.name,
                "project": "",
                "size": st.st_size,
                "mtime": int(st.st_mtime * 1000),
                "generated": p.name.startswith("test_visual_"),
                "editable": True,
                "has_backup": p.with_suffix(".py.bak").exists(),
            })
    theirs, notes = _project_test_scan(project)
    return {"dir": str(root), "exists": root.exists(), "tests": tests,
            "project_tests": theirs, "project_notes": notes}


@router.get("/api/tests/source")
def get_test_source(request: Request, name: str = Query(...),
                    project: str = Query(default="")):
    """Source of a single test for viewing and editing.

    With `project` the file comes from a connected repository and is read-only:
    it is someone else's code, with its own git and its own review. It is shown
    because a snapshot now names the test that captured it, and a name you
    cannot read is half an answer.
    """
    _guard_tests(request, "viewer")
    if project:
        for item in _project_tests(project):
            if item["name"] == name:
                path = Path(item["path"])
                # Можно ли этому проекту назвать браузер. Меню выбора на
                # экране «Tests» обязано знать это ДО показа: пункт, который
                # заведомо ответит отказом, — не строгость, а неправда.
                proj = _project_for(project)
                return {**item, "text": path.read_text("utf-8"),
                        "project": project,
                        "browser_control": (proj.browser_control() if proj
                                            else "none"),
                        "browser_refusal": (proj.browser_refusal() if proj
                                            else ""),
                        "snapshots": _snapshots_of(name, project)}
        raise HTTPException(404, "Test not found in this project")
    p = _resolve_test(name)
    if not p.exists():
        raise HTTPException(404, "Test not found")
    st = p.stat()
    return {
        "name": p.name, "path": str(p), "text": p.read_text("utf-8"),
        "mtime": int(st.st_mtime * 1000),
        "generated": p.name.startswith("test_visual_"),
        "editable": True,
        "has_backup": p.with_suffix(".py.bak").exists(),
        # Наши тесты лежат в каталоге тестов, и в паспорте снимка стоит путь
        # относительно рабочего каталога — то есть `tests/<файл>`.
        "snapshots": _snapshots_of(f"{_tests_dir().name}/{p.name}"),
    }


@router.put("/api/tests/source")
def save_test_source(request: Request, body: dict = Body(...)):
    """Save a test edit. The previous version goes into *.py.bak.

    Optimistic locking by mtime: if the file was changed on disk (for example,
    by rebuilding from baselines) while it was being edited in the UI, the save
    is rejected — so that other edits are not silently overwritten.
    """
    _guard_tests(request, "reviewer")
    name = body.get("name") or ""
    text = body.get("text")
    if text is None:
        raise HTTPException(400, "No content to save")
    p = _resolve_test(name)

    exp = body.get("expected_mtime")
    if exp and p.exists():
        cur = int(p.stat().st_mtime * 1000)
        if abs(cur - int(exp)) > 1500:
            raise HTTPException(409, {
                "error": "File changed",
                "message": "The test was changed on disk while you were editing it. "
                           "Refresh the content and repeat the save.",
                "current_mtime": cur,
            })

    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        p.with_suffix(".py.bak").write_text(p.read_text("utf-8"), encoding="utf-8")
    p.write_text(text, encoding="utf-8")
    st = p.stat()
    return {"ok": True, "name": p.name, "mtime": int(st.st_mtime * 1000)}


#  Флаги pytest, которые запускают ЧУЖОЙ код или уводят прогон из каталога
#  тестов. `-p` подключает произвольный плагин по имени модуля — то есть это
#  прямое исполнение кода под пользователем сервиса; `-c`, `--rootdir` и
#  `--import-mode` меняют, что вообще считается проектом. Раньше сюда пускался
#  любой аргумент, и от этого спасал только замок «запросы с машины сервиса»,
#  который снимается одной переменной окружения (`VISTEST_TESTS_UI=all`).
FORBIDDEN_PYTEST_FLAGS = (
    "-p", "--plugin", "-c", "--config-file", "--rootdir", "--import-mode",
    "--pdb", "--pdbcls", "--basetemp",
)


def _pytest_args(value) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(a, str) for a in value):
        raise HTTPException(400, "args must be a list of strings")
    for arg in value:
        head = arg.split("=", 1)[0]
        if head in FORBIDDEN_PYTEST_FLAGS or head.startswith("-p"):
            raise HTTPException(
                400, f"argument {arg!r} is not allowed: it loads code or moves "
                     "the run outside the tests directory")
    return value


def _pytest_path(value: str) -> str:
    """Что запускаем — только внутри каталога тестов.

    `path` уходил в командную строку как есть, то есть прогнать можно было что
    угодно на диске. Это не про инъекцию (аргумент один и не через оболочку), а
    про то, что «запустить тесты» не должно означать «запустить любой питон, до
    которого дотянулся сервис».
    """
    root = _tests_dir()
    text = (value or "").strip()
    if not text:
        return str(root)
    # Допускается имя файла, `имя::тест` и подкаталог.
    file_part, _, selector = text.partition("::")
    try:
        candidate = Path(file_part)
        target = (candidate if candidate.is_absolute() else root / candidate).resolve()
    except (OSError, ValueError):
        raise HTTPException(400, "invalid test path") from None
    if target != root and root not in target.parents:
        raise HTTPException(
            400, f"path {value!r} is outside the tests directory ({root})")
    return str(target) + (f"::{selector}" if selector else "")


@router.post("/api/tests/run")
def run_pytest(request: Request, body: dict = Body(default={})):
    """Прогнать наш собственный набор pytest.

    body: {path?, args?, browser? | browsers?: [...], mode?: separate|together}

    Браузер здесь доезжает надёжнее, чем к чужому проекту: тесты собраны нами,
    `page` в них — фикстура pytest-playwright, а она понимает `--browser`.
    Поэтому спрашивать, чем управлять, не надо, и отказываться не от чего.

    Два вида, как и у проектов. `separate` — по прогону на браузер: каждый со
    своим ключом сериализации, то есть параллельно. `together` — один прогон,
    браузеры подряд, один общий ответ.
    """
    _guard_tests(request, "reviewer")
    path = _pytest_path(body.get("path") or "")
    extra = _pytest_args(body.get("args") or [])

    from .projects import parse_run_browsers

    browsers, mode = parse_run_browsers(body)

    if browsers and mode == "separate":
        jobs = []
        for name in browsers:
            job = runner.submit(
                "pytest", f"pytest {path} · {name}",
                (lambda b: lambda j: _run_pytest(j, path, extra, b))(name),
                lock_key=f"pytest:{path}:{name}")
            jobs.append({"browser": name, "job_id": job.id})
        return {"mode": "separate", "jobs": jobs,
                "job_id": jobs[0]["job_id"] if jobs else None, "parallel": True}

    if browsers and mode == "together":
        def work_all(job: Job) -> dict:
            out = []
            for i, name in enumerate(browsers):
                if job.cancelled:
                    break
                job.progress = i / max(len(browsers), 1)
                job.say(f"── {name} ──")
                # Упавший браузер не отменяет остальные: «прогон во всех»,
                # который останавливается на первом красном, — это «прогон в
                # первом», и хуже всего то, что выглядит он как полный отчёт.
                #
                # Красный pytest сюда и так не бросает — он возвращает код
                # выхода. А вот не запустившийся браузер бросает, и ловить это
                # надо здесь: иначе отсутствие webkit в образе отменяет
                # chromium, который отработал.
                try:
                    out.append({"browser": name,
                                **_run_pytest(job, path, extra, name)})
                except Exception as e:
                    job.say(f"{name}: {type(e).__name__}: {e}", "error")
                    out.append({"browser": name, "exit_code": -1,
                                "error": f"{type(e).__name__}: {e}"})
            job.progress = 1.0
            failed = [r["browser"] for r in out if r.get("exit_code")]
            if failed:
                job.say("failed in: " + ", ".join(failed), "error")
            return {"browsers": browsers, "runs": out, "failed": failed}

        job = runner.submit("pytest", f"pytest {path} · {len(browsers)} browsers",
                            work_all)
        return {"mode": "together", "job_id": job.id, "browsers": browsers}

    one = browsers[0] if browsers else ""
    job = runner.submit("pytest", f"pytest {path}" + (f" · {one}" if one else ""),
                        lambda j: _run_pytest(j, path, extra, one))
    return {"job_id": job.id, "browser": one or None}


def _run_pytest(job: Job, path: str, extra: list[str], browser: str = "") -> dict:
    cmd = [sys.executable, "-m", "pytest", path, "-v", "--color=no", *extra]
    env = {**os.environ, "VISTEST_ROOT": str(ROOT), "PYTHONUNBUFFERED": "1"}
    if browser:
        # `--browser` вводит pytest-playwright, а `page` в наших собранных
        # тестах — его фикстура. Переменная выставляется рядом с флагом: за
        # неё цепляются тесты, написанные руками и берущие браузер сами.
        env["VISTEST_BROWSER"] = browser
        if not any(str(a).startswith("--browser") for a in extra):
            cmd += ["--browser", browser]
    job.say("$ " + " ".join(cmd))

    proc = subprocess.Popen(
        cmd, cwd=str(Path.cwd()), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1, env=env,
    )
    try:
        for line in proc.stdout:
            if job.cancelled:
                proc.terminate()
                break
            line = line.rstrip()
            if not line:
                continue
            level = "error" if ("FAILED" in line or "ERROR" in line) else \
                ("ok" if "PASSED" in line else "info")
            job.say(line, level)
    finally:
        code = proc.wait()

    job.say(f"pytest finished with code {code}", "ok" if code == 0 else "error")
    return {"exit_code": code}


# --------------------------------------------------------------------------- #
#  Jobs
# --------------------------------------------------------------------------- #
#  A job log is the raw output of someone else's pytest: paths, environment
#  diagnostics, occasionally a value a person pasted into the settings. It is
#  not public, and cancelling someone else's run is not a public action either.
@router.get("/api/jobs")
def list_jobs(request: Request, limit: int = 20):
    _require(request, "viewer")
    return runner.list(limit)


@router.get("/api/jobs/{job_id}")
def get_job(request: Request, job_id: str, since: int = 0):
    _require(request, "viewer")
    job = runner.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    data = job.to_dict()
    # Incremental log output by an absolute offset. The in-memory window is
    # bounded, so the job itself translates the offset: slicing the window by
    # `since` silently skipped everything already evicted from it — and that is
    # the middle of the output, which is where the cause is usually written.
    data["log"], data["log_offset"] = job.log_since(max(0, since))
    return data


@router.post("/api/jobs/{job_id}/cancel")
def cancel_job(request: Request, job_id: str):
    """Cancel a job. Its own owner, or an admin.

    A reviewer may stop what they started — waiting for an admin to kill your
    own stuck run is not a workflow. Someone else's run is an admin decision.
    """
    user = _require(request, "reviewer")
    job = runner.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    if job.owner and job.owner != user["login"] and user["role"] != "admin":
        raise HTTPException(
            403, f"Job started by {job.owner} — only they or an admin can cancel it")
    if not runner.cancel(job_id):
        raise HTTPException(400, "job is not running")
    return {"ok": True}


# --------------------------------------------------------------------------- #
def _norm_name(value: str) -> str:
    if value.startswith("http"):
        from urllib.parse import urlparse

        p = urlparse(value)
        path = (p.path or "/").strip("/").replace("/", "-") or "index"
        value = f"{p.netloc.replace(':', '_')}-{path}"
    return value if value.endswith(".png") else value + ".png"


def _viewport(value) -> tuple[int, int]:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return int(value[0]), int(value[1])
    try:
        w, h = str(value or "1440x900").lower().split("x")
        return int(w), int(h)
    except Exception:
        return 1440, 900


# --------------------------------------------------------------------------- #
#  Эталон на ветку
#
#  Ветка не получает свой набор целиком — только наложение поверх основного:
#  снимки, которые она переопределила. Отсюда три вопроса, на которые должен
#  уметь отвечать интерфейс, и три роута ниже.
#
#  Самый важный из них — «влить в основной набор». Без него ветка становится
#  ловушкой: намеренный редизайн принят, ветка слита, а main продолжает падать
#  на том же снимке, потому что решение осталось в наложении, которое после
#  слияния уже никто не читает.
# --------------------------------------------------------------------------- #
def _branch_root(cfg=None) -> Path:
    return (cfg or _cfg_fresh()).root_path / "branch-baselines"


def _branch_overlay_dirs(slug_dir: Path) -> list[Path]:
    """Каталоги платформ внутри наложения одной ветки.

    Корень платформы опознаётся по раскладке хранилища: `FileBaselineStore`
    кладёт снимок как `<name>/baseline.png`. Имя снимка может нести префикс
    проекта (`shop.example/login.png`), то есть быть на уровень глубже, —
    поэтому платформа это каталог, у чьих детей есть `baseline.png` где-то в
    поддереве, а у него самого — нет.
    """
    out = []
    for candidate in sorted(p for p in slug_dir.rglob("*") if p.is_dir()):
        if candidate.name == "history" or (candidate / "baseline.png").exists():
            continue
        if any(child.is_dir() and next(child.rglob("baseline.png"), None)
               for child in candidate.iterdir()):
            out.append(candidate)
    # Оставляем самые ГЛУБОКИЕ: платформа — это последний каталог перед
    # снимками. Родительские уровни (`global`, `project/<key>`) тоже проходят
    # проверку «у детей есть baseline.png», и отдать их хранилищу значило бы
    # получить имена снимков вида «linux-chromium-1x/login.png».
    return [d for d in out
            if not any(other != d and d in other.parents for other in out)]


@router.get("/api/branches")
def list_branches(request: Request):
    """Ветки, у которых есть собственные эталоны.

    Основной ветки здесь нет и быть не может: её набор — это и есть основной
    набор, а не наложение поверх него.
    """
    _require(request, "viewer")
    from ..storage.branch import safe_branch
    from .main import db
    from .prefs import branch_baselines_on, default_branch

    root = _branch_root()
    branches: list[dict] = []
    if root.exists():
        # Имя ветки живёт в `meta.json` снимков: каталог — это уже слаг, из
        # которого исходное имя не восстановить, а показывать человеку слаг
        # вместо «feature/pay-v2» — значит заставить его гадать.
        for slug_dir in sorted(root.iterdir()):
            if not slug_dir.is_dir():
                continue
            names, name_of, updated = 0, "", ""
            for meta_path in slug_dir.rglob("meta.json"):
                if not (meta_path.parent / "baseline.png").exists():
                    continue
                names += 1
                try:
                    meta = json.loads(meta_path.read_text("utf-8"))
                except Exception:
                    continue
                name_of = name_of or str(meta.get("branch") or "")
                stamp = str(meta.get("updated_at") or "")
                if stamp > updated:
                    updated = stamp
            if not names:
                continue
            branches.append({
                "branch": name_of or slug_dir.name,
                "slug": slug_dir.name,
                "resolved": bool(name_of),
                "snapshots": names,
                "updated_at": updated,
                "directory": str(slug_dir),
            })

    branches.sort(key=lambda b: b["updated_at"], reverse=True)
    base = default_branch(db)
    return {
        "enabled": branch_baselines_on(db),
        "default_branch": base,
        "default_branch_slug": safe_branch(base),
        "branches": branches,
    }


@router.post("/api/branches/promote")
def promote_branch(request: Request, body: dict = Body(...)):
    """Влить эталоны ветки в основной набор.

    body: {branch, names?: [...], delete_after?: bool}

    Это то, что делают после слияния. Без этого шага ветка — ловушка: решение
    принято, ветка слита, а main падает на том же снимке, потому что решение
    осталось в наложении, которое после слияния уже никто не читает.

    Картинка переносится обычным `save()`, то есть основной набор получает
    НОВУЮ версию, а прежняя уходит в его историю. Откат остаётся возможен — а
    он понадобится, потому что «влить» это ровно то действие, которое делают
    второпях.
    """
    user = _require(request, "admin")
    from ..storage import BaselineRecord, FileBaselineStore
    from ..storage.branch import safe_branch
    from .main import db
    from .prefs import default_branch

    branch = (body.get("branch") or "").strip()
    if not branch:
        raise HTTPException(400, "branch is required")
    if branch == default_branch(db):
        raise HTTPException(
            400, f"«{branch}» is the base branch — its baselines are the main "
                 "set already, there is nowhere to promote them to")

    slug_dir = _branch_root() / safe_branch(branch)
    if not slug_dir.exists():
        raise HTTPException(404, f"branch «{branch}» has no baselines of its own")

    only = set(body.get("names") or [])
    cfg = _cfg_fresh()
    promoted, skipped = [], []

    for platform_dir in _branch_overlay_dirs(slug_dir):
        overlay = FileBaselineStore(platform_dir)
        # Раскладка наложения: <slug>/[project/<key>/]<scope>/<platform>
        parts = platform_dir.relative_to(slug_dir).parts
        platform = parts[-1]
        scope_kind = parts[-2] if len(parts) >= 2 else "global"
        project_key = parts[1] if len(parts) >= 3 and parts[0] == "project" else None

        if scope_kind == "vistest" and project_key:
            base_dir = _project_of(project_key).vistest_baselines_path(cfg, platform)
        else:
            base_dir = cfg.root_path / cfg.paths.baselines / platform
        base = FileBaselineStore(base_dir)

        for name in overlay.list_names():
            if only and name not in only:
                skipped.append(name)
                continue
            record = overlay.load(name)
            if record is None:
                continue
            meta = dict(record.meta or {})
            meta.pop("forked_from", None)
            meta.pop("forked_from_version", None)
            meta["promoted_from_branch"] = branch
            meta["approved_by"] = user["login"]
            base.save(BaselineRecord(
                name=name, image=record.image,
                stability_mask=record.stability_mask, dom=record.dom,
                ignore_boxes=record.ignore_boxes, meta=meta))
            promoted.append({"name": name, "platform": platform,
                             "scope": scope_kind, "project": project_key})

    from .auth import audit

    audit(db, user["login"], "branch.promoted", branch, count=len(promoted))

    deleted = False
    if body.get("delete_after") and promoted and not only:
        _rmtree_safe(slug_dir)
        deleted = True

    return {"ok": True, "branch": branch, "promoted": promoted,
            "skipped": sorted(set(skipped)), "deleted": deleted}


@router.delete("/api/branches/{branch:path}")
def drop_branch(branch: str, request: Request):
    """Выбросить наложение ветки, не трогая основной набор.

    Ветку слили или закрыли — её эталоны больше никого не касаются. Отдельно от
    «влить»: выбросить и влить это противоположные решения, и путать их нельзя.
    """
    _require(request, "admin")
    from ..storage.branch import safe_branch
    from .main import db
    from .prefs import default_branch

    branch = (branch or "").strip()
    if not branch:
        raise HTTPException(400, "branch is required")
    if branch == default_branch(db):
        raise HTTPException(
            400, f"«{branch}» is the base branch — deleting the main set is not "
                 "what this route is for")

    slug_dir = _branch_root() / safe_branch(branch)
    if not slug_dir.exists():
        raise HTTPException(404, f"branch «{branch}» has no baselines of its own")

    freed = _rmtree_safe(slug_dir)

    from .auth import audit, current_user

    who = current_user(db, request.cookies.get("vistest_session"))["login"]
    audit(db, who, "branch.dropped", branch, freed_kb=round(freed / 1024))
    return {"ok": True, "branch": branch, "freed_kb": round(freed / 1024)}
