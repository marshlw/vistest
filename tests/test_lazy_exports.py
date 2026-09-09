# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The package root promises the same names three times. They have to agree.

`vistest/__init__.py` defers the layers above the engine, and that costs it a
single source of truth. The same set of names is now written down in three
places, each read by somebody different:

* `_LAZY` — read at runtime by `__getattr__`;
* the `if TYPE_CHECKING:` block — read by mypy and by an editor's completion,
  and never executed;
* `__all__` — read by `from vistest import *` and by documentation tools.

Nothing makes them agree on its own. A name added to `_LAZY` and forgotten in
the `TYPE_CHECKING` block works perfectly at runtime and is an error in the
editor; forgotten in `__all__`, it disappears from a star import. Both are the
kind of mistake that is found by a user rather than by a test, so this is the
test.

The `TYPE_CHECKING` block is read out of the source with `ast` rather than
imported, because importing it is precisely what never happens.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

import vistest

INIT = Path(vistest.__file__)


def _type_checking_imports() -> dict[str, str]:
    """`name -> module` out of the `if TYPE_CHECKING:` block of the source."""
    tree = ast.parse(INIT.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        guarded = (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
            isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING")
        if not guarded:
            continue
        for stmt in node.body:
            if isinstance(stmt, ast.ImportFrom):
                module = "." * stmt.level + (stmt.module or "")
                for alias in stmt.names:
                    out[alias.asname or alias.name] = module
    return out


def test_the_type_checking_block_covers_every_deferred_name():
    """What mypy sees and what `__getattr__` does must be the same list."""
    declared = _type_checking_imports()
    missing = sorted(set(vistest._LAZY) - set(declared))
    assert not missing, (
        f"{', '.join(missing)} are deferred in `_LAZY` but absent from the "
        "`if TYPE_CHECKING:` block, so they work at runtime and are unknown "
        "to mypy and to autocompletion. Add the import there too.")

    extra = sorted(set(declared) - set(vistest._LAZY))
    assert not extra, (
        f"the `if TYPE_CHECKING:` block imports {', '.join(extra)}, which "
        "`__getattr__` will not provide at runtime. A type checker would "
        "accept code that then fails with AttributeError.")


def test_the_type_checking_block_names_the_right_modules():
    """A name declared from the wrong module type-checks against the wrong thing."""
    declared = _type_checking_imports()
    wrong = {name: (declared.get(name), module)
             for name, module in vistest._LAZY.items()
             if name in declared and declared[name] != module}
    assert not wrong, (
        "the `if TYPE_CHECKING:` block and `_LAZY` disagree about where these "
        f"come from: {wrong} (declared, actual)")


def test_every_public_name_is_reachable():
    """`__all__` is the promise; nothing in it may be missing."""
    missing = [name for name in vistest.__all__ if not hasattr(vistest, name)]
    assert not missing, (
        f"{', '.join(missing)} are in `__all__` and cannot be resolved. "
        "`from vistest import *` would fail on them.")


def test_all_and_lazy_do_not_drift_apart():
    forgotten = sorted(set(vistest._LAZY) - set(vistest.__all__))
    assert not forgotten, (
        f"{', '.join(forgotten)} are deferred but missing from `__all__`, so "
        "`from vistest import *` does not bring them in.")


def test_dir_lists_what_has_not_been_imported_yet():
    """`dir()` must not depend on what happened to run before it.

    A fresh process, one name touched, and `dir()` still has to list all of
    them — otherwise interactive completion shows a different package
    depending on history.
    """
    probe = (
        "import vistest;"
        " vistest.compare;"
        " import sys;"
        " print(all(n in dir(vistest) for n in vistest.__all__));"
        " print('vistest.service' in sys.modules)"
    )
    done = subprocess.run([sys.executable, "-c", probe],
                          cwd=INIT.parents[1], capture_output=True,
                          text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    complete, service_loaded = done.stdout.split()
    assert complete == "True", "dir() does not list the deferred names"
    assert service_loaded == "False", (
        "`dir()` imported the service layer. It has to list the deferred "
        "names without loading them, or it defeats the deferral.")


@pytest.mark.parametrize("name", sorted(vistest._LAZY))
def test_each_deferred_name_resolves_to_the_module_it_claims(name):
    value = getattr(vistest, name)
    module = vistest._LAZY[name].lstrip(".")
    assert getattr(value, "__module__", "").startswith(f"vistest.{module}") or \
        value.__name__ == name, (
        f"{name} resolved to something from {getattr(value, '__module__', '?')}, "
        f"and `_LAZY` says it comes from vistest.{module}")
