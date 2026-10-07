# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""What the CLI and the matrix say is in English (review v1: 3.5).

`vistest --help` showed the help of `matrix` in Russian among English lines,
and a typo in `matrix.viewports` was reported in Russian — in a CI log on a
cp1252 runner as a row of `\\u04..` escapes. The comments
of these files are the i18n ratchet's business; the strings a person sees
are this test's.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from vistest import cli
from vistest.config import ConfigError, VisTestConfig

ROOT = Path(__file__).resolve().parent.parent
CYRILLIC = re.compile("[\u0400-\u04ff]")


def _visible_strings(path: Path) -> list[tuple[int, str]]:
    """String constants that are not docstrings."""
    tree = ast.parse(path.read_text("utf-8"))
    docs = {id(node.body[0].value) for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef))
            and node.body and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)}
    return [(n.lineno, n.value) for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docs]


@pytest.mark.parametrize("module", ["vistest/cli.py", "vistest/matrix.py"])
def test_no_string_a_person_sees_is_in_russian(module):
    russian = [(line, text[:60]) for line, text in _visible_strings(ROOT / module)
               if CYRILLIC.search(text)]
    assert not russian, russian


def test_help_has_no_russian(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    for command in ("snap", "matrix"):
        with pytest.raises(SystemExit):
            cli.main([command, "--help"])
    assert not CYRILLIC.search(capsys.readouterr().out)


def test_vistest_matrix_says_it_in_english(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["matrix"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("No matrix is declared — one variant, as before.")
    assert "1 variant:" in out and "Baselines: " in out
    assert not CYRILLIC.search(out)


def test_a_bad_window_size_in_vistest_yaml_is_said_in_english(tmp_path):
    path = tmp_path / "vistest.yaml"
    path.write_text("matrix:\n  viewports: ['1440*900']\n", "utf-8")
    with pytest.raises(ConfigError) as refused:
        VisTestConfig.load(path)
    assert str(refused.value) == (f"{path}: matrix: window size '1440*900' is not "
                                  "WIDTHxHEIGHT, for example 1440x900")
