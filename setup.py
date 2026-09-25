# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""One build step on top of pyproject.toml: the review UI goes into the wheel.

Everything else about the package is declared in `pyproject.toml`. This file
exists for a single thing `package-data` cannot express: the interface lives at
the repository root, in `frontend/`, outside the `vistest` package, and an
installed package has to carry it as `vistest/frontend`. That is the first
place `vistest.api.main._find_frontend` looks, and without it
`pip install "vistest[server]" && vistest serve` answers 404 on `/ui/`.

Moving `frontend/` into the package would do the same, at the price of every
path in the Dockerfiles, the tests and the documentation. A copy at build time
leaves the tree as it is.

An editable install is left alone: there the package runs from this checkout,
and `_find_frontend` finds `frontend/` beside it.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py

HERE = Path(__file__).resolve().parent
FRONTEND = HERE / "frontend"
#  Inside the package, where `_find_frontend` looks first.
TARGET = Path("vistest") / "frontend"
#  Notes for people reading the source, not files the interface loads.
NOT_SHIPPED = ("*.md",)


class BuildWithFrontend(build_py):
    def run(self) -> None:
        super().run()
        if getattr(self, "editable_mode", False):
            return
        if not (FRONTEND / "index.html").is_file():
            raise SystemExit(
                f"{FRONTEND / 'index.html'} is missing: a wheel without the "
                "review UI would serve 404 on /ui/. Build from a full checkout "
                "or from the sdist (MANIFEST.in includes frontend/).")
        dest = Path(self.build_lib) / TARGET
        if dest.exists():
            shutil.rmtree(dest)
        for src in sorted(FRONTEND.rglob("*")):
            if not src.is_file() or any(src.match(p) for p in NOT_SHIPPED):
                continue
            out = dest / src.relative_to(FRONTEND)
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, out)


setup(cmdclass={"build_py": BuildWithFrontend})
