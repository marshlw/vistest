# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The wheel carries the review UI: `pip install "vistest[server]"`, `/ui/` is 200.

From a checkout the interface is found beside the package, in `frontend/`, and
every other test in this suite runs from a checkout. So nothing here would
notice a wheel without it — and that wheel is exactly what a user gets:
`vistest serve` starts, the API answers, and `/ui/` is a 404 with a warning in
a log nobody reads.

So this is done the way a user does it, with nothing borrowed from this
environment: build the wheel from a copy of the sources, install it with the
`server` extra into a fresh virtual environment, start `vistest serve` from an
empty directory — where there is no `frontend/` to stumble on — and ask for the
page. It downloads the server's dependencies, which takes a minute; the
`packaging` mark lets a quick local run leave it out (`-m "not packaging"`).
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import venv
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#  What the build reads. Copied rather than built in place: a build in the
#  checkout leaves `build/` and `*.egg-info` behind, and those are then what
#  the next editable install and the next run of this test pick up.
SOURCES = ("pyproject.toml", "setup.py", "MANIFEST.in", "README.md", "LICENSE",
           "NOTICE", "vistest", "frontend")

#  pip's own words for "could not reach an index". Anything else is a failure.
_OFFLINE = ("Could not find a version that satisfies", "No matching distribution",
            "Failed to establish a new connection", "Temporary failure in name",
            "ProxyError", "ConnectionError", "Network is unreachable")

pytestmark = pytest.mark.packaging


def _bin(env: Path, name: str) -> Path:
    scripts = env / ("Scripts" if os.name == "nt" else "bin")
    return scripts / (name + (".exe" if os.name == "nt" else ""))


def _run(cmd, **kw) -> subprocess.CompletedProcess:
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                          timeout=900, **kw)


def _skip_if_offline(done: subprocess.CompletedProcess, what: str) -> None:
    if done.returncode != 0 and any(w in done.stdout + done.stderr for w in _OFFLINE):
        pytest.skip(f"{what} could not be downloaded here: "
                    + (done.stderr.strip().splitlines() or ["?"])[-1])


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""


@pytest.fixture(scope="module")
def installed(tmp_path_factory):
    """A fresh environment with the wheel and its `server` extra, nothing else."""
    work = tmp_path_factory.mktemp("wheel")
    src = work / "src"
    src.mkdir()
    for name in SOURCES:
        path = ROOT / name
        if not path.exists():
            continue
        if path.is_dir():
            shutil.copytree(path, src / name,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(path, src / name)

    dist = work / "dist"
    #  With build isolation, as a user's pip does it: the build requirements
    #  come from pyproject.toml, not from whatever this environment has.
    built = _run([sys.executable, "-m", "pip", "wheel", "--no-deps",
                  "--disable-pip-version-check", "-w", dist, src])
    _skip_if_offline(built, "the build requirements")
    assert built.returncode == 0, built.stdout + built.stderr
    wheels = list(dist.glob("vistest-*.whl"))
    assert len(wheels) == 1, wheels

    env = work / "env"
    venv.EnvBuilder(with_pip=True, clear=True).create(env)
    python = _bin(env, "python")
    done = _run([python, "-m", "pip", "install", "--disable-pip-version-check",
                 f"{wheels[0]}[server]"])
    _skip_if_offline(done, "the server's dependencies")
    assert done.returncode == 0, done.stdout + done.stderr
    return env, wheels[0]


def test_the_wheel_contains_the_interface(installed):
    import zipfile

    _, wheel = installed
    names = set(zipfile.ZipFile(wheel).namelist())
    assert "vistest/frontend/index.html" in names
    assert "vistest/frontend/ui.css" in names
    shipped_js = {n.rsplit("/", 1)[-1] for n in names
                  if n.startswith("vistest/frontend/js/")}
    in_tree = {p.name for p in (ROOT / "frontend" / "js").glob("*.js")}
    assert shipped_js == in_tree, "every module the page loads, and only those"


def test_ui_is_served_from_an_installed_wheel(installed, tmp_path):
    env, _ = installed
    python = _bin(env, "python")

    #  The installed package, not this checkout, and its own copy of the UI.
    where = _run([python, "-c",
                  "import vistest.api.main as m; print(m.__file__); print(m.FRONTEND)"],
                 cwd=tmp_path, env={**_clean_env(), "VISTEST_ROOT": str(tmp_path / "data")})
    assert where.returncode == 0, where.stdout + where.stderr
    module, frontend = where.stdout.strip().splitlines()[-2:]
    assert Path(module).resolve().is_relative_to(env.resolve()), module
    assert Path(frontend) == Path(module).parents[1] / "frontend", frontend

    port = _free_port()
    server = subprocess.Popen(
        [str(_bin(env, "vistest")), "serve", "--port", str(port)],
        cwd=tmp_path, env={**_clean_env(), "VISTEST_ROOT": str(tmp_path / "data")},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        base = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 60
        while True:
            try:
                if _get(base + "/api/health")[0] == 200:
                    break
            except OSError:
                pass
            if server.poll() is not None or time.monotonic() > deadline:
                server.kill()
                pytest.fail("the server did not come up:\n" + server.stdout.read())
            time.sleep(0.3)

        status, body = _get(base + "/ui/")
        assert status == 200, body[:500]
        assert "<html" in body.lower()
        #  A module the page loads, from the wheel's copy: the page is not
        #  only its shell.
        assert _get(base + "/ui/js/boot.js")[0] == 200
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()


def _clean_env() -> dict:
    """This interpreter's environment minus anything that points at the checkout."""
    drop = ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV")
    return {k: v for k, v in os.environ.items()
            if k not in drop and not k.startswith("VISTEST_")}
