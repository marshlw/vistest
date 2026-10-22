# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The documentation and the scripts say which package is which (o1c).

Since o1b the library `vistest` has the extras `browser`, `report` and `dev`;
`server`, `ai`, `ldap` and `full` are `vistest-server`'s. An instruction that
says `pip install "vistest[server]"` or `pip install -e ".[full]"` now fails
at the first step, on the reader's machine. And since o1c the server's tests
are in `server/tests/`: `pytest tests/api` runs nothing.

So, in what a person reads or runs — the READMEs, `docs/` (the development
journal is a record and is left as written), `examples/`, `scripts/`,
`docker/`, `deploy/`, the `run` launchers and the contributing section — no
extra of the library that is gone, and no path of a test that moved. The
audit of 13.10 (`docs/dev/dependencies.md`) is a record of the dev3 wheel and
is not rewritten either: it carries one line on top that says the extras
moved. And the contributing section says how to run the two halves.

Since o1p2 the minimum is Python 3.11 (Pillow 13, which carries the security
fixes, needs it). In the same places, in both `pyproject.toml` and in the
changelog's Unreleased section, 3.10 is not named as a version that works.
The changelog's sections of past releases are records, like the journal.

Since o1d2 this file is the library's half of these checks and reads only the
open repository (`tests/open_paths.py`): its READMEs, docs, examples and
scripts. The server's half — the launchers, `docker/`, `deploy/`,
`docs/server/`, the server's `pyproject.toml`, and the paths of the tests that
moved, which only the server's side can list — is
`server/tests/test_docs_sides_server.py`. The open documents do not name the
server's tests at all, moved or not: they name no `server/` path.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

from .open_paths import is_open

ROOT = Path(__file__).resolve().parents[1]

#  What a person reads or runs, of the open repository; a directory is read
#  file by file, the open files only.
PLACES = ["README.md", "README.ru.md", "CONTRIBUTORS.md", "docs", "examples", "scripts"]
#  Records, left as they were written.
RECORDS = {"docs/dev/journal.md", "docs/dev/dependencies.md"}
#  Not a text a person reads: the ledger of `scripts/check_i18n.py` lists every
#  file with Russian text in it, the server's included, until the open
#  repository's first commit rewrites it (`check_i18n.py --update`, o1e). The
#  gone extras are still checked in it here, the moved test paths in server/tests.
LEDGERS = {"scripts/i18n_debt.txt"}
GONE_EXTRAS = ("server", "full", "ai", "ldap")

#  `vistest[server]`, `vistest[browser,full]` — but not `vistest-server[full]`.
_LIBRARY_EXTRA = re.compile(
    r"(?<![\w-])vistest\[[^\]\n]*\b(" + "|".join(GONE_EXTRAS) + r")\b[^\]\n]*\]")
#  `".[server,dev]"`, `.[full]` — but not `./server[full]`.
_LOCAL_EXTRA = re.compile(
    r"(?<![\w/.])\.\[[^\]\n]*\b(" + "|".join(GONE_EXTRAS) + r")\b[^\]\n]*\]")


def _texts() -> list[Path]:
    out = []
    for place in PLACES:
        path = ROOT / place
        if path.is_file():
            out.append(path)
        elif path.is_dir():
            out += [p for p in sorted(path.rglob("*")) if p.is_file()
                    and "__pycache__" not in p.parts and "node_modules" not in p.parts]
    return [p for p in out if p.relative_to(ROOT).as_posix() not in RECORDS
            and is_open(p.relative_to(ROOT).as_posix())]


def _read(path: Path) -> str | None:
    try:
        return path.read_text("utf-8")
    except (UnicodeDecodeError, OSError):
        return None


#  A path of the server's half: `server/tests/api`, `./server[full]`,
#  `server/pyproject.toml`, `docs/server/GUIDE.ru.md`. The open documents name
#  none — the reader has no such directory.
_SERVER_PATH = re.compile(r"(?<![\w-])(?:\./)?(?:docs/)?server(?:/|\[)")


def _server_paths(text: str) -> list[str]:
    return [f"line {n}: {m.group(0)}…" for n, line in enumerate(text.splitlines(), 1)
            for m in _SERVER_PATH.finditer(line)]


def _old_spellings(text: str, moved: list[str]) -> list[str]:
    found = []
    for n, line in enumerate(text.splitlines(), 1):
        for rx in (_LIBRARY_EXTRA, _LOCAL_EXTRA):
            found += [f"line {n}: {m.group(0)}" for m in rx.finditer(line)]
        for path in moved:
            for m in re.finditer(re.escape(path) + r"(?![\w.-])", line):
                if not line[:m.start()].endswith("server/"):
                    found.append(f"line {n}: {path} (now server/{path})")
    return found


def test_the_places_are_where_they_are_expected():
    texts = {p.relative_to(ROOT).as_posix() for p in _texts()}
    #  Over 30 before o1d2, with the launchers, docker/ and docs/server/ in it.
    assert len(texts) > 20
    assert {"README.md", "docs/GUIDE.md", "scripts/check_install.py"} <= texts
    assert all(is_open(t) for t in texts)


@pytest.mark.parametrize("path", _texts(), ids=lambda p: p.relative_to(ROOT).as_posix())
def test_no_gone_extra_and_no_server_path(path):
    """No extra of the library that is gone (o1b), and no path of the server's
    half — which covers every test path that moved to server/tests (o1c) and
    every other: in the open repository there is no `server/` to point at."""
    text = _read(path)
    if text is None:
        pytest.skip("not text")
    assert _old_spellings(text, []) == []
    if path.relative_to(ROOT).as_posix() not in LEDGERS:
        assert _server_paths(text) == []


def test_the_audit_record_says_the_extras_moved():
    head = (ROOT / "docs" / "dev" / "dependencies.md").read_text("utf-8").splitlines()[:5]
    assert any("vistest-server" in line and "extras" in line for line in head), head


def test_the_contributing_section_says_how_to_run_the_suite():
    """The library's half, in the environment its users have. The server's half
    is said in the server's own contributing notes (server/tests checks them)."""
    text = (ROOT / "CONTRIBUTORS.md").read_text("utf-8")
    assert 'pip install -e ".[browser,report,dev]"' in text
    assert "python -m pytest tests" in text
    assert "ruff check vistest/ tests/" in text


def test_the_server_path_check_sees_what_it_is_meant_to():
    bad = ('pip install -e ".[dev]" -e "./server[full]"\n'
           "python -m pytest server/tests -n auto\n"
           "see docs/server/GUIDE.ru.md\n"
           "ruff check vistest/ server/ tests/\n")
    assert len(_server_paths(bad)) == 4, _server_paths(bad)
    good = ('pip install "vistest[browser]"\n'
            "a server for teams — a commercial product\n"
            "the vistest-server/ package, VISTEST_SERVER/x, vistest_server\n"
            "python -m pytest tests -n auto\n")
    assert _server_paths(good) == [], _server_paths(good)


def test_the_check_sees_what_it_is_meant_to():
    """The rule itself, on the spellings it is for and on the ones it is not."""
    moved = ["tests/api", "tests/test_wheel_ui.py"]
    bad = ('pip install "vistest[server]"\n'
           'pip install "vistest[browser,full]"\n'
           'pip install -e ".[server,dev]"\n'
           "pip install -e .[full]\n"
           "pytest tests/api/test_auth.py\n"
           "pytest tests/api   # the API\n"
           "pytest tests/test_wheel_ui.py\n")
    assert len(_old_spellings(bad, moved)) == 7, _old_spellings(bad, moved)
    good = ('pip install "vistest[browser]" "vistest-server[full]"\n'
            'pip install -e ".[dev]" -e "./server[full]"\n'
            "python -m pytest server/tests/api/test_auth.py tests/apidoc.md\n"
            "pytest server/tests/test_wheel_ui.py tests/test_library.py\n")
    assert _old_spellings(good, moved) == []


#  "Python 3.10", "Python ≥ 3.10", "3.10+", "3.10–3.14", ">=3.10", a CI matrix
#  entry, ruff's py310 — but not "3.10%" in a table of numbers, and not
#  "3.10 is no longer supported".
_OLD_PYTHON = re.compile(
    r"(?i)python\s*(?:≥|>=)?\s*3\.10(?![\d%])"
    r"|(?<![\d.])3\.10\+"
    r"|(?<![\d.])3\.10\s*[–-]\s*3\.\d+"
    r"|>=\s*[\"']?3\.10(?!\d)"
    r"|python-version:\s*[\"']?3\.10(?!\d)"
    r"|\[[^\]\n]*[\"']3\.10[\"'][^\]\n]*\]"
    r"|\b(?:py|cp)310\b")


def _old_python(text: str) -> list[str]:
    return [f"line {n}: {m.group(0)}" for n, line in enumerate(text.splitlines(), 1)
            for m in _OLD_PYTHON.finditer(line)]


def _unreleased(changelog: str) -> str:
    """The changelog down to its first released version: what is still news."""
    head, _, rest = changelog.partition("## Unreleased")
    assert rest, "CHANGELOG.md has no Unreleased section"
    return re.split(r"(?m)^## (?!#)", rest, maxsplit=1)[0]


@pytest.mark.parametrize("path", _texts(), ids=lambda p: p.relative_to(ROOT).as_posix())
def test_python_3_10_is_not_named_as_supported(path):
    text = _read(path)
    if text is None:
        pytest.skip("not text")
    assert _old_python(text) == []


def test_the_library_asks_for_python_3_11():
    """The server's `pyproject.toml` is held to the same in server/tests."""
    for path in ("pyproject.toml",):
        raw = (ROOT / path).read_text("utf-8")
        project = tomllib.loads(raw)["project"]
        assert project["requires-python"] == ">=3.11", path
        assert not any(c.startswith("Programming Language :: Python :: 3.10")
                       for c in project.get("classifiers", [])), path
        assert _old_python(raw) == [], path
        #  A marker for a Python below the minimum never applies: a dependency
        #  behind one is dead (tomli for < 3.11 was, until o1i).
        dead = [(op, minor) for op, minor
                in re.findall(r"python_version\s*(<=?)\s*[\"']3\.(\d+)", raw)
                if int(minor) < 11 or (op == "<" and int(minor) == 11)]
        assert dead == [], path


def test_the_changelog_says_3_10_is_gone_and_names_it_nowhere_else():
    unreleased = _unreleased((ROOT / "CHANGELOG.md").read_text("utf-8"))
    assert _old_python(unreleased) == []
    breaking = unreleased.split("### Breaking", 1)[1]
    assert "Python 3.11" in breaking and "Pillow 13" in breaking


def test_the_python_check_sees_what_it_is_meant_to():
    bad = ("Python 3.10 or newer\n- Python 3.10+\nPython 3.10–3.14.\n"
           "needs Python ≥ 3.10 and Node\nrequires-python = \">=3.10\"\n"
           'python: ["3.10", "3.13"]\npython-version: "3.10"\n'
           'target-version = "py310"\n')
    assert len(_old_python(bad)) == 8, _old_python(bad)
    good = ("Python 3.11 or newer\n- Python 3.11+\nPython 3.11–3.14.\n"
            "  within one load    3.10%  animations\n"
            "3.10 is no longer supported\nPillow 13.10.0\n"
            'python: ["3.11", "3.13"]\nrequires-python = ">=3.11"\n')
    assert _old_python(good) == [], _old_python(good)
