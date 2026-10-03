# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The version has one source, and the audit of the built archives is not blind.

`scripts/audit_dist.py` is what stands between a build and a public index, so
it is tested the way a smoke detector is: with something that must set it off,
and with something that must not. The archives here are tiny and made on the
spot; nothing is built or downloaded.
"""

from __future__ import annotations

import importlib.util
import io
import re
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("audit_dist", ROOT / "scripts/audit_dist.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["audit_dist"] = mod
    spec.loader.exec_module(mod)
    return mod


audit = _load()

ENTRY = (b"[console_scripts]\nvistest = vistest.cli:main\n\n"
         b"[pytest11]\nvistest = vistest.pytest_plugin\n")
WHEEL_OK = {
    "vistest/__init__.py": b"x = 1\n",
    "vistest/library/fonts/canary-sans.ttf": b"\0ttf",
    "vistest/bench/playwright_compare.mjs": b"// mjs\n",
    "vistest/ai/region_gate.json": b"{}",
    "vistest/frontend/index.html": b"<html></html>",
    "vistest-1.dist-info/entry_points.txt": ENTRY,
}
SDIST_OK = {"LICENSE": b"l", "NOTICE": b"n", "README.md": b"r", "pyproject.toml": b"p"}


def _dist(tmp_path: Path, wheel: dict, sdist: dict) -> Path:
    d = tmp_path / "dist"
    d.mkdir()
    with zipfile.ZipFile(d / "vistest-1-py3-none-any.whl", "w") as z:
        for name, data in wheel.items():
            z.writestr(name, data)
    with tarfile.open(d / "vistest-1.tar.gz", "w:gz") as t:
        for name, data in sdist.items():
            info = tarfile.TarInfo(f"vistest-1/{name}")
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return d


def test_a_clean_pair_of_archives_passes(tmp_path, capsys):
    assert audit.main([str(_dist(tmp_path, WHEEL_OK, SDIST_OK))]) == 0
    assert "nothing found" in capsys.readouterr().out


#  The things the audit must find are put together here, from halves: a literal
#  key block or token in this file would be in the sdist, where the audit would
#  find it, and where a scanner of the index would too.
LEAKS = [
    ("vistest/leak.py", b"-----BEGIN " + b"RSA PRIVATE KEY-----\n", "private key block"),
    ("vistest/leak.py", b'T = "' + b"pypi-" + b"AgEI" + b"a" * 40 + b'"', "PyPI token"),
    ("vistest/leak.py", b'p = "D:' + b"\\\\project\\\\some-repo" + b'"', "Windows path"),
    ("vistest/leak.py", b'p = "/ho' + b'me/someone/work/repo"', "home path"),
    (".env", b"A=1", ".env file"),
    ("vistest/keys/signing.pem", b"x", "key file"),
    ("vistest/frame.png", b"\x89PNG", "a picture"),
    ("tests/x.py", b"x = 1", "outside the package"),
]


@pytest.mark.parametrize("name, data, what", LEAKS)
def test_what_must_not_be_in_the_wheel_is_found(tmp_path, capsys, name, data, what):
    wheel = {**WHEEL_OK, name: data}
    assert audit.main([str(_dist(tmp_path, wheel, SDIST_OK))]) == 1
    assert "FOUND" in capsys.readouterr().out, what


def test_a_given_term_is_found_in_content_and_in_names(tmp_path, capsys):
    sdist = {**SDIST_OK, "docs/Workproj-notes.md": b"see the Workproj repo"}
    d = _dist(tmp_path, WHEEL_OK, sdist)
    assert audit.main([str(d)]) == 0                       # not a generic pattern
    assert audit.main([str(d), "--forbid", "workproj"]) == 1
    out = capsys.readouterr().out
    assert "in the file name" in out and "x1" in out


def test_documentation_placeholders_are_not_leaks(tmp_path):
    text = b'"D:' + b'\\project\\..." and "/ho' + b'me/user/x/y"'
    wheel = {**WHEEL_OK, "vistest/doc.py": text}
    assert audit.main([str(_dist(tmp_path, wheel, SDIST_OK))]) == 0


def test_a_missing_required_file_or_entry_point_fails(tmp_path, capsys):
    for i, drop in enumerate(("vistest/library/fonts/canary-sans.ttf",
                              "vistest/bench/playwright_compare.mjs",
                              "vistest-1.dist-info/entry_points.txt")):
        where = tmp_path / str(i)
        where.mkdir()
        wheel = {k: v for k, v in WHEEL_OK.items() if k != drop}
        assert audit.main([str(_dist(where, wheel, SDIST_OK))]) == 1
    assert "MISSING" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
def test_the_version_is_written_once():
    import vistest

    text = (ROOT / "pyproject.toml").read_text("utf-8")
    assert re.search(r'^dynamic\s*=\s*\[[^\]]*"version"', text, re.M)
    project = text.split("[project]", 1)[1].split("\n[", 1)[0]
    assert not re.search(r'^version\s*=', project, re.M), \
        "a static version in [project] would be a second source"
    assert 'attr = "vistest.__version__"' in text
    #  PEP 440, and the string literal setuptools reads without importing.
    assert re.fullmatch(r"\d+\.\d+\.\d+(\.?(dev|a|b|rc)\d+)?", vistest.__version__)
    init = (ROOT / "vistest/__init__.py").read_text("utf-8")
    assert re.search(r'^__version__ = "[^"]+"$', init, re.M)


def test_the_browser_extra_brings_what_the_readme_example_needs():
    """The README and `examples/` take `page` from pytest-playwright's fixture."""
    try:
        import tomllib
    except ModuleNotFoundError:          # Python 3.10
        import tomli as tomllib

    extras = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8")
                           )["project"]["optional-dependencies"]
    for name in ("browser", "full"):
        names = {re.split(r"[<>=!~\[ ;]", d, maxsplit=1)[0].lower() for d in extras[name]}
        assert {"playwright", "pytest", "pytest-playwright"} <= names, name
