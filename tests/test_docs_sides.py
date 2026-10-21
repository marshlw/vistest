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
"""

from __future__ import annotations

import re
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
