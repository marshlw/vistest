# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The library's tests do not need the server (o1c).

`tests/` is the library's half of the suite: it runs in an environment with
`pip install -e ".[browser,report,dev]"` and nothing else — no `vistest-server`,
no fastapi, no uvicorn. The server's tests are in `server/tests/`. So no file
under `tests/` imports `vistest_server`: not in an import statement, at the
top or inside a function; not as a string handed to `import_module`,
`__import__`, `find_spec` or `pytest.importorskip`; not as the dotted target of
`monkeypatch.setattr` or `delattr`, which imports it just the same. Read with
`ast`, the way `tests/test_split_boundary.py` reads `vistest/`.

A string that only *mentions* the package is not an import and is allowed:
the library's own checks have to spell the name to look for it (the split
boundary, the archive audit, `_no_server`).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
SERVER = "vistest_server"

#  A call whose first argument is a module name that gets imported.
_IMPORTING_CALLS = {"import_module", "__import__", "importorskip", "find_spec"}
#  monkeypatch.setattr("pkg.mod.attr", value) imports pkg.mod.
_DOTTED_TARGET_CALLS = {"setattr", "delattr"}


def _files() -> list[Path]:
    return sorted(p for p in TESTS.rglob("*.py") if "__pycache__" not in p.parts)


def _is_server(name: str) -> bool:
    return name == SERVER or name.startswith(SERVER + ".")


def _call_name(func: ast.expr) -> str:
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _imports_the_server(path: Path) -> list[str]:
    tree = ast.parse(path.read_text("utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [f"line {node.lineno}: import {a.name}" for a in node.names
                      if _is_server(a.name)]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module \
                and _is_server(node.module):
            found.append(f"line {node.lineno}: from {node.module} import …")
        elif isinstance(node, ast.Call) and node.args:
            name = _call_name(node.func)
            first = node.args[0]
            if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                continue
            if name in _IMPORTING_CALLS and _is_server(first.value):
                found.append(f"line {node.lineno}: {name}({first.value!r})")
            elif name in _DOTTED_TARGET_CALLS and first.value.startswith(SERVER + "."):
                found.append(f"line {node.lineno}: {name}({first.value!r}, …)")
    return found


def test_the_library_tests_are_where_they_are_expected():
    assert len(_files()) > 50
    assert (ROOT / "server" / "tests" / "conftest.py").is_file()


@pytest.mark.parametrize("path", _files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_no_library_test_imports_the_server(path):
    assert _imports_the_server(path) == []


def test_the_check_sees_what_it_is_meant_to(tmp_path):
    """The rule itself: every way of importing is caught; a mention is not."""
    probe = tmp_path / "probe.py"
    probe.write_text(
        '"""Moved to vistest_server.api — a docstring may say so."""\n'
        "import vistest_server\n"
        "HIDDEN = ('vistest_server', 'fastapi')\n"
        "def test_f(monkeypatch):\n"
        "    from vistest_server.api import main\n"
        "    import importlib, pytest\n"
        "    importlib.import_module('vistest_server.cli')\n"
        "    pytest.importorskip('vistest_server')\n"
        "    __import__('vistest_server.service')\n"
        "    importlib.util.find_spec('vistest_server')\n"
        "    monkeypatch.setattr('vistest_server.api.db.SCHEMA_VERSION', -1)\n"
        "    monkeypatch.setattr('os.sep', '/')\n"
        "    return 'vistest_server'\n", encoding="utf-8")
    found = _imports_the_server(probe)
    assert len(found) == 7, found
    assert found[0] == "line 2: import vistest_server"
    assert any("setattr('vistest_server.api.db.SCHEMA_VERSION'" in f for f in found)
