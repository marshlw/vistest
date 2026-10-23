# VisTest - visual regression testing for Python and Playwright.
# Copyright 2026 Kirill Kulagin
# SPDX-License-Identifier: Apache-2.0
#
# This file is part of VisTest, licensed under the Apache License, Version 2.0.
# You may not use it except in compliance with the License. See LICENSE for
# the terms and NOTICE for attribution and the trademark.

"""The library does not name the server (o1).

`vistest` — the engine, the library, the report, the library's CLI — is the
open package; `vistest_server` stands on it and plugs in through entry points
(`vistest.commands`, `vistest.pytest`, `vistest.plugins`). The day the
repositories are split, `vistest` must not lose a single import. So no module
under `vistest/` names `vistest_server`: not in an import statement, at the
top or inside a function; not as a string handed to `import_module` or
`__import__`; not in any other string either — a docstring may say where a
thing went, code may not. Read with `ast`, so a comment does not count.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LIBRARY = ROOT / "vistest"
SERVER = "vistest_server"


def _modules() -> list[Path]:
    return sorted(p for p in LIBRARY.rglob("*.py") if "__pycache__" not in p.parts)


def _docstrings(tree: ast.AST) -> set[int]:
    """id() of every docstring node: a module's, a class's, a function's."""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            first = body[0].value if body and isinstance(body[0], ast.Expr) else None
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                out.add(id(first))
    return out


def _names_the_server(path: Path) -> list[str]:
    tree = ast.parse(path.read_text("utf-8"))
    docs = _docstrings(tree)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [f"line {node.lineno}: import {a.name}" for a in node.names
                      if a.name == SERVER or a.name.startswith(SERVER + ".")]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module and (
                node.module == SERVER or node.module.startswith(SERVER + ".")):
            found.append(f"line {node.lineno}: from {node.module} import …")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and SERVER in node.value and id(node) not in docs:
            found.append(f"line {node.lineno}: the string {node.value[:60]!r}")
    return found


def test_the_library_is_where_it_is_expected():
    #  That SERVER is the name the server's package really has — so the check
    #  below looks for the right word — is asserted on the server's side,
    #  server/tests/test_split_server.py: this half cannot see the package (o1d2).
    assert len(_modules()) > 50
    assert SERVER == "vistest_server"


@pytest.mark.parametrize("path", _modules(), ids=lambda p: str(p.relative_to(ROOT)))
def test_no_library_module_names_the_server(path):
    assert _names_the_server(path) == []


def test_the_check_sees_what_it_is_meant_to(tmp_path):
    """The rule itself: an import inside a function, an import_module string
    and a plain string are caught; a docstring and a comment are not."""
    probe = tmp_path / "probe.py"
    probe.write_text(
        '"""Moved to vistest_server.api — a docstring may say so."""\n'
        "# vistest_server in a comment\n"
        "def f():\n"
        "    from vistest_server.api import main\n"
        "    import importlib\n"
        "    importlib.import_module('vistest_server.cli')\n"
        "    return 'vistest_server'\n", encoding="utf-8")
    found = _names_the_server(probe)
    assert len(found) == 3, found
    assert found[0].startswith("line 4: from vistest_server.api")


def _imported_names(path: Path) -> list[tuple[int, str, str]]:
    """(line, module, name) for every `from <vistest module> import name`,
    relative or absolute, at the top or inside a function."""
    rel = path.relative_to(ROOT).with_suffix("")
    parts = list(rel.parts)
    package = parts[:-1]          # a module's package; an __init__'s own
    out = []
    for node in ast.walk(ast.parse(path.read_text("utf-8"))):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level:
            base = package[: len(package) - node.level + 1]
            module = ".".join(base + ([node.module] if node.module else []))
        else:
            module = node.module or ""
        if module == "vistest" or module.startswith("vistest."):
            out += [(node.lineno, module, a.name) for a in node.names if a.name != "*"]
    return out


def test_every_name_the_library_imports_from_itself_is_there():
    """The seam the AST check above cannot see: `from .storage import X`
    where X went to the server. It names nothing of the server's — it just
    breaks, and only on the path that runs that import (o1a found two:
    `baselines import` over an existing baseline, and `doctor <name>`)."""
    import importlib

    broken = []
    for path in _modules():
        for line, module, name in _imported_names(path):
            target = importlib.import_module(module)
            if hasattr(target, name):
                continue
            try:
                importlib.import_module(f"{module}.{name}")
            except ModuleNotFoundError:
                broken.append(f"{path.relative_to(ROOT)}:{line}: from {module} import {name}")
    assert broken == []
