# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The plugin contract, piece by piece.

`test_degradation.py` proves the whole thing end to end; this file pins the
rules one at a time, so that when one breaks the failure names it: the loader
never raises, a version mismatch is refused, one implementation per role,
annotators cannot touch the verdict, a score acts only through `fail_on`,
plugin migrations cannot reach core tables, unknown config keys are kept.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

import numpy as np
import pytest

from vistest.ai.pipeline import AIPipeline
from vistest.config import AIConfig, PluginsConfig, VisTestConfig
from vistest.core.settings import ConfigError
from vistest.models import ChangeKind, CompareResult, DiffRegion, Verdict
from vistest.plugins import api, loader, migrations, runtime
from vistest.plugins.registry import PluginRegistry


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #
@dataclass
class FakeEntryPoint:
    name: str
    target: object = None
    error: BaseException | None = None
    value: str = "fake:module"

    def load(self):
        if self.error is not None:
            raise self.error
        return self.target


class Plugin:
    """A module-like object: `API_VERSION` and `register`."""

    def __init__(self, register, version=api.API_VERSION):
        self.API_VERSION = version
        self.register = register


class Scorer:
    def __init__(self, values=None, error=None):
        self.values, self.error, self.calls = values, error, 0

    def score(self, regions, ctx):
        self.calls += 1
        if self.error:
            raise self.error
        return self.values(regions) if callable(self.values) else self.values


class Annotator:
    def __init__(self, text="a remark", mutate=False, error=None):
        self.text, self.mutate, self.error = text, mutate, error

    def annotate(self, region, ctx):
        if self.error:
            raise self.error
        if self.mutate:
            region.kind = ChangeKind.NOISE
            region.severity = 0.0
            region.suppressed_by = "noise: sneaky"
            region.score = 0.0
        return [api.Annotation(text=self.text, source="forged")]


def _region(**kw) -> DiffRegion:
    base = dict(x=10, y=20, w=100, h=40, kind=ChangeKind.TEXT, severity=50.0,
                de_mean=3.0, pixel_count=800, fill_ratio=0.2)
    base.update(kw)
    return DiffRegion(**base)


def _result(**kw) -> CompareResult:
    base = dict(name="x", verdict=Verdict.PASS, total_pixels=1_000_000)
    base.update(kw)
    return CompareResult(**base)


def _refine(registry, regions, *, fail_on="likely-real", result=None, **plugins):
    result = result or _result()
    pipeline = AIPipeline(AIConfig(attribution_enabled=False), registry=registry,
                          plugins=PluginsConfig(fail_on=fail_on, **plugins))
    return pipeline.refine(regions, None, None, result), result


# --------------------------------------------------------------------------- #
#  Loader
# --------------------------------------------------------------------------- #
def test_a_good_plugin_registers(caplog):
    reg = PluginRegistry()
    plugin = Plugin(lambda r: r.add_annotator(Annotator(), name="words"))
    loader.load_plugins(reg, entry_points=[FakeEntryPoint("good", plugin)])

    assert [a.name for a in reg.annotators()] == ["words"]
    assert reg.annotators()[0].plugin == "good"
    assert reg.loaded[0]["name"] == "good"


@pytest.mark.parametrize("entry, reason", [
    (FakeEntryPoint("broken-import", error=ImportError("no such module")),
     "import failed"),
    (FakeEntryPoint("exits", error=SystemExit(3)), "import failed"),
    (FakeEntryPoint("no-register", object()), "no register"),
    (FakeEntryPoint("no-version", Plugin(lambda r: None, version=None)),
     "does not declare"),
    (FakeEntryPoint("bool-version", Plugin(lambda r: None, version=True)),
     "does not declare"),
    (FakeEntryPoint("old", Plugin(lambda r: None, version=0)),
     "API version 0"),
    (FakeEntryPoint("new", Plugin(lambda r: None, version=2)),
     "API version 2"),
])
def test_a_bad_plugin_is_a_warning_and_nothing_else(entry, reason, caplog):
    reg = PluginRegistry()
    with caplog.at_level(logging.WARNING, logger="vistest.plugins"):
        loader.load_plugins(reg, entry_points=[entry])

    assert reg.is_empty()
    assert reg.refused and reason in reg.refused[0]["reason"]
    assert any(entry.name in r.getMessage() for r in caplog.records)


def test_a_version_mismatch_is_refused_before_register_runs():
    called = []
    plugin = Plugin(lambda r: called.append(1), version=api.API_VERSION + 1)
    loader.load_plugins(PluginRegistry(), entry_points=[FakeEntryPoint("x", plugin)])
    assert called == []


def test_a_failing_register_leaves_nothing_behind():
    """Half a plugin is a configuration nobody tested."""
    def register(r):
        r.add_annotator(Annotator(), name="first")
        r.set_scorer(Scorer([1.0]), name="second")
        raise RuntimeError("halfway")

    reg = PluginRegistry()
    loader.load_plugins(reg, entry_points=[FakeEntryPoint("half", Plugin(register))])
    assert reg.is_empty()
    assert "halfway" in reg.refused[0]["reason"]


def test_something_that_is_not_an_implementation_is_refused():
    plugin = Plugin(lambda r: r.set_scorer(object()))
    reg = PluginRegistry()
    loader.load_plugins(reg, entry_points=[FakeEntryPoint("junk", plugin)])
    assert reg.scorer() is None
    assert "does not implement RegionScorer" in reg.refused[0]["reason"]


def test_one_bad_plugin_does_not_stop_the_next():
    reg = PluginRegistry()
    loader.load_plugins(reg, entry_points=[
        FakeEntryPoint("a-broken", error=RuntimeError("boom")),
        FakeEntryPoint("b-good", Plugin(lambda r: r.set_scorer(Scorer([1.0])))),
    ])
    assert reg.scorer() is not None


def test_the_register_function_itself_can_be_the_entry_point(tmp_path, monkeypatch):
    module = tmp_path / "fn_plugin.py"
    module.write_text("API_VERSION = 1\n"
                      "def register(registry):\n"
                      "    registry.add_annotator(Words())\n"
                      "class Words:\n"
                      "    def annotate(self, region, ctx):\n"
                      "        return ()\n", "utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    import fn_plugin

    reg = PluginRegistry()
    loader.load_plugins(reg, entry_points=[FakeEntryPoint("fn", fn_plugin.register)])
    assert len(reg.annotators()) == 1


def test_the_switch_turns_loading_off_entirely(monkeypatch):
    monkeypatch.setenv(loader.ENV_DISABLE, "1")
    touched = []

    class Loud(FakeEntryPoint):
        def load(self):
            touched.append(self.name)
            return super().load()

    reg = PluginRegistry()
    loader.load_plugins(reg, entry_points=[Loud("x", Plugin(lambda r: None))])
    assert touched == [], "nothing from the group may even be imported"
    assert reg.loaded == [] and reg.refused == []


@pytest.mark.parametrize("value, disabled", [
    ("1", True), ("true", True), ("yes", True), ("anything", True),
    ("0", False), ("false", False), ("off", False), ("", False),
])
def test_the_switch_reads_leniently(monkeypatch, value, disabled):
    monkeypatch.setenv(loader.ENV_DISABLE, value)
    assert loader.plugins_disabled() is disabled


def test_disabled_in_the_config_is_not_loaded():
    reg = PluginRegistry()
    loader.load_plugins(reg, disabled={"quiet"}, entry_points=[
        FakeEntryPoint("quiet", Plugin(lambda r: r.set_scorer(Scorer([1.0]))))])
    assert reg.scorer() is None
    assert reg.refused[0]["reason"] == "disabled in vistest.yaml"


def test_the_process_registry_loads_once(monkeypatch):
    calls = []
    monkeypatch.setattr(loader, "discover",
                        lambda: calls.append(1) or [])
    loader.reset(None)
    try:
        loader.ensure_loaded(options={}, disabled=set())
        loader.ensure_loaded(options={}, disabled=set())
        loader.active_registry()
        assert calls == [1]
    finally:
        loader.reset(None)


# --------------------------------------------------------------------------- #
#  Registry
# --------------------------------------------------------------------------- #
def test_one_active_implementation_per_role_highest_priority_wins(caplog):
    reg = PluginRegistry()
    low, high = Scorer([0.1]), Scorer([0.9])
    with caplog.at_level(logging.WARNING, logger="vistest.plugins"):
        reg.set_scorer(low, name="low", priority=1)
        reg.set_scorer(high, name="high", priority=5)
    assert reg.scorer().impl is high
    assert any("low" in r.getMessage() and "high" in r.getMessage()
               for r in caplog.records), "the loser is named"
    assert [i["name"] for i in reg.describe()["inactive"]] == ["low"]


def test_on_a_tie_the_first_registered_stays():
    reg = PluginRegistry()
    first, second = Scorer([0.1]), Scorer([0.9])
    reg.set_scorer(first, name="first")
    reg.set_scorer(second, name="second")
    assert reg.scorer().impl is first


def test_annotators_chain_by_priority_then_order():
    reg = PluginRegistry()
    reg.add_annotator(Annotator("a"), name="a", priority=0)
    reg.add_annotator(Annotator("b"), name="b", priority=10)
    reg.add_annotator(Annotator("c"), name="c", priority=0)
    assert [a.name for a in reg.annotators()] == ["b", "a", "c"]


def test_capabilities_are_plain_booleans():
    reg = PluginRegistry()
    assert reg.capabilities() == {"region_scores": False, "region_annotations": False,
                                  "external_sign_in": False, "baseline_sync": False}
    reg.set_scorer(Scorer([1.0]))
    assert reg.capabilities()["region_scores"] is True


# --------------------------------------------------------------------------- #
#  Scores and fail_on
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("fail_on, value, counts, prefix", [
    ("any", 0.1, True, None),
    ("any", 0.6, True, None),
    ("likely-real", 0.1, False, "noise:"),
    ("likely-real", 0.6, True, None),
    ("likely-real", 0.95, True, None),
    ("confirmed", 0.1, False, "noise:"),
    ("confirmed", 0.6, False, "below-fail-on:"),
    ("confirmed", 0.95, True, None),
])
def test_the_fail_on_table(fail_on, value, counts, prefix):
    reg = PluginRegistry()
    reg.set_scorer(Scorer([value]), name="s")
    regions, _ = _refine(reg, [_region()], fail_on=fail_on)

    region = regions[0]
    assert region.score == pytest.approx(value)
    assert (region.suppressed_by is None) is counts
    if prefix:
        assert region.suppressed_by.startswith(prefix)
        assert "s scored" in region.suppressed_by


def test_likely_real_below_confirmed_keeps_its_kind_and_severity():
    reg = PluginRegistry()
    reg.set_scorer(Scorer([0.6]))
    regions, _ = _refine(reg, [_region(severity=40.0)], fail_on="confirmed")
    assert regions[0].kind is ChangeKind.TEXT and regions[0].severity == 40.0


def test_a_suppressed_region_leaves_the_verdict_but_not_the_result():
    """The rule without exceptions: suppressed is listed, never dropped."""
    from vistest.core.comparator import compare

    #  Small enough to stay under the area limit: a change that large fails
    #  on its area alone, whatever any scorer says about its regions.
    base = np.full((300, 400, 3), 250, np.uint8)
    changed = base.copy()
    changed[100:112, 100:112] = (20, 20, 20)

    reg = PluginRegistry()
    reg.set_scorer(Scorer(lambda regions: [0.0] * len(regions)), name="s")
    plain = compare(base, changed, name="n")
    scored = compare(base, changed, name="n",
                     ai_hooks=AIPipeline(AIConfig(attribution_enabled=False),
                                         registry=reg))

    assert plain.verdict is Verdict.FAIL
    assert scored.verdict is Verdict.PASS
    assert len(scored.suppressed) == len(plain.regions) + len(plain.suppressed)
    counts = runtime.count_suppressed(scored.suppressed)
    assert runtime.say_suppressed(counts) == [
        "1 difference suppressed as rendering noise"]


def test_the_safety_rules_outrank_any_score():
    reg = PluginRegistry()
    reg.set_scorer(Scorer(lambda rs: [0.0] * len(rs)))
    big = _region(x=0, y=0, w=900, h=900)
    small = _region()
    regions, result = _refine(reg, [big, small],
                              result=_result(total_pixels=1000 * 1000))
    assert big.suppressed_by is None
    assert small.suppressed_by is not None
    assert any("safety rule" in n for n in result.notes)

    geometry = _region()
    _refine(reg, [geometry], result=_result(size_changed=True))
    assert geometry.suppressed_by is None


@pytest.mark.parametrize("answer", [
    [0.5, 0.5],                 # wrong length
    ["high"],                   # not a number
    [float("nan")],
    [float("inf")],
    [True],
    "0.1",
    {"a": 1},
    42,
])
def test_nonsense_from_a_scorer_is_discarded_whole(answer, caplog):
    reg = PluginRegistry()
    reg.set_scorer(Scorer(answer), name="odd")
    with caplog.at_level(logging.WARNING, logger="vistest.plugins"):
        regions, result = _refine(reg, [_region()])
    assert regions[0].score is None and regions[0].suppressed_by is None
    assert any("Region scorer skipped" in n for n in result.notes)
    assert any("odd" in r.getMessage() for r in caplog.records)


def test_a_scorer_that_raises_is_the_deterministic_path():
    reg = PluginRegistry()
    reg.set_scorer(Scorer(error=MemoryError("model too big")), name="boom")
    region = _region()
    before = region.to_dict()
    _refine(reg, [region])
    assert region.to_dict() == before


def test_a_scorer_may_abstain():
    reg = PluginRegistry()
    reg.set_scorer(Scorer(None))
    regions, result = _refine(reg, [_region()])
    assert regions[0].score is None
    assert not any("skipped" in n for n in result.notes)


def test_scores_are_clamped_and_numpy_is_fine():
    reg = PluginRegistry()
    reg.set_scorer(Scorer([np.float32(1.7)]))
    regions, _ = _refine(reg, [_region()])
    assert regions[0].score == 1.0


def test_regions_already_set_aside_are_not_offered():
    seen = []
    reg = PluginRegistry()
    reg.set_scorer(Scorer(lambda rs: seen.extend(rs) or [1.0] * len(rs)))
    kept = _region()
    _refine(reg, [_region(kind=ChangeKind.ANTIALIAS),
                  _region(suppressed_by="noise: stability"), kept])
    assert seen == [kept]


def test_plugins_config_is_validated():
    with pytest.raises(ConfigError, match="fail_on"):
        PluginsConfig(fail_on="sometimes").validated()
    with pytest.raises(ConfigError, match="noise_below"):
        PluginsConfig(noise_below=1.5).validated()
    with pytest.raises(ConfigError, match="above"):
        PluginsConfig(noise_below=0.9, confirmed_at=0.5).validated()


def test_fail_on_from_the_environment(monkeypatch):
    monkeypatch.setenv("VISTEST_FAIL_ON", "confirmed")
    assert VisTestConfig._apply_env(VisTestConfig()).plugins.fail_on == "confirmed"
    monkeypatch.setenv("VISTEST_FAIL_ON", "never")
    with pytest.raises(ConfigError, match="VISTEST_FAIL_ON"):
        VisTestConfig._apply_env(VisTestConfig())


# --------------------------------------------------------------------------- #
#  Annotators
# --------------------------------------------------------------------------- #
def test_annotations_are_attached_with_the_registered_name():
    reg = PluginRegistry()
    reg.add_annotator(Annotator("hello"), name="greeter")
    regions, _ = _refine(reg, [_region()])
    assert regions[0].annotations == [
        {"text": "hello", "kind": "note", "value": None, "source": "greeter"}]


def test_an_annotator_cannot_change_the_verdict(caplog):
    reg = PluginRegistry()
    reg.set_scorer(Scorer([0.8]))
    reg.add_annotator(Annotator(mutate=True), name="sneaky")
    with caplog.at_level(logging.WARNING, logger="vistest.plugins"):
        regions, _ = _refine(reg, [_region()])
    region = regions[0]
    assert region.kind is ChangeKind.TEXT and region.severity == 50.0
    assert region.suppressed_by is None and region.score == pytest.approx(0.8)
    assert any("reverted" in r.getMessage() for r in caplog.records)


def test_a_failing_annotator_does_not_stop_the_chain():
    reg = PluginRegistry()
    reg.add_annotator(Annotator(error=RuntimeError("x")), name="bad", priority=5)
    reg.add_annotator(Annotator("still here"), name="good")
    regions, _ = _refine(reg, [_region()])
    assert [a["text"] for a in regions[0].annotations] == ["still here"]


@pytest.mark.parametrize("bad", [
    "just a string", [None], [api.Annotation(text="")],
    [api.Annotation(text="x", value=object())],
])
def test_unusable_annotations_are_dropped(bad):
    class Bad:
        def annotate(self, region, ctx):
            return bad

    reg = PluginRegistry()
    reg.add_annotator(Bad())
    regions, _ = _refine(reg, [_region()])
    assert regions[0].annotations == []


def test_annotations_are_capped_and_trimmed():
    class Chatty:
        def annotate(self, region, ctx):
            return [api.Annotation(text="x" * 5000) for _ in range(40)]

    reg = PluginRegistry()
    reg.add_annotator(Chatty())
    regions, _ = _refine(reg, [_region()])
    notes = regions[0].annotations
    assert len(notes) == api.MAX_ANNOTATIONS_PER_REGION
    assert all(len(n["text"]) == api.MAX_ANNOTATION_TEXT for n in notes)


def test_pixels_reach_plugins_read_only():
    grabbed = {}

    class Peek:
        def annotate(self, region, ctx):
            grabbed["writeable"] = ctx.expected.flags.writeable
            ctx.expected[0, 0] = 1          # must raise
            return ()

    frame = np.zeros((10, 10, 3), np.uint8)
    reg = PluginRegistry()
    reg.add_annotator(Peek())
    AIPipeline(AIConfig(attribution_enabled=False), registry=reg).refine(
        [_region()], frame, frame.copy(), _result())
    assert grabbed["writeable"] is False
    assert frame.sum() == 0


def test_the_programmatic_annotator_keeps_its_old_contract():
    """`set_annotator` as it was: whole comparison, writes captions."""
    from vistest.ai import get_annotator, set_annotator

    class Legacy:
        def annotate(self, regions, expected, actual, result):
            for r in regions:
                r.caption = "a caption"
                r.severity = 0.0          # not allowed; rolled back
            result.notes.append("legacy was here")

    legacy = Legacy()
    #  A registry of its own: the process one may already hold whatever this
    #  worker loaded for an earlier test.
    loader.reset(PluginRegistry(), loaded=True)
    set_annotator(legacy)
    try:
        assert get_annotator() is legacy
        reg = loader.programmatic_registry()
        result = _result()
        regions = AIPipeline(AIConfig(attribution_enabled=False),
                             registry=reg).refine([_region()], None, None, result)
        assert regions[0].caption == "a caption"
        assert regions[0].severity == 50.0
        assert "legacy was here" in result.notes
        assert {"text": "a caption", "kind": "caption", "value": None,
                "source": "programmatic"} in regions[0].annotations
        set_annotator(None)
        assert get_annotator() is None
        assert all(a.name != "programmatic"
                   for a in loader.programmatic_registry().annotators())
    finally:
        set_annotator(None)
        loader.reset(None)


# --------------------------------------------------------------------------- #
#  Sign-in
# --------------------------------------------------------------------------- #
class Provider:
    def __init__(self, answer=None, error=None):
        self.answer, self.error = answer, error

    def authenticate(self, login, secret):
        if self.error:
            raise self.error
        return self.answer


def _auth(provider, login="anna", secret="pw"):
    reg = PluginRegistry()
    reg.set_auth_provider(provider, name="p")
    return runtime.authenticate(reg.auth_provider(), login, secret)


def test_no_provider_is_no_answer():
    assert runtime.authenticate(None, "anna", "pw") is None


def test_a_provider_that_raises_is_reported_as_unavailable():
    with pytest.raises(runtime.ProviderUnavailable, match="down"):
        _auth(Provider(error=OSError("down")))


def test_a_provider_answering_for_somebody_else_is_ignored():
    assert _auth(Provider(api.AuthResult(login="root", role="admin"))) is None


def test_a_provider_cannot_hand_out_an_unknown_role_or_a_local_source():
    result = _auth(Provider(api.AuthResult(login="Anna", role="owner",
                                           source="local")))
    assert result.role == "viewer"
    assert result.source == "external"


def test_empty_credentials_never_reach_the_provider():
    called = []

    class Spy(Provider):
        def authenticate(self, login, secret):
            called.append(1)

    assert _auth(Spy(), secret="") is None
    assert called == []


# --------------------------------------------------------------------------- #
#  Migrations
# --------------------------------------------------------------------------- #
@pytest.fixture
def core_db(tmp_path):
    from vistest.api.db import Database

    db = Database(tmp_path / "vistest.db")
    yield db
    db.close()


def _plugin_with(steps, name="acme"):
    reg = PluginRegistry()
    with reg.registering(name):
        reg.add_migrations(steps)
    return reg


def test_plugin_migrations_run_once_and_record_their_own_version(core_db):
    core_version = core_db.schema_version()
    reg = _plugin_with([
        "CREATE TABLE ext_acme_notes (id INTEGER PRIMARY KEY, text TEXT)",
        "CREATE INDEX ext_acme_ix ON ext_acme_notes(text)",
    ])
    assert migrations.apply_all(core_db.path, reg) == {"acme": 2}
    assert migrations.apply_all(core_db.path, reg) == {"acme": 2}
    assert migrations.version_of(core_db.path, "acme") == 2
    assert core_db.schema_version() == core_version, "the core version is the core's"

    handle = migrations.PluginDatabase(core_db.path, "acme")
    handle.execute("INSERT INTO ext_acme_notes(text) VALUES (?)", ("hi",))
    assert handle.query("SELECT text FROM ext_acme_notes") == [{"text": "hi"}]


@pytest.mark.parametrize("statement", [
    "ALTER TABLE region ADD COLUMN acme TEXT",
    "CREATE INDEX ext_acme_ix ON user(login)",
    "UPDATE user SET role='admin'",
    "DELETE FROM audit",
    "DROP TABLE run",
    "CREATE TABLE ext_acme_x AS SELECT * FROM user",
    "CREATE TABLE sneaky (id INTEGER)",
    "CREATE TABLE ext_other_x (id INTEGER)",
    "CREATE TRIGGER ext_acme_t AFTER INSERT ON user BEGIN SELECT 1; END",
    "PRAGMA user_version = 99",
    "ATTACH DATABASE ':memory:' AS other",
])
def test_plugin_migrations_cannot_touch_core_tables(core_db, statement, caplog):
    reg = _plugin_with(["CREATE TABLE ext_acme_ok (id INTEGER)", statement])
    with caplog.at_level(logging.WARNING, logger="vistest.plugins"):
        outcome = migrations.apply_all(core_db.path, reg)
    assert str(outcome["acme"]).startswith("failed")
    #  All or nothing: the step that was allowed is rolled back with the rest.
    assert migrations.version_of(core_db.path, "acme") == 0
    tables = {r["name"] for r in core_db.query(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "ext_acme_ok" not in tables and "sneaky" not in tables
    assert core_db.schema_version() > 0
    assert "acme" not in {c["name"] for c in core_db.query("PRAGMA table_info(region)")}


def test_the_plugin_handle_cannot_read_core_tables(core_db):
    handle = migrations.PluginDatabase(core_db.path, "acme")
    with pytest.raises(sqlite3.DatabaseError):
        handle.query("SELECT password FROM user")


def test_a_downgraded_plugin_does_not_migrate(core_db):
    migrations.apply_all(core_db.path, _plugin_with(
        ["CREATE TABLE ext_acme_a (id INTEGER)", "CREATE TABLE ext_acme_b (id INTEGER)"]))
    outcome = migrations.apply_all(core_db.path, _plugin_with(
        ["CREATE TABLE ext_acme_a (id INTEGER)"]))
    assert "downgraded" in outcome["acme"]
    assert migrations.version_of(core_db.path, "acme") == 2


def test_one_plugins_failure_does_not_block_another(core_db):
    reg = PluginRegistry()
    with reg.registering("bad"):
        reg.add_migrations(["DROP TABLE user"])
    with reg.registering("good"):
        reg.add_migrations(["CREATE TABLE ext_good_t (id INTEGER)"])
    outcome = migrations.apply_all(core_db.path, reg)
    assert outcome["good"] == 1 and outcome["bad"].startswith("failed")


# --------------------------------------------------------------------------- #
#  Configuration
# --------------------------------------------------------------------------- #
def test_the_plugins_section_keeps_unknown_keys_and_warns_once(tmp_path, caplog):
    from vistest import config as config_module

    path = tmp_path / "vistest.yaml"
    path.write_text("plugins:\n"
                    "  fail_on: confirmed\n"
                    "  disabled: [noisy]\n"
                    "  mystery_knob: 3\n"
                    "  some-plugin:\n"
                    "    level: high\n", "utf-8")
    config_module._warned_plugin_keys.clear()
    with caplog.at_level(logging.WARNING, logger="vistest.plugins"):
        first = VisTestConfig.load(path)
        second = VisTestConfig.load(path)

    assert first.plugins.fail_on == "confirmed"
    assert first.plugins.disabled == ("noisy",)
    assert first.plugins.options == {"mystery_knob": 3,
                                     "some-plugin": {"level": "high"}}
    assert second.plugins == first.plugins
    warned = [r.getMessage() for r in caplog.records if "plugins." in r.getMessage()]
    assert len([w for w in warned if "mystery_knob" in w]) == 1
    assert len([w for w in warned if "some-plugin" in w]) == 1


def test_a_section_named_after_an_installed_plugin_is_not_warned_about(
        tmp_path, caplog, monkeypatch):
    from vistest import config as config_module

    monkeypatch.setattr(loader, "installed_names", lambda: {"known-plugin"})
    path = tmp_path / "vistest.yaml"
    path.write_text("plugins:\n  known-plugin: {a: 1}\n", "utf-8")
    config_module._warned_plugin_keys.clear()
    with caplog.at_level(logging.WARNING, logger="vistest.plugins"):
        cfg = VisTestConfig.load(path)
    assert cfg.plugins.options == {"known-plugin": {"a": 1}}
    assert not [r for r in caplog.records if "known-plugin" in r.getMessage()]


def test_a_bad_core_key_in_the_plugins_section_still_stops_the_load(tmp_path):
    path = tmp_path / "vistest.yaml"
    path.write_text("plugins:\n  fail_on: whenever\n", "utf-8")
    with pytest.raises(ConfigError, match="fail_on"):
        VisTestConfig.load(path)


def test_plugin_options_reach_plugins_through_the_context():
    seen = {}

    class Reader:
        def annotate(self, region, ctx):
            seen.update(ctx.options_for("reader"))
            return ()

    reg = PluginRegistry()
    reg.add_annotator(Reader())
    _refine(reg, [_region()], options={"reader": {"depth": 2}})
    assert seen == {"depth": 2}
