# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Without extensions, and with broken ones, everything still works.

This is the proof the open core rests on. Every extension point has a
deterministic path, and here each mode is driven through a whole scenario
with ``VISTEST_DISABLE_PLUGINS=1``:

* **library mode** — a throwaway project, pytest in a subprocess: no
  baseline → created → green → changed → red → a change set aside by
  configuration is green and *said out loud*. Run twice; the results are
  identical.
* **server mode** — the app in-process: capabilities all false, the extension
  routes absent, local sign-in, a check against a baseline, a run recorded
  with every region field present and empty.

Then the same scenarios run with a set of deliberately broken plugins
installed — one that fails to import, one that calls ``sys.exit``, one for the
wrong API version, one that raises half way through ``register``, one that
registers junk, and one whose scorer, annotator and auth provider raise on
every call. The outcome must be the outcome without them.

The broken plugins are real distributions as far as ``importlib.metadata`` is
concerned: a ``*.dist-info`` directory with an ``entry_points.txt`` on
``PYTHONPATH``. Nothing is mocked at the discovery layer.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]

# --------------------------------------------------------------------------- #
#  A distribution full of broken plugins
# --------------------------------------------------------------------------- #
BROKEN = {
    "vt_broken_import.py": (
        "import os, pathlib\n"
        "pathlib.Path(os.environ['VT_MARKERS'], 'imported-import').touch()\n"
        "raise ImportError('this plugin cannot be imported')\n"),
    "vt_broken_exit.py": (
        "import os, pathlib, sys\n"
        "pathlib.Path(os.environ['VT_MARKERS'], 'imported-exit').touch()\n"
        "sys.exit(7)\n"),
    "vt_broken_version.py": (
        "API_VERSION = 0\n"
        "def register(registry):\n"
        "    raise AssertionError('must not be called')\n"),
    "vt_broken_register.py": (
        "API_VERSION = 1\n"
        "class Words:\n"
        "    def annotate(self, region, ctx):\n"
        "        return ()\n"
        "def register(registry):\n"
        "    registry.add_annotator(Words())\n"
        "    raise RuntimeError('half way')\n"),
    "vt_broken_junk.py": (
        "API_VERSION = 1\n"
        "def register(registry):\n"
        "    registry.set_scorer('not a scorer')\n"),
    "vt_broken_runtime.py": (
        "import os, pathlib\n"
        "API_VERSION = 1\n"
        "def _mark(what):\n"
        "    pathlib.Path(os.environ['VT_MARKERS'], what).touch()\n"
        "class Boom:\n"
        "    def score(self, regions, ctx):\n"
        "        _mark('scored')\n"
        "        raise MemoryError('the model does not fit')\n"
        "    def annotate(self, region, ctx):\n"
        "        _mark('annotated')\n"
        "        region.suppressed_by = 'noise: I decide'\n"
        "        raise RuntimeError('annotator down')\n"
        "    def authenticate(self, login, secret):\n"
        "        _mark('authenticated')\n"
        "        raise ConnectionError('directory down')\n"
        "def register(registry):\n"
        "    boom = Boom()\n"
        "    registry.set_scorer(boom, name='boom', priority=100)\n"
        "    registry.add_annotator(boom, name='boom', priority=100)\n"
        "    registry.set_auth_provider(boom, name='boom', priority=100)\n"
        "    registry.add_migrations(['DROP TABLE user'])\n"),
}

ENTRY_POINTS = """[vistest.plugins]
a-broken-import = vt_broken_import
b-broken-exit = vt_broken_exit
c-broken-version = vt_broken_version
d-broken-register = vt_broken_register:register
e-broken-junk = vt_broken_junk
f-broken-runtime = vt_broken_runtime
"""


def _install_broken(site: Path) -> Path:
    site.mkdir(parents=True, exist_ok=True)
    for name, body in BROKEN.items():
        (site / name).write_text(body, "utf-8")
    info = site / "vt_broken_plugins-0.0.0.dist-info"
    info.mkdir(exist_ok=True)
    (info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: vt-broken-plugins\nVersion: 0.0.0\n", "utf-8")
    (info / "entry_points.txt").write_text(ENTRY_POINTS, "utf-8")
    return site


@pytest.fixture
def broken_site(tmp_path) -> Path:
    return _install_broken(tmp_path / "site")


@pytest.fixture
def markers(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "markers"
    path.mkdir()
    monkeypatch.setenv("VT_MARKERS", str(path))
    return path


# --------------------------------------------------------------------------- #
#  Library mode
# --------------------------------------------------------------------------- #
TEST_FILE = '''
import os

import numpy as np

from vistest import expect_screenshot
from vistest.core import pngio


def _frame():
    frame = np.full((300, 400, 3), 245, np.uint8)
    frame[20:60, 20:380] = (40, 60, 120)          # a header
    frame[150:180, 150:230] = (30, 120, 200)      # a button
    state = os.environ.get("DEMO_STATE", "before")
    if state == "after":
        frame[200:280, 60:340] = (200, 30, 30)    # a real change
    elif state == "recoloured":
        frame[150:180, 150:230] = (30, 160, 90)   # the button's colour only
    return pngio.encode(frame)


def test_page():
    expect_screenshot(_frame(), "page.png")
'''

#  Colour changes are set aside by configuration, so the "recoloured" state
#  produces a difference that does not fail — and must still be reported. The
#  unknown key under `plugins:` must be kept and warned about, not refused.
CONFIG = """
diff:
  ignore_kinds: [noise, antialias, color]
plugins:
  fail_on: likely-real
  some_future_plugin:
    anything: goes
"""


def _plugin_args() -> list[str]:
    from importlib.metadata import entry_points

    registered = any(e.value == "vistest.pytest_plugin"
                     for e in entry_points(group="pytest11"))
    return [] if registered else ["-p", "vistest.pytest_plugin"]


def _pytest(project: Path, *args: str, state: str, env: dict | None = None):
    base = {k: v for k, v in os.environ.items() if not k.startswith("VISTEST_")}
    base.pop("PYTEST_ADDOPTS", None)
    base.update(env or {})
    paths = [str(ROOT)]
    if base.get("PYTHONPATH"):
        paths.append(base["PYTHONPATH"])
    base["PYTHONPATH"] = os.pathsep.join(paths)
    base["DEMO_STATE"] = state
    done = subprocess.run(
        [sys.executable, "-m", "pytest", *_plugin_args(),
         "-p", "no:cacheprovider", "-q", "-rN", *args],
        cwd=project, env=base, capture_output=True, text=True, timeout=300)
    return done.returncode, done.stdout + done.stderr


def _parts(project: Path) -> list[dict]:
    """The report rows, without what legitimately differs between runs."""
    rows = []
    for path in sorted((project / ".vistest" / "report" / "parts").glob("*.json")):
        row = json.loads(path.read_text("utf-8"))
        for volatile in ("written_at", "duration_ms", "nodeid"):
            row.pop(volatile, None)
        rows.append(row)
    return rows


def _scenario(project: Path, env: dict) -> dict:
    """The whole library journey. Returns what each step produced."""
    out = {}

    code, text = _pytest(project, state="before", env=env)
    assert code == 1, text
    assert "no baseline for 'page.png'" in text
    assert "INTERNALERROR" not in text

    code, text = _pytest(project, "--vistest-update", state="before", env=env)
    assert code == 0, text
    assert (project / "tests" / "__vistest__" / "page.png").exists()

    code, text = _pytest(project, state="before", env=env)
    assert code == 0, text
    out["same"] = _parts(project)

    code, text = _pytest(project, state="after", env=env)
    assert code == 1, text
    assert "differs from the baseline" in text
    assert "INTERNALERROR" not in text
    out["changed"] = _parts(project)
    out["changed_text"] = text

    code, text = _pytest(project, state="recoloured", env=env)
    assert code == 0, text
    assert "1 difference suppressed by diff.ignore_kinds" in text, text
    out["recoloured"] = _parts(project)
    out["recoloured_text"] = text
    return out


@pytest.fixture
def project(tmp_path) -> Path:
    root = tmp_path / "project"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_page.py").write_text(TEST_FILE, "utf-8")
    (root / "vistest.yaml").write_text(CONFIG, "utf-8")
    return root


def _verdicts(parts: list[dict]) -> list[tuple]:
    """What must not depend on plugins being broken: the outcome and the regions."""
    out = []
    for row in parts:
        out.append((row["verdict"], row.get("metrics"),
                    row.get("regions"), row.get("suppressed"),
                    row.get("suppressed_by_reason")))
    return out


def test_library_mode_with_plugins_disabled_is_complete_and_deterministic(
        project, tmp_path, markers, broken_site):
    env = {"VISTEST_DISABLE_PLUGINS": "1", "PYTHONPATH": str(broken_site),
           "VT_MARKERS": str(markers)}
    first = _scenario(project, env)

    # Nothing from the group was even imported.
    assert not list(markers.iterdir())

    # Every region carries the extension fields, empty.
    changed = first["changed"][0]
    assert changed["verdict"] == "fail"
    assert changed["regions"], "the change must be found"
    for region in changed["regions"]:
        assert region["score"] is None
        assert region["annotations"] == []
        assert region["suppressed_by"] is None

    recoloured = first["recoloured"][0]
    assert recoloured["verdict"] == "pass"
    assert recoloured["suppressed_count"] == 1
    assert recoloured["suppressed"][0]["suppressed_by"].startswith("ignored-kind: color")
    report = (project / ".vistest" / "report" / "index.html").read_text("utf-8")
    assert "suppressed by diff.ignore_kinds" in report

    # Same inputs, a fresh project, the same answers.
    again_root = tmp_path / "again"
    again_root.mkdir()
    for item in ("tests/test_page.py", "vistest.yaml"):
        target = again_root / item
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((project / item).read_text("utf-8"), "utf-8")
    second = _scenario(again_root, env)
    for step in ("same", "changed", "recoloured"):
        assert _strip_paths(first[step], project) == \
            _strip_paths(second[step], again_root), step


def _strip_paths(parts: list[dict], root: Path) -> str:
    return json.dumps(parts, sort_keys=True).replace(str(root), "<root>")


def test_library_mode_with_broken_plugins_behaves_as_without_them(
        project, tmp_path, markers, broken_site):
    """Every broken plugin is a warning; the run is the run without them."""
    reference_root = tmp_path / "reference"
    (reference_root / "tests").mkdir(parents=True)
    (reference_root / "tests" / "test_page.py").write_text(TEST_FILE, "utf-8")
    (reference_root / "vistest.yaml").write_text(CONFIG, "utf-8")
    reference = _scenario(reference_root, {"VISTEST_DISABLE_PLUGINS": "1"})

    #  The bundled extensions are switched off by name, so the only plugins
    #  that load are the broken ones.
    (project / "vistest.yaml").write_text(
        CONFIG + "  disabled: [bundled-extensions]\n", "utf-8")
    env = {"PYTHONPATH": str(broken_site), "VT_MARKERS": str(markers)}
    broken = _scenario(project, env)

    # They really were there, and the runtime one really was called.
    found = {p.name for p in markers.iterdir()}
    assert {"imported-import", "imported-exit", "scored", "annotated"} <= found

    for step in ("same", "changed", "recoloured"):
        assert _verdicts(broken[step]) == _verdicts(reference[step]), step


# --------------------------------------------------------------------------- #
#  Server mode
# --------------------------------------------------------------------------- #
def _png(frame: np.ndarray) -> bytes:
    from vistest.core import pngio

    return pngio.encode(frame)


def _frames():
    before = np.full((300, 400, 3), 245, np.uint8)
    before[20:60, 20:380] = (40, 60, 120)
    before[150:180, 150:230] = (30, 120, 200)
    after = before.copy()
    after[200:280, 60:340] = (200, 30, 30)
    after[150:180, 150:230] = (30, 160, 90)
    return before, after


def _server(tmp_path, monkeypatch, registry=None):
    """A fresh app with the given plugin registry (None — discover, if allowed)."""
    import importlib

    from fastapi import APIRouter
    from fastapi.testclient import TestClient

    import vistest.api.auth as authmod
    import vistest.api.db as dbmod
    from vistest.plugins import loader

    tmp_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VISTEST_ROOT", str(tmp_path / ".vistest"))
    for name in ("VISTEST_AUTH", "VISTEST_INGEST_TOKEN", "VISTEST_INGEST_OPEN"):
        monkeypatch.delenv(name, raising=False)

    loader.reset(registry, loaded=registry is not None)
    dbmod._local = threading.local()
    authmod.router = APIRouter()
    import vistest.api.check as checkmod
    importlib.reload(checkmod)
    import vistest.api.main as mainmod
    importlib.reload(mainmod)
    return TestClient(mainmod.app), mainmod


@pytest.fixture
def restore_plugins():
    from vistest.plugins import loader

    yield
    loader.reset(None)


def _check(client, frame, name="page.png"):
    return client.post(
        "/api/check",
        data={"name": name, "project": "shop", "browser": "chromium",
              "platform": "linux-chromium-1x"},
        files={"image": ("shot.png", _png(frame), "image/png")})


def _stable(body: dict) -> dict:
    body = dict(body)
    for volatile in ("duration_ms", "artifacts"):
        body.pop(volatile, None)
    return body


def _server_scenario(client, mainmod) -> dict:
    from vistest.api.auth import create_user

    out = {}
    create_user(mainmod.db, "root", "password123", role="admin")

    # Local sign-in works; an unknown pair is a plain refusal.
    assert client.post("/api/auth/login",
                       json={"login": "anna", "password": "whatever"}
                       ).status_code == 401
    r = client.post("/api/auth/login",
                    json={"login": "root", "password": "password123"})
    assert r.status_code == 200, r.text

    before, after = _frames()
    first = _check(client, before)
    assert first.status_code == 200, first.text
    assert first.json()["verdict"] == "new_baseline"

    same = _check(client, before)
    assert same.json()["verdict"] == "pass", same.text

    changed = _check(client, after)
    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert body["verdict"] == "fail"
    out["changed"] = _stable(body)
    out["changed_again"] = _stable(_check(client, after).json())

    # Record it as a run and read it back the way the interface does.
    payload = {"run_id": "degradation-1", "project": "shop",
               "platform": "linux-chromium-1x", "browser": "chromium",
               "comparisons": [{**body, "name": "page.png"}]}
    r = client.post("/api/runs", json=payload)
    assert r.status_code == 200, r.text
    comp = mainmod.db.one("SELECT id FROM comparison ORDER BY id DESC LIMIT 1")
    detail = client.get(f"/api/comparisons/{comp['id']}")
    assert detail.status_code == 200, detail.text
    out["detail"] = detail.json()
    return out


def test_server_mode_with_plugins_disabled_is_complete_and_deterministic(
        tmp_path, monkeypatch, restore_plugins, markers, broken_site):
    from vistest.plugins.registry import PluginRegistry

    monkeypatch.setenv("VISTEST_DISABLE_PLUGINS", "1")
    monkeypatch.syspath_prepend(str(broken_site))
    import importlib

    importlib.invalidate_caches()
    client, mainmod = _server(tmp_path, monkeypatch, registry=None)

    from vistest.plugins.loader import active_registry

    assert active_registry().is_empty()
    assert not list(markers.iterdir()), "nothing from the group was imported"
    assert isinstance(active_registry(), PluginRegistry)

    out = _server_scenario(client, mainmod)

    # Two runs of the same comparison, one answer.
    assert out["changed"] == out["changed_again"]

    # The extension fields: present, empty, in the JSON and in the database.
    for region in out["changed"]["regions"]:
        assert region["score"] is None
        assert region["annotations"] == []
        assert region["suppressed_by"] is None
    detail = out["detail"]
    assert detail["regions"]
    for region in detail["regions"]:
        assert region["score"] is None
        assert region["annotations"] == []
        assert region["suppressed_by"] is None
    assert detail["suppressed"] == [] or all(
        r["suppressed_by"] for r in detail["suppressed"])
    columns = {c["name"] for c in mainmod.db.query("PRAGMA table_info(region)")}
    assert {"score", "annotations", "suppressed_by"} <= columns
    assert mainmod.db.query("SELECT * FROM plugin_schema_version") == []

    # No extension: no such capability, and no such routes — not a refusal,
    # an unknown path.
    caps = client.get("/api/capabilities").json()
    assert caps == {"region_scores": False, "region_annotations": False,
                    "external_sign_in": False, "baseline_sync": False}
    for method, path in (("get", "/api/ldap"), ("post", "/api/ldap/test"),
                         ("get", "/api/baselines/export")):
        r = getattr(client, method)(path)
        assert r.status_code in (404, 405), (path, r.status_code)
        assert "ldap" not in r.text.lower()
    r = client.post("/api/baselines/import",
                    files={"file": ("a.tar.gz", b"x", "application/gzip")})
    assert r.status_code in (404, 405)

    # The health answer lists routes; none of the extension ones is there.
    routes = client.get("/api/health").json().get("routes", [])
    assert "/api/ldap" not in routes and "/api/baselines/export" not in routes


def test_server_mode_with_broken_plugins_behaves_as_without_them(
        tmp_path, monkeypatch, restore_plugins, markers, broken_site):
    import importlib

    # Reference: nothing loaded.
    monkeypatch.setenv("VISTEST_DISABLE_PLUGINS", "1")
    client, mainmod = _server(tmp_path / "reference", monkeypatch)
    reference = _server_scenario(client, mainmod)
    mainmod.db.close()

    # The broken set, discovered for real; the bundled extensions left out.
    monkeypatch.delenv("VISTEST_DISABLE_PLUGINS")
    monkeypatch.syspath_prepend(str(broken_site))
    importlib.invalidate_caches()
    config = tmp_path / "broken" / "vistest.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("plugins:\n  disabled: [bundled-extensions]\n", "utf-8")
    client, mainmod = _server(tmp_path / "broken", monkeypatch)

    from vistest.plugins.loader import active_registry

    registry = active_registry()
    assert registry.scorer().name == "boom", "the runtime plugin must be active"
    refused = {r["name"] for r in registry.refused}
    assert {"a-broken-import", "b-broken-exit", "c-broken-version",
            "d-broken-register", "e-broken-junk"} <= refused

    broken = _server_scenario(client, mainmod)

    # The provider was asked and failed; the refusal is the same, and it is
    # recorded.
    assert (markers / "authenticated").exists()
    assert mainmod.db.query(
        "SELECT * FROM audit WHERE action='auth.provider_unavailable'")
    # Its migration tried to drop a core table and was refused.
    assert mainmod.db.one("SELECT COUNT(*) AS n FROM user")["n"] == 1
    assert mainmod.db.query("SELECT * FROM plugin_schema_version") == []

    def outcome(result):
        return {"verdict": result["verdict"], "metrics": result["metrics"],
                "regions": result["regions"], "suppressed": result["suppressed"]}

    assert outcome(broken["changed"]) == outcome(reference["changed"])
    assert [r["suppressed_by"] for r in broken["detail"]["regions"]] == \
        [r["suppressed_by"] for r in reference["detail"]["regions"]]
    assert any("Region scorer skipped" in n for n in broken["changed"]["notes"])


# --------------------------------------------------------------------------- #
#  The command line
# --------------------------------------------------------------------------- #
def test_the_sync_command_says_it_is_not_available(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("VISTEST_")}
    env["VISTEST_DISABLE_PLUGINS"] = "1"
    env["PYTHONPATH"] = str(ROOT)
    done = subprocess.run(
        [sys.executable, "-m", "vistest.cli", "baselines", "export",
         str(tmp_path / "out.tar.gz")],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    assert done.returncode == 2, done.stdout + done.stderr
    assert "not available in this installation" in done.stderr
    assert "Traceback" not in done.stderr
    assert not (tmp_path / "out.tar.gz").exists()
