# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The command line, as a newcomer meets it (review v1: 3.3, 3.4, 3.6, 3.7, 3.8)."""

from __future__ import annotations

import io
import os
import subprocess
import sys

import numpy as np
import pytest

import vistest
from vistest import _term, cli
from vistest.core import pngio


def _vistest(*args: str, cwd, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, **(env_extra or {})}
    env.pop("PYTHONUTF8", None)
    return subprocess.run([sys.executable, "-m", "vistest.cli", *args], cwd=cwd, env=env,
                          capture_output=True, timeout=300)


# --- 3.8 ------------------------------------------------------------------- #
def test_the_version_is_one_flag_away(capsys):
    with pytest.raises(SystemExit) as done:
        cli.main(["--version"])
    assert done.value.code == 0
    assert capsys.readouterr().out.strip() == f"vistest {vistest.__version__}"


# --- 3.4 and 3.6 ----------------------------------------------------------- #
@pytest.fixture
def two_pngs(tmp_path):
    a = np.full((60, 80, 3), 255, np.uint8)
    b = a.copy()
    b[20:40, 20:60] = (37, 99, 235)
    (tmp_path / "a.png").write_bytes(pngio.encode(a))
    (tmp_path / "b.png").write_bytes(pngio.encode(b))
    return tmp_path


def test_compare_does_not_die_on_a_stdout_that_cannot_hold_delta(two_pngs):
    done = _vistest("compare", "a.png", "b.png", "-o", "out", cwd=two_pngs,
                    env_extra={"PYTHONIOENCODING": "cp1252"})
    err = done.stderr.decode("cp1252", "replace")
    assert "Traceback" not in err, err
    assert done.returncode == 1, err          # the pictures differ: a verdict, not a crash
    assert b"?E" in done.stdout or b"E00" in done.stdout


def test_an_expected_error_is_one_line_and_exit_2(two_pngs):
    done = _vistest("compare", "nope.png", "b.png", cwd=two_pngs)
    err = done.stderr.decode("utf-8", "replace").strip()
    assert done.returncode == 2
    assert "Traceback" not in err
    assert err.splitlines() == [err]
    assert err.startswith("vistest compare: cannot read nope.png")


def test_a_bug_still_gets_its_traceback(monkeypatch):
    monkeypatch.setattr(cli, "_compare", lambda args: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        cli.main(["compare", "a.png", "b.png"])


# --- 3.7 ------------------------------------------------------------------- #
class _Tty(io.StringIO):
    def isatty(self):
        return True


def test_colour_only_on_a_terminal(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    assert _term.Colours(io.StringIO())["r"] == ""
    assert _term.Colours(_Tty())["r"] == "\033[31m"


def test_no_color_wins_over_a_terminal_and_force_color_over_a_pipe(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert _term.Colours(_Tty())["g"] == ""
    monkeypatch.delenv("NO_COLOR")
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert _term.Colours(io.StringIO())["g"] == "\033[32m"


def test_every_command_that_colours_uses_the_one_switch():
    """The library's half; the server's commands are checked in
    server/tests/test_cli_review_a_server.py."""
    from vistest import doctor

    assert doctor.C is _term.C


def test_doctor_writes_no_escape_codes_into_a_pipe(tmp_path):
    pytest.importorskip("playwright.sync_api")
    page = tmp_path / "page.html"
    page.write_text("<h1>steady</h1>", encoding="utf-8")
    done = _vistest("doctor", page.as_uri(), "--runs", "2", cwd=tmp_path,
                    env_extra={"NO_COLOR": "", "FORCE_COLOR": ""})
    out = done.stdout + done.stderr
    #  No browser here: Playwright's own words, or the library's hint, which
    #  replaces them and always names the command (vistest.matrix.install_hint).
    if b"Executable doesn't exist" in out or b"playwright install chromium" in out:
        pytest.skip("no Chromium for Playwright on this machine")
    assert b"suitable for visual testing" in done.stdout, out
    assert b"\033[" not in out


def test_the_switch_is_a_mapping_like_the_dict_it_replaces(monkeypatch):
    """`render_text(color=False)` does `dict.fromkeys(C, "")`."""
    monkeypatch.setenv("NO_COLOR", "1")
    assert dict.fromkeys(_term.C, "") == dict.fromkeys(_term.CODES, "")
    assert _term.C.get("r") == "" and _term.C.get("nope", "x") == "x"
    with pytest.raises(KeyError):
        _term.C["nope"]
