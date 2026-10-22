# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The library's tests read nothing outside the open repository (o1d2).

The open repository is made of the paths in `tests/open_paths.py`; the server,
its documentation, the launchers and the root `vistest.yaml` are not in it. A
test under `tests/` that reads one of them passes here, where they lie next to
it, and fails there, on the first run of somebody who cloned the library. The
rehearsal of the split found 49 such checks; this keeps the 50th out.

Read with `ast`, file by file, two ways:

* **path expressions** — `ROOT / "server" / "pyproject.toml"`,
  `Path(__file__).resolve().parents[1] / "run.py"`, `.joinpath(...)`,
  `os.path.join(...)`: the names in the file are followed to what they were
  assigned, and the path is worked out relative to the repository. It must be
  open, or a directory on the way to an open path (the root, `docs`), which
  names nothing by itself;
* **path strings** — a literal like `"docs/server/GUIDE.ru.md"`, handed to a
  helper that reads it: any string with a `/` that names an existing file or
  directory of this checkout outside the open paths.

A docstring may say where a thing went. What this cannot see — a path built
from a variable, a `git ls-files` that lists everything — is what the run of
`tests/` in the filtered repository is for.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from .open_paths import is_above_open, is_open

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent

#  Strings that spell a closed path on purpose and are not read: each says why.
NOT_READ = {
    ("tests/test_dist_audit.py", "server/vistest_server/cli.py"):
        "a member's name in a made-up sdist, built in tmp_path",
    ("tests/test_dist_audit.py", "server/frontend/index.html"):
        "a member's name in a made-up sdist, built in tmp_path",
}

#  This file names closed paths as data, to check the rule on them; its path
#  expressions are held to the rule like everybody else's.
SELF = "tests/test_open_half.py"

_PATHLIKE = re.compile(r"^[\w.-]+(?:/[\w.*-]+)+/?$")


class _Loc(tuple):
    """A path relative to the repository's root, as a tuple of its parts."""


def _docstrings(tree: ast.AST) -> set[int]:
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            first = body[0].value if body and isinstance(body[0], ast.Expr) else None
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                out.add(id(first))
    return out


def _assignments(tree: ast.AST) -> dict[str, ast.expr]:
    """Names assigned once — or several times to the same expression."""
    seen: dict[str, list[ast.expr]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                seen.setdefault(target.id, []).append(value)
    return {name: values[0] for name, values in seen.items()
            if len({ast.dump(v) for v in values}) == 1}


def _join(base: _Loc, part: str) -> _Loc | None:
    parts = list(base)
    for piece in part.split("/"):
        if piece in ("", "."):
            continue
        if piece == "..":
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(piece)
    return _Loc(parts)


def _up(loc: _Loc, n: int) -> _Loc | None:
    return _Loc(loc[:-n]) if len(loc) >= n else None       # None: above the root


class _Evaluator:
    def __init__(self, path: Path, root: Path, tree: ast.AST):
        self.file = _Loc(path.resolve().relative_to(root.resolve()).parts)
        self.names = _assignments(tree)

    def __call__(self, node: ast.AST, depth: int = 0):
        if depth > 40:
            return None
        ev = lambda n: self(n, depth + 1)                  # noqa: E731
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            if node.id == "__file__":
                return self.file
            if node.id in self.names:
                return ev(self.names[node.id])
            return None
        if isinstance(node, ast.Attribute):
            if node.attr == "parent":
                base = ev(node.value)
                return _up(base, 1) if isinstance(base, _Loc) else None
            return None
        if isinstance(node, ast.Subscript):
            index = node.slice
            if isinstance(node.value, ast.Attribute) and node.value.attr == "parents" \
                    and isinstance(index, ast.Constant) and isinstance(index.value, int):
                base = ev(node.value.value)
                return _up(base, node.slice.value + 1) if isinstance(base, _Loc) else None
            return None
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            left, right = ev(node.left), ev(node.right)
            if isinstance(left, _Loc) and isinstance(right, str):
                return _join(left, right)
            return None
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else \
                func.id if isinstance(func, ast.Name) else ""
            args = [ev(a) for a in node.args]
            if name in ("resolve", "absolute", "abspath", "realpath") \
                    and isinstance(func, ast.Attribute):
                inner = args[0] if name in ("abspath", "realpath") and args \
                    else ev(func.value)
                return inner if isinstance(inner, _Loc) else None
            if name == "dirname" and args and isinstance(args[0], _Loc):
                return _up(args[0], 1)
            if name in ("Path", "PurePath", "join", "joinpath"):
                base = ev(func.value) if name == "joinpath" and \
                    isinstance(func, ast.Attribute) else None
                parts = ([base] if base is not None else []) + args
                if not parts or not isinstance(parts[0], _Loc):
                    return None
                out = parts[0]
                for part in parts[1:]:
                    if not isinstance(part, str):
                        return None
                    out = _join(out, part)
                    if out is None:
                        return None
                return out
        return None


def _reads_outside(path: Path, root: Path = ROOT) -> list[str]:
    """Every place in `path` that names a path of `root` outside the open ones."""
    tree = ast.parse(path.read_text("utf-8"))
    docs = _docstrings(tree)
    evaluate = _Evaluator(path, root, tree)
    rel = path.resolve().relative_to(root.resolve()).as_posix()
    found: set[tuple[int, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.expr) or not hasattr(node, "lineno"):
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
            if id(node) in docs or not _PATHLIKE.match(text) or (rel, text) in NOT_READ \
                    or rel == SELF:
                continue
            name = text.strip("/")
            if not (is_open(name) or is_above_open(name)) and (root / name).exists():
                found.add((node.lineno, f"the string {text!r}"))
            continue
        if isinstance(node, (ast.Name, ast.Constant)):
            continue
        value = evaluate(node)
        if isinstance(value, _Loc):
            name = "/".join(value)
            if is_open(name) or is_above_open(name):
                continue
            parent = "/".join(value[:-1])
            if not (root / name).exists() and (is_open(parent) or is_above_open(parent)):
                continue                    # a made-up name in an open directory
            found.add((node.lineno, f"the path {name}"))
    return [f"line {n}: {what}" for n, what in sorted(found)]


def _files() -> list[Path]:
    return sorted(p for p in TESTS.rglob("*.py") if "__pycache__" not in p.parts)


def test_the_tests_are_where_they_are_expected():
    assert len(_files()) > 50
    assert is_open("tests/test_open_half.py") and is_open("vistest/__init__.py")
    assert is_open("docs/benchmark_native.json") and not is_open("docs/server/LICENSING.md")


@pytest.mark.parametrize("path", _files(), ids=lambda p: p.relative_to(ROOT).as_posix())
def test_no_library_test_reads_outside_the_open_repository(path):
    assert _reads_outside(path) == []


def test_every_exception_is_still_needed():
    """A string let through that is no longer there would let the next one in."""
    for (rel, text), why in NOT_READ.items():
        assert why
        assert repr(text)[1:-1] in (ROOT / rel).read_text("utf-8"), (rel, text)


def test_the_open_paths_are_what_they_say():
    for name in ("vistest/core/comparator.py", "tests/conftest.py", "README.md",
                 "docs/GUIDE.md", "docs/dev/journal.md", "scripts/check_i18n.py",
                 ".github/workflows/ci.yml", "examples/test_library.py"):
        assert is_open(name), name
    for name in ("server/pyproject.toml", "docs/server/GUIDE.ru.md", "run.py",
                 "vistest.yaml", "docker/Dockerfile.api", "scripts/split/rehearse.sh",
                 "scripts/issue_license.py", ".github/workflows/visual.yml",
                 "vistest_server/api/main.py", "conftest.py"):
        assert not is_open(name), name
    assert is_above_open("") and is_above_open("docs") and is_above_open(".github/workflows")
    assert not is_above_open("server") and not is_above_open("docs/server")


def test_the_check_sees_what_it_is_meant_to(tmp_path):
    """The rule itself, in a repository made up for it: every way of spelling
    a closed path is caught; an open one, a docstring and a variable are not."""
    root = tmp_path / "repo"
    (root / "tests").mkdir(parents=True)
    (root / "server").mkdir()
    (root / "server" / "pyproject.toml").write_text("", "utf-8")
    (root / "docs" / "server").mkdir(parents=True)
    (root / "docs" / "server" / "GUIDE.ru.md").write_text("", "utf-8")
    (root / "run.py").write_text("", "utf-8")
    (root / "vistest.yaml").write_text("", "utf-8")
    probe = root / "tests" / "probe.py"
    probe.write_text(
        '"""It reads docs/server/GUIDE.ru.md — a docstring may say so."""\n'
        "import os\n"
        "from pathlib import Path\n"
        "ROOT = Path(__file__).resolve().parents[1]\n"
        "HERE = Path(__file__).parent\n"
        "SERVER = 'server'\n"
        "def test_a(name):\n"
        "    (ROOT / SERVER / 'pyproject.toml').read_text()\n"
        "    (HERE.parent / 'run.py').exists()\n"
        "    root = Path(__file__).resolve().parent.parent\n"
        "    open(os.path.join(os.path.dirname(os.path.dirname(__file__)), 'vistest.yaml'))\n"
        "    ROOT.joinpath('docker', 'Dockerfile.api')\n"
        "    _read('docs/server/GUIDE.ru.md')\n"
        "    (root / 'README.md').read_text()\n"
        "    (ROOT / 'docs' / 'GUIDE.md').read_text()\n"
        "    (ROOT / name).read_text()\n"
        "    _read('docs/server/not-there.md')\n"
        "    (ROOT / 'docs' / 'made-up.json').exists()\n"
        "    (ROOT / 'server' / 'made-up.toml').exists()\n", encoding="utf-8")
    found = _reads_outside(probe, root)
    named = " ".join(found)
    for closed in ("the path server ", "the path server/pyproject.toml", "the path run.py",
                   "the path vistest.yaml", "the path docker/Dockerfile.api",
                   "'docs/server/GUIDE.ru.md'"):
        assert closed in named + " ", (closed, found)
    assert "README" not in named and "GUIDE.md" not in named.replace("GUIDE.ru.md", "")
    assert "not-there" not in named and "made-up.json" not in named
    assert "the path server/made-up.toml" in named          # under a closed directory
    assert not any(f.startswith("line 1:") for f in found), found
