# VisTest - visual regression testing for Python and Playwright.
# Copyright 2026 Kirill Kulagin
# SPDX-License-Identifier: Apache-2.0
#
# This file is part of VisTest, licensed under the Apache License, Version 2.0.
# You may not use it except in compliance with the License. See LICENSE for
# the terms and NOTICE for attribution and the trademark.

"""`vistest --help` in two groups, and a server command without the server
(review v1: R6, 3.1, 3.9; o1b).

Twenty-four commands in one list, half of them the server's, and `vistest
serve` with `vistest[browser]` died on `ModuleNotFoundError: No module named
'fastapi'`. The library's commands come first. The server's come from another
package, `vistest-server`; without it, `--help` says so in one line, and a
command that is not the library's says so in one line and exits 2.
"""

from __future__ import annotations

import re

import pytest

from vistest import cli
from vistest._extras import NOT_INSTALLED

from . import _no_server

LIBRARY = ["compare", "check", "bench", "doctor", "baselines"]

#: Every server command, with the least it parses with.
SERVER = {
    "snap": [], "matrix": [], "record": [], "codegen": [], "project": ["list"],
    "report": [], "evidence": [], "user": ["list"], "serve": [], "push": [],
    "list": [], "approve": ["a.png", "--actual", "a.png"], "rm": [], "comment": [],
    "notify": [], "gate": [], "backup": ["a.tar.gz"], "restore": ["a.tar.gz"],
    "prune": [],
}


def _help(capsys) -> str:
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    return capsys.readouterr().out


def _section(text: str, head: str) -> list[str]:
    block = text.split(head, 1)[1].split("\n\n", 1)[0]
    return re.findall(r"^  ([a-z]+) ", block, re.M)


@pytest.fixture
def no_server(monkeypatch):
    """The server's package offers nothing: no `vistest.commands` entry point."""
    monkeypatch.setattr(cli, "_server_commands", lambda sub: None)


def test_help_without_the_server_says_so_in_one_line(no_server, capsys):
    text = _help(capsys)
    assert _section(text, "library:") == LIBRARY
    assert f"server: {NOT_INSTALLED}." in text
    assert not set(SERVER) & set(re.findall(r"^  ([a-z]+) ", text, re.M))


@pytest.mark.parametrize("command", sorted(SERVER))
def test_a_server_command_without_the_server_is_one_line_and_exit_2(
        command, no_server, monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert cli.main([command, *SERVER[command]]) == 2
    err = capsys.readouterr().err.strip()
    assert err == f"vistest {command}: not a library command; {NOT_INSTALLED}"


def test_check_through_a_server_needs_the_server_too(no_server, monkeypatch, capsys,
                                                     tmp_path):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["check", "a.png", "a.png", "--api", "http://127.0.0.1:9"]) == 2
    assert capsys.readouterr().err.strip() == \
        f"vistest check: --api is the server's; {NOT_INSTALLED}"


def test_in_a_child_without_the_server_serve_says_it_and_compare_works(tmp_path):
    import numpy as np

    from vistest.core import pngio

    served = _no_server.run("vistest", ["serve"], tmp_path)
    assert served.returncode == 2, served.stdout + served.stderr
    assert served.stderr.strip() == f"vistest serve: not a library command; {NOT_INSTALLED}"
    assert "Traceback" not in served.stderr

    png = pngio.encode(np.full((20, 30, 3), 90, np.uint8))
    (tmp_path / "a.png").write_bytes(png)
    compared = _no_server.run("vistest", ["compare", "a.png", "a.png", "-o", "out"],
                              tmp_path)
    assert compared.returncode == 0, compared.stdout + compared.stderr
