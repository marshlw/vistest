# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Engine v1 and the presets are off the public surface (review v1: R5, 8.1).

Nothing a project writes chooses v1 or a preset any more: not the call, not
vistest.yaml, not the environment, not a flag of pytest or of the CLI. Each
of those is refused in one line that says why, rather than ignored. v1 stays
inside — the benchmark, the bench corpus and the server choose it in code —
and that is tested where it is used.
"""

from __future__ import annotations

import inspect
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from vistest import expect_screenshot
from vistest.config import ConfigError, VisTestConfig
from vistest.core import pngio

GONE = "v1 and presets were removed before the first release; v2 is the only engine"


def test_the_call_has_no_engine():
    assert "engine" not in inspect.signature(expect_screenshot).parameters
    with pytest.raises(TypeError, match="unexpected keyword argument 'engine'"):
        expect_screenshot(b"", "x.png", engine="v1")


@pytest.mark.parametrize("text, key", [
    ("engine: v1\n", "engine"),
    ("engine: v2\n", "engine"),
    ("preset: strict\n", "preset"),
    ("preset: balanced\n", "preset"),
    ("diff:\n  engine: v1\n", "diff.engine"),
    ("diff:\n  preset: loose\n", "diff.preset"),
])
def test_vistest_yaml_that_chooses_an_engine_or_a_preset_is_refused(tmp_path, monkeypatch,
                                                                    text, key):
    monkeypatch.delenv("VISTEST_ENGINE", raising=False)
    path = tmp_path / "vistest.yaml"
    path.write_text(text, "utf-8")
    with pytest.raises(ConfigError) as refused:
        VisTestConfig.load(path)
    assert str(refused.value) == f"{path}: {key}: {GONE}"


@pytest.mark.parametrize("value", ["v1", "v2"])
def test_vistest_engine_in_the_environment_is_refused(tmp_path, monkeypatch, value):
    monkeypatch.setenv("VISTEST_ENGINE", value)
    with pytest.raises(ConfigError, match=f"^VISTEST_ENGINE: {GONE}$"):
        VisTestConfig.load(tmp_path / "missing.yaml")


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("VISTEST_")}
    return subprocess.run([sys.executable, *args], cwd=cwd, capture_output=True,
                          encoding="utf-8", timeout=180,
                          env={**env, "PYTHONIOENCODING": "utf-8"})


def test_pytest_has_no_preset_flag(tmp_path):
    (tmp_path / "test_x.py").write_text("def test_x():\n    pass\n", "utf-8")
    done = _run(["-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:xdist",
                 "--vistest-preset", "balanced", "test_x.py"], tmp_path)
    assert done.returncode == 4, done.stdout + done.stderr
    assert "unrecognized arguments: --vistest-preset" in done.stderr


def test_pytest_says_a_preset_in_vistest_yaml_in_one_line(tmp_path):
    (tmp_path / "vistest.yaml").write_text("preset: strict\n", "utf-8")
    (tmp_path / "test_x.py").write_text("def test_x():\n    pass\n", "utf-8")
    done = _run(["-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:xdist",
                 "test_x.py"], tmp_path)
    assert done.returncode == 4, done.stdout + done.stderr
    said = [ln for ln in done.stderr.splitlines() if ln.startswith("ERROR: vistest:")]
    assert said == [f"ERROR: vistest: {tmp_path / 'vistest.yaml'}: preset: {GONE}"]


@pytest.mark.parametrize("args", [
    ["compare", "a.png", "b.png", "--engine", "v1"],
    ["compare", "a.png", "b.png", "--preset", "strict"],
    ["check", "a.png", "b.png", "--preset", "strict"],
])
def test_the_cli_has_no_engine_or_preset_flag(tmp_path, args):
    done = _run(["-m", "vistest.cli", *args], tmp_path)
    assert done.returncode == 2, done.stdout + done.stderr
    assert "unrecognized arguments: --" in done.stderr


def _png(fill: int, box: bool = False) -> bytes:
    picture = np.full((80, 120, 3), fill, np.uint8)
    if box:
        picture[10:40, 10:70] = 235
    return pngio.encode(picture)


def test_check_set_engine_is_refused_in_one_line(tmp_path):
    (tmp_path / "a.png").write_bytes(_png(40))
    done = _run(["-m", "vistest.cli", "check", "a.png", "a.png", "--api",
                 "http://127.0.0.1:9", "--set", "engine=v1"], tmp_path)
    assert done.returncode == 2, done.stdout + done.stderr
    assert done.stderr.strip() == f"vistest check: --set engine: {GONE}"


def test_compare_reads_vistest_yaml_instead_of_a_preset(tmp_path):
    """The preset `compare` built used to replace vistest.yaml whole (review v1, 2.1)."""
    (tmp_path / "a.png").write_bytes(_png(40))
    (tmp_path / "b.png").write_bytes(_png(40, box=True))
    fails = _run(["-m", "vistest.cli", "compare", "a.png", "b.png", "-o", "out"], tmp_path)
    assert fails.returncode == 1, fails.stdout + fails.stderr
    (tmp_path / "vistest.yaml").write_text(
        "diff:\n  fail_severity: 100\n  max_changed_area_pct: 100\n", "utf-8")
    passes = _run(["-m", "vistest.cli", "compare", "a.png", "b.png", "-o", "out"], tmp_path)
    assert passes.returncode == 0, passes.stdout + passes.stderr
