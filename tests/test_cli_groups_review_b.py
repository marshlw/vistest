# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""`vistest --help` in two groups, and a server command without the server
(review v1: R6, 3.1, 3.9).

Twenty-four commands in one list, half of them the server's, and `vistest
serve` with `vistest[browser]` died on `ModuleNotFoundError: No module named
'fastapi'`. The library's commands come first; the server's say what they need
and how to get it, in one line, and exit 2.
"""

from __future__ import annotations

import re

import pytest

from vistest import _extras, cli

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
NEEDS = 'needs vistest[server] — pip install "vistest[server]"'


def _help(capsys) -> str:
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    return capsys.readouterr().out


def _section(text: str, head: str) -> list[str]:
    block = text.split(head, 1)[1].split("\n\n", 1)[0]
    return re.findall(r"^  ([a-z]+) ", block, re.M)


def test_help_lists_the_library_first_and_the_server_with_what_it_needs(capsys):
    text = _help(capsys)
    assert text.index("library:") < text.index("server (needs vistest[server]")
    assert 'server (needs vistest[server]: pip install "vistest[server]"):' in text
    assert _section(text, "library:") == LIBRARY
    assert sorted(_section(text, "server (needs")) == sorted(SERVER)


def test_every_command_is_in_one_group_and_the_table_knows_them_all(capsys):
    text = _help(capsys)
    listed = _section(text, "library:") + _section(text, "server (needs")
    assert len(listed) == len(set(listed)) == len(LIBRARY) + len(SERVER)


@pytest.mark.parametrize("command", sorted(SERVER))
def test_a_server_command_without_the_server_is_one_line_and_exit_2(
        command, monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_extras, "server_missing", lambda: ["fastapi"])
    assert cli.main([command, *SERVER[command]]) == 2
    err = capsys.readouterr().err.strip()
    assert err == f"vistest {command}: this is the server's command and {NEEDS}"


def test_check_through_a_server_needs_the_server_too(monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_extras, "server_missing", lambda: ["requests"])
    assert cli.main(["check", "a.png", "a.png", "--api", "http://127.0.0.1:9"]) == 2
    assert capsys.readouterr().err.strip() == \
        f"vistest check: this is the server's command and {NEEDS}"


def test_in_a_child_without_the_server_serve_says_it_and_compare_works(tmp_path):
    import numpy as np

    from vistest.core import pngio

    served = _no_server.run("vistest", ["serve"], tmp_path)
    assert served.returncode == 2, served.stdout + served.stderr
    assert served.stderr.strip() == f"vistest serve: this is the server's command and {NEEDS}"
    assert "Traceback" not in served.stderr

    png = pngio.encode(np.full((20, 30, 3), 90, np.uint8))
    (tmp_path / "a.png").write_bytes(png)
    compared = _no_server.run("vistest", ["compare", "a.png", "a.png", "-o", "out"],
                              tmp_path)
    assert compared.returncode == 0, compared.stdout + compared.stderr
