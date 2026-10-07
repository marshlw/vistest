# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""`vistest check` without a server is `expect_screenshot` on a file (review v1: R4, 3.2).

It wrote into the server's layout (`.vistest/baselines/<os>-chromium-1x/`),
which a library project does not read, created a baseline on the first run
with exit 0, and had no `--baselines`. The JS wrapper is going to stand on it,
so its contract is tested here: the exit codes, the files, the JSON.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from vistest import cli
from vistest.core import pngio


def _png(path: Path, box: bool = False) -> Path:
    picture = np.full((80, 120, 3), 40, np.uint8)
    if box:
        picture[10:40, 10:70] = 235
    path.write_bytes(pngio.encode(picture))
    return path


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.chdir(tmp_path)
    for name in ("VISTEST_API_URL", "VISTEST_BASELINES", "VISTEST_PLATFORM", "VISTEST_ROOT",
                 "VISTEST_FAIL_SEVERITY", "VISTEST_MAX_CHANGED_AREA_PCT"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "tests").mkdir()
    _png(tmp_path / "shot.png")
    _png(tmp_path / "changed.png", box=True)
    return tmp_path


def check(capsys, *args: str) -> tuple[int, dict]:
    code = cli.main(["check", *args, "--json"])
    out = capsys.readouterr().out.strip()
    assert len(out.splitlines()) == 1, out          # one object, nothing else
    return code, json.loads(out)


def test_red_then_update_then_green_then_red_after_a_change(project, capsys):
    code, said = check(capsys, "checkout.png", "shot.png")
    assert (code, said["verdict"]) == (2, "missing")
    assert not list((project / "tests" / "__vistest__").rglob("*.png"))
    assert said["message"].startswith("vistest: no baseline for 'checkout.png'")

    code, said = check(capsys, "checkout.png", "shot.png", "--update")
    assert (code, said["verdict"]) == (0, "new_baseline")
    assert Path(said["baseline"]) == project / "tests" / "__vistest__" / "checkout.png"
    assert Path(said["baseline"]).is_file()

    code, said = check(capsys, "checkout.png", "shot.png")
    assert (code, said["verdict"]) == (0, "pass")
    assert said["diff"] is None and Path(said["actual"]).is_file()

    code, said = check(capsys, "checkout.png", "changed.png")
    assert (code, said["verdict"]) == (1, "fail")
    assert "differs from the baseline" in said["message"]
    assert Path(said["diff"]).is_file()

    code, said = check(capsys, "checkout.png", "changed.png", "--update")
    assert (code, said["verdict"]) == (0, "new_baseline")
    assert check(capsys, "checkout.png", "changed.png")[0] == 0


def test_the_json_is_the_five_fields(project, capsys):
    _, said = check(capsys, "checkout.png", "shot.png")
    assert set(said) == {"verdict", "message", "baseline", "actual", "diff"}


def test_the_layout_is_the_librarys_never_the_servers(project, capsys):
    check(capsys, "checkout.png", "shot.png", "--update", "--platform", "chromium-1440x900")
    assert (project / "tests" / "__vistest__" / "chromium-1440x900" / "checkout.png").is_file()
    assert not (project / ".vistest" / "baselines").exists()


def test_baselines_dir_is_a_flag(project, capsys):
    code, said = check(capsys, "a/b.png", "shot.png", "--update", "--baselines", "shots")
    assert code == 0 and Path(said["baseline"]) == project / "shots" / "a" / "b.png"
    assert check(capsys, "a/b.png", "shot.png", "--baselines", "shots")[1]["verdict"] == "pass"


def test_the_name_follows_the_rules_of_expect_screenshot(project, capsys):
    code, said = check(capsys, "checkout?step=2.png", "shot.png", "--update")
    assert code == 0 and Path(said["baseline"]).name == "checkout_step_2.png"
    assert cli.main(["check", "/tmp/evil.png", "shot.png"]) == 2
    err = capsys.readouterr().err.strip()
    assert err.startswith("vistest check: '/tmp/evil.png' is an absolute path"), err


def test_the_text_output_is_the_message(project, capsys):
    assert cli.main(["check", "checkout.png", "shot.png"]) == 2
    assert capsys.readouterr().out.startswith("vistest: no baseline for 'checkout.png'")


def test_the_servers_options_need_a_server(project, capsys):
    assert cli.main(["check", "checkout.png", "shot.png", "--frames", "shot.png"]) == 2
    err = capsys.readouterr().err.strip()
    assert err.startswith("vistest check: --frames is the server's — pass --api URL"), err


def test_a_configured_server_gets_the_check(project, capsys, monkeypatch):
    """VISTEST_API_URL (or service.api_url) sends it to the server, in its layout."""
    import requests

    seen = {}

    class Answer:
        def raise_for_status(self):
            pass

        def json(self):
            return {"verdict": "pass", "name": "checkout.png", "passed": True,
                    "metrics": {"max_severity": 0.0}}

    def post(url, data, files, timeout):
        seen["url"], seen["data"] = url, data
        return Answer()

    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setenv("VISTEST_API_URL", "http://127.0.0.1:9")
    assert cli.main(["check", "checkout.png", "shot.png"]) == 0
    assert seen["url"] == "http://127.0.0.1:9/api/check"
    assert seen["data"]["name"] == "checkout.png"
    assert not (project / "tests" / "__vistest__").exists()
