# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Step C of f5: engine v2 by default, v1 by name for one release.

What is held here is the choice and what the settings mean under it, not the
engines themselves (their own tests do that):

* the engine is one field, `DiffConfig.engine`, that `compare()` reads — no
  caller in the package chooses on its own;
* it is chosen, in increasing strength, by the default, vistest.yaml, the
  environment and the call;
* a v1 result says v1 goes in the next release; a v2 result under a preset
  says the preset does not act on it;
* a threshold a person set lets regions below it through under v2 — listed,
  with its source, in the result, the notes, the report, the failure message
  and the API answer; a preset's number does not;
* (step C2) so does the area limit: the one a person set, or the default
  0.15 % whatever the preset, named next to the number, and a failure on it
  says what to do.
"""

from __future__ import annotations

import ast
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from vistest.config import VisTestConfig
from vistest.core import engines, pngio
from vistest.core.comparator import compare
from vistest.core.settings import ConfigError, DiffConfig
from vistest.core.thresholds import patch_for
from vistest.models import Verdict

ROOT = Path(__file__).resolve().parents[1]


def _pair():
    """A strong change and a faint one: severities far apart."""
    a = np.full((200, 300, 3), 250, np.uint8)
    b = a.copy()
    b[20:40, 20:60] = (60, 60, 60)
    b[100:104, 200:210] = (244, 244, 244)
    return a, b


# --------------------------------------------------------------------------- #
#  One place
# --------------------------------------------------------------------------- #
def test_the_default_is_v2_and_it_is_written_once():
    assert engines.DEFAULT == "v2"
    assert DiffConfig().engine == "v2"
    assert VisTestConfig().diff.engine == "v2"
    for preset in ("strict", "balanced", "loose"):
        assert VisTestConfig.preset_of(preset).diff.engine == "v2"
        assert VisTestConfig.preset_of(preset, engine="v1").diff.engine == "v1"


def test_no_caller_in_the_package_chooses_the_engine_on_its_own():
    """`compare(engine=...)` is for the tests and the benchmarks. In the
    package only training asks for v1 by name — the gate learns from v1's
    regions; every other call site reads `cfg.engine`."""
    allowed = {"vistest/ai/train.py"}
    found = []
    for path in sorted((ROOT / "vistest").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith("vistest/core/v2/") or rel == "vistest/core/comparator.py":
            continue
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "id",
                                                      getattr(node.func, "attr", "")) \
                    == "compare" and any(k.arg == "engine" for k in node.keywords):
                found.append(f"{rel}:{node.lineno}")
    assert {f.split(":")[0] for f in found} <= allowed, found


def test_an_engine_that_is_not_one_is_refused_everywhere(tmp_path, monkeypatch):
    with pytest.raises(ConfigError, match="must be 'v1' or 'v2'"):
        DiffConfig(engine="v3")
    a, b = _pair()
    with pytest.raises(ValueError, match="engine must be"):
        compare(a, b, engine="V9")
    monkeypatch.setenv("VISTEST_ENGINE", "v9")
    with pytest.raises(ConfigError, match="VISTEST_ENGINE"):
        VisTestConfig.load(tmp_path / "missing.yaml")


# --------------------------------------------------------------------------- #
#  Who chooses: the code. vistest.yaml, VISTEST_ENGINE, the library's call and
#  the presets do not since 0.2.0.dev3 (review v1, R5) — that refusal is
#  tests/test_removed_v1_and_presets.py; what stays is compare(engine=...) and
#  a DiffConfig, for the benchmark, the bench corpus and the server.
# --------------------------------------------------------------------------- #
def _yaml(tmp_path, text: str) -> Path:
    path = tmp_path / "vistest.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_the_derived_fields_of_diff_are_not_written_in_vistest_yaml(tmp_path, monkeypatch):
    monkeypatch.delenv("VISTEST_ENGINE", raising=False)
    with pytest.raises(ValueError, match="unknown key diff.threshold_source"):
        VisTestConfig.load(_yaml(tmp_path, "diff:\n  threshold_source: x\n"))


def test_the_call_beats_everything(monkeypatch):
    a, b = _pair()
    cfg = DiffConfig(engine="v1")
    assert any(n.startswith("Engine v2") for n in compare(a, b, cfg=cfg, engine="v2").notes)
    assert compare(a, b, engine="v1").notes[-1] == engines.V1_DEPRECATED


# --------------------------------------------------------------------------- #
#  What a result says about the choice
# --------------------------------------------------------------------------- #
def test_a_v1_result_says_v1_goes_in_the_next_release_once():
    a, b = _pair()
    for pair in ((a, b), (a, a.copy())):
        notes = compare(*pair, engine="v1").notes
        assert notes.count(engines.V1_DEPRECATED) == 1
        assert "next release" in engines.V1_DEPRECATED
    assert engines.V1_DEPRECATED not in compare(a, b).notes


@pytest.mark.parametrize("preset", ["strict", "loose"])
def test_a_preset_under_v2_says_it_acts_on_v1_only(preset):
    a, b = _pair()
    notes = compare(a, b, cfg=VisTestConfig.preset_of(preset).diff).notes
    assert engines.preset_note(preset) in notes
    assert "acts on engine v1 only" in engines.preset_note(preset)
    assert not any("acts on engine v1 only" in n
                   for n in compare(a, b, cfg=VisTestConfig.preset_of("balanced").diff).notes)


# --------------------------------------------------------------------------- #
#  The threshold under v2
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("preset", ["strict", "balanced", "loose"])
def test_a_presets_number_is_not_a_threshold_for_v2(preset):
    """0 in every preset: whatever no rule explains fails, faint or not."""
    a, b = _pair()
    cfg = VisTestConfig.preset_of(preset).diff
    assert cfg.fail_severity > 0 and cfg.v2_threshold == 0
    r = compare(a, b, cfg=cfg)
    assert r.verdict is Verdict.FAIL and len(r.regions) == 2
    assert r.threshold is None and not r.below_threshold
    assert "threshold" not in r.to_dict() and "below_threshold" not in r.to_dict()


def test_a_threshold_a_person_set_lists_what_is_below_it():
    a, b = _pair()
    cfg = DiffConfig(fail_severity=25, threshold_source="project override")
    r = compare(a, b, cfg=cfg)
    assert r.verdict is Verdict.FAIL                  # the strong one is above
    assert len(r.regions) == 1 and len(r.below_threshold) == 1
    faint = r.below_threshold[0]
    assert faint.severity < 25 <= r.regions[0].severity
    line = engines.below_line(r)
    assert line.startswith("below the threshold 25 (project override): 1 region — ")
    assert engines.region_words(faint) in line
    assert line in r.notes
    body = r.to_dict()
    assert body["threshold"]["source"] == "project override"
    assert body["threshold"]["value"] == 25 and body["threshold"]["regions"] == 1
    assert [x["x"] for x in body["below_threshold"]] == [faint.x]
    #  Every candidate pixel is still somewhere.
    assert r.unassigned_pixels == 0


def test_below_the_threshold_alone_passes_and_is_still_said():
    a, b = _pair()
    b[20:40, 20:60] = 250                              # only the faint one is left
    r = compare(a, b, cfg=DiffConfig(fail_severity=25, threshold_source="call"))
    assert r.verdict is Verdict.PASS and not r.regions
    assert len(r.below_threshold) == 1
    assert any(n.startswith("below the threshold 25 (call): 1 region — ") for n in r.notes)


def test_the_share_of_the_frame_still_fails_as_in_v1():
    """max_changed_area_pct: enough on its own, whatever the severity — named
    with where it came from, and with what to do about it."""
    a, b = _pair()
    high = DiffConfig(fail_severity=99, threshold_source="call")
    r = compare(a, b, cfg=high)                        # 840 px of 60 000: 1.4 %
    assert r.verdict is Verdict.FAIL and len(r.regions) == 2 and not r.below_threshold
    assert any("cover 1.40% of the frame together, at least the area limit 0.15% "
               "(default) — raise max_changed_area_pct or mask the area" in n
               for n in r.notes), r.notes
    wide = DiffConfig(fail_severity=99, threshold_source="call",
                      max_changed_area_pct=5, area_source="call")
    assert compare(a, b, cfg=wide).verdict is Verdict.PASS


def _faint(pixels_wide: int):
    """Only a faint change, 4 px high: 40 px is 0.07 % of the frame, 180 px 0.30 %."""
    a = np.full((200, 300, 3), 250, np.uint8)
    b = a.copy()
    b[100:104, 200:200 + pixels_wide] = (244, 244, 244)
    return a, b


@pytest.mark.parametrize("preset", ["strict", "balanced", "loose"])
def test_a_preset_does_not_set_the_area_limit_for_v2(preset):
    """Step C2: like the threshold, the area limit under v2 is the one a person
    set or the default, 0.15 %, whatever the preset — strict's 0.02 % and
    loose's 0.8 % are v1's."""
    cfg = replace(VisTestConfig.preset_of(preset).diff,
                  fail_severity=99, threshold_source="call")
    assert (cfg.v2_area_limit, cfg.v2_area_source) == (0.15, "default")
    small = compare(*_faint(10), cfg=cfg)               # 0.07 %: strict's would fail it
    assert small.verdict is Verdict.PASS and len(small.below_threshold) == 1
    assert small.threshold["area_limit"] == 0.15
    assert small.threshold["area_source"] == "default"
    wider = compare(*_faint(45), cfg=cfg)               # 0.30 %: loose's would pass it
    assert wider.verdict is Verdict.FAIL
    assert any("at least the area limit 0.15% (default)" in n for n in wider.notes)
    #  v1 keeps the preset's number.
    assert VisTestConfig.preset_of(preset).diff.max_changed_area_pct == {
        "strict": 0.02, "balanced": 0.15, "loose": 0.8}[preset]


def test_the_threshold_does_not_touch_v1():
    a, b = _pair()
    plain = compare(a, b, cfg=DiffConfig(fail_severity=25), engine="v1")
    named = compare(a, b, cfg=DiffConfig(fail_severity=25, threshold_source="call"),
                    engine="v1")
    one, two = plain.to_dict(), named.to_dict()
    one.pop("duration_ms"), two.pop("duration_ms")
    assert one == two
    assert named.threshold is None


# --------------------------------------------------------------------------- #
#  Where a threshold comes from
# --------------------------------------------------------------------------- #
def test_every_layer_names_itself(tmp_path, monkeypatch):
    monkeypatch.delenv("VISTEST_FAIL_SEVERITY", raising=False)
    monkeypatch.delenv("VISTEST_THRESHOLD_SOURCE", raising=False)
    assert VisTestConfig.load(tmp_path / "none.yaml").diff.threshold_source == ""
    #  A preset's number (the server's presets, built in code) names no source.
    assert VisTestConfig.preset_of("strict").diff.threshold_source == ""
    path = _yaml(tmp_path, "diff:\n  fail_severity: 30\n")
    assert VisTestConfig.load(path).diff.threshold_source == "vistest.yaml"
    monkeypatch.setenv("VISTEST_FAIL_SEVERITY", "12")
    assert VisTestConfig.load(path).diff.threshold_source == "VISTEST_FAIL_SEVERITY"
    monkeypatch.setenv("VISTEST_THRESHOLD_SOURCE", "project override")
    cfg = VisTestConfig.load(path).diff
    assert (cfg.fail_severity, cfg.threshold_source) == (12, "project override")

    assert patch_for({"thresholds": {"fail_severity": 40}}, None)["threshold_source"] \
        == "snapshot passport"
    assert patch_for({"thresholds": {"fail_severity": 40}},
                     {"fail_severity": 5})["threshold_source"] == "call"
    assert "threshold_source" not in patch_for({"thresholds": {"max_changed_area_pct": 1}},
                                               {"max_changed_area_pct": 2})


def test_every_layer_names_the_area_limit_too(tmp_path, monkeypatch):
    for name in ("VISTEST_MAX_CHANGED_AREA_PCT", "VISTEST_AREA_SOURCE"):
        monkeypatch.delenv(name, raising=False)
    assert VisTestConfig.load(tmp_path / "none.yaml").diff.area_source == ""
    cfg = VisTestConfig.preset_of("loose").diff                # a preset's (the server's)
    assert (cfg.area_source, cfg.v2_area_limit, cfg.v2_area_source) == ("", 0.15, "default")
    path = _yaml(tmp_path, "diff:\n  max_changed_area_pct: 2\n")
    cfg = VisTestConfig.load(path).diff
    assert (cfg.v2_area_limit, cfg.v2_area_source) == (2, "vistest.yaml")
    monkeypatch.setenv("VISTEST_MAX_CHANGED_AREA_PCT", "3")
    assert VisTestConfig.load(path).diff.v2_area_source == "VISTEST_MAX_CHANGED_AREA_PCT"
    monkeypatch.setenv("VISTEST_AREA_SOURCE", "project override")
    cfg = VisTestConfig.load(path).diff
    assert (cfg.v2_area_limit, cfg.v2_area_source) == (3, "project override")

    assert patch_for({"thresholds": {"max_changed_area_pct": 1}}, None)["area_source"] \
        == "snapshot passport"
    assert patch_for({"thresholds": {"max_changed_area_pct": 1}},
                     {"max_changed_area_pct": 2})["area_source"] == "call"
    assert "area_source" not in patch_for({"thresholds": {"fail_severity": 1}},
                                          {"fail_severity": 2})
    with pytest.raises(ValueError, match="unknown key diff.area_source"):
        VisTestConfig.load(_yaml(tmp_path, "diff:\n  area_source: call\n"))


def test_an_area_limit_saved_in_the_interface_is_named():
    from vistest.api.thresholds import apply, env_for

    class DB:
        rows = {("global", ""): {"fail_severity": 30.0, "max_changed_area_pct": 1.0},
                ("project", "acme"): {"max_changed_area_pct": 2.0}}

        def query(self, _sql, args):
            scope, key = args
            return [{"name": k, "value": v} for k, v in self.rows.get((scope, key), {}).items()]

    cfg = VisTestConfig()
    acme = apply(cfg, DB(), "acme").diff
    assert (acme.v2_area_limit, acme.v2_area_source) == (2, "project override")
    assert acme.threshold_source == "global override"
    other = apply(cfg, DB(), "other").diff
    assert (other.v2_area_limit, other.v2_area_source) == (1, "global override")
    env = env_for(DB(), "acme")
    assert env["VISTEST_AREA_SOURCE"] == "project override"
    assert env["VISTEST_THRESHOLD_SOURCE"] == "global override"


def test_an_override_saved_in_the_interface_is_named():
    """Saved before the switch, respected after it — and named, so a person
    sees that the old number now lets something through."""
    from vistest.api.thresholds import apply, env_for

    class DB:
        rows = {("global", ""): {"fail_severity": 30.0},
                ("project", "acme"): {"fail_severity": 20.0}}

        def query(self, _sql, args):
            scope, key = args
            return [{"name": k, "value": v} for k, v in self.rows.get((scope, key), {}).items()]

    cfg = VisTestConfig()
    assert apply(cfg, DB(), "acme").diff.threshold_source == "project override"
    assert apply(cfg, DB(), "other").diff.threshold_source == "global override"
    assert apply(cfg, DB(), "other").diff.fail_severity == 30
    assert env_for(DB(), "acme")["VISTEST_THRESHOLD_SOURCE"] == "project override"


# --------------------------------------------------------------------------- #
#  The library: engine=, the message, the report
# --------------------------------------------------------------------------- #
@pytest.fixture
def ctx(tmp_path: Path, monkeypatch):
    from vistest.library import context as _context

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("VISTEST_ENGINE", raising=False)
    context = _context.LibraryContext(root=tmp_path)
    _context.install(context)
    yield context
    _context.uninstall()


def _png(img) -> bytes:
    return pngio.encode(img)


def _accept(ctx, png, name="page.png"):
    from vistest import expect_screenshot

    ctx.update = True
    try:
        expect_screenshot(png, name)
    finally:
        ctx.update = False


def test_the_library_compares_with_v2_and_says_so(ctx):
    from vistest import expect_screenshot
    from vistest.library.errors import ScreenshotMismatch

    a, b = _pair()
    _accept(ctx, _png(a))
    with pytest.raises(ScreenshotMismatch) as v2:
        expect_screenshot(_png(b), "page.png")
    #  The words of review v1, step C (4.3): no «engine v2», «rule», «region».
    assert "2 changes not explained as rendering noise" in str(v2.value)
    assert "no threshold set, so any change fails" in str(v2.value)
    assert engines.V1_DEPRECATED not in str(v2.value)


def test_below_the_threshold_in_the_message_and_the_report(ctx):
    from vistest import expect_screenshot
    from vistest.library.errors import ScreenshotMismatch
    from vistest.report.library import read_parts, render

    a, b = _pair()
    _accept(ctx, _png(a))
    with pytest.raises(ScreenshotMismatch) as e:
        expect_screenshot(_png(b), "page.png", fail_severity=25)
    text = str(e.value)
    assert "threshold 25 (set in the call)" in text
    assert "(limit 0.15%, the default)" in " ".join(text.split())
    assert "  let through: below the threshold 25 (set in the call): 1 change — " in text

    c = a.copy()
    c[100:104, 200:210] = (244, 244, 244)
    result = expect_screenshot(_png(c), "page.png", fail_severity=25)
    assert result.verdict is Verdict.PASS and len(result.below_threshold) == 1

    rows = [r for r in read_parts(ctx.parts_dir).entries if r.get("action") == "compared"]
    assert rows[-1]["verdict"] == "pass"
    assert rows[-1]["reason"].startswith(
        "below the threshold 25 (set in the call): 1 change — ")
    assert rows[-1]["limits"]["threshold_source"] == "call"
    assert len(rows[-1]["below_threshold"]) == 1
    html = render(read_parts(ctx.parts_dir))
    assert 'class="below" open' in html and "below the threshold 25 (set in the call)" in html
    assert "threshold 25 (call)" in html


def test_the_area_limit_in_the_message_says_where_from_and_what_to_do(ctx):
    from vistest import expect_screenshot
    from vistest.library.errors import ScreenshotMismatch
    from vistest.report.library import read_parts, render

    a, b = _pair()
    _accept(ctx, _png(a))
    _accept(ctx, _png(a), "other.png")
    with pytest.raises(ScreenshotMismatch) as e:
        expect_screenshot(_png(b), "page.png",
                          fail_severity=99, max_changed_area_pct=1)
    said = " ".join(str(e.value).split())
    assert "2 changes, all below the threshold 99 (set in the call)" in said
    assert "at or over the limit (1.00%, set in the call)" in said
    with pytest.raises(ScreenshotMismatch) as e:
        expect_screenshot(_png(b), "page.png", fail_severity=99)
    assert ("together 1.40% of the frame, at or over the limit (0.15%, the default) · "
            "raise max_changed_area_pct or mask the area") in " ".join(str(e.value).split())
    passed = expect_screenshot(_png(b), "other.png",
                               fail_severity=99, max_changed_area_pct=5)
    assert passed.verdict is Verdict.PASS
    html = render(read_parts(ctx.parts_dir))              # the last check of each
    assert "area 1.40% / 0.15% (default)" in html and "area 1.40% / 5.00% (call)" in html


def test_the_api_answer_carries_the_list(tmp_path, monkeypatch):
    """`POST /api/check` answers with `to_dict()`: the threshold, what is below
    it, and the line in the notes."""
    from vistest.service import CheckService

    monkeypatch.delenv("VISTEST_ENGINE", raising=False)
    cfg = VisTestConfig()
    cfg.paths = type(cfg.paths)(root=str(tmp_path))
    svc = CheckService(cfg, platform="p", run_dir=tmp_path / "run")
    a, b = _pair()
    svc.check("page.png", a, render=False)
    res = svc.check("page.png", b, render=False,
                    diff_overrides={"fail_severity": 25})
    body = json.loads(res.to_json())
    assert body["threshold"]["source"] == "call" and len(body["below_threshold"]) == 1
    assert (body["threshold"]["area_limit"], body["threshold"]["area_source"]) \
        == (0.15, "default")
    assert any(n.startswith("below the threshold 25 (call)") for n in body["notes"])
    old = svc.check("page.png", b, render=False, diff_overrides={"engine": "v1"})
    assert engines.V1_DEPRECATED in old.notes


def test_the_api_takes_the_engine_as_an_option():
    from fastapi import HTTPException

    from vistest.api.check import _clean_overrides

    assert _clean_overrides({"engine": "V1"}) == {"engine": "v1"}
    with pytest.raises(HTTPException) as e:
        _clean_overrides({"engine": "v3"})
    assert "must be 'v1' or 'v2'" in str(e.value.detail)
