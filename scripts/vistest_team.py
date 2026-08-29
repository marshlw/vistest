#!/usr/bin/env python3
# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Провижининг изолированных инстансов VisTest — по одному на команду.

Модель изоляции — «инстанс на команду»: свой контейнер, свой том, свой порт
на localhost, за общим обратным прокси. Этот скрипт заводит, перечисляет,
обновляет, бэкапит и убирает такие инстансы одной командой, а также
пересобирает конфиг прокси из списка команд.

    python scripts/vistest_team.py create alpha --admin anna
    python scripts/vistest_team.py list
    python scripts/vistest_team.py proxy --domain vistest.example.com
    python scripts/vistest_team.py backup alpha
    python scripts/vistest_team.py upgrade --all
    python scripts/vistest_team.py remove alpha --purge

Требуется docker с плагином `docker compose`. Ничего, кроме стандартной
библиотеки, скрипт не тянет — работает и в закрытом контуре.
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import NoReturn

REPO = Path(__file__).resolve().parents[1]
DEPLOY = REPO / "deploy"
TEAMS = DEPLOY / "teams"
COMPOSE = DEPLOY / "team.compose.yml"
COMPOSE_FULL = DEPLOY / "team.full.compose.yml"
CONF = DEPLOY / "vistest-teams.conf"
BACKUPS = DEPLOY / "backups"

PORT_BASE = 8481
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,38}$")

C = {"g": "\033[32m", "y": "\033[33m", "r": "\033[31m", "b": "\033[34m", "0": "\033[0m"}


def say(msg: str, color: str = "0") -> None:
    print(f"{C.get(color, '')}{msg}{C['0']}", flush=True)


def die(msg: str) -> NoReturn:
    say(msg, "r")
    raise SystemExit(1)


# --------------------------------------------------------------------------- #
#  Реестр команд — плоские .env файлы под deploy/teams/
# --------------------------------------------------------------------------- #
def read_env(path: Path) -> dict:
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for line in path.read_text("utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        data[k.strip()] = v.strip()
    return data


def write_env(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(f"{k}={v}\n" for k, v in data.items())
    path.write_text(body, encoding="utf-8")


def team_file(team: str) -> Path:
    return TEAMS / f"{team}.env"


def all_teams() -> list[dict]:
    out = []
    if TEAMS.exists():
        for f in sorted(TEAMS.glob("*.env")):
            d = read_env(f)
            if d.get("TEAM"):
                out.append(d)
    return out


def used_ports() -> set[int]:
    ports = set()
    for d in all_teams():
        try:
            ports.add(int(d.get("PORT", 0)))
        except ValueError:
            pass
    return ports


def alloc_port() -> int:
    used = used_ports()
    p = PORT_BASE
    while p in used:
        p += 1
    return p


# --------------------------------------------------------------------------- #
#  docker
# --------------------------------------------------------------------------- #
def have_docker() -> bool:
    try:
        subprocess.run(["docker", "compose", "version"],
                       capture_output=True, check=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def compose_file(team: str) -> Path:
    """Толстый инстанс (с браузером) или лёгкий — по флагу FULL в реестре."""
    return COMPOSE_FULL if read_env(team_file(team)).get("FULL") == "1" else COMPOSE


def compose(team: str, *args: str, env: dict | None = None,
            check: bool = True) -> subprocess.CompletedProcess:
    proc_env = {**os.environ, **read_env(team_file(team)), **(env or {})}
    cmd = ["docker", "compose", "-p", f"vistest-{team}",
           "-f", str(compose_file(team)), "--env-file", str(team_file(team)), *args]
    say("$ " + " ".join(cmd), "b")
    return subprocess.run(cmd, cwd=str(REPO), env=proc_env, check=check)


def container_status(team: str) -> str:
    try:
        out = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Status}}", f"vistest-{team}"],
            capture_output=True, text=True, check=True)
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "not running"


def wait_health(port: int, tries: int = 40) -> bool:
    url = f"http://127.0.0.1:{port}/api/health"
    for _ in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(1.5)
    return False


# --------------------------------------------------------------------------- #
#  Команды
# --------------------------------------------------------------------------- #
def cmd_create(a) -> int:
    team = a.team.lower()
    if not SLUG.match(team):
        die("Team name: latin letters, digits and hyphen, starts with a letter or digit.")
    if team_file(team).exists() and not a.force:
        die(f"Team {team!r} already exists. To recreate it: --force.")

    port = a.port or alloc_port()
    if a.port and a.port in (used_ports() - {a.port}):
        die(f"Port {a.port} is already taken by another team.")

    write_env(team_file(team), {
        "TEAM": team, "PORT": str(port),
        "CPUS": a.cpus, "MEM": a.mem,
        "FULL": "1" if a.full else "0",
        "DOMAIN": a.domain or read_env(CONF).get("BASE_DOMAIN", ""),
    })
    kind = "full (with browser)" if a.full else "light (review only)"
    say(f"Starting the instance of team {team!r} on 127.0.0.1:{port} — {kind}…", "b")
    compose(team, "up", "-d", "--build")

    say("Waiting for the service to become ready…", "b")
    if not wait_health(port):
        say("The service did not answer /api/health in time. "
            "Check: docker logs vistest-" + team, "y")

    # первый администратор
    admin = a.admin or "admin"
    password = a.password or secrets.token_urlsafe(9)
    say(f"Creating administrator {admin!r}…", "b")
    rc = subprocess.run(
        ["docker", "exec", f"vistest-{team}", "python", "-m", "vistest.cli",
         "user", "add", admin, "--role", "admin",
         "--password", password, "--name", admin],
        cwd=str(REPO)).returncode
    regen_proxy(read_env(CONF).get("BASE_DOMAIN", "") or a.domain or "")

    say("", "0")
    say(f"Done. Team {team!r} is up.", "g")
    say(f"  port (localhost):      127.0.0.1:{port}", "0")
    dom = read_env(team_file(team)).get("DOMAIN", "")
    if dom:
        say(f"  address behind proxy:  https://{team}.{dom}/ui/", "0")
    if rc == 0:
        say(f"  admin:                 {admin}", "0")
        if not a.password:
            say(f"  password (shown once): {password}", "y")
    else:
        say("  Could not create the administrator — add one manually:", "y")
        say(f"    docker exec -it vistest-{team} python -m vistest.cli "
            f"user add {admin} --role admin", "y")
    return 0


def cmd_list(_a) -> int:
    teams = all_teams()
    if not teams:
        say("No teams yet. To create one: python scripts/vistest_team.py create <name>", "y")
        return 0
    say(f"{'TEAM':<16}{'PORT':<8}{'STATUS':<14}ADDRESS", "b")
    for d in teams:
        team = d["TEAM"]
        dom = d.get("DOMAIN", "")
        addr = f"https://{team}.{dom}/ui/" if dom else f"http://127.0.0.1:{d.get('PORT','?')}/ui/"
        say(f"{team:<16}{d.get('PORT',''):<8}{container_status(team):<14}{addr}")
    return 0


def cmd_remove(a) -> int:
    team = a.team.lower()
    if not team_file(team).exists():
        die(f"Team {team!r} not found.")
    say(f"Stopping instance {team!r}…", "b")
    compose(team, "down", *(["-v"] if a.purge else []), check=False)
    team_file(team).unlink(missing_ok=True)
    regen_proxy(read_env(CONF).get("BASE_DOMAIN", ""))
    if a.purge:
        say(f"Team {team!r} and its volume have been removed.", "g")
    else:
        say(f"Team {team!r} is stopped and removed from the proxy. The volume is kept "
            f"(vistest-{team}_data). To remove it completely: --purge.", "g")
    return 0


def cmd_upgrade(a) -> int:
    targets = [d["TEAM"] for d in all_teams()] if a.all else [a.team.lower()]
    if not targets or targets == [None]:
        die("Specify a team or --all.")
    for team in targets:
        if not team_file(team).exists():
            say(f"skipping {team!r}: not found", "y")
            continue
        say(f"Upgrading instance {team!r} (rebuilding the image)…", "b")
        compose(team, "up", "-d", "--build", check=False)
    say("Done.", "g")
    return 0


def cmd_backup(a) -> int:
    team = a.team.lower()
    if not team_file(team).exists():
        die(f"Team {team!r} not found.")
    BACKUPS.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = Path(a.out) if a.out else BACKUPS / f"{team}-{ts}.tar.gz"
    out = out.resolve()
    vol = f"vistest-{team}_data"
    say(f"Backing up volume {vol} → {out}", "b")
    rc = subprocess.run(
        ["docker", "run", "--rm", "-v", f"{vol}:/data:ro",
         "-v", f"{out.parent}:/backup", "alpine",
         "tar", "czf", f"/backup/{out.name}", "-C", "/data", "."],
        cwd=str(REPO)).returncode
    if rc != 0:
        die("Could not create the backup (does the volume exist? was the team ever started?).")
    say(f"Done: {out}", "g")
    return 0


def cmd_restore(a) -> int:
    team = a.team.lower()
    src = Path(a.src).resolve()
    if not src.exists():
        die(f"Backup file not found: {src}")
    vol = f"vistest-{team}_data"
    say(f"Restoring {vol} from {src} (current data will be overwritten)…", "y")
    if input("Continue? [y/N] ").strip().lower() not in ("y", "yes", "д", "да"):
        return 1
    subprocess.run(["docker", "volume", "create", vol], capture_output=True)
    rc = subprocess.run(
        ["docker", "run", "--rm", "-v", f"{vol}:/data",
         "-v", f"{src.parent}:/backup", "alpine", "sh", "-c",
         f"rm -rf /data/* /data/..?* /data/.[!.]* 2>/dev/null; "
         f"tar xzf /backup/{src.name} -C /data"],
        cwd=str(REPO)).returncode
    if rc != 0:
        die("Restore failed.")
    if team_file(team).exists():
        compose(team, "up", "-d", check=False)
    say("Done.", "g")
    return 0


def cmd_proxy(a) -> int:
    domain = a.domain or read_env(CONF).get("BASE_DOMAIN", "")
    if not domain:
        die("No domain set. Pass --domain vistest.example.com "
            "or set BASE_DOMAIN in deploy/vistest-teams.conf.")
    if a.domain:
        write_env(CONF, {**read_env(CONF), "BASE_DOMAIN": a.domain})
    regen_proxy(domain)
    say(f"Rebuilt {DEPLOY / 'Caddyfile'} for domain {domain}.", "g")
    say("Reload the proxy: caddy reload --config deploy/Caddyfile", "0")
    return 0


def regen_proxy(domain: str) -> None:
    """Собрать рабочий Caddyfile из списка команд. Без домена — пропускаем."""
    if not domain:
        return
    lines = [
        "# Generated by scripts/vistest_team.py — edits will be overwritten.",
        "# Single entry point: the team subdomain → its isolated instance.",
        "{",
        "\temail admin@" + domain,
        "}",
        "",
    ]
    for d in all_teams():
        team, port = d["TEAM"], d.get("PORT", "")
        if not port:
            continue
        lines += [f"{team}.{domain} {{", f"\treverse_proxy 127.0.0.1:{port}", "}", ""]
    (DEPLOY / "Caddyfile").write_text("\n".join(lines), encoding="utf-8")


# --------------------------------------------------------------------------- #
def main() -> int:
    p = argparse.ArgumentParser(
        prog="vistest_team.py",
        description="Isolated VisTest instances, one per team")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create", help="create a team instance")
    c.add_argument("team")
    c.add_argument("--port", type=int, help="port on localhost (auto by default)")
    c.add_argument("--admin", help="login of the first administrator (admin by default)")
    c.add_argument("--password", help="administrator password (generated and shown by default)")
    c.add_argument("--cpus", default="1.0", help="CPU limit (1.0 by default)")
    c.add_argument("--mem", default="1g", help="memory limit (1g by default)")
    c.add_argument("--full", action="store_true",
                   help="full image with a browser: record baselines and run "
                        "tests inside the instance")
    c.add_argument("--domain", help="base domain for the address behind the proxy")
    c.add_argument("--force", action="store_true", help="recreate if it already exists")

    sub.add_parser("list", help="list teams and their status")

    r = sub.add_parser("remove", help="take down a team instance")
    r.add_argument("team")
    r.add_argument("--purge", action="store_true", help="also delete the data volume")

    u = sub.add_parser("upgrade", help="rebuild the image and upgrade the instance(s)")
    u.add_argument("team", nargs="?")
    u.add_argument("--all", action="store_true", help="all teams")

    b = sub.add_parser("backup", help="back up the team volume to .tar.gz")
    b.add_argument("team")
    b.add_argument("--out", help="path of the backup file")

    rs = sub.add_parser("restore", help="restore the team volume from a backup")
    rs.add_argument("team")
    rs.add_argument("src", help=".tar.gz file")

    px = sub.add_parser("proxy", help="rebuild deploy/Caddyfile from the team list")
    px.add_argument("--domain", help="base domain (saved in vistest-teams.conf)")

    a = p.parse_args()
    if a.cmd not in ("list",) and not have_docker():
        die("Could not find docker with the `docker compose` plugin.")

    handlers = {
        "create": cmd_create, "list": cmd_list, "remove": cmd_remove,
        "upgrade": cmd_upgrade, "backup": cmd_backup, "restore": cmd_restore,
        "proxy": cmd_proxy,
    }
    try:
        return handlers[a.cmd](a)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
