# VisTest - visual regression testing for Python and Playwright.
# Copyright 2026 Kirill Kulagin
# SPDX-License-Identifier: Apache-2.0
#
# This file is part of VisTest, licensed under the Apache License, Version 2.0.
# You may not use it except in compliance with the License. See LICENSE for
# the terms and NOTICE for attribution and the trademark.

"""Verify the README's CLI demo and the six-line Playwright quickstart.

Both languages link to the same preview and demonstrate the same comparison.
The downloadable screenshots are exercised from the local browser corpus.
The Playwright test runs against a temporary localhost site and goes through
a missing baseline, baseline creation, and a successful comparison.
"""

from __future__ import annotations

import http.server
import importlib.util
import os
import re
import shlex
import shutil
import struct
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
READMES = ("README.md", "README.ru.md")
PICTURE = ROOT / "docs" / "img" / "underline-demo.png"
RAW_URL = "https://raw.githubusercontent.com/marshlw/vistest/"
PICTURE_URL = f"{RAW_URL}main/docs/img/{PICTURE.name}"
ADDRESS = "http://localhost:8000"


def _read(rel: str) -> str:
    return (ROOT / rel).read_text("utf-8")


def _top_test(doc: str) -> str:
    return re.search(r"```python\n(.*?)```", _read(doc), re.S).group(1)


# --------------------------------------------------------------------------- #
#  What the top says
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("doc", READMES)
def test_the_demo_is_the_first_action(doc):
    text = _read(doc)
    top, first_section = text.split("\n## ", 1)
    heading = first_section.splitlines()[0]
    anchor = "#" + heading.lower().replace(" ", "-")
    assert f"]({PICTURE_URL})" in top
    assert re.search(rf"\*\*\[[^\]]+\]\({re.escape(anchor)}\)\*\*", top)
    assert "vistest compare " in first_section.split("\n## ", 1)[0]
    assert text.index("vistest compare ") < text.index(_top_test(doc))
    assert 'pip install --pre "vistest[browser]"' in text
    assert "python -m playwright install chromium" in text
    assert "--vistest-update=missing" in text


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
def test_the_demo_reproduces_the_line_in_the_preview(doc, tmp_path, monkeypatch, capsys):
    from vistest import cli

    text = _read(doc)
    block = re.search(r"```bash\n(.*?)```", text, re.S).group(1)
    install, *downloads, compare = map(shlex.split, block.splitlines())
    assert install == ["python", "-m", "pip", "install", "vistest==0.2.0a5"]
    assert compare[:2] == ["vistest", "compare"]
    assert len(downloads) == 2
    for command, flags, url in downloads:
        assert (command, flags) == ("curl", "-fLO")
        assert url.startswith(RAW_URL)
        revision, file = url.removeprefix(RAW_URL).split("/", 1)
        assert re.fullmatch(r"[0-9a-f]{40}", revision)
        source = ROOT / file
        assert source.parent == ROOT / "tests/browser_corpus/frames/table"
        shutil.copyfile(source, tmp_path / source.name)

    monkeypatch.chdir(tmp_path)
    assert cli.main(compare[1:]) == 1
    assert "line added: 57×1 px, #2563eb" in capsys.readouterr().out
    assert "57×1 px" in text and "#2563eb" in text
    assert (tmp_path / "vistest-diff/boxes.png").is_file()


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
    path = PICTURE.relative_to(ROOT).as_posix()
    assert audit.IMAGE.search(path)
    assert audit.WHEEL_OUTSIDE.match(path)


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

    cases, said = run("--vistest-update=missing")
    assert [o for _, o, _ in cases] == ["passed"], said[-1500:]
    assert len(written()) == 1 and written()[0].endswith("/home.png"), written()

    before = (baselines / written()[0]).read_bytes()
    cases, said = run()
    assert [o for _, o, _ in cases] == ["passed"], said[-1500:]
    assert (baselines / written()[0]).read_bytes() == before
