# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Run pytest or the CLI as if the `server` extra were not installed.

The test environment has `vistest[server]`, and a second venv per test is a
minute each. A finder at the front of `sys.meta_path` makes the server's
modules fail to import the way an absent package does — `import fastapi`
raises ModuleNotFoundError, `importlib.util.find_spec` too — in a child
process only. The clean venv with `vistest[browser]` alone is checked by hand
for the review report.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HIDDEN = ("fastapi", "uvicorn", "requests", "multipart", "python_multipart",
          "starlette")

_PRELUDE = f"""
import sys

class _NoServer:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in {HIDDEN!r}:
            raise ModuleNotFoundError(f"No module named {{name!r}}", name=name)
        return None

sys.meta_path.insert(0, _NoServer())
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
