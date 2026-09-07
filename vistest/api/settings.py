# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Environment variables from the UI: reading and writing `.vistest/secrets.env`.

Why this is in the interface. A login behind a form is half of a real product, and
it is described via `${VISTEST_USER}` / `${VISTEST_PASSWORD}`. As long as values
are placed into the file by hand, "set up visual tests without code" stops being
true on the very first page behind authentication.

About security, plainly, because a buyer in the target segment will ask about it
first. This file contains passwords to the stand. Here it is edited as plain
text, meaning the values **travel over HTTP in cleartext**. This is acceptable
exactly in the mode VisTest was designed for: the service listens on loopback on the
engineer's machine or inside the perimeter. Therefore:

* by default the endpoints respond **only to requests from localhost**;
* they can be opened to the outside deliberately — `VISTEST_SECRETS_UI=all`;
* turned off entirely — `VISTEST_SECRETS_UI=off`;
* the file lives in `.gitignore`, and the response always carries a reminder of that.

File permissions are set to 0600 where the OS supports it.
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, Request

from ..config import VisTestConfig
from . import net

router = APIRouter()

# Names by which a secret is recognized. Needed not to forbid anything, but so that
# the interface can honestly say «this is a password» and not show it in the list.
SECRET_RE = re.compile(r"pass|secret|token|key|otp|pin|cvv|credential", re.I)

HEADER = """# VisTest environment variables.
#
# The file is in .gitignore and must not end up in the repository.
# Format: NAME=value, one pair per line, # is a comment.
#
# Scenario steps reference these names: ${VISTEST_USER}, ${VISTEST_PASSWORD}.
"""

#  «Локально» живёт в `api/net.py`: считается по адресу соединения,
#  который заголовком не подделать.


def _mode() -> str:
    return (os.getenv("VISTEST_SECRETS_UI") or "local").strip().lower()


def _guard(request: Request) -> None:
    # Secrets are passwords to the stand. Only an administrator may view them, and
    # only from where it is allowed. Two locks, not one.
    from .auth import require
    from .main import db

    require(db, request.cookies.get("vistest_session"), "admin")

    mode = _mode()
    if mode == "off":
        raise HTTPException(
            403, "The variable editor is turned off (VISTEST_SECRETS_UI=off)")
    if mode == "all":
        return
    if not net.is_loopback(request):
        raise HTTPException(
            403,
            "Variables can be edited only from the local machine (request from "
            f"{net.client_ip(request)}). To deliberately open to the outside: "
            "VISTEST_SECRETS_UI=all",
        )


def _path() -> Path:
    root = Path(os.getenv("VISTEST_ROOT", ".vistest")).resolve()
    return root / "secrets.env"


def parse_env(text: str) -> list[dict]:
    """Text -> list of variables. Comments and empty lines are discarded."""
    out: list[dict] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if not name:
            continue
        out.append({
            "name": name,
            "value": value.strip().strip("'\""),
            "secret": bool(SECRET_RE.search(name)),
        })
    return out


def render_env(items: list[dict]) -> str:
    """List of variables -> file text. Order is preserved."""
    lines = [HEADER]
    for it in items:
        name = str(it.get("name", "")).strip()
        if not name:
            continue
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise HTTPException(
                400, f"Invalid variable name: {name!r}. "
                     "Latin letters, digits and underscore are allowed, "
                     "not starting with a digit.")
        value = str(it.get("value", ""))
        if "\n" in value:
            raise HTTPException(400, f"{name}: line break in the value")
        lines.append(f"{name}={value}")
    return "\n".join(lines).rstrip() + "\n"


def _write(text: str) -> Path:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)   # 0600; on Windows this is a no-op
    except OSError:
        pass
    return path


# --------------------------------------------------------------------------- #
@router.get("/api/env")
def read_env(request: Request):
    """The current contents of secrets.env plus what is missing from it."""
    _guard(request)
    path = _path()
    text = path.read_text("utf-8") if path.exists() else ""
    items = parse_env(text)

    return {
        "path": str(path),
        "exists": path.exists(),
        "text": text,
        "vars": items,
        "missing": sorted(_referenced_names() - {i["name"] for i in items}),
        "mode": _mode(),
        "warning": (
            "The file contains passwords to the stand. It is in .gitignore - do not commit it "
            "and do not publish it together with the baselines."
        ),
    }


@router.put("/api/env")
def write_env(request: Request, payload: dict = Body(...)):
    """Save variables. Accepts either `text` or a list of `vars`."""
    _guard(request)

    if isinstance(payload.get("text"), str):
        text = payload["text"]
        parse_env(text)                       # early format check
    else:
        text = render_env(payload.get("vars") or [])

    path = _write(text)
    _apply_to_process(text)
    return {"ok": True, "path": str(path), "vars": parse_env(text)}


@router.post("/api/env/reload")
def reload_env(request: Request):
    """Pull the file into the process environment without restarting the service."""
    _guard(request)
    path = _path()
    if not path.exists():
        return {"ok": True, "loaded": 0}
    text = path.read_text("utf-8")
    return {"ok": True, "loaded": _apply_to_process(text)}


def _apply_to_process(text: str) -> int:
    """Update os.environ.

    Specifically by overwriting, not setdefault: a person just corrected a value
    in the interface, and they expect to see the corrected one, not the one that was
    picked up at service start.
    """
    count = 0
    for item in parse_env(text):
        os.environ[item["name"]] = item["value"]
        count += 1
    return count


def _referenced_names() -> set[str]:
    """Which variables are mentioned at all in the config and in the baseline manifests.

    This turns the window from an «input field» into a hint: the interface itself
    shows what is missing for a run, instead of waiting for a failure
    with an error about an unknown variable.
    """
    names: set[str] = set()
    pattern = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
    root = Path(os.getenv("VISTEST_ROOT", ".vistest")).resolve()

    for candidate in (root.parent / "vistest.yaml", root.parent / "vistest.yml"):
        if candidate.exists():
            try:
                names |= set(pattern.findall(candidate.read_text("utf-8")))
            except OSError:
                pass

    baselines = root / "baselines"
    if baselines.exists():
        for meta in baselines.rglob("meta.json"):
            try:
                names |= set(pattern.findall(meta.read_text("utf-8")))
            except OSError:
                continue
    return names


# --------------------------------------------------------------------------- #
#  Пороги вердикта
#
#  Отдельно от секретов и без loopback-замка: порог — это не пароль, менять его
#  должен уметь тот, кто разбирает падения, а сидит он не за машиной сервиса.
#  Роль — `admin`: порог общий для всех, кто пользуется инсталляцией, и тихо
#  сдвинутый порог меняет вердикты чужих прогонов.
# --------------------------------------------------------------------------- #
@router.get("/api/settings/thresholds")
def read_thresholds(request: Request, project: str | None = None):
    """Действующие пороги и — обязательно — откуда каждый взялся."""
    from .auth import require
    from .main import db
    from .thresholds import effective

    require(db, request.cookies.get("vistest_session"), "viewer")
    return effective(db, VisTestConfig.load(), project or None)


@router.put("/api/settings/thresholds")
def write_thresholds(request: Request, payload: dict = Body(...)):
    """Задать или снять переопределение.

    body: {project?: "<key>", values: {fail_severity: 30, ...}}

    `null` в значении снимает переопределение и возвращает то, что говорит
    `vistest.yaml`. Это не то же самое, что ноль: ноль здесь — осмысленная
    настройка «падать на любом видимом различии».
    """
    from .auth import audit, require
    from .main import db
    from .thresholds import ThresholdError, effective, put

    user = require(db, request.cookies.get("vistest_session"), "admin")
    values = payload.get("values")
    if not isinstance(values, dict) or not values:
        raise HTTPException(400, "values must be a non-empty object")

    project = (payload.get("project") or "").strip() or None
    try:
        result = put(db, values, project_key=project, who=user["login"])
    except ThresholdError as e:
        raise HTTPException(400, str(e)) from None

    audit(db, user["login"], "thresholds.changed", project or "global",
          written=result["written"], cleared=result["cleared"])
    return {**result, **effective(db, VisTestConfig.load(), project)}


# --------------------------------------------------------------------------- #
#  Эталон на ветку
#
#  Отдельно от порогов: там числа с диапазонами, здесь флаг и имя ветки.
#  Выключено по умолчанию — команде с одной веткой режим не даёт ничего, а
#  вопросов добавляет («почему мой апрув не изменил эталон, который я вижу»).
# --------------------------------------------------------------------------- #
@router.get("/api/settings/branches")
def read_branch_settings(request: Request):
    from .auth import require
    from .main import db
    from .prefs import branch_baselines_on, default_branch

    require(db, request.cookies.get("vistest_session"), "viewer")
    return {"enabled": branch_baselines_on(db), "default_branch": default_branch(db)}


@router.put("/api/settings/branches")
def write_branch_settings(request: Request, payload: dict = Body(...)):
    """body: {enabled?: bool, default_branch?: str}"""
    from .auth import audit, require
    from .main import db
    from .prefs import (
        BRANCH_BASELINES,
        DEFAULT_BRANCH,
        branch_baselines_on,
        default_branch,
        set_flag,
        set_text,
    )

    user = require(db, request.cookies.get("vistest_session"), "admin")

    if "enabled" in payload:
        set_flag(db, BRANCH_BASELINES, bool(payload["enabled"]), user["login"])
    if payload.get("default_branch") is not None:
        name = str(payload["default_branch"]).strip()
        if not name:
            raise HTTPException(400, "default_branch cannot be empty")
        if len(name) > 200:
            raise HTTPException(400, "default_branch is too long")
        set_text(db, DEFAULT_BRANCH, name, user["login"])

    result = {"enabled": branch_baselines_on(db),
              "default_branch": default_branch(db)}
    audit(db, user["login"], "branches.settings", result["default_branch"],
          enabled=result["enabled"])
    return result


# --------------------------------------------------------------------------- #
#  Notifications
#
#  The one thing that leaves the service on its own. Everything else here works
#  by someone coming and looking — which holds exactly until the first day
#  nobody comes.
# --------------------------------------------------------------------------- #
@router.get("/api/settings/notifications")
def read_notifications(request: Request):
    from .auth import require
    from .main import db
    from .notify import settings as notify_settings

    require(db, request.cookies.get("vistest_session"), "admin")
    return notify_settings(db)


@router.put("/api/settings/notifications")
def write_notifications(request: Request, payload: dict = Body(...)):
    """body: {enabled?: bool, webhook?: str, after_hours?: number}"""
    from .auth import audit, require
    from .main import db
    from .notify import configure

    user = require(db, request.cookies.get("vistest_session"), "admin")
    try:
        result = configure(db, payload, user["login"])
    except (TypeError, ValueError) as e:
        raise HTTPException(400, str(e)) from e
    audit(db, user["login"], "notifications.settings",
          result["webhook"][:80], enabled=result["enabled"])
    return result


@router.post("/api/settings/notifications/test")
def send_notification_now(request: Request):
    """Send the digest right now — the only way to find out the URL works.

    Without it the first check of a webhook happens a day later, and a typo in
    it looks exactly like «nothing is waiting»: silence.

    `force` skips «the list has not changed», but not «the list is empty»:
    sending nothing to prove a channel works teaches people the channel says
    nothing.
    """
    import os as _os

    from .auth import audit, require
    from .main import db
    from .notify import run_once

    user = require(db, request.cookies.get("vistest_session"), "admin")
    result = run_once(db, base_url=(_os.getenv("VISTEST_PUBLIC_URL") or "").strip(),
                      force=True)
    audit(db, user["login"], "notifications.test", str(result.get("sent")))
    return result


# --------------------------------------------------------------------------- #
#  Retention
#
#  The only background action that DELETES data, which is why it is off by
#  default and why an unreviewed failure is never touched by it.
# --------------------------------------------------------------------------- #
@router.get("/api/settings/retention")
def read_retention(request: Request):
    from .auth import require
    from .main import db
    from .retention import settings as retention_settings

    require(db, request.cookies.get("vistest_session"), "admin")
    return retention_settings(db)


@router.put("/api/settings/retention")
def write_retention(request: Request, payload: dict = Body(...)):
    """body: {enabled?: bool, days?: number, keep_last?: int, max_gb?: number}"""
    from .auth import audit, require
    from .main import db
    from .retention import configure

    user = require(db, request.cookies.get("vistest_session"), "admin")
    try:
        result = configure(db, payload, user["login"])
    except (TypeError, ValueError) as e:
        raise HTTPException(400, str(e)) from e
    audit(db, user["login"], "retention.settings", str(result["days"]),
          enabled=result["enabled"], keep_last=result["keep_last"])
    return result


@router.post("/api/settings/retention/preview")
def preview_retention(request: Request):
    """What the policy would delete right now, deleting nothing.

    Turning on automatic deletion blind is how a person finds out what it meant
    a day later and from the empty list. A dry run answers before, not after.
    """
    from .auth import require
    from .main import _drop_run_files, _storage_kb, db
    from .retention import settings as retention_settings
    from .retention import sweep

    require(db, request.cookies.get("vistest_session"), "admin")
    conf = retention_settings(db)
    return sweep(db, _drop_run_files, days=conf["days"],
                 keep_last=conf["keep_last"], project="*",
                 max_gb=conf["max_gb"], used_kb=_storage_kb(), dry_run=True)
