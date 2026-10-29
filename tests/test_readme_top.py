# VisTest - visual regression testing for Python and Playwright.
# Copyright 2026 Kirill Kulagin
# SPDX-License-Identifier: Apache-2.0
#
# This file is part of VisTest, licensed under the Apache License, Version 2.0.
# You may not use it except in compliance with the License. See LICENSE for
# the terms and NOTICE for attribution and the trademark.

"""The top of the README: what it is, the install, a six-line test, a failure (o4).

A person who opens the repository reads the top and nothing else, so what is
there has to be true as written:

* the six-line test is the same in README.md and README.ru.md, has no fixture
  and no mask, and runs: red without a baseline, `--vistest-update` writes
  one, green after — against a page served on localhost, its address put
  where the README has `http://localhost:8000`, nothing else changed;
* the failure under it is a real run's: the picture is `docs/img/failure.png`
  and the message is `docs/img/failure.txt`, both written by
  `scripts/docs_picture.py`, and both READMEs quote that message word for word;
* the picture is small enough for a README (at most 200 KB, at most 1280 px
  wide) and is linked by an absolute address, because PyPI shows the README
  too.
"""

from __future__ import annotations

import http.server
import importlib.util
import os
import re
import struct
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
READMES = ("README.md", "README.ru.md")
PICTURE = ROOT / "docs" / "img" / "failure.png"
MESSAGE = ROOT / "docs" / "img" / "failure.txt"
PICTURE_URL = "https://raw.githubusercontent.com/marshlw/vistest/main/docs/img/failure.png"
ADDRESS = "http://localhost:8000"


def _read(rel: str) -> str:
    return (ROOT / rel).read_text("utf-8")


def _top_test(doc: str) -> str:
    return re.search(r"```python\n(.*?)```", _read(doc), re.S).group(1)


def _before_the_first_section(doc: str) -> str:
    return _read(doc).split("\n## ", 1)[0]


# --------------------------------------------------------------------------- #
#  What the top says
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("doc", READMES)
def test_the_top_has_the_install_the_test_and_the_failure(doc):
    top = _before_the_first_section(doc)
    assert 'pip install "vistest[browser]"\nplaywright install chromium\n' in top
    assert _top_test(doc) in top
    assert "`pytest --vistest-update`" in top
    assert PICTURE_URL in top
    assert MESSAGE.read_text("utf-8") in top


def test_the_six_line_test_is_one_in_both_readmes():
    english, russian = (_top_test(doc) for doc in READMES)
    assert english == russian
    lines = english.splitlines()
    assert len(lines) <= 6, lines
    assert lines[0] == "from vistest import expect_screenshot"
    definition = next(ln for ln in lines if ln.startswith("def"))
    assert re.fullmatch(r"def test_\w+\(page\):", definition), definition
    assert sum(ADDRESS in ln and "page.goto(" in ln for ln in lines) == 1
    assert sum(ln.strip().startswith("expect_screenshot(page,") for ln in lines) == 1
    assert "fixture" not in english and "mask" not in english


@pytest.mark.parametrize("doc", READMES)
def test_the_readme_quotes_the_run_of_its_picture(doc):
    """The message under the picture is the one `docs_picture.py` wrote, in a block."""
    message = MESSAGE.read_text("utf-8")
    assert message.startswith("vistest: 'landing.png' differs from the baseline")
    assert f"```\n{message}```\n" in _read(doc)
    assert f"]({PICTURE_URL})" in _read(doc)


def test_the_picture_fits_a_readme():
    data = PICTURE.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    assert len(data) <= 200_000, len(data)
    assert 0 < width <= 1280 and height > 0, (width, height)


def test_the_picture_is_left_out_of_the_wheel():
    """`scripts/audit_dist.py` refuses an image and anything under docs/ in a wheel."""
    spec = importlib.util.spec_from_file_location(
        "audit_dist_under_test", ROOT / "scripts" / "audit_dist.py")
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)
    assert audit.IMAGE.search("docs/img/failure.png")
    assert audit.WHEEL_OUTSIDE.match("docs/img/failure.png")


# --------------------------------------------------------------------------- #
#  The six lines, run
# --------------------------------------------------------------------------- #
PAGE = b"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>home</title>
<style>body{margin:40px;font:16px sans-serif}
a{display:inline-block;background:#3478f6;color:#fff;padding:10px 24px;border-radius:8px}
</style></head>
<body><h1>Home</h1><p>Nothing here moves.</p><a href="#">Sign up</a></body></html>
"""


class _Page(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - the name http.server calls
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *args):
        pass


@pytest.fixture
def served():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Page)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _check_install():
    spec = importlib.util.spec_from_file_location(
        "check_install_for_the_top", ROOT / "scripts" / "check_install.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_six_lines_run_red_then_update_then_green(tmp_path, served):
    pytest.importorskip("pytest_playwright")
    junit_cases = _check_install().junit_cases
    test = _top_test("README.md")
    assert test.count(ADDRESS) == 1
    project = tmp_path / "project"
    (project / "tests").mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        '[project]\nname = "readme-top"\nversion = "0"\n\n[tool.pytest.ini_options]\n',
        encoding="utf-8")
    (project / "tests" / "test_home.py").write_text(test.replace(ADDRESS, served),
                                                    encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("VISTEST_")}
    env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    report = project / "junit.xml"

    def run(*extra):
        report.unlink(missing_ok=True)
        done = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/test_home.py", "-q",
             "-p", "no:cacheprovider", f"--junitxml={report}", *extra],
            cwd=project, env=env, capture_output=True, text=True, encoding="utf-8",
            timeout=600)
        said = (done.stdout or "") + (done.stderr or "")
        if "Executable doesn't exist" in said or "playwright install" in said:
            pytest.skip("no Chromium for Playwright on this machine")
        assert report.is_file(), said[-1500:]
        return junit_cases(report), said

    baselines = project / "tests" / "__vistest__"

    def written():
        if not baselines.is_dir():
            return []
        return sorted(p.relative_to(baselines).as_posix() for p in baselines.rglob("home.png"))

    cases, said = run()
    assert [(o, e.rsplit(".", 1)[-1]) for _, o, e in cases] == \
        [("failed", "BaselineMissing")], said[-1500:]
    assert written() == []

    cases, said = run("--vistest-update")
    assert [o for _, o, _ in cases] == ["passed"], said[-1500:]
    assert len(written()) == 1 and written()[0].endswith("/home.png"), written()

    before = (baselines / written()[0]).read_bytes()
    cases, said = run()
    assert [o for _, o, _ in cases] == ["passed"], said[-1500:]
    assert (baselines / written()[0]).read_bytes() == before
