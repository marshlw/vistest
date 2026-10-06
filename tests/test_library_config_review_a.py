# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""vistest.yaml as the library reads it (review v1: 2.4, 2.6)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from vistest import expect_screenshot
from vistest.config import VisTestConfig
from vistest.core import pngio
from vistest.core.settings import ConfigError
from vistest.library import context as _context


# --- 2.4: the service's project is not a folder of the library ------------ #
@pytest.mark.parametrize("how", ["yaml", "env"])
def test_the_service_project_does_not_move_the_librarys_baselines(tmp_path, monkeypatch, how):
    monkeypatch.chdir(tmp_path)
    if how == "yaml":
        (tmp_path / "vistest.yaml").write_text("service:\n  project: shop\n", "utf-8")
    else:
        monkeypatch.setenv("VISTEST_PROJECT", "shop")
    ctx = _context.LibraryContext(root=tmp_path, platform_override="p", update=True)
    _context.install(ctx)
    try:
        expect_screenshot(pngio.encode(np.zeros((8, 8, 3), np.uint8)), "home.png")
    finally:
        _context.uninstall()
    written = sorted(str(p.relative_to(tmp_path)).replace("\\", "/")
                     for p in tmp_path.rglob("home.png") if "__vistest__" in p.parts)
    assert written == ["__vistest__/p/home.png"]


# --- 2.6: a typo is refused with the nearest name --------------------------- #
def _load(tmp_path: Path, text: str) -> VisTestConfig:
    path = tmp_path / "vistest.yaml"
    path.write_text(text, "utf-8")
    return VisTestConfig.load(path)


def test_a_misspelt_section_is_refused_not_ignored(tmp_path):
    with pytest.raises(ConfigError, match=r"vistest\.yaml: unknown key 'captur' — did you "
                                          r"mean 'capture'\?"):
        _load(tmp_path, "captur:\n  keep_pointer: true\n")


def test_a_misspelt_top_level_choice_is_refused(tmp_path):
    with pytest.raises(ConfigError, match=r"unknown key 'engin' — did you mean 'engine'\?"):
        _load(tmp_path, "engin: v1\n")


def test_a_misspelt_key_in_a_section_names_the_file_and_the_nearest_key(tmp_path):
    with pytest.raises(ConfigError) as refused:
        _load(tmp_path, "capture:\n  keep_pointr: true\n")
    text = str(refused.value)
    assert text.startswith(str(tmp_path / "vistest.yaml"))
    assert "unknown key capture.keep_pointr — did you mean 'capture.keep_pointer'?" in text


def test_a_key_with_nothing_close_says_only_that_it_is_unknown(tmp_path):
    with pytest.raises(ConfigError, match=r"unknown key 'zzz'$"):
        _load(tmp_path, "zzz: 1\n")


def test_broken_yaml_is_a_config_error_naming_the_file(tmp_path):
    with pytest.raises(ConfigError, match=r"vistest\.yaml: not valid YAML"):
        _load(tmp_path, "capture: [1\n")


def test_a_section_that_is_not_a_mapping_is_refused(tmp_path):
    with pytest.raises(ConfigError, match="capture: expected a mapping"):
        _load(tmp_path, "capture: true\n")


def test_every_known_section_and_choice_still_loads(tmp_path):
    cfg = _load(tmp_path, "preset: balanced\nengine: v2\nupdate_baselines: false\n"
                          "plugins: {}\nflows: {}\ndiff: {}\ncapture: {}\nmatrix: {}\n"
                          "ai: {}\nrender: {}\npaths: {}\nservice: {}\nauth: {}\n")
    assert cfg.diff.engine == "v2"


def test_a_config_named_on_purpose_must_exist(tmp_path):
    (tmp_path / "test_x.py").write_text("def test_x():\n    pass\n", "utf-8")
    done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                           "-p", "no:xdist", "--vistest-config", "vistes.yaml", "test_x.py"],
                          cwd=tmp_path, capture_output=True, text=True, timeout=120)
    assert done.returncode == 4, done.stdout + done.stderr
    assert "ERROR: vistest: --vistest-config vistes.yaml: no such file" in done.stderr


@pytest.mark.parametrize("text, words", [
    ("capture:\n  keep_pointr: true\n", "did you mean 'capture.keep_pointer'?"),
    ("captur:\n  keep_pointer: true\n", "did you mean 'capture'?"),
])
def test_pytest_says_it_in_one_line_before_any_test(tmp_path, text, words):
    (tmp_path / "vistest.yaml").write_text(text, "utf-8")
    (tmp_path / "test_x.py").write_text("def test_x():\n    pass\n", "utf-8")
    done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                           "-p", "no:xdist", "test_x.py"], cwd=tmp_path,
                          capture_output=True, text=True, timeout=120,
                          env={k: v for k, v in os.environ.items() if k != "VISTEST_PROJECT"})
    out = done.stdout + done.stderr
    assert done.returncode == 4, out
    assert "INTERNALERROR" not in out
    said = [ln for ln in out.splitlines() if ln.startswith("ERROR: vistest:")]
    assert len(said) == 1 and words in said[0], out
