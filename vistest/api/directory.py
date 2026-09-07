# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Signing in against a corporate directory: LDAP and Active Directory.

Why this exists. It is the first question asked after a demo by anyone with
more than ten engineers, and the reason is not convenience. A company that
already has a directory has a leaving process built around it: an account is
disabled in one place, and everything the person could reach stops working.
A tool with its own separate list of logins and passwords quietly opts out of
that process — and the way you find out is an ex-employee still approving
baselines four months later.

How it works here, and what each decision costs.

**Local accounts keep working, always.** The directory is checked second, not
first. If it is unreachable — a certificate expired, someone moved the server,
the VPN is down — an administrator with a local password can still get in and
turn it off. An installation that can only be entered through a service that
is down is an installation nobody can fix.

**A person appears on first sign-in.** There is no import and no sync job. The
directory answers «is this the right password» and «which groups is this person
in»; from that, a local row is created with a role, so that everything else in
the service — the audit trail, per-project rights, review claims — keeps
working on one kind of user. The row carries `source='ldap'` and no usable
password hash: it cannot be used to sign in locally, and the password never
travels anywhere but to the directory itself.

**The role comes from group membership, on every sign-in.** Not once at
creation: a person moved out of the reviewers group should stop being a
reviewer at their next sign-in, not at the next time someone remembers. The
exception is deliberate — a role RAISED by hand in VisTest is kept, because
otherwise «make Anna an admin here» would silently undo itself.

**We do not store the service account password in the database.** It goes in
the environment (`VISTEST_LDAP_BIND_PASSWORD`) or in `secrets.env`. The
database travels in backups; a password that can read the whole directory
should not travel with it.

`ldap3` is a pure-Python client — no compiler, no system libraries, installs
inside a closed perimeter. Without it the feature is simply off, and says so.
"""

from __future__ import annotations

import logging
import os
import ssl
from dataclasses import dataclass, field

log = logging.getLogger("vistest.directory")

ENV_BIND_PASSWORD = "VISTEST_LDAP_BIND_PASSWORD"

#  Settings live in the `setting` table under these names, global scope.
PREFIX = "ldap_"
ENABLED = PREFIX + "enabled"
SERVER = PREFIX + "server"
BASE_DN = PREFIX + "base_dn"
BIND_DN = PREFIX + "bind_dn"
USER_FILTER = PREFIX + "user_filter"
NAME_ATTR = PREFIX + "name_attr"
GROUP_ATTR = PREFIX + "group_attr"
ROLE_MAP = PREFIX + "role_map"
DEFAULT_ROLE = PREFIX + "default_role"
TLS_MODE = PREFIX + "tls"
TIMEOUT = PREFIX + "timeout_s"

#  Active Directory answers to `sAMAccountName`; OpenLDAP to `uid`. AD is the
#  common case in the segment this is sold into, so it is the default — and
#  the field is right there in the form for everyone else.
DEFAULTS = {
    ENABLED: "0",
    SERVER: "",
    BASE_DN: "",
    BIND_DN: "",
    USER_FILTER: "(sAMAccountName={login})",
    NAME_ATTR: "displayName",
    GROUP_ATTR: "memberOf",
    ROLE_MAP: "",          # "CN=QA Leads,...=admin\nCN=QA,...=reviewer"
    DEFAULT_ROLE: "viewer",
    TLS_MODE: "starttls",  # starttls | ldaps | plain
    TIMEOUT: "8",
}

ROLES = ("viewer", "reviewer", "admin")
_RANK = {role: i for i, role in enumerate(ROLES)}


class DirectoryError(RuntimeError):
    """The directory could not answer. Not the same as «wrong password»."""


@dataclass
class Settings:
    enabled: bool = False
    server: str = ""
    base_dn: str = ""
    bind_dn: str = ""
    user_filter: str = DEFAULTS[USER_FILTER]
    name_attr: str = DEFAULTS[NAME_ATTR]
    group_attr: str = DEFAULTS[GROUP_ATTR]
    role_map: dict[str, str] = field(default_factory=dict)
    default_role: str = "viewer"
    tls: str = "starttls"
    timeout_s: float = 8.0

    @property
    def configured(self) -> bool:
        return bool(self.server and self.base_dn)

    def to_dict(self, *, bind_password_set: bool = False) -> dict:
        return {
            "enabled": self.enabled,
            "server": self.server,
            "base_dn": self.base_dn,
            "bind_dn": self.bind_dn,
            "user_filter": self.user_filter,
            "name_attr": self.name_attr,
            "group_attr": self.group_attr,
            "role_map": "\n".join(f"{k}={v}" for k, v in self.role_map.items()),
            "default_role": self.default_role,
            "tls": self.tls,
            "timeout_s": self.timeout_s,
            "configured": self.configured,
            "available": available(),
            # Пароль сервисного аккаунта наружу не отдаётся никогда — только
            # признак, что он задан. Иначе экран настроек стал бы способом его
            # прочитать любому, кто дотянулся до сессии администратора.
            "bind_password_set": bind_password_set,
            "bind_password_env": ENV_BIND_PASSWORD,
        }


def available() -> bool:
    """Is the client library installed at all."""
    try:
        import ldap3  # noqa: F401
    except ImportError:
        return False
    return True


def parse_role_map(raw: str) -> dict[str, str]:
    """«group=role» per line → mapping.

    Case is folded on the group side: directories are case-insensitive about
    distinguished names, and a mapping that silently fails to match because
    someone typed `cn=` instead of `CN=` is a support call that takes an hour
    to get to the bottom of.
    """
    out: dict[str, str] = {}
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        group, _, role = line.rpartition("=")
        role = role.strip().lower()
        group = group.strip().lower()
        if group and role in ROLES:
            out[group] = role
    return out


def settings(db) -> Settings:
    from .prefs import get_text

    def value(name: str) -> str:
        return get_text(db, name, DEFAULTS[name])

    try:
        timeout = float(value(TIMEOUT) or 8)
    except ValueError:
        timeout = 8.0

    role = value(DEFAULT_ROLE).lower()
    return Settings(
        enabled=value(ENABLED) in ("1", "true", "yes", "on"),
        server=value(SERVER).strip(),
        base_dn=value(BASE_DN).strip(),
        bind_dn=value(BIND_DN).strip(),
        user_filter=value(USER_FILTER).strip() or DEFAULTS[USER_FILTER],
        name_attr=value(NAME_ATTR).strip() or DEFAULTS[NAME_ATTR],
        group_attr=value(GROUP_ATTR).strip() or DEFAULTS[GROUP_ATTR],
        role_map=parse_role_map(value(ROLE_MAP)),
        default_role=role if role in ROLES else "viewer",
        tls=value(TLS_MODE).strip().lower() or "starttls",
        timeout_s=timeout,
    )


def save_settings(db, payload: dict, who: str = "") -> Settings:
    """Store what the form sent. Missing keys are left alone."""
    from .prefs import set_text

    fields = {
        ENABLED: lambda v: "1" if v else "0",
        SERVER: str, BASE_DN: str, BIND_DN: str, USER_FILTER: str,
        NAME_ATTR: str, GROUP_ATTR: str, ROLE_MAP: str,
        DEFAULT_ROLE: lambda v: str(v).lower(),
        TLS_MODE: lambda v: str(v).lower(),
        TIMEOUT: lambda v: str(float(v)),
    }
    for name, cast in fields.items():
        key = name[len(PREFIX):]
        if key in payload and payload[key] is not None:
            set_text(db, name, cast(payload[key]), who)
    return settings(db)


def bind_password() -> str:
    """Пароль сервисного аккаунта — только из окружения."""
    return (os.getenv(ENV_BIND_PASSWORD) or "").strip()


# --------------------------------------------------------------------------- #
#  Talking to the directory
# --------------------------------------------------------------------------- #
def _escape(value: str) -> str:
    """RFC 4515 escaping for a value going into a search filter.

    Without this a login of `*` matches every account in the directory, and
    `)(uid=admin` rewrites the filter itself — the LDAP equivalent of SQL
    injection, and the reason the filter is a template with one placeholder
    rather than a string the caller assembles.
    """
    out = []
    for ch in value or "":
        if ch in "\\*()\0":
            out.append(f"\\{ord(ch):02x}")
        else:
            out.append(ch)
    return "".join(out)


def _connect(cfg: Settings, dn: str = "", password: str = ""):
    """A bound connection, or a DirectoryError explaining what went wrong."""
    try:
        from ldap3 import ALL, Connection, Server, Tls
        from ldap3.core.exceptions import LDAPException
    except ImportError:
        raise DirectoryError(
            "Signing in against a directory needs the `ldap3` package: "
            "pip install 'vistest[ldap]'") from None

    tls = None
    if cfg.tls in ("starttls", "ldaps"):
        # Проверка сертификата включена. Отключаемого переключателя здесь нет
        # намеренно: «поставим галочку, пока не выпишут нормальный сертификат»
        # переживает любой срок и превращает шифрование в декорацию — пароль
        # уезжает тому, кто встал посередине.
        tls = Tls(validate=ssl.CERT_REQUIRED)

    try:
        server = Server(cfg.server, use_ssl=(cfg.tls == "ldaps"),
                        tls=tls, get_info=ALL,
                        connect_timeout=cfg.timeout_s)
        conn = Connection(server, user=dn or None, password=password or None,
                          auto_bind=False, receive_timeout=cfg.timeout_s,
                          raise_exceptions=False)
        if cfg.tls == "starttls":
            if not conn.start_tls():
                raise DirectoryError(
                    f"StartTLS refused by {cfg.server}: {conn.result}")
        if not conn.bind():
            return None, conn
        return conn, conn
    except DirectoryError:
        raise
    except LDAPException as e:
        raise DirectoryError(f"{cfg.server} did not answer: {e}") from None


def find_user(cfg: Settings, login: str) -> dict | None:
    """Look the person up with the service account. None — no such person."""
    conn, _ = _connect(cfg, cfg.bind_dn, bind_password())
    if conn is None:
        raise DirectoryError(
            "The service account could not sign in to the directory. Check "
            f"{BIND_DN} and {ENV_BIND_PASSWORD}.")
    try:
        filt = cfg.user_filter.replace("{login}", _escape(login))
        attrs = [a for a in (cfg.name_attr, cfg.group_attr, "mail") if a]
        conn.search(cfg.base_dn, filt, attributes=attrs)
        if not conn.entries:
            return None
        entry = conn.entries[0]

        def one(attr: str) -> str:
            try:
                value = entry[attr].value
            except Exception:
                return ""
            if isinstance(value, list):
                return str(value[0]) if value else ""
            return str(value or "")

        groups: list[str] = []
        if cfg.group_attr:
            try:
                raw = entry[cfg.group_attr].value
            except Exception:
                raw = None
            if isinstance(raw, list):
                groups = [str(g) for g in raw]
            elif raw:
                groups = [str(raw)]

        return {"dn": str(entry.entry_dn),
                "name": one(cfg.name_attr),
                "mail": one("mail"),
                "groups": groups}
    finally:
        try:
            conn.unbind()
        except Exception:                                    # pragma: no cover
            pass


def check_password(cfg: Settings, dn: str, password: str) -> bool:
    """Bind as the person: the only way to verify a password."""
    if not password:
        # Пустой пароль в LDAP — это анонимный bind, и он УДАЁТСЯ. То есть без
        # этой строки любой логин из каталога пускался бы без пароля вовсе.
        return False
    conn, _ = _connect(cfg, dn, password)
    if conn is None:
        return False
    try:
        conn.unbind()
    except Exception:                                        # pragma: no cover
        pass
    return True


def role_for(cfg: Settings, groups: list[str]) -> str:
    """Highest role among the person's groups, or the default one."""
    best = cfg.default_role
    for group in groups:
        mapped = cfg.role_map.get(str(group).strip().lower())
        if mapped and _RANK[mapped] > _RANK.get(best, -1):
            best = mapped
    return best


def authenticate(cfg: Settings, login: str, password: str) -> dict | None:
    """Verified person, or None if the directory says no.

    Raises `DirectoryError` when the directory could not be asked at all —
    the caller must tell those two apart, because one means «wrong password»
    and the other means «your directory is down», and showing the first when
    the second is true sends a whole team to reset passwords they never got
    wrong.
    """
    if not cfg.enabled or not cfg.configured:
        return None
    person = find_user(cfg, login)
    if person is None:
        return None
    if not check_password(cfg, person["dn"], password):
        return None
    person["role"] = role_for(cfg, person["groups"])
    return person


def probe(cfg: Settings, login: str = "") -> dict:
    """“Test connection” for the settings screen.

    Reports what it managed to do, step by step, because «it does not work» is
    not something anyone can act on. `login` is optional: with it the answer
    also says whether that person is found and what role they would get.
    """
    result = {"ok": False, "steps": [], "error": ""}

    def step(text: str) -> None:
        result["steps"].append(text)

    if not available():
        result["error"] = ("The `ldap3` package is not installed: "
                           "pip install 'vistest[ldap]'")
        return result
    if not cfg.configured:
        result["error"] = "Fill in the server address and the base DN first"
        return result

    try:
        step(f"connecting to {cfg.server} ({cfg.tls})")
        conn, _ = _connect(cfg, cfg.bind_dn, bind_password())
        if conn is None:
            result["error"] = ("The service account could not sign in. Check "
                               f"the bind DN and {ENV_BIND_PASSWORD}.")
            return result
        step("the service account signed in")
        try:
            conn.unbind()
        except Exception:                                    # pragma: no cover
            pass

        if login:
            person = find_user(cfg, login)
            if person is None:
                result["error"] = f"«{login}» was not found under {cfg.base_dn}"
                return result
            step(f"found {person['dn']}")
            step(f"groups: {len(person['groups'])}")
            step(f"the role would be «{role_for(cfg, person['groups'])}»")

        result["ok"] = True
        return result
    except DirectoryError as e:
        result["error"] = str(e)
        return result
