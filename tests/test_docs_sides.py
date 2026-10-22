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
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

PLACES = ["README.md", "README.ru.md", "CONTRIBUTORS.md", "run.py", "run.ps1", "run.sh",
          "docs", "examples", "scripts", "docker", "deploy"]
#  Records, left as they were written.
RECORDS = {"docs/dev/journal.md", "docs/dev/dependencies.md"}
GONE_EXTRAS = ("server", "full", "ai", "ldap")

#  `vistest[server]`, `vistest[browser,full]` — but not `vistest-server[full]`.
_LIBRARY_EXTRA = re.compile(
    r"(?<![\w-])vistest\[[^\]\n]*\b(" + "|".join(GONE_EXTRAS) + r")\b[^\]\n]*\]")
#  `".[server,dev]"`, `.[full]` — but not `./server[full]`.
_LOCAL_EXTRA = re.compile(
    r"(?<![\w/.])\.\[[^\]\n]*\b(" + "|".join(GONE_EXTRAS) + r")\b[^\]\n]*\]")


def _moved_tests() -> list[str]:
    """Paths under tests/ that are server/tests/ now: the four directories, and
    every file of server/tests that has no namesake left in tests/."""
    server = ROOT / "server" / "tests"
    dirs = [f"tests/{d.name}" for d in sorted(server.iterdir())
            if d.is_dir() and d.name != "__pycache__"]
    files = [f"tests/{p.name}" for p in sorted(server.glob("test_*.py"))
             if not (ROOT / "tests" / p.name).exists()]
    return dirs + files


def _texts() -> list[Path]:
    out = []
    for place in PLACES:
        path = ROOT / place
        if path.is_file():
            out.append(path)
        elif path.is_dir():
            out += [p for p in sorted(path.rglob("*")) if p.is_file()
                    and "__pycache__" not in p.parts and "node_modules" not in p.parts]
    return [p for p in out if p.relative_to(ROOT).as_posix() not in RECORDS]


def _read(path: Path) -> str | None:
    try:
        return path.read_text("utf-8")
    except (UnicodeDecodeError, OSError):
        return None


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
    assert len(_texts()) > 30
    moved = _moved_tests()
    assert "tests/api" in moved and "tests/test_wheel_ui.py" in moved
    assert "tests/test_naming.py" not in moved        # split: both halves exist


@pytest.mark.parametrize("path", _texts(), ids=lambda p: p.relative_to(ROOT).as_posix())
def test_no_gone_extra_and_no_moved_test_path(path):
    text = _read(path)
    if text is None:
        pytest.skip("not text")
    assert _old_spellings(text, _moved_tests()) == []


def test_the_audit_record_says_the_extras_moved():
    head = (ROOT / "docs" / "dev" / "dependencies.md").read_text("utf-8").splitlines()[:5]
    assert any("vistest-server" in line and "extras" in line for line in head), head


def test_the_contributing_section_says_how_to_run_both_halves():
    text = (ROOT / "CONTRIBUTORS.md").read_text("utf-8")
    assert 'pip install -e ".[browser,report,dev]"' in text
    assert "python -m pytest tests" in text
    assert 'pip install -e ".[dev]" -e "./server[full]"' in text
    assert "python -m pytest server/tests" in text


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


def test_both_packages_ask_for_python_3_11():
    for path in ("pyproject.toml", "server/pyproject.toml"):
        raw = (ROOT / path).read_text("utf-8")
        project = tomllib.loads(raw)["project"]
        assert project["requires-python"] == ">=3.11", path
        assert not any(c.startswith("Programming Language :: Python :: 3.10")
                       for c in project.get("classifiers", [])), path
        assert _old_python(raw) == [], path


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
