# VisTest - visual regression testing for Python and Playwright.
# Copyright 2026 Kirill Kulagin
# SPDX-License-Identifier: Apache-2.0
#
# This file is part of VisTest, licensed under the Apache License, Version 2.0.
# You may not use it except in compliance with the License. See LICENSE for
# the terms and NOTICE for attribution and the trademark.

"""One license, said the same way everywhere (o2).

The library moved from its earlier copyleft license to Apache-2.0 (CHANGELOG,
0.2.0a1). A license is stated in
several places that nothing else ties together: the LICENSE text, the package
metadata, the provenance line in every report, the file headers, the bench
package. These tests keep them one answer.
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from pathlib import Path

import pytest

from vistest import provenance

ROOT = Path(__file__).resolve().parents[1]
SPDX = "Apache-2.0"

#: sha256 of the Apache License 2.0 as apache.org publishes it
#: (https://www.apache.org/licenses/LICENSE-2.0.txt) and as many packages ship it.
APACHE_2_SHA256 = "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"

#: Where the old license may still be named: the record of the change.
HISTORY = {"CHANGELOG.md", "NOTICE", "docs/dev/journal.md"}

#: The old license's short and long name, put together here so that this file
#: does not name it itself.
OLD = re.compile(r"\bA" + r"GPL\b|Aff" + "ero")

SOURCE_DIRS = ("vistest", "tests", "scripts", "examples")
TEXT_SUFFIXES = {".py", ".mjs", ".js", ".html", ".md", ".toml", ".json", ".yml",
                 ".yaml", ".txt", ".cfg", ".in"}


def _text_files():
    for p in sorted(ROOT.rglob("*")):
        rel = p.relative_to(ROOT).as_posix()
        if not p.is_file() or rel.startswith((".git/", ".vistest/")) \
                or "/node_modules/" in f"/{rel}" or "__pycache__" in rel:
            continue
        if p.suffix in TEXT_SUFFIXES or p.name in {"LICENSE", "NOTICE", "MANIFEST.in"}:
            yield rel, p


def test_the_license_text_is_apache_2_unchanged():
    data = (ROOT / "LICENSE").read_bytes()
    assert hashlib.sha256(data).hexdigest() == APACHE_2_SHA256


def test_the_package_says_apache_2():
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]
    assert meta["license"] == SPDX
    assert set(meta["license-files"]) == {"LICENSE", "NOTICE"}


def test_every_report_says_the_same_license_and_home():
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]
    assert provenance.LICENSE == SPDX
    assert provenance.HOMEPAGE == meta["urls"]["Homepage"]


def test_the_bench_package_says_apache_2():
    pkg = json.loads((ROOT / "scripts" / "bench" / "package.json").read_text("utf-8"))
    assert pkg["license"] == SPDX


def test_every_file_header_names_apache_2():
    found = []
    for d in SOURCE_DIRS:
        for p in sorted((ROOT / d).rglob("*")):
            if not p.is_file() or "node_modules" in p.parts or "__pycache__" in p.parts:
                continue
            try:
                head = p.read_text("utf-8")[:2000]
            except (UnicodeDecodeError, OSError):
                continue
            m = re.search(r"SPDX-License-Identifier:\s*(\S+)", head)
            if m:
                found.append((p.relative_to(ROOT).as_posix(), m.group(1)))
    assert len(found) > 150, "the headers were not found where they are expected"
    wrong = [f for f, lic in found if lic != SPDX]
    assert not wrong, wrong


@pytest.mark.parametrize("rel", [r for r, _ in _text_files() if r not in HISTORY])
def test_the_old_license_is_named_only_in_its_history(rel):
    text = (ROOT / rel).read_text("utf-8", errors="replace")
    assert not OLD.search(text), rel
