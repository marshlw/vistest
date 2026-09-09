# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The comparison core imports on its own, and drags nothing along.

This is the test that makes the library mode possible to promise. A package
somebody drops into their own project has to bring the engine and only the
engine: a test run that installs a web framework, opens an sqlite connection or
loads an ONNX runtime because it wanted to compare two PNGs is not a library,
it is a service with a different entry point.

Two entry points are checked, and both matter:

* `import vistest.core` — the engine on its own, which is what the library mode
  will actually import;
* `import vistest` — the package root. It is checked separately because it is
  what a person types first, and because it is where the guarantee is easiest
  to lose: one plain `from .service import CheckService` at the top of
  `__init__.py` and the root drags in the orchestrator, the baseline storage
  and the Playwright capture module. That is exactly what it used to do. The
  names are still reachable, through `__getattr__`; the modules behind them are
  not loaded until somebody asks.

The rule is an **allowlist**, not a list of things to avoid. A denylist quietly
approves everything nobody thought to forbid, and this test exists to be a
tripwire for exactly the imports nobody thought about. When a module genuinely
belongs to the engine it is added to `ALLOWED_PREFIXES` below, and that line is
the whole review: one line in a diff saying the surface of the core has grown,
and by what.

`FORBIDDEN_PACKAGES` is kept alongside it — the allowlist already implies them
— for two reasons. A failure that names `fastapi` reads better than one that
names a stray `vistest.api.main` several steps away from the cause. And those
six names are the promise as it is written down for users, so they are worth
asserting in the words the promise uses.

Everything runs in a fresh interpreter. Checking `sys.modules` inside the
pytest process would prove nothing — by then the whole suite has imported
everything.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#  What the engine is allowed to consist of. Anything else under `vistest.`
#  that gets loaded is a failure, whether or not anybody thought of it.
#
#  Two lists rather than one, and the distinction is the whole point: `vistest`
#  is allowed as a module and NOT as a subtree. Written as a single list of
#  prefixes, the entry `"vistest"` matches `vistest.anything` and the test
#  silently approves the entire package — which is exactly what it did on the
#  first attempt, and it passed while `vistest/__init__.py` was importing the
#  service eagerly.
#
#  `vistest` itself is the package root: importing any submodule runs it.
#  `vistest.models` is the domain dataclasses — stdlib only, no behaviour.
ALLOWED_MODULES = (
    "vistest",
    "vistest.models",
)

#  These packages and everything under them.
ALLOWED_PACKAGES = (
    "vistest.core",
)

#  Third-party packages the engine must never pull in. Each is a promise to a
#  different audience: no web framework and no database for someone embedding
#  the engine, no ldap3 for an installation without a directory, no onnxruntime
#  for a machine with no models on it.
FORBIDDEN_PACKAGES = (
    "fastapi", "uvicorn", "starlette", "sqlite3", "ldap3", "onnxruntime",
)

#  Both entry points get both checks.
ENTRY_POINTS = ("vistest.core", "vistest")

_PROBE = """
import json, sys
import {entry}

forbidden = {forbidden!r}
loaded = set(sys.modules)
print(json.dumps({{
    "packages": sorted(p for p in forbidden if p in loaded),
    "vistest": sorted(m for m in loaded
                      if m == "vistest" or m.startswith("vistest.")),
}}))
"""


def _import_in_a_clean_process(entry: str) -> dict:
    probe = _PROBE.format(entry=entry, forbidden=FORBIDDEN_PACKAGES)
    done = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, (
        f"`import {entry}` did not work on its own:\n"
        f"{done.stdout}\n{done.stderr}")
    return json.loads(done.stdout.strip().splitlines()[-1])


def _outside_the_allowlist(loaded) -> list[str]:
    return [m for m in loaded
            if m not in ALLOWED_MODULES
            and not any(m == package or m.startswith(package + ".")
                        for package in ALLOWED_PACKAGES)]


@pytest.mark.parametrize("entry", ENTRY_POINTS)
def test_it_pulls_in_no_forbidden_package(entry):
    seen = _import_in_a_clean_process(entry)
    assert not seen["packages"], (
        f"importing {entry} pulled in {', '.join(seen['packages'])}. The "
        "engine has to work inside somebody else's test run, where none of "
        "that is installed. Whatever needs it belongs above the core, or "
        "behind an import inside a function.")


@pytest.mark.parametrize("entry", ENTRY_POINTS)
def test_it_loads_nothing_outside_the_allowlist(entry):
    seen = _import_in_a_clean_process(entry)
    stray = _outside_the_allowlist(seen["vistest"])
    assert not stray, (
        f"importing {entry} also loaded {', '.join(stray)}. Those are the "
        "layers above the engine — storage, capture, artifacts, the API, the "
        "config loader. The core is given what it needs as an argument; it "
        "does not reach up for it.\n\n"
        "If one of them genuinely belongs to the engine now, move it under "
        "vistest/core/ and add it to ALLOWED_MODULES or ALLOWED_PACKAGES — "
        "that line in the diff is the point of this test.")


def test_the_allowlist_describes_modules_that_exist():
    """An entry nobody can load any more is a rule that stopped being checked."""
    root = ROOT / "vistest"
    for name in (*ALLOWED_MODULES, *ALLOWED_PACKAGES):
        rest = name.removeprefix("vistest").lstrip(".")
        target = root.joinpath(*rest.split(".")) if rest else root
        assert target.is_dir() or target.with_suffix(".py").is_file(), (
            f"the allowlist names {name!r}, and there is no such module. "
            "Either it was renamed and the allowlist was not, or the entry is "
            "left over from a move.")


def test_the_allowlist_does_not_approve_the_whole_package():
    """The guard on the mistake this test made once.

    `vistest` has to be allowed — every submodule import runs the package root
    — but allowing it as a *prefix* approves everything under it, and then the
    test passes no matter what the core drags in. This asserts the shape of the
    rule, not the behaviour of the code, which is the only way a mistake in the
    rule itself gets caught.
    """
    assert "vistest" not in ALLOWED_PACKAGES
    assert _outside_the_allowlist(["vistest.service"]) == ["vistest.service"]
    assert _outside_the_allowlist(["vistest", "vistest.models"]) == []
    assert _outside_the_allowlist(["vistest.core.comparator"]) == []
