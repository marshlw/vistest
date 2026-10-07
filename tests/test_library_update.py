# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""`--vistest-update=missing|changed|all`: what gets written into the repository.

It used to be one flag with one meaning — write every baseline whose bytes
differ — and that meaning was wrong for the everyday case. Accept two real
changes, and every check that passed on a re-encoded or subpixel-shifted
picture got its baseline rewritten too: a pull request touching every PNG in
the project, with the two that matter invisible in it.

Four snapshots cover every case a mode has to decide about:

    new.png      no baseline yet
    same.png     the new picture is byte-identical to the baseline
    noisy.png    different bytes, and the check passes
    broken.png   the check fails

and each mode is one row of what gets written:

    missing   new
    changed   new, broken            (the default, and a bare flag)
    all       new, noisy, broken     (same.png is identical: nothing to write)
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from vistest import expect_screenshot
from vistest.core import pngio
from vistest.library import context as _context
from vistest.library.errors import BaselineMissing, ScreenshotMismatch

ROOT = Path(__file__).resolve().parents[1]


def picture(kind: str, state: str) -> bytes:
    img = np.full((80, 120, 3), 200, np.uint8)
    img[10:30, 10:60] = 40
    if state == "after":
        if kind == "noisy":
            img[0, 0] = 201                  # one unit on one pixel: noise
        elif kind == "broken":
            img[40:70, 60:110] = 20          # a block appeared
    return pngio.encode(img)


NAMES = ("same", "noisy", "broken")


@pytest.fixture
def ctx(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    context = _context.LibraryContext(root=tmp_path)
    _context.install(context)
    context.update = "all"
    for name in NAMES:
        expect_screenshot(picture(name, "before"), f"{name}.png")
    context.update = False
    yield context
    _context.uninstall()


def run_all(ctx, mode) -> dict[str, str]:
    """Every check once, in the given mode -> what each one did."""
    ctx.update = mode
    outcome = {}
    for name in ("new", *NAMES):
        try:
            expect_screenshot(picture(name, "after"), f"{name}.png")
            outcome[name] = "ok"
        except BaselineMissing:
            outcome[name] = "missing"
        except ScreenshotMismatch:
            outcome[name] = "fail"
    return outcome


def written(ctx) -> set[str]:
    """Which baselines were written by the run: created, or now «after».

    `same.png` can never appear: its «after» picture is its «before» picture,
    and an identical picture is not written.
    """
    out = set()
    for name in ("new", *NAMES):
        path = ctx.baselines / f"{name}.png"
        if not path.exists():
            continue
        after, before = picture(name, "after"), picture(name, "before")
        if name == "new" or (after != before and path.read_bytes() == after):
            out.add(name)
    return out


def test_the_noisy_pair_really_passes_and_the_broken_one_really_fails(ctx):
    """The control: without it the rows below could be right by accident."""
    assert picture("noisy", "after") != picture("noisy", "before")
    assert run_all(ctx, None) == {"new": "missing", "same": "ok", "noisy": "ok",
                                  "broken": "fail"}


def test_missing_writes_only_what_does_not_exist(ctx):
    outcome = run_all(ctx, "missing")
    assert written(ctx) == {"new"}
    assert outcome["broken"] == "fail", "an existing baseline is not touched"


def test_changed_writes_the_missing_and_the_failed(ctx):
    outcome = run_all(ctx, "changed")
    assert written(ctx) == {"new", "broken"}
    assert outcome == {"new": "ok", "same": "ok", "noisy": "ok", "broken": "ok"}


def test_all_writes_everything_that_differs_by_a_byte(ctx):
    run_all(ctx, "all")
    assert written(ctx) == {"new", "noisy", "broken"}


def test_true_means_changed(ctx):
    """What the flag used to be, and what the environment variable still is."""
    run_all(ctx, True)
    assert written(ctx) == {"new", "broken"}


def test_nonsense_is_refused_by_name(ctx):
    ctx.update = "everything"
    with pytest.raises(ValueError, match="everything"):
        expect_screenshot(picture("same", "after"), "same.png")


def test_the_report_row_names_the_mode_on_a_new_baseline(ctx):
    import json

    ctx.update = "missing"
    expect_screenshot(picture("new", "after"), "new.png")
    rows = [json.loads(p.read_text("utf-8")) for p in ctx.parts_dir.glob("*.json")]
    created = [r for r in rows if r["action"] == "created" and r["name"] == "new.png"]
    assert created and "--vistest-update=missing" in created[0]["reason"]


def test_the_plugin_and_the_context_agree_on_the_modes():
    from vistest import pytest_plugin

    assert pytest_plugin._UPDATE_MODES == _context.UPDATE_MODES
    assert _context.DEFAULT_UPDATE_MODE == "changed"


def test_the_environment_variable_means_changed(monkeypatch, tmp_path):
    monkeypatch.setenv(_context.ENV_UPDATE, "1")
    assert _context.LibraryContext.from_env(tmp_path).update_mode == "changed"


# --------------------------------------------------------------------------- #
#  From the command line, in a project that is not this one
# --------------------------------------------------------------------------- #
TEST_FILE = '''
import os

import numpy as np

from vistest import expect_screenshot
from vistest.core import pngio


def picture(kind):
    img = np.full((80, 120, 3), 200, np.uint8)
    img[10:30, 10:60] = 40
    if os.environ.get("DEMO_STATE") == "after":
        if kind == "noisy":
            img[0, 0] = 201
        elif kind == "broken":
            img[40:70, 60:110] = 20
    return pngio.encode(img)


def test_same():
    expect_screenshot(picture("same"), "same.png")


def test_noisy():
    expect_screenshot(picture("noisy"), "noisy.png")


def test_broken():
    expect_screenshot(picture("broken"), "broken.png")
'''


def _plugin_args() -> list[str]:
    from importlib.metadata import entry_points

    registered = any(e.value == "vistest.pytest_plugin"
                     for e in entry_points(group="pytest11"))
    return [] if registered else ["-p", "vistest.pytest_plugin"]


def run(project: Path, *args: str, state: str = "before"):
    env = {k: v for k, v in os.environ.items() if not k.startswith("VISTEST_")}
    env.pop("PYTEST_ADDOPTS", None)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT), *([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])])
    env["DEMO_STATE"] = state
    #  Written and read as UTF-8 whatever the console's code page (review A2).
    env["PYTHONIOENCODING"] = "utf-8"
    done = subprocess.run(
        [sys.executable, "-m", "pytest", *_plugin_args(), "-p", "no:cacheprovider",
         "-q", *args], cwd=project, env=env, capture_output=True, encoding="utf-8",
        timeout=300)
    return done.returncode, done.stdout + done.stderr


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_visual.py").write_text(TEST_FILE, "utf-8")
    code, output = run(tmp_path, "--vistest-update=all")
    assert code == 0, output
    return tmp_path


def _stamps(project: Path) -> dict[str, int]:
    root = project / "tests" / "__vistest__"
    return {p.stem: p.stat().st_mtime_ns for p in root.glob("*.png")}


@pytest.mark.parametrize("args, rewritten", [
    (("--vistest-update",), {"broken"}),
    (("--vistest-update=changed",), {"broken"}),
    (("--vistest-update", "missing"), set()),
    (("--vistest-update=all",), {"noisy", "broken"}),
])
def test_each_spelling_from_the_command_line(project, args, rewritten):
    before = _stamps(project)
    code, output = run(project, *args, state="after")
    after = _stamps(project)
    changed = {name for name in before if after[name] != before[name]}
    assert changed == rewritten, output
    if rewritten >= {"broken"}:
        assert code == 0, output
    assert "--vistest-update=" in output          # the summary names the mode


def test_missing_leaves_the_failure_red(project):
    code, output = run(project, "--vistest-update=missing", state="after")
    assert code != 0 and "broken.png" in output, output


def test_a_path_after_a_bare_flag_is_explained_not_swallowed(project):
    code, output = run(project, "--vistest-update", "tests", state="after")
    assert code != 0
    assert "'tests' is not a mode" in output, output
    assert "--vistest-update=changed" in output, output


def test_a_path_before_a_bare_flag_works(project):
    code, output = run(project, "tests", "--vistest-update", state="after")
    assert code == 0, output
