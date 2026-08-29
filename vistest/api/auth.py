# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Users, sign in and roles.

Appeared when the installation moved from an engineer machine to a server inside
the perimeter. Before that the question «who did this» was meaningless: it was
done by whoever sat at the computer.

About responsibility, honestly. Writing authentication is a bad idea by default:
it is easy to make it almost right, and «almost» here means «no». We do this
deliberately and with limits that are named out loud:

* **Passwords are hashed with PBKDF2-HMAC-SHA256** from the standard library.
  bcrypt and argon2 are slower to crack, but they pull in external dependencies,
  and offline installation is a mandatory requirement of the target segment.
  Iterations are taken with a margin; the parameter lives in a constant and grows
  over time, and the hash stores its own iteration count, so old passwords do not
  break.
* **Sessions are server-side**, not signed tokens. Signing out and revoking
  access must work for real; with JWT they do not work without a revocation
  list, which cancels out all the savings.
* **The first administrator is created from the command line**, not from a
  «create an admin» window on first entry. Such a window is a hole exactly up to
  the moment it is filled in, and on the network it will be found faster than by
  you.
* **We do not provide HTTPS.** The password travels over HTTP if there is no TLS
  termination in front of the service. This is written in SECURITY.md and
  repeated here.

There are exactly three roles, because a fourth one nobody would be able to
explain:

| Role | What it can do |
|---|---|
| `viewer` | view runs, baselines, metrics, download reports |
| `reviewer` | + accept and reject, start runs |
| `admin` | + users, connecting projects, secrets, mouse recording |
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Cookie, HTTPException, Request, Response
from fastapi.responses import JSONResponse

router = APIRouter()

PBKDF2_ITERATIONS = 480_000
SESSION_DAYS = 14
COOKIE = "vistest_session"

ROLES = ("viewer", "reviewer", "admin")
_RANK = {role: i for i, role in enumerate(ROLES)}


# --------------------------------------------------------------------------- #
#  Passwords
# --------------------------------------------------------------------------- #
def hash_password(password: str, *, iterations: int = PBKDF2_ITERATIONS) -> str:
    """Password to a string `pbkdf2$iterations$salt$hash`.

    The iteration count is stored inside: when we raise it, old passwords keep
    verifying, while new ones become more expensive.
    """
    if not password or len(password) < 8:
        raise ValueError("Password is shorter than 8 characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt_hex, digest_hex = stored.split("$")
        if algo != "pbkdf2":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations))
    except (ValueError, AttributeError):
        return False
    # Constant-time comparison: a plain `==` leaks the length of the match.
    return hmac.compare_digest(digest.hex(), digest_hex)


def needs_rehash(stored: str) -> bool:
    try:
        return int(stored.split("$")[1]) < PBKDF2_ITERATIONS
    except (ValueError, IndexError):
        return True


# --------------------------------------------------------------------------- #
#  Users
# --------------------------------------------------------------------------- #
def create_user(db, login: str, password: str, *, role: str = "viewer",
                name: str = "", status: str = "active") -> int:
    login = (login or "").strip().lower()
    if not login:
        raise ValueError("Empty login")
    if role not in ROLES:
        raise ValueError(f"Role must be one of: {', '.join(ROLES)}")
    if status not in USER_STATUS:
        raise ValueError(f"Status must be one of: {', '.join(USER_STATUS)}")
    if db.one("SELECT id FROM user WHERE login=?", (login,)):
        raise ValueError(f"User {login!r} already exists")

    with db.connect() as c:
        cur = c.execute(
            "INSERT INTO user(login, name, password, role, status, active)"
            " VALUES(?,?,?,?,?,?)",
            (login, name or login, hash_password(password), role, status,
             1 if status == "active" else 0))
        return cur.lastrowid


# --------------------------------------------------------------------------- #
#  Регистрация и первый вход
#
#  Раньше свежая инсталляция, открытая по сети, показывала стену: «пользователей
#  нет, зайдите на машину сервиса и выполните `vistest user add`». Стена стояла
#  не зря — пока пользователей нет, `current_user()` раздаёт роль admin каждому,
#  кто дотянулся до порта, — но из интерфейса выйти из этого состояния было
#  нельзя вовсе. Человек, раскативший сервис в докере, упирался в неё первым же
#  экраном.
#
#  Теперь состояние «инсталляцию ещё можно занять» — это флаг в базе. Пока он
#  открыт и активных пользователей нет, форма позволяет завести первого
#  администратора прямо в браузере; в момент создания флаг закрывается
#  **навсегда**, и второй раз этим путём воспользоваться нельзя.
#
#  Что это значит по-честному: в промежутке между стартом сервиса и первой
#  регистрацией инсталляцию может занять любой, кто откроет адрес раньше вас.
#  Внутри периметра это приемлемо, в открытой сети — нет; для второго случая
#  есть `VISTEST_SETUP_TOKEN` (см. `setup_token_ok`), и он же остаётся
#  единственным способом снова открыть флаг, если администратор потерял доступ.
# --------------------------------------------------------------------------- #
USER_STATUS = ("pending", "active", "disabled")

SETUP_OPEN = "setup_open"
OPEN_REGISTRATION = "open_registration"


def setup_open(db) -> bool:
    """Можно ли ещё завести первого администратора через форму."""
    from .prefs import get_flag

    if any_users(db):
        return False
    return get_flag(db, SETUP_OPEN, True)


def close_setup(db, who: str = "") -> None:
    from .prefs import set_flag

    set_flag(db, SETUP_OPEN, False, who)


def reopen_setup(db, who: str = "") -> None:
    """Открыть окно заново — например, если единственный админ потерял доступ.

    Доступно только с машины сервиса (`vistest user setup-reopen`): иначе это
    была бы кнопка «сделать меня администратором» на видном месте.
    """
    from .prefs import set_flag

    set_flag(db, SETUP_OPEN, True, who)


def setup_token_ok(supplied: str) -> bool:
    """Проверка `VISTEST_SETUP_TOKEN`, если он задан.

    Переменная не задана — проверки нет вовсе, и занять инсталляцию может любой,
    кто первым откроет адрес. Это осознанный выбор для периметра; для открытой
    сети переменную задают, и тогда без неё форма первого администратора не
    проходится.
    """
    import hmac as _hmac

    expected = (os.getenv("VISTEST_SETUP_TOKEN") or "").strip()
    if not expected:
        return True
    return _hmac.compare_digest(expected, (supplied or "").strip())


def open_registration(db) -> bool:
    """Может ли человек завести себе заявку сам.

    Включено по умолчанию, и это безопасно ровно потому, что заявка ничего не
    даёт: пока администратор её не одобрил, войти по ней нельзя.
    """
    from .prefs import get_flag

    return get_flag(db, OPEN_REGISTRATION, True)


def pending_users(db) -> list[dict]:
    return db.query(
        "SELECT id, login, name, created_at FROM user"
        " WHERE status='pending' ORDER BY created_at")


def approve_user(db, login: str, role: str = "viewer") -> bool:
    if role not in ROLES:
        raise ValueError(f"Role must be one of: {', '.join(ROLES)}")
    return db.execute(
        "UPDATE user SET status='active', active=1, role=? "
        " WHERE login=? AND status='pending'",
        (role, (login or "").strip().lower())) > 0


def reject_user(db, login: str) -> bool:
    """Заявку удаляем, а не отключаем.

    Отключённый пользователь — это след решения о человеке, который в системе
    был; отклонённая заявка — след о человеке, которого не было. Держать её
    вечно в списке значит копить мусор и мешать тому же человеку подать заявку
    заново, если отказ был по ошибке.
    """
    return db.execute(
        "DELETE FROM user WHERE login=? AND status='pending'",
        ((login or "").strip().lower(),)) > 0


def set_password(db, login: str, password: str) -> bool:
    return db.execute("UPDATE user SET password=? WHERE login=?",
                      (hash_password(password), login.strip().lower())) > 0


def set_role(db, login: str, role: str) -> bool:
    if role not in ROLES:
        raise ValueError(f"Role must be one of: {', '.join(ROLES)}")
    return db.execute("UPDATE user SET role=? WHERE login=?",
                      (role, login.strip().lower())) > 0


def deactivate(db, login: str) -> bool:
    """We switch off rather than delete: otherwise the audit loses the author of decisions."""
    login = login.strip().lower()
    ok = db.execute("UPDATE user SET active=0, status='disabled' WHERE login=?",
                    (login,)) > 0
    if ok:
        row = db.one("SELECT id FROM user WHERE login=?", (login,))
        if row:
            db.execute("DELETE FROM session WHERE user_id=?", (row["id"],))
    return ok


def list_users(db) -> list[dict]:
    return db.query(
        "SELECT id, login, name, role, active, status, created_at, last_login"
        "  FROM user ORDER BY login")


def any_users(db) -> bool:
    """Есть ли хоть один пользователь, который может войти.

    Заявка, ждущая одобрения, здесь не считается — и это принципиально. Иначе
    первая же чужая заявка переводила бы инсталляцию из «локального режима» в
    «требуется вход», а войти было бы некому: сам заявитель ещё не одобрен, а
    администратора не существует. Сервис запирался бы сам от всех, включая
    владельца.
    """
    return bool(db.one("SELECT id FROM user WHERE active=1 LIMIT 1"))


# --------------------------------------------------------------------------- #
#  Throttling failed sign-ins
#
#  Two reasons, and the second one is the less obvious of the pair.
#
#  Guessing. `login.failed` was already written into the audit, and nothing
#  ever looked at it. A record that nobody reads is not protection, it is a
#  post-mortem.
#
#  Load. PBKDF2 at 480k iterations is deliberately expensive — that is the
#  whole point of it. It also means twenty parallel attempts eat the whole
#  service CPU. So the check stands *before* the hash, not after: a locked-out
#  attempt must cost us nothing.
#
#  The counter lives in the audit table rather than in memory on purpose:
#  a lockout that a service restart clears is a lockout an attacker can clear.
# --------------------------------------------------------------------------- #
LOGIN_WINDOW_MINUTES = 15
LOGIN_MAX_FAILURES = 8          # per login within the window
LOGIN_MAX_FAILURES_IP = 30      # per source address within the window


def _last_success(db, who: str = "", source: str = "") -> str:
    """When this login — or this address — last signed in successfully."""
    if who:
        row = db.one("SELECT MAX(at) AS at FROM audit"
                     " WHERE action='login.ok' AND who=?", (who,))
    else:
        row = db.one("SELECT MAX(at) AS at FROM audit"
                     " WHERE action='login.ok' AND target=?", (source,))
    return (row or {}).get("at") or ""


def _recent_failures(db, who: str = "", source: str = "") -> int:
    """Failures inside the window that happened *after* the last success.

    The reset used to be a DELETE: a successful sign-in wiped every
    `login.failed` row for that login. It did reset the counter — and it also
    erased the record of the attempts, so a successful guess swept away its own
    tracks and the audit log was least useful in exactly the case it exists
    for. Nothing is deleted now; the last success is simply the mark we count
    from.
    """
    since = f"-{LOGIN_WINDOW_MINUTES} minutes"
    mark = _last_success(db, who=who, source=source)
    if who:
        row = db.one(
            "SELECT COUNT(*) AS n FROM audit"
            " WHERE action='login.failed' AND who=?"
            "   AND at >= datetime('now', ?) AND at > ?", (who, since, mark))
    else:
        row = db.one(
            "SELECT COUNT(*) AS n FROM audit"
            " WHERE action='login.failed' AND target=?"
            "   AND at >= datetime('now', ?) AND at > ?", (source, since, mark))
    return int((row or {}).get("n") or 0)


def login_blocked(db, who: str, source: str) -> int:
    """Seconds to wait, or 0 if the attempt may proceed."""
    try:
        if who and _recent_failures(db, who=who) >= LOGIN_MAX_FAILURES:
            return LOGIN_WINDOW_MINUTES * 60
        if source and _recent_failures(db, source=source) >= LOGIN_MAX_FAILURES_IP:
            return LOGIN_WINDOW_MINUTES * 60
    except Exception:
        # A broken counter must never lock everyone out of their own service.
        return 0
    return 0


def clear_failures(db, who: str) -> None:      # pragma: no cover - kept for callers
    """Deprecated: the counter resets by itself once `login.ok` is recorded.

    Left as a no-op rather than removed, because deleting audit rows is the one
    thing this must never do again.
    """


# --------------------------------------------------------------------------- #
#  Cookies
# --------------------------------------------------------------------------- #
def cookie_secure(request: Request) -> bool:
    """Whether the session cookie may carry the `Secure` flag.

    `request.url.scheme` is the scheme uvicorn saw, and behind nginx or Caddy
    that is plain http unless the process was started with `--proxy-headers`.
    So the cookie never got `Secure` on installations that terminate TLS in
    front — which is all of them. We look at the forwarded scheme as well, and
    `VISTEST_COOKIE_SECURE=1|0` settles it outright for anyone who would rather
    not depend on either.
    """
    mode = (os.getenv("VISTEST_COOKIE_SECURE") or "auto").strip().lower()
    if mode in ("1", "true", "yes", "on"):
        return True
    if mode in ("0", "false", "no", "off"):
        return False
    if request.url.scheme == "https":
        return True
    forwarded = (request.headers.get("x-forwarded-proto") or "").split(",")[0]
    return forwarded.strip().lower() == "https"


# --------------------------------------------------------------------------- #
#  Sessions
# --------------------------------------------------------------------------- #
def open_session(db, user_id: int, agent: str = "") -> str:
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
    db.execute(
        "INSERT INTO session(token, user_id, expires_at, agent) VALUES(?,?,?,?)",
        (token, user_id, expires.isoformat(timespec="seconds"), agent[:200]))
    db.execute("UPDATE user SET last_login=datetime('now') WHERE id=?", (user_id,))
    return token


def close_session(db, token: str) -> None:
    db.execute("DELETE FROM session WHERE token=?", (token,))


def session_user(db, token: str | None) -> dict | None:
    if not token:
        return None
    row = db.one(
        "SELECT u.id, u.login, u.name, u.role, u.active, s.expires_at"
        "  FROM session s JOIN user u ON u.id = s.user_id"
        " WHERE s.token=?", (token,))
    if not row or not row["active"]:
        return None
    try:
        if datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
            db.execute("DELETE FROM session WHERE token=?", (token,))
            return None
    except ValueError:
        return None
    return {"id": row["id"], "login": row["login"],
            "name": row["name"], "role": row["role"]}


def purge_expired(db) -> int:
    return db.execute(
        "DELETE FROM session WHERE expires_at < datetime('now')")


# --------------------------------------------------------------------------- #
#  Audit
# --------------------------------------------------------------------------- #
def audit(db, who: str, action: str, target: str = "", **details) -> None:
    """A journal entry. It must not crash the action it is recording."""
    try:
        db.execute(
            "INSERT INTO audit(who, action, target, details) VALUES(?,?,?,?)",
            (who or "—", action, str(target)[:300],
             json.dumps(details, ensure_ascii=False) if details else None))
    except Exception:
        pass


# --------------------------------------------------------------------------- #
#  Invites
# --------------------------------------------------------------------------- #
INVITE_DAYS = 7


def create_invite(db, role: str, name: str, created_by: str) -> dict:
    if role not in ROLES:
        raise ValueError(f"Role must be one of: {', '.join(ROLES)}")
    token = secrets.token_urlsafe(24)
    expires = (datetime.now(timezone.utc)
               + timedelta(days=INVITE_DAYS)).isoformat(timespec="seconds")
    db.execute(
        "INSERT INTO invite(token, role, name, created_by, expires_at)"
        " VALUES(?,?,?,?,?)", (token, role, name or "", created_by, expires))
    return {"token": token, "role": role, "name": name or "", "expires_at": expires}


def invite_status(row: dict) -> str:
    if row.get("used_at"):
        return "used"
    try:
        if datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
            return "expired"
    except (ValueError, TypeError):
        return "expired"
    return "active"


def valid_invite(db, token: str) -> dict | None:
    row = db.one("SELECT * FROM invite WHERE token=?", (token or "",))
    if not row or invite_status(row) != "active":
        return None
    return row


# --------------------------------------------------------------------------- #
#  Team profile
# --------------------------------------------------------------------------- #
def get_team(db) -> dict:
    row = db.one("SELECT name, brand_color FROM team WHERE id=1")
    return {"name": (row or {}).get("name", "") if row else "",
            "brand_color": (row or {}).get("brand_color", "#C2410C") if row else "#C2410C"}


def set_team(db, name: str | None, brand_color: str | None) -> dict:
    cur = get_team(db)
    name = cur["name"] if name is None else str(name)[:80]
    color = cur["brand_color"] if not brand_color else str(brand_color)[:16]
    db.execute(
        "INSERT INTO team(id, name, brand_color, updated_at)"
        " VALUES(1, ?, ?, datetime('now'))"
        " ON CONFLICT(id) DO UPDATE SET name=excluded.name,"
        " brand_color=excluded.brand_color, updated_at=datetime('now')",
        (name, color))
    return {"name": name, "brand_color": color}


# --------------------------------------------------------------------------- #
#  Access check
# --------------------------------------------------------------------------- #
def _own_project() -> str:
    """Проект, под именем которого сервис ведёт свою историю.

    Нужен интерфейсу: собственный набор эталонов (`scope=global`) принадлежит
    ему, и без этого имени фронт не может сопоставить выданное право с той
    вкладкой, которую человек открыл, — то есть погасил бы кнопки, которые на
    самом деле работают.
    """
    try:
        from ..config import VisTestConfig
        return (VisTestConfig.load().service.project or "").strip()
    except Exception:
        return ""


def auth_disabled() -> bool:
    """Single-user mode: the service runs on an engineer machine, no need to separate people.

    Turned on explicitly. By default it is also on, because before the first
    user appears there is otherwise no way to sign in at all; as soon as an
    administrator is created, authentication starts acting on its own.
    """
    return (os.getenv("VISTEST_AUTH") or "auto").strip().lower() == "off"


def current_user(db, token: str | None) -> dict:
    """Who is performing the request.

    While there are no users yet — single-user mode: access is full, the author
    of decisions is recorded as «local». This is the same behaviour as before
    sign in appeared, and it must not break from an update alone.
    """
    if auth_disabled() or not any_users(db):
        return {"id": 0, "login": "local", "name": "local mode",
                "role": "admin", "anonymous": True}
    user = session_user(db, token)
    if user is None:
        raise HTTPException(401, "Sign in required")
    return user


def require(db, token: str | None, role: str) -> dict:
    user = current_user(db, token)
    if _RANK.get(user["role"], -1) < _RANK[role]:
        raise HTTPException(
            403, f"Not enough rights: role {role} is required, you have {user['role']}")
    return user


# --------------------------------------------------------------------------- #
#  Endpoints
# --------------------------------------------------------------------------- #
def build_router(db):
    """The router is assembled with an already prepared database — one per process."""

    # ---------------- what the front door should show ----------------
    @router.get("/api/auth/state")
    def auth_state(request: Request):
        """Какую форму показывать. Публичный роут — до входа спросить некому.

        Существует потому, что интерфейс раньше выяснял это методом тыка: бил в
        первый попавшийся роут с данными и разбирал код ответа. Свежая
        инсталляция, открытая по сети, отвечала на это 503 с текстом «зайдите на
        машину сервиса», и человек упирался в стену первым же экраном.
        """
        # Пускают ли этого вызывающего прямо сейчас без всякого входа. На
        # машине инженера — да, и форму первого администратора ему навязывать
        # незачем: локальный режим работал так всегда, и ломать его обновлением
        # нельзя. По сети — нет, и там форма как раз и нужна.
        try:
            from .main import open_install_allowed

            local = open_install_allowed(request) and not any_users(db)
        except Exception:
            local = not any_users(db)

        return {
            "auth": "off" if auth_disabled() else "on",
            "local_mode": bool(auth_disabled() or local),
            # «Инсталляцию ещё можно занять» — форма первого администратора.
            "needs_setup": setup_open(db),
            # Задан ли `VISTEST_SETUP_TOKEN`: форма спросит его, а не будет
            # молча отвергать ввод.
            "setup_token_required": bool(
                (os.getenv("VISTEST_SETUP_TOKEN") or "").strip()),
            "registration": "open" if open_registration(db) else "closed",
        }

    @router.post("/api/auth/setup")
    def setup(response: Response, request: Request, payload: dict = Body(...)):
        """Первый администратор — из браузера, ровно один раз.

        Проверка «ещё можно занять» и закрытие флага стоят вокруг одного
        `create_user`, и порядок здесь имеет значение: флаг закрывается ПОСЛЕ
        создания. Закрыть его раньше значило бы, что упавшее создание оставляет
        инсталляцию без администратора и без способа его завести.
        """
        if not setup_open(db):
            raise HTTPException(
                409, "This installation already has an administrator. "
                     "Sign in, or ask them for an invite.")
        if not setup_token_ok(payload.get("token") or ""):
            audit(db, "—", "setup.refused",
                  (request.client.host if request.client else "") or "?")
            raise HTTPException(403, "Wrong setup token")

        try:
            create_user(db, payload.get("login") or "",
                        payload.get("password") or "",
                        role="admin", name=payload.get("name") or "")
        except ValueError as e:
            raise HTTPException(400, str(e)) from None

        close_setup(db, payload.get("login") or "")
        row = db.one("SELECT id, login, name, role FROM user WHERE login=?",
                     ((payload.get("login") or "").strip().lower(),))
        audit(db, row["login"], "setup.completed",
              (request.client.host if request.client else "") or "?")

        token = open_session(db, row["id"],
                             request.headers.get("user-agent", ""))
        response.set_cookie(
            COOKIE, token, httponly=True, samesite="lax",
            max_age=SESSION_DAYS * 86400, secure=cookie_secure(request))
        return {"login": row["login"], "name": row["name"], "role": row["role"]}

    @router.post("/api/auth/register")
    def register(request: Request, payload: dict = Body(...)):
        """Заявка на доступ. Ничего не даёт, пока её не одобрили.

        Именно поэтому регистрация может быть открытой: заявка — это не доступ,
        а очередь к администратору. Ответ 202, а не 200: запрос принят, но
        ничего ещё не произошло.
        """
        if not open_registration(db):
            raise HTTPException(
                403, "Registration is closed on this installation — "
                     "ask an administrator for an invite.")
        if setup_open(db):
            # Пользователей нет вовсе: заявка ушла бы в очередь, которую некому
            # разобрать. Такой человек — и есть первый администратор.
            raise HTTPException(
                409, "This installation has no administrator yet — "
                     "the first account to be created becomes one.")
        try:
            create_user(db, payload.get("login") or "",
                        payload.get("password") or "",
                        role="viewer", name=payload.get("name") or "",
                        status="pending")
        except ValueError as e:
            raise HTTPException(400, str(e)) from None

        audit(db, (payload.get("login") or "").strip().lower(),
              "register.requested",
              (request.client.host if request.client else "") or "?")
        return JSONResponse(
            status_code=202,
            content={"status": "pending",
                     "message": "The request is in. An administrator has to "
                                "approve it before you can sign in."})

    @router.get("/api/auth/me")
    def me(request: Request, vistest_session: str | None = Cookie(default=None)):
        if auth_disabled() or not any_users(db):
            return {
                "login": "local", "name": "local mode", "role": "admin",
                "anonymous": True,
                "hint": "There are no users — access is open. Create the first "
                        "administrator on the sign-in screen.",
            }
        user = session_user(db, vistest_session)
        if user is None:
            raise HTTPException(401, "Sign in required")
        # Права по проектам едут вместе с ролью: интерфейс гасит кнопки по
        # ним, а кнопка, которая не может сработать, — это не строгий бэкенд,
        # это неправда в интерфейсе.
        from . import rights as _rights
        return {**user, "grants": _rights.grants_of(db, user["login"]),
                "own_project": _own_project()}

    @router.post("/api/auth/login")
    def login(response: Response, request: Request, payload: dict = Body(...)):
        login_name = (payload.get("login") or "").strip().lower()
        password = payload.get("password") or ""
        source = (request.client.host if request.client else "") or "?"

        # Before the hash, not after: a throttled attempt must not cost us
        # 480k PBKDF2 rounds.
        wait = login_blocked(db, login_name, source)
        if wait:
            raise HTTPException(
                429, f"Too many failed attempts. Try again in "
                     f"{wait // 60} minutes.",
                headers={"Retry-After": str(wait)})

        row = db.one(
            "SELECT id, login, name, role, password, active, status FROM user"
            " WHERE login=?", (login_name,))

        # We check the password even when the user is absent: otherwise the
        # response time reveals which logins exist.
        stored = row["password"] if row else hash_password("x" * 12)
        password_ok = verify_password(password, stored)
        ok = password_ok and row and row["active"]

        # Заявка, ждущая одобрения, — это не «неверный пароль». Человек только
        # что зарегистрировался и получил бы ответ, из которого следует, что он
        # ошибся при вводе; он пошёл бы менять пароль вместо того, чтобы ждать.
        #
        # Существование логина этим не выдаётся: сообщение показывается только
        # тогда, когда пароль УЖЕ угадан, то есть тому, кто его и завёл.
        if password_ok and row and row["status"] == "pending":
            raise HTTPException(
                403, "Your request is waiting for an administrator to approve "
                     "it. You will be able to sign in once they do.")

        if not ok:
            # The source address goes into `target`: without it the per-address
            # counter has nothing to count, and login enumeration stays free.
            audit(db, login_name or "—", "login.failed", source)
            raise HTTPException(401, "Wrong login or password")

        if needs_rehash(row["password"]):
            db.execute("UPDATE user SET password=? WHERE id=?",
                       (hash_password(password), row["id"]))

        token = open_session(db, row["id"],
                             request.headers.get("user-agent", ""))
        response.set_cookie(
            COOKIE, token, httponly=True, samesite="lax",
            max_age=SESSION_DAYS * 86400,
            secure=cookie_secure(request))
        # The source goes into `target` here as well as on failure: that row is
        # what the throttling counter resets from, and without a symmetric mark
        # the per-address counter would never clear.
        audit(db, row["login"], "login.ok", source)
        purge_expired(db)
        return {"login": row["login"], "name": row["name"], "role": row["role"]}

    @router.post("/api/auth/logout")
    def logout(response: Response,
               vistest_session: str | None = Cookie(default=None)):
        user = session_user(db, vistest_session)
        close_session(db, vistest_session or "")
        response.delete_cookie(COOKIE)
        if user:
            audit(db, user["login"], "logout")
        return {"ok": True}

    @router.post("/api/auth/password")
    def change_password(payload: dict = Body(...),
                        vistest_session: str | None = Cookie(default=None)):
        user = current_user(db, vistest_session)
        if user.get("anonymous"):
            raise HTTPException(400, "In local mode there are no passwords")

        row = db.one("SELECT password FROM user WHERE id=?", (user["id"],))
        if not verify_password(payload.get("old") or "", row["password"]):
            raise HTTPException(403, "The current password is wrong")
        try:
            set_password(db, user["login"], payload.get("new") or "")
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        audit(db, user["login"], "password.changed")
        return {"ok": True}

    # ---------------- user management ----------------
    @router.get("/api/users")
    def users(vistest_session: str | None = Cookie(default=None)):
        require(db, vistest_session, "admin")
        from . import rights as _rights

        people = list_users(db)
        by_login: dict[str, dict] = {}
        for row in _rights.list_grants(db):
            by_login.setdefault(row["login"], {})[row["project_key"]] = row["role"]
        for person in people:
            person["grants"] = by_login.get(person["login"], {})
        return {"users": people, "roles": list(ROLES),
                "projects": _rights.known(db)}

    @router.put("/api/users/{login}/rights/{project_key:path}")
    def grant_right(login: str, project_key: str, payload: dict = Body(...),
                    vistest_session: str | None = Cookie(default=None)):
        """Выдать право на один проект.

        Право только ДОБАВЛЯЕТ: действующая роль — максимум из глобальной и
        выданной. Понижающего права нет намеренно — иначе администратора можно
        было бы лишить доступа к проекту, а чинить это стало бы некому.
        """
        from . import rights as _rights

        me_ = require(db, vistest_session, "admin")
        if not db.one("SELECT id FROM user WHERE login=?",
                      ((login or "").strip().lower(),)):
            raise HTTPException(404, f"There is no user {login!r}")
        role = _rights.normalize(payload.get("role", ""))
        out = _rights.grant(db, (login or "").strip().lower(), project_key,
                            role, by=me_["login"])
        audit(db, me_["login"], "rights.granted", login,
              project=out["project_key"], role=role)
        return {"ok": True, **out}

    @router.delete("/api/users/{login}/rights/{project_key:path}")
    def revoke_right(login: str, project_key: str,
                     vistest_session: str | None = Cookie(default=None)):
        from . import rights as _rights

        me_ = require(db, vistest_session, "admin")
        gone = _rights.revoke(db, (login or "").strip().lower(), project_key)
        if gone:
            audit(db, me_["login"], "rights.revoked", login, project=project_key)
        return {"ok": True, "removed": gone}

    @router.post("/api/users")
    def add_user(payload: dict = Body(...),
                 vistest_session: str | None = Cookie(default=None)):
        me_ = require(db, vistest_session, "admin")
        try:
            create_user(db, payload.get("login", ""), payload.get("password", ""),
                        role=payload.get("role", "viewer"),
                        name=payload.get("name", ""))
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        audit(db, me_["login"], "user.created", payload.get("login", ""),
              role=payload.get("role", "viewer"))
        return {"ok": True}

    @router.patch("/api/users/{login}")
    def update_user(login: str, payload: dict = Body(...),
                    vistest_session: str | None = Cookie(default=None)):
        me_ = require(db, vistest_session, "admin")

        if payload.get("role"):
            if login.lower() == me_["login"] and payload["role"] != "admin":
                raise HTTPException(
                    400, "You cannot remove the administrator role from yourself — "
                         "the installation would be left without management")
            try:
                set_role(db, login, payload["role"])
            except ValueError as e:
                raise HTTPException(400, str(e)) from None
            audit(db, me_["login"], "user.role", login, role=payload["role"])

        if payload.get("password"):
            try:
                set_password(db, login, payload["password"])
            except ValueError as e:
                raise HTTPException(400, str(e)) from None
            audit(db, me_["login"], "user.password", login)

        if payload.get("active") is False:
            if login.lower() == me_["login"]:
                raise HTTPException(400, "You cannot disable yourself")
            deactivate(db, login)
            audit(db, me_["login"], "user.disabled", login)

        return {"ok": True}

    @router.get("/api/audit")
    def audit_log(limit: int = 200,
                  vistest_session: str | None = Cookie(default=None)):
        require(db, vistest_session, "admin")
        return {"entries": db.query(
            "SELECT * FROM audit ORDER BY at DESC, id DESC LIMIT ?",
            (min(limit, 1000),))}

    # ---------------- заявки на доступ ----------------
    @router.get("/api/users/pending")
    def list_pending(vistest_session: str | None = Cookie(default=None)):
        require(db, vistest_session, "admin")
        return {"pending": pending_users(db)}

    @router.post("/api/users/{login}/approve")
    def approve(login: str, payload: dict = Body(default={}),
                vistest_session: str | None = Cookie(default=None)):
        """Одобрить заявку и сразу назначить роль.

        Роль назначается здесь, а не после: одобрить сначала «как-нибудь», а
        потом идти выставлять роль отдельным действием — это два шага, второй из
        которых забывают, и человек остаётся с правами, которых ему не давали
        осознанно.
        """
        me_ = require(db, vistest_session, "admin")
        role = payload.get("role") or "viewer"
        try:
            ok = approve_user(db, login, role)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        if not ok:
            raise HTTPException(404, "no such request")
        audit(db, me_["login"], "register.approved", login, role=role)
        return {"ok": True, "login": login.strip().lower(), "role": role}

    @router.post("/api/users/{login}/reject")
    def reject(login: str, vistest_session: str | None = Cookie(default=None)):
        me_ = require(db, vistest_session, "admin")
        if not reject_user(db, login):
            raise HTTPException(404, "no such request")
        audit(db, me_["login"], "register.rejected", login)
        return {"ok": True, "login": login.strip().lower()}

    @router.get("/api/settings/registration")
    def read_registration(vistest_session: str | None = Cookie(default=None)):
        require(db, vistest_session, "admin")
        return {"open": open_registration(db), "pending": len(pending_users(db))}

    @router.put("/api/settings/registration")
    def write_registration(payload: dict = Body(...),
                           vistest_session: str | None = Cookie(default=None)):
        from .prefs import set_flag

        me_ = require(db, vistest_session, "admin")
        if payload.get("open") is not None:
            set_flag(db, OPEN_REGISTRATION, bool(payload["open"]), me_["login"])
        result = {"open": open_registration(db), "pending": len(pending_users(db))}
        audit(db, me_["login"], "registration.settings", str(result["open"]))
        return result

    # ---------------- invites ----------------
    @router.get("/api/invites")
    def list_invites(vistest_session: str | None = Cookie(default=None)):
        require(db, vistest_session, "admin")
        rows = db.query("SELECT * FROM invite ORDER BY created_at DESC LIMIT 200")
        for r in rows:
            r["status"] = invite_status(r)
        return {"invites": rows}

    @router.post("/api/invites")
    def add_invite(payload: dict = Body(default={}),
                   vistest_session: str | None = Cookie(default=None)):
        me_ = require(db, vistest_session, "admin")
        try:
            inv = create_invite(db, payload.get("role", "viewer"),
                                 payload.get("name", ""), me_["login"])
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        audit(db, me_["login"], "invite.created", inv["token"][:8], role=inv["role"])
        return inv

    @router.delete("/api/invites/{token}")
    def revoke_invite(token: str,
                      vistest_session: str | None = Cookie(default=None)):
        me_ = require(db, vistest_session, "admin")
        db.execute("DELETE FROM invite WHERE token=? AND used_at IS NULL", (token,))
        audit(db, me_["login"], "invite.revoked", token[:8])
        return {"ok": True}

    @router.get("/api/auth/invite/{token}")
    def check_invite(token: str):
        """Public check of an invite — for the sign in by link page."""
        inv = valid_invite(db, token)
        team = get_team(db)
        if not inv:
            return {"valid": False, "team": team.get("name", "")}
        return {"valid": True, "role": inv["role"], "name": inv["name"],
                "team": team.get("name", "")}

    @router.post("/api/auth/accept-invite")
    def accept_invite(response: Response, request: Request,
                      payload: dict = Body(...)):
        """The invited person sets their own login and password — their account is created."""
        token = payload.get("token") or ""
        inv = valid_invite(db, token)
        if not inv:
            raise HTTPException(400, "The invite link is invalid or has expired")
        try:
            uid = create_user(db, payload.get("login", ""),
                              payload.get("password", ""),
                              role=inv["role"], name=payload.get("name", ""))
        except ValueError as e:
            raise HTTPException(400, str(e)) from None

        login_name = (payload.get("login") or "").strip().lower()
        db.execute("UPDATE invite SET used_at=datetime('now'), used_by=?"
                   " WHERE token=?", (login_name, token))
        token_s = open_session(db, uid, request.headers.get("user-agent", ""))
        response.set_cookie(
            COOKIE, token_s, httponly=True, samesite="lax",
            max_age=SESSION_DAYS * 86400, secure=cookie_secure(request))
        audit(db, login_name, "invite.accepted", role=inv["role"])
        return {"login": login_name, "name": payload.get("name", "") or login_name,
                "role": inv["role"]}

    # ---------------- team profile ----------------
    @router.get("/api/team")
    def team_get():
        return get_team(db)

    @router.put("/api/team")
    def team_put(payload: dict = Body(default={}),
                 vistest_session: str | None = Cookie(default=None)):
        me_ = require(db, vistest_session, "admin")
        res = set_team(db, payload.get("name"), payload.get("brand_color"))
        audit(db, me_["login"], "team.updated", res["name"])
        return res

    return router
