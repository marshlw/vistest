# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Two things the owner's check of step B found (review v1, step C, 8b and 8c).

8b: «does not read service.api_url — they change nothing»: one key is «it».
8c: `timeout_ms` on a page that stopped answering raises about 1.5 s after
the deadline — the time it takes to ask whether the page is alive and whether
its tab crashed. The diagnosis stays; the words say so, and the time is held.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

from vistest.config import VisTestConfig
from vistest.library import context as _context


def test_one_key_the_library_does_not_read_is_said_in_the_singular(tmp_path):
    path = tmp_path / "vistest.yaml"
    path.write_text("service:\n  api_url: http://127.0.0.1:9\n", "utf-8")
    text = _context.unread_text(VisTestConfig.load(path))
    assert text == (f"vistest: {path}: the library does not read service.api_url — it "
                    "changes nothing in this run (it is the server's setting)")


def test_two_keys_are_said_in_the_plural(tmp_path):
    path = tmp_path / "vistest.yaml"
    path.write_text("service:\n  api_url: http://127.0.0.1:9\n  project: shop\n", "utf-8")
    text = _context.unread_text(VisTestConfig.load(path))
    assert text.endswith("service.api_url, service.project — they change nothing in this "
                         "run (they are the server's settings)"), text


def test_the_docstring_says_what_a_hung_page_costs():
    from vistest import expect_screenshot

    said = " ".join(expect_screenshot.__doc__.split())
    assert "A page that stopped answering takes up to 1.5 s more, to say why" in said


HUNG = textwrap.dedent('''
    import sys, time, warnings
    warnings.simplefilter("ignore")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("SKIP no playwright"); sys.exit(0)
    from vistest import expect_screenshot
    from vistest.library.errors import CaptureError
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as e:
            print("SKIP", str(e).splitlines()[0]); sys.exit(0)
        page = browser.new_page()
        page.set_content("<h1>hi</h1>")
        page.evaluate("setTimeout(() => { while (true) {} }, 100)")
        page.wait_for_timeout(400)
        started = time.monotonic()
        try:
            expect_screenshot(page, "hung.png", timeout_ms=2000)
            print("NO ERROR")
        except CaptureError as e:
            print(f"TOOK {time.monotonic() - started:.2f} {e}")
        browser.close()
''')


def test_a_hung_page_with_timeout_ms_2000_fails_within_3_7_s(tmp_path):
    script = tmp_path / "hung.py"
    script.write_text(HUNG, encoding="utf-8")
    done = subprocess.run([sys.executable, str(script)], cwd=tmp_path, capture_output=True,
                          encoding="utf-8", timeout=120,
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    out = done.stdout + done.stderr
    if out.startswith("SKIP"):
        pytest.skip(out.strip())
    assert out.startswith("TOOK "), out
    took = float(out.split()[1])
    assert took <= 3.7, out
    assert "stopped answering" in out, out

