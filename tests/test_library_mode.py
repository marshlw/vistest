# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The library mode, end to end, in a project that is not this one.

Everything else about the library mode can be tested in-process; this cannot.
The promise is «drop it into somebody's repository and it behaves like a
built-in screenshot assertion», and the parts that make or break that promise
are all outside the interpreter running these tests: the plugin registering,
the flags being understood, the baseline landing in the working tree, the exit
code, and the text a person reads in CI when it goes red.

So a temporary project is built on disk, and pytest is started in it as a
subprocess, four times, in the order a person meets them:

    pytest                    -> red: there is no baseline
    pytest --vistest-update   -> the PNG appears under tests/__vistest__/
    pytest                    -> green
    (the page changes)
    pytest                    -> red, with the diff and the report named

No browser is involved. The target is PNG bytes, which is one of the accepted
kinds and the only one that makes this test runnable anywhere.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

#  The project under test. `DEMO_STATE` is what stands in for «somebody changed
#  the page»: the same test, a different picture.
TEST_FILE = '''
import os

import numpy as np

from vistest import expect_screenshot
from vistest.core import pngio


def _screenshot():
    frame = np.full((80, 120, 3), 40, np.uint8)
    if os.environ.get("DEMO_STATE") == "after":
        frame[10:50, 10:90] = 230
    return pngio.encode(frame)


def test_home():
    expect_screenshot(_screenshot(), "home.png")
'''

SECOND_TEST_FILE = '''
import numpy as np

from vistest import expect_screenshot
from vistest.core import pngio


def test_checkout():
    expect_screenshot(pngio.encode(np.full((60, 90, 3), 90, np.uint8)),
                      "checkout.png")
'''


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_visual.py").write_text(TEST_FILE, "utf-8")
    return tmp_path


def _plugin_args() -> list[str]:
    """Load the plugin by hand only when the entry point is not registered.

    Normally it is: `pip install -e .` (or the `.egg-info` left by a build)
    puts vistest into the `pytest11` group and pytest picks it up on its own —
    which is the path a user takes and therefore the one worth testing. In a
    checkout with no metadata at all there is nothing to pick up, and the
    explicit `-p` keeps this test about the library mode rather than about
    whether somebody remembered to install the package.

    Passing both would fail: pytest refuses to register one plugin twice under
    two names.
    """
    from importlib.metadata import entry_points

    registered = any(e.value == "vistest.pytest_plugin"
                     for e in entry_points(group="pytest11"))
    return [] if registered else ["-p", "vistest.pytest_plugin"]


PLUGIN_ARGS = _plugin_args()


def run(project: Path, *args: str, state: str = "before"):
    """pytest, in that project, with this checkout importable."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("VISTEST_")}
    env.pop("PYTEST_ADDOPTS", None)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT), *([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])])
    env["DEMO_STATE"] = state
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", *PLUGIN_ARGS,
         "-p", "no:cacheprovider", "-q", *args],
        cwd=project, env=env, capture_output=True, text=True, timeout=300)
    return completed.returncode, completed.stdout + completed.stderr


# --------------------------------------------------------------------------- #
#  The scenario, in order
# --------------------------------------------------------------------------- #
def test_the_whole_first_encounter(project: Path):
    baseline = project / "tests" / "__vistest__" / "home.png"
    report = project / ".vistest" / "report" / "index.html"

    # ---- 1. no baseline: red, and it says what to do ------------------ #
    code, output = run(project)
    assert code != 0, output
    assert "no baseline" in output
    assert "home.png" in output
    assert "--vistest-update" in output
    assert not baseline.exists(), "a run without the flag must not write one"

    # ---- 2. accept ---------------------------------------------------- #
    code, output = run(project, "--vistest-update")
    assert code == 0, output
    assert baseline.exists(), output
    assert baseline.read_bytes()[:8] == PNG_SIGNATURE
    assert (project / "tests" / "__vistest__" / "home.json").exists()

    # ---- 3. green ----------------------------------------------------- #
    code, output = run(project)
    assert code == 0, output

    # ---- 4. the page changes: red, with everything named -------------- #
    code, output = run(project, state="after")
    assert code != 0, output
    assert "differs from the baseline" in output
    for expected in ("baseline:", "actual:", "diff:", "report:"):
        assert expected in output, output
    assert report.exists()
    assert (project / ".vistest" / "diff" / "home.png").exists()

    body = report.read_text("utf-8")
    assert "data:image/png;base64," in body        # pictures are inside it
    assert "http://" not in body and "https://" not in body


def test_the_baseline_is_left_alone_when_it_already_matches(project: Path):
    """`--vistest-update` over an unchanged suite must not dirty the tree.

    Otherwise accepting two snapshots produces a pull request touching every
    baseline in the project, and the two that matter are invisible in it.
    """
    assert run(project, "--vistest-update")[0] == 0
    baseline = project / "tests" / "__vistest__" / "home.png"
    before = baseline.stat().st_mtime_ns

    assert run(project, "--vistest-update")[0] == 0
    assert baseline.stat().st_mtime_ns == before


def test_the_baselines_directory_can_be_moved(project: Path):
    code, output = run(project, "--vistest-update",
                       "--vistest-baselines", "tests/snapshots")
    assert code == 0, output
    assert (project / "tests" / "snapshots" / "home.png").exists()
    assert not (project / "tests" / "__vistest__").exists()


def test_the_platform_directory_comes_from_the_flag(project: Path):
    code, output = run(project, "--vistest-update",
                       "--vistest-platform", "chromium-1440x900")
    assert code == 0, output
    assert (project / "tests" / "__vistest__" / "chromium-1440x900"
            / "home.png").exists()


def test_the_report_goes_where_it_is_asked_to(project: Path):
    code, output = run(project, "--vistest-update",
                       "--vistest-report", "out/visual.html")
    assert code == 0, output
    assert (project / "out" / "visual.html").exists()


def test_a_broken_setting_stops_the_run_and_names_itself(project: Path):
    """The rule of this stage, seen from the outside: config errors are loud."""
    (project / "vistest.yaml").write_text("diff:\n  nonsense: 3\n", "utf-8")
    code, output = run(project, "--vistest-update")
    assert code != 0
    assert "vistest.yaml" in output and "nonsense" in output


# --------------------------------------------------------------------------- #
#  In parallel
# --------------------------------------------------------------------------- #
def test_two_workers_produce_one_complete_report(project: Path):
    """`pytest -n 2`: separate processes, no shared file, one report at the end.

    The failure this guards against is not a crash. Each worker writes its own
    rows and the controller renders them, so a mistake here shows up as a
    report that is missing whatever the other worker did — or as two reports
    overwriting each other, which looks like a report that is simply wrong.
    """
    pytest.importorskip("xdist")
    (project / "tests" / "test_second.py").write_text(SECOND_TEST_FILE, "utf-8")

    code, output = run(project, "--vistest-update", "-n", "2")
    assert code == 0, output

    body = (project / ".vistest" / "report" / "index.html").read_text("utf-8")
    assert "home.png" in body and "checkout.png" in body
    assert body.count("<details") == 2

    #  And nothing half-written was left behind under any of them.
    for directory in ("tests/__vistest__", ".vistest"):
        leftovers = [p.name for p in (project / directory).rglob("*")
                     if p.name.endswith(".tmp")]
        assert leftovers == []


# --------------------------------------------------------------------------- #
#  Two tests, one name
# --------------------------------------------------------------------------- #
COLLIDING_A = '''
import numpy as np

from vistest import expect_screenshot
from vistest.core import pngio


def test_alpha():
    expect_screenshot(pngio.encode(np.full((40, 60, 3), 30, np.uint8)),
                      "shared.png")
'''

COLLIDING_B = '''
import numpy as np

from vistest import expect_screenshot
from vistest.core import pngio


def test_beta():
    expect_screenshot(pngio.encode(np.full((40, 60, 3), 200, np.uint8)),
                      "shared.png")
'''


def test_two_tests_writing_one_name_are_reported_as_a_collision(project: Path):
    """The failure this catches is silent, and under `--vistest-update` it is
    corruption of the user's repository.

    Two tests using the same snapshot name compare against one file. Whichever
    finishes last decides what the baseline is — and under `-n` that is not
    even the same test from run to run. The report used to show one row, the
    run went green, and nothing anywhere said that a check had been overwritten
    by another.

    Run under `-n 2` deliberately: the two tests land in different processes,
    which is the case a per-process check cannot see. The collision is found
    where every process's rows meet — in the controller, at the end.
    """
    pytest.importorskip("xdist")
    (project / "tests" / "test_visual.py").unlink()
    (project / "tests" / "test_alpha.py").write_text(COLLIDING_A, "utf-8")
    (project / "tests" / "test_beta.py").write_text(COLLIDING_B, "utf-8")

    code, output = run(project, "--vistest-update", "-n", "2")

    assert code != 0, "a run that overwrote a baseline must not be green\n" + output
    assert "name collision" in output, output
    assert "shared" in output
    assert "test_alpha.py::test_alpha" in output
    assert "test_beta.py::test_beta" in output
    assert "--vistest-update" in output          # says what it did to the tree

    body = (project / ".vistest" / "report" / "index.html").read_text("utf-8")
    assert "Name collision" in body
    assert "test_alpha.py::test_alpha" in body and "test_beta.py::test_beta" in body


def test_the_same_test_writing_twice_is_a_retry_and_stays_quiet(project: Path):
    """A rerun of one test is not a collision — one name, one owner."""
    (project / "tests" / "test_visual.py").write_text(TEST_FILE + '''

def test_home_again():
    expect_screenshot(_screenshot(), "second.png")
''', "utf-8")

    assert run(project, "--vistest-update")[0] == 0
    code, output = run(project)
    assert code == 0, output
    assert "name collision" not in output


# --------------------------------------------------------------------------- #
#  A part that cannot be read
# --------------------------------------------------------------------------- #
BROKEN_PART_FILE = '''
import pathlib

import numpy as np

from vistest import expect_screenshot
from vistest.core import pngio


def test_home():
    expect_screenshot(pngio.encode(np.full((40, 60, 3), 30, np.uint8)),
                      "home.png")
    #  What a worker killed mid-write leaves behind.
    parts = pathlib.Path(".vistest") / "report" / "parts"
    parts.mkdir(parents=True, exist_ok=True)
    (parts / "truncated.json").write_text('{"key": "home.png", "verd',
                                          encoding="utf-8")
'''


def test_a_part_that_cannot_be_read_is_counted_out_loud(project: Path):
    """Tolerating it is right; doing so in silence is not.

    A report with rows missing and no mention of it is worse than a report that
    failed to build: the second one is obviously broken, the first is read as
    complete.
    """
    (project / "tests" / "test_visual.py").write_text(BROKEN_PART_FILE, "utf-8")

    code, output = run(project, "--vistest-update")
    assert code == 0, output
    assert "1 check could not be read into this report" in output, output

    body = (project / ".vistest" / "report" / "index.html").read_text("utf-8")
    assert "could not be read into this report" in body


# --------------------------------------------------------------------------- #
#  Blast radius: our hooks run inside somebody else's session
# --------------------------------------------------------------------------- #
#  Recorded from a live trial on a foreign project. The fifth of eleven tests
#  found a real difference, the report hook raised while attaching the diff,
#  and because that hook is a hookwrapper pytest turned it into INTERNALERROR:
#  the session died mid-run, the remaining six tests never ran, and no report
#  was written. A screenshot differing is the most ordinary thing that can
#  happen to this tool, and it cost a whole run.
#
#  What these tests pin is not the specific exception — that one is fixed at
#  its source — but the property: whatever our decoration does, the session
#  survives it.
BREAK_REPORT_HOOK = '''
import vistest.pytest_plugin as plugin


def _explode(result):
    raise RuntimeError("attaching the diff went wrong")


plugin._attach_allure_result = _explode
'''

BREAK_REPORT_BUILD = '''
import vistest.report.library as report


def _explode(*args, **kwargs):
    raise RuntimeError("the report could not be built")


report.build = _explode
'''

FAILING_TEST = '''
import os

import numpy as np

from vistest import expect_screenshot
from vistest.core import pngio


def _screenshot():
    frame = np.full((80, 120, 3), 40, np.uint8)
    if os.environ.get("DEMO_STATE") == "after":
        frame[10:50, 10:90] = 230
    return pngio.encode(frame)


def test_one():
    expect_screenshot(_screenshot(), "one.png")


def test_two():
    expect_screenshot(_screenshot(), "two.png")


def test_three():
    assert True
'''


def test_a_broken_report_hook_does_not_kill_the_session(project: Path):
    """The original outage, reproduced through the hook that caused it."""
    (project / "tests" / "test_visual.py").write_text(FAILING_TEST, "utf-8")
    (project / "conftest.py").write_text(BREAK_REPORT_HOOK, "utf-8")

    assert run(project, "--vistest-update")[0] == 0          # baselines first

    code, output = run(project, state="after")

    assert "INTERNALERROR" not in output, output
    assert code == 1, output              # tests failed, the session did not
    #  Every test ran: two visual failures and the one that does not compare.
    assert "2 failed, 1 passed" in output, output
    assert "could not be attached to the report" in output, output


def test_a_report_that_cannot_be_built_does_not_kill_the_session(project: Path):
    """`pytest_sessionfinish` runs after the last test — and can still lose it.

    Nothing measured is at stake by then, which is exactly why an exception
    here is intolerable: it would turn a run that passed into a run that reads
    as broken.
    """
    (project / "tests" / "test_visual.py").write_text(FAILING_TEST, "utf-8")
    (project / "conftest.py").write_text(BREAK_REPORT_BUILD, "utf-8")

    code, output = run(project, "--vistest-update")

    assert "INTERNALERROR" not in output, output
    assert code == 0, output
    assert "3 passed" in output, output
    assert "the report could not be assembled" in output, output
