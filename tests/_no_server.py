# VisTest - visual regression testing for Python and Playwright.
# Copyright 2026 Kirill Kulagin
# SPDX-License-Identifier: Apache-2.0
#
# This file is part of VisTest, licensed under the Apache License, Version 2.0.
# You may not use it except in compliance with the License. See LICENSE for
# the terms and NOTICE for attribution and the trademark.

"""Run pytest or the CLI as if the server were not installed.

The test environment has `vistest-server` installed next to `vistest`, and a
second venv per test is a minute each. Without the server there is no
`vistest_server` to import and none of its entry points; so, in a child
process only, a finder at the front of `sys.meta_path` makes the server's
package (and its web stack) fail to import the way an absent one does, and
`importlib.metadata.entry_points` stops listing what that package offers. The
clean venv with `vistest[browser]` alone is checked by `scripts/check_install.py`.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HIDDEN = ("vistest_server", "fastapi", "uvicorn", "requests", "multipart",
          "python_multipart", "starlette")

_PRELUDE = f"""
import sys
import importlib.metadata as _metadata

class _NoServer:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in {HIDDEN!r}:
            raise ModuleNotFoundError(f"No module named {{name!r}}", name=name)
        return None

sys.meta_path.insert(0, _NoServer())

_entry_points = _metadata.entry_points

def _without_the_server(**select):
    found = _entry_points(**select)
    return _metadata.EntryPoints(
        e for e in found if e.value.split(".")[0] != "vistest_server")

_metadata.entry_points = _without_the_server
"""


def run(entry: str, args: list[str], cwd: Path, *, hide: bool = True,
        env: dict | None = None) -> subprocess.CompletedProcess:
    """`entry` is "pytest" or "vistest"; the server's modules hidden when `hide`."""
    call = ("import pytest; sys.exit(pytest.main(sys.argv[1:]))" if entry == "pytest"
            else "from vistest.cli import main; sys.exit(main(sys.argv[1:]))")
    code = (_PRELUDE if hide else "import sys\n") + call
    clean = {k: v for k, v in os.environ.items() if not k.startswith("VISTEST_")}
    return subprocess.run([sys.executable, "-c", code, *args], cwd=cwd,
                          capture_output=True, encoding="utf-8", timeout=300,
                          env={**clean, "PYTHONIOENCODING": "utf-8", "COLUMNS": "200",
                               **(env or {})})
