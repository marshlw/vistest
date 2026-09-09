# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""`pip install vistest` must not bring a web server with it.

The library mode's whole proposition is that this package can go into somebody
else's test environment. Their environment already has pins of its own, and
every dependency of ours is one more constraint they have to resolve against —
so the base install is three names, and the server, the AI runtime, the browser
and the directory client are extras.

Two checks, and the second is the one that catches things.

**The declared list** is what `pip` reads. It is asserted against an allowlist
rather than against a list of things to avoid, for the same reason
`test_core_standalone.py` uses one: a denylist silently approves whatever
nobody thought of, and this file exists precisely for what nobody thought of.

**The imports** are what actually happens. A dependency list cannot notice that
somebody added `import requests` to a module on the library path — the name is
already installed in every developer's environment and in CI, so nothing goes
red until a user with a clean environment tries it. So a fresh interpreter
imports the library surface, runs a real check through it, and then looks at
`sys.modules`.
"""

from __future__ import annotations

import json
import subprocess
import sys
from importlib.metadata import entry_points
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#  What `pip install vistest` is allowed to pull. Growing this list is a
#  decision, and the one-line diff is the whole review.
ALLOWED_DEPENDENCIES = {"numpy", "opencv-python-headless", "pillow", "pyyaml"}

#  Named explicitly, although the allowlist already implies them, for the same
#  reason `test_core_standalone.py` keeps its list: a failure that says
#  `fastapi` reads better than one that says a package name nobody recognises,
#  and these are the promise as it is written in the README.
#  `yaml` is on this list although PyYAML is a base dependency, and that is
#  the point: it may be installed, and it still must not be imported by a run
#  that has no `vistest.yaml` to read. The probe below runs in an empty
#  directory, so importing it there would mean the loader stopped being lazy.
FORBIDDEN_MODULES = (
    "fastapi", "uvicorn", "starlette", "yaml", "requests", "playwright",
    "sqlite3", "onnxruntime", "ldap3", "allure",
)


def _pyproject() -> dict:
    try:
        import tomllib
    except ImportError:  # Python 3.10 has no tomllib
        try:
            import tomli as tomllib
        except ImportError:
            pytest.fail(
                "reading pyproject.toml needs tomllib (3.11+) or tomli. "
                "Install the dev extra: pip install -e '.[dev]'")
    return tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))


def _dependencies() -> list[str]:
    return _pyproject()["project"]["dependencies"]


def _name_of(requirement: str) -> str:
    """`opencv-python-headless>=4.8` -> `opencv-python-headless`."""
    for separator in ("[", "<", ">", "=", "!", "~", ";", " "):
        requirement = requirement.split(separator)[0]
    return requirement.strip().lower()


# --------------------------------------------------------------------------- #
#  What is declared
# --------------------------------------------------------------------------- #
def test_the_base_install_declares_only_the_engine_dependencies():
    declared = {_name_of(r) for r in _dependencies()}
    extra = declared - ALLOWED_DEPENDENCIES
    assert not extra, (
        f"{sorted(extra)} appeared in the base install. Anything that is not "
        "the comparison engine belongs in an extra — see the list in "
        "pyproject.toml. If it truly belongs here, add it to "
        "ALLOWED_DEPENDENCIES in this test and say why in the commit.")


def test_the_server_lives_in_its_own_extra():
    extras = _pyproject()["project"]["optional-dependencies"]
    assert "server" in extras
    names = {_name_of(r) for r in extras["server"]}
    assert {"fastapi", "uvicorn"} <= names


def test_the_pytest_plugin_is_declared_as_an_entry_point():
    """Without this the flags do not exist and `expect_screenshot` has no context."""
    raw = _pyproject()
    declared = raw["project"]["entry-points"]["pytest11"]
    assert declared == {"vistest": "vistest.pytest_plugin"}


def test_the_entry_point_resolves_in_this_environment():
    """Declared is not the same as registered; a broken module fails here."""
    found = [e for e in entry_points(group="pytest11")
             if e.value == "vistest.pytest_plugin"]
    if not found:
        pytest.skip("vistest is not installed in this environment "
                    "(no dist metadata); nothing to resolve")
    assert found[0].load() is not None


# --------------------------------------------------------------------------- #
#  What actually gets imported
# --------------------------------------------------------------------------- #
PROBE = r'''
import json, sys, os
sys.path.insert(0, %(root)r)

import numpy as np

import vistest
from vistest import expect_screenshot
from vistest.core import pngio
import vistest.library, vistest.storage, vistest.pytest_plugin
import vistest.report.library

os.environ["VISTEST_UPDATE_BASELINES"] = "1"
png = pngio.encode(np.zeros((8, 12, 3), np.uint8))
expect_screenshot(png, "probe.png")          # writes a baseline
os.environ.pop("VISTEST_UPDATE_BASELINES")
import vistest.library.context as ctx; ctx.uninstall()
expect_screenshot(png, "probe.png")          # and compares against it

print(json.dumps(sorted(m for m in sys.modules if "." not in m)))
'''


def test_a_whole_check_runs_without_importing_the_server(tmp_path: Path):
    """The library surface, exercised end to end, in a clean interpreter.

    Run in an empty directory on purpose: `VisTestConfig.load` searches upwards
    for a `vistest.yaml`, and inside this repository it would find ours and
    import PyYAML — proving nothing about a user's project, which has no such
    file.
    """
    completed = subprocess.run(
        [sys.executable, "-c", PROBE % {"root": str(ROOT)}],
        cwd=tmp_path, capture_output=True, text=True, timeout=300,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(ROOT),
             "SYSTEMROOT": "C:\\Windows"})
    assert completed.returncode == 0, completed.stdout + completed.stderr

    loaded = set(json.loads(completed.stdout.strip().splitlines()[-1]))
    leaked = sorted(set(FORBIDDEN_MODULES) & loaded)
    assert not leaked, (
        f"{leaked} was imported by the library surface. Everything above the "
        "engine is reached lazily — see the `_LAZY` maps in vistest/__init__.py "
        "and vistest/storage/__init__.py — so a new plain import at the top of "
        "a module on this path is what usually breaks it.")

    assert (tmp_path / "tests" / "__vistest__" / "probe.png").exists()
