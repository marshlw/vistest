# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""What the open repository is made of: the paths this half of the suite may read.

The library's tests run in a repository that holds these paths and nothing
else, so a test that reads anything outside them reads a file that is not
there. `tests/test_open_half.py` holds every file under `tests/` to that, and
the documentation tests list their files through `is_open`.

The rules are those of the split's open-path list (one path per rule, a
directory matches everything under it, `glob:` is a glob), copied here so this
half does not have to read that list. The other half of the suite checks that
the two copies say the same.
"""

from __future__ import annotations

import fnmatch

RULES = (
    "vistest/",
    "pyproject.toml",
    "MANIFEST.in",
    "tests/",
    "scripts/audit_dist.py",
    "scripts/bench/",
    "scripts/bench_argos.mjs",
    "scripts/bench_pixelmatch.mjs",
    "scripts/bench_playwright_grid.mjs",
    "scripts/browser_corpus.py",
    "scripts/check_i18n.py",
    "scripts/check_install.py",
    "scripts/diagnose_browser.py",
    "scripts/i18n_debt.txt",
    "scripts/live_corpus.py",
    "scripts/live_photos.py",
    "docs/GUIDE.md",
    "glob:docs/benchmark*",
    "docs/dev/",
    "docs/ARCHITECTURE.ru.md",
    "examples/",
    "README.md",
    "README.ru.md",
    "CHANGELOG.md",
    "LICENSE",
    "NOTICE",
    "SECURITY.md",
    "CONTRIBUTORS.md",
    ".gitignore",
    ".gitattributes",
    ".gitlab-ci.example.yml",
    ".vistest/secrets.env.example",
    ".github/workflows/ci.yml",
    ".github/workflows/install-check.yml",
)


def _matches(rule: str, path: str) -> bool:
    if rule.startswith("glob:"):
        return fnmatch.fnmatchcase(path, rule[5:])
    rule = rule.rstrip("/")
    return path == rule or path.startswith(rule + "/")


def is_open(path: str) -> bool:
    """`path` (relative to the repository, with `/`) is in the open repository."""
    path = path.strip("/")
    return any(_matches(rule, path) for rule in RULES)


def is_above_open(path: str) -> bool:
    """`path` is a directory on the way to an open path: the root, `docs`,
    `.github/workflows`. Naming it reads nothing; listing it would read what is
    under it, open or not, so a listing goes through `is_open` file by file."""
    path = path.strip("/")
    if not path:
        return True
    for rule in RULES:
        target = rule[5:] if rule.startswith("glob:") else rule.rstrip("/")
        fixed = target.split("*", 1)[0]
        if fixed.startswith(path + "/"):
            return True
    return False
