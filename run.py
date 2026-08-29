#!/usr/bin/env python3
# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Единая точка входа. Работает одинаково на Windows и Linux/macOS.

    python run.py setup        поставить зависимости и браузеры
    python run.py ui           поднять сервис и открыть ревью-UI
    python run.py test [args]  прогнать визуальные тесты (аргументы уходят в pytest)
    python run.py update       перезаписать эталоны
    python run.py record       открыть браузер и наснимать эталоны мышью
    python run.py record --pom   то же, но с генерацией page objects
    python run.py snap URL     снять эталон по адресу без написания теста
    python run.py check NAME IMG   проверить готовый PNG (для чужих тестов)
    python run.py compare a.png b.png
    python run.py docker       то же самое, но в контейнере
    python run.py doctor       проверить окружение
    python run.py doctor URL   измерить собственный шум проекта
    python run.py push         догрузить прогоны, накопленные офлайн

Скрипт сам создаёт venv, поэтому запускать можно системным Python.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
IS_WIN = platform.system() == "Windows"
HOST, PORT = "127.0.0.1", 8420

C = {"g": "\033[32m", "y": "\033[33m", "r": "\033[31m", "b": "\033[34m", "0": "\033[0m"}
if IS_WIN and not os.getenv("WT_SESSION"):
    os.system("")  # включить ANSI в старом cmd.exe


def say(msg: str, color: str = "0") -> None:
    print(f"{C.get(color, '')}{msg}{C['0']}", flush=True)


# --------------------------------------------------------------------------- #
#  venv
# --------------------------------------------------------------------------- #
def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if IS_WIN else "bin/python")


def ensure_venv(quiet: bool = False) -> Path:
    py = venv_python()
    if py.exists():
        return py
    say("Creating virtual environment…", "b")
    subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
    py = venv_python()
    subprocess.run([str(py), "-m", "pip", "install", "-q", "--upgrade", "pip"], check=True)
    install_deps(py, quiet=quiet)
    return py


def install_deps(py: Path, *, extras: str = "[full]", quiet: bool = False) -> None:
    say("Installing dependencies…", "b")
    cmd = [str(py), "-m", "pip", "install", "-e", f".{extras}"]
    if quiet:
        cmd.insert(4, "-q")
    subprocess.run(cmd, cwd=str(ROOT), check=True)
    say("Installing Playwright browsers…", "b")
    subprocess.run([str(py), "-m", "playwright", "install", "chromium"],
                   cwd=str(ROOT), check=False)
    if not IS_WIN:
        # На Linux нужны системные библиотеки; на CI это делает образ.
        subprocess.run([str(py), "-m", "playwright", "install-deps", "chromium"],
                       cwd=str(ROOT), check=False)


# --------------------------------------------------------------------------- #
#  команды
# --------------------------------------------------------------------------- #
def cmd_setup(_args) -> int:
    py = ensure_venv()
    say(f"Done. Interpreter: {py}", "g")
    return 0


def cmd_ui(args) -> int:
    py = ensure_venv()
    env = {**os.environ, "VISTEST_ROOT": str(ROOT / ".vistest")}
    url = f"http://{args.host}:{args.port}/ui/"

    proc = subprocess.Popen(
        [str(py), "-m", "uvicorn", "vistest.api.main:app",
         "--host", args.host, "--port", str(args.port)],
        cwd=str(ROOT), env=env,
    )
    if not args.no_browser:
        threading.Timer(2.0, lambda: webbrowser.open(url)).start()
    say(f"Review UI: {url}   (Ctrl+C to stop)", "g")
    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
    return 0


def cmd_test(args) -> int:
    py = ensure_venv()
    env = {**os.environ, "VISTEST_ROOT": str(ROOT / ".vistest")}
    if args.api:
        env["VISTEST_API_URL"] = args.api
    if args.update:
        env["VISTEST_UPDATE_BASELINES"] = "1"

    cmd = [str(py), "-m", "pytest"] + (args.pytest_args or ["tests/"])
    if args.preset:
        cmd += ["--vistest-preset", args.preset]
    say("$ " + " ".join(cmd), "b")
    return subprocess.run(cmd, cwd=str(ROOT), env=env).returncode


def cmd_update(args) -> int:
    args.update = True
    return cmd_test(args)


def cmd_compare(args) -> int:
    py = ensure_venv()
    code = (
        "import sys, json; from vistest import VisualTester;"
        "t=VisualTester();"
        "r=None\n"
        "try:\n"
        "    r=t.compare_files(sys.argv[1], sys.argv[2])\n"
        "except Exception as e:\n"
        "    r=getattr(e,'result',None)\n"
        "    print(e)\n"
        "print('artifacts:', json.dumps(getattr(r,'artifacts',{}),\n"
        "                                indent=2, ensure_ascii=False))"
    )
    return subprocess.run([str(py), "-c", code, args.expected, args.actual],
                          cwd=str(ROOT)).returncode


def cmd_passthrough(args) -> int:
    """Команды, которые целиком реализованы в CLI пакета."""
    py = ensure_venv()
    env = {**os.environ, "VISTEST_ROOT": str(ROOT / ".vistest")}
    cmd = [str(py), "-m", "vistest.cli", args.cmd, *args.rest]
    say("$ " + " ".join(cmd), "b")
    return subprocess.run(cmd, cwd=str(ROOT), env=env).returncode


def cmd_push(_args) -> int:
    py = ensure_venv()
    code = (
        "from pathlib import Path; from vistest.config import VisTestConfig;"
        "from vistest.client import push_pending;"
        "cfg=VisTestConfig.load();"
        "print('runs pushed:', push_pending(cfg.runs_path(), cfg.service))"
    )
    return subprocess.run([str(py), "-c", code], cwd=str(ROOT)).returncode


def cmd_docker(args) -> int:
    if not shutil.which("docker"):
        say("Docker not found in PATH", "r")
        return 1
    compose = ["docker", "compose", "-f", str(ROOT / "docker" / "docker-compose.yml")]
    if args.docker_cmd == "test":
        return subprocess.run(compose + ["run", "--rm", "runner"], cwd=str(ROOT)).returncode
    if args.docker_cmd == "down":
        return subprocess.run(compose + ["down"], cwd=str(ROOT)).returncode
    rc = subprocess.run(compose + ["up", "-d", "api"], cwd=str(ROOT)).returncode
    if rc == 0:
        url = f"http://localhost:{PORT}/ui/"
        say(f"Service is up: {url}", "g")
        if not args.no_browser:
            webbrowser.open(url)
    return rc


def cmd_doctor(args) -> int:
    """Две разные проверки под одним именем — и это намеренно.

        run.py doctor              что не так с МАШИНОЙ
        run.py doctor <url>        что не так с ПРОЕКТОМ

    Первый вопрос человек задаёт один раз после установки, второй — каждый раз,
    когда заводит новый набор снимков.
    """
    if getattr(args, "rest", None):
        return cmd_passthrough(args)

    say("=== Environment ===", "b")
    print(f"  OS:            {platform.system()} {platform.release()}")
    print(f"  Python:        {sys.version.split()[0]} ({sys.executable})")
    print(f"  Project:       {ROOT}")
    print(f"  venv:          {'yes' if venv_python().exists() else 'no'}")
    print(f"  docker:        {shutil.which('docker') or 'not found'}")
    print(f"  git:           {shutil.which('git') or 'not found'}")

    py = venv_python() if venv_python().exists() else Path(sys.executable)

    # Откуда venv грузит сам пакет. Если это копия в site-packages, а не
    # каталог репозитория, правки кода до сервиса не доезжают — и перезапуск
    # не помогает. По симптомам это не диагностируется никак.
    say("\n=== Package installation ===", "b")
    probe_pkg = (
        "import vistest, pathlib, json;"
        "p=str(pathlib.Path(vistest.__file__).resolve().parent);"
        "print(json.dumps({'path': p,"
        " 'copy': 'site-packages' in p.replace('\\\\','/').split('/')}))"
    )
    try:
        out = subprocess.run([str(py), "-c", probe_pkg],
                             capture_output=True, text=True)
        info = json.loads(out.stdout or "{}")
        print(f"  vistest:       {info.get('path', '?')}")
        if info.get("copy"):
            say("  WARNING: the package is installed as a copy, not linked to the repo.",
                "r")
            say("  Code edits will not reach the service even after a restart.", "r")
            say(f"  Fix:  {py} -m pip install -e {ROOT}", "y")
        else:
            say("  linked to the repo — edits apply right away", "g")
    except Exception as e:
        say(f"  could not check: {e}", "y")

    say("\n=== Dependencies ===", "b")
    probe = (
        "import json,importlib;"
        "mods=['numpy','cv2','PIL','playwright','fastapi','uvicorn',"
        "'onnxruntime','pytest','yaml','requests','allure'];"
        "print(json.dumps({m: bool(importlib.util.find_spec(m)) for m in mods}))"
    )
    try:
        out = subprocess.run([str(py), "-c", probe], capture_output=True, text=True)
        for mod, ok in json.loads(out.stdout or "{}").items():
            mark = f"{C['g']}yes{C['0']}" if ok else f"{C['y']}no{C['0']}"
            required = mod in ("numpy", "cv2", "PIL")
            note = "  (required)" if required and not ok else ""
            print(f"  {mod:14s} {mark}{note}")
    except Exception as e:
        say(f"  could not check: {e}", "r")

    say("\n=== Baselines ===", "b")
    base = ROOT / ".vistest" / "baselines"
    if base.exists():
        for d in sorted(base.iterdir()):
            if d.is_dir():
                n = len([x for x in d.iterdir() if (x / "baseline.png").exists()])
                print(f"  {d.name:24s} {n} snapshots")
    else:
        print("  none yet")

    say("\nHint: only docker mode gives identical baselines across machines.", "y")
    say("To check the project itself, not the machine — how much noise it has:", "y")
    say("  python run.py doctor https://my-app.local", "y")
    return 0


# --------------------------------------------------------------------------- #
def main() -> int:
    p = argparse.ArgumentParser(prog="run.py", description="VisTest launcher")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("setup", help="install dependencies and browsers")

    ui = sub.add_parser("ui", help="start the service and open the UI")
    ui.add_argument("--host", default=HOST)
    ui.add_argument("--port", type=int, default=PORT)
    ui.add_argument("--no-browser", action="store_true")

    for name, help_ in (("test", "run the tests"), ("update", "overwrite the baselines")):
        t = sub.add_parser(name, help=help_)
        t.add_argument("pytest_args", nargs="*")
        t.add_argument("--preset", choices=["strict", "balanced", "loose"])
        t.add_argument("--api", default=os.getenv("VISTEST_API_URL"))
        t.add_argument("--update", action="store_true")

    cmp_ = sub.add_parser("compare", help="compare two PNGs without a browser")
    cmp_.add_argument("expected")
    cmp_.add_argument("actual")

    # Команды, которые просто проксируются в vistest.cli со всеми аргументами
    for name, help_ in (
        ("record", "open the browser with the panel and capture baselines"),
        ("snap", "capture baseline(s) by URL"),
        ("check", "check a ready PNG against the baseline"),
        ("list", "list the baselines"),
        ("rm", "delete baseline(s)"),
        ("prune", "delete old runs and free up space"),
        ("codegen", "rebuild the tests from baseline passports"),
        ("project", "test suites connected from outside"),
        ("report", "offline HTML report on a run"),
        ("evidence", "accumulated quality statistics"),
    ):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("rest", nargs=argparse.REMAINDER)

    d = sub.add_parser("docker", help="run in a container")
    d.add_argument("docker_cmd", nargs="?", default="up", choices=["up", "test", "down"])
    d.add_argument("--no-browser", action="store_true")

    sub.add_parser("push", help="upload offline runs to the service")

    doc = sub.add_parser(
        "doctor", help="without arguments — check the environment; with a URL — project noise")
    doc.add_argument("rest", nargs=argparse.REMAINDER)

    args = p.parse_args()
    if not args.cmd:
        # Двойной клик по файлу — самый ожидаемый сценарий: поднять UI.
        args = p.parse_args(["ui"])

    handlers = {
        "setup": cmd_setup, "ui": cmd_ui, "test": cmd_test, "update": cmd_update,
        "compare": cmd_compare, "docker": cmd_docker, "doctor": cmd_doctor,
        "push": cmd_push,
        "record": cmd_passthrough, "snap": cmd_passthrough,
        "check": cmd_passthrough, "list": cmd_passthrough,
        "rm": cmd_passthrough, "prune": cmd_passthrough,
        "codegen": cmd_passthrough, "project": cmd_passthrough,
        "report": cmd_passthrough, "evidence": cmd_passthrough,
    }
    try:
        return handlers[args.cmd](args)
    except KeyboardInterrupt:
        return 130
    except subprocess.CalledProcessError as e:
        say(f"Command exited with code {e.returncode}", "r")
        return e.returncode


if __name__ == "__main__":
    sys.exit(main())
