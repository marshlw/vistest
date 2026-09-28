# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The browser corpus: its templates, its capture and its frozen frames.

The checks on the templates run everywhere. The checks that draw run only
where Chromium can be started; without it they are skipped with the reason,
never red.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import browser_corpus as bc  # noqa: E402

#: Anything that could reach the network from a template.
_REMOTE = re.compile(
    r"""(?:src|href)\s*=\s*["']?\s*(?:https?:)?//"""
    r"""|url\(\s*["']?\s*(?:https?:)?//""", re.IGNORECASE)


def _template(key: str) -> str:
    return (bc.TEMPLATE_DIR / f"{key}.html").read_text("utf-8")


# --------------------------------------------------------------------------- #
#  Templates
# --------------------------------------------------------------------------- #
def test_there_are_six_templates_and_nothing_else():
    on_disk = sorted(p.stem for p in bc.TEMPLATE_DIR.glob("*.html"))
    assert on_disk == sorted(t.key for t in bc.TEMPLATES)
    assert len(bc.TEMPLATES) >= 6


@pytest.mark.parametrize("key", [t.key for t in bc.TEMPLATES])
def test_a_template_loads_nothing_from_the_network(key):
    html = _template(key)
    assert not _REMOTE.search(html), f"{key}.html references a remote resource"
    css = (bc.TEMPLATE_DIR / "fonts.css").read_text("utf-8")
    assert not _REMOTE.search(css)
    for url in re.findall(r"""url\(["']?([^"')]+)""", css):
        assert (bc.TEMPLATE_DIR / url).is_file(), f"fonts.css: {url} is missing"


@pytest.mark.parametrize("key", [t.key for t in bc.TEMPLATES])
def test_a_template_carries_every_mutation_target(key):
    """One element per kind of change, or a family silently loses a page."""
    tokens = set()
    for value in re.findall(r'data-m="([^"]+)"', _template(key)):
        tokens.update(value.split())
    missing = [t for t in bc.REQUIRED_TARGETS if t not in tokens]
    assert not missing, f"{key}.html has no data-m target for {missing}"


@pytest.mark.parametrize("key", [t.key for t in bc.TEMPLATES])
def test_every_target_is_one_element(key):
    """A mutation changes exactly one element: its label is about that element."""
    counts: dict[str, int] = {}
    for value in re.findall(r'data-m="([^"]+)"', _template(key)):
        for token in value.split():
            counts[token] = counts.get(token, 0) + 1
    assert {k: n for k, n in counts.items() if n != 1} == {}


def test_the_fonts_are_shipped_with_their_licence():
    fonts = sorted(p.name for p in (bc.TEMPLATE_DIR / "fonts").glob("*.ttf"))
    assert fonts, "no fonts next to the templates"
    licence = (bc.TEMPLATE_DIR / "fonts" / "LICENSE").read_text("utf-8")
    assert "Bitstream" in licence and "DejaVu" in licence


# --------------------------------------------------------------------------- #
#  Capture — only where Chromium runs
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def pw():
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        manager = sync_api.sync_playwright().start()
    except Exception as e:  # pragma: no cover - depends on the machine
        pytest.skip(f"Playwright could not start: {e}")
    try:
        manager.chromium.launch().close()
    except Exception as e:  # pragma: no cover - depends on the machine
        manager.stop()
        pytest.skip(f"no Chromium to run against: {str(e).splitlines()[0]}")
    yield manager
    manager.stop()


def test_every_template_is_drawn_with_the_shipped_fonts_only(pw):
    """A system font here means a glyph fell outside the subset."""
    s = bc.Session(pw)
    try:
        for t in bc.TEMPLATES:
            _, fonts = s.frame(t.key, fonts=True)
            assert fonts, t.key
            system = [f for f in fonts if not f["custom"]]
            assert not system, f"{t.key}: drawn partly with {system}"
    finally:
        s.close()


def test_two_fresh_browsers_draw_the_same_pixels(pw):
    """The control the whole corpus stands on."""
    a, b = bc.Session(pw), bc.Session(pw)
    try:
        for t in bc.TEMPLATES:
            assert bc.differing_pixels(a.frame(t.key), b.frame(t.key)) == 0, t.key
    finally:
        a.close()
        b.close()


def test_every_family_changes_the_page_the_same_way_twice(pw):
    """One magnitude of every family, on one template, in two browsers."""
    a, b = bc.Session(pw), bc.Session(pw)
    try:
        base = a.frame("table")
        for fam in bc.FAMILIES:
            mag = fam.magnitudes[-1]
            mutation, _ = bc.plan_mutation(a, "table", fam, mag)
            png = a.frame("table", mutation)
            assert bc.differing_pixels(base, png), f"{fam.key} changed nothing"
            assert bc.differing_pixels(png, b.frame("table", mutation)) == 0, fam.key
    finally:
        a.close()
        b.close()


def test_a_mutation_that_matches_nothing_is_refused(pw):
    s = bc.Session(pw)
    try:
        with pytest.raises(bc.CorpusError, match="0 elements"):
            s.frame("table", ('[data-m~="nothing"]', "", None))
        with pytest.raises(bc.CorpusError, match="did not run"):
            s.frame("table", ('[data-m~="fill"]', "throw new Error('x')", None))
    finally:
        s.close()


def test_the_environment_names_the_build_that_drew_the_frame(pw):
    env = bc.environment(pw)
    assert env["browser_build"].startswith("chromium_headless_shell-")
    assert env["browser_product"].startswith("HeadlessChrome/")
    assert env["viewport"] == bc.VIEWPORT
    assert env["capture"]["via"] == "vistest.library.targets.capture"
    assert not bc.env_mismatch(env, env)
    other = dict(env, chromium="0.0")
    assert bc.env_mismatch(env, other) == [
        f"chromium: recorded {env['chromium']!r}, here '0.0'"]


# --------------------------------------------------------------------------- #
#  Mutations and noise — the definitions
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("lab1, lab2, expected", [
    #  Sharma, Wu, Dalal (2005), test pairs 1, 7, 17, 25, 13 and 34.
    ((50, 2.6772, -79.7751), (50, 0, -82.7485), 2.0425),
    ((50, 0, 0), (50, -1, 2), 2.3669),
    ((50, 2.5, 0), (73, 25, -18), 27.1492),
    ((60.2574, -34.0099, 36.2677), (60.4626, -34.1751, 39.4387), 1.2644),
    ((50, 2.49, -0.001), (50, -2.49, 0.0009), 7.1792),
    ((2.0776, 0.0795, -1.135), (0.9033, -0.0636, -0.5514), 0.9082),
])
def test_the_colour_ruler_is_ciede2000(lab1, lab2, expected):
    assert round(bc.delta_e00_lab(lab1, lab2), 4) == expected


@pytest.mark.parametrize("rgb", [(37, 99, 235), (31, 41, 55), (229, 231, 235),
                                 (56, 189, 248), (234, 88, 12), (15, 118, 110)])
@pytest.mark.parametrize("target", [2, 4, 8, 15])
def test_a_colour_step_lands_near_its_delta_e(rgb, target):
    new, de = bc.shift_colour(rgb, target)
    assert abs(de - target) <= 0.25
    assert de == pytest.approx(bc.delta_e00(rgb, new))


def test_there_are_at_least_twenty_families_each_with_a_reason():
    assert len(bc.FAMILIES) >= 20
    assert len({f.key for f in bc.FAMILIES}) == len(bc.FAMILIES)
    for f in bc.FAMILIES:
        assert f.target in bc.REQUIRED_TARGETS, f.key
        assert f.why and f.what and f.magnitudes, f.key
        assert len({m.key for m in f.magnitudes}) == len(f.magnitudes), f.key
        for m in f.magnitudes:
            assert m.label in (bc.SIGNAL, bc.NOISE, bc.DISPUTED), (f.key, m.key)
            if m.label != bc.SIGNAL:
                assert m.why, f"{f.key}/{m.key}: a label that is not SIGNAL says why"


def test_the_judgement_calls_are_the_maintainers_and_say_so():
    by = {(f.key, m.key): m for f in bc.FAMILIES for m in f.magnitudes}
    for fam in ("fill", "text_color", "link_color"):
        assert by[(fam, "de2")].label == bc.DISPUTED
        assert by[(fam, "de4")].label == bc.SIGNAL
    assert by[("opacity", "0.98")].label == bc.NOISE
    assert by[("letter_spacing", "plus0.2px")].label == bc.SIGNAL
    for key in (("padding", "plus1px"), ("padding", "minus1px"),
                ("offset", "plus1px"), ("line_height", "plus1px")):
        assert by[key].label == bc.SIGNAL
    decided = [m for m in by.values() if m.why]
    assert decided and all(bc.DECIDED in m.why for m in decided)


def test_the_noise_configurations_are_the_ones_asked_for():
    keys = {c.key for c in bc.NOISE_CONFIGS}
    assert len(keys) >= 6
    assert {"hinting_none", "hinting_full", "no_lcd_no_subpixel",
            "geometric_precision", "shift_0.25px", "shift_0.5px",
            "full_chromium", "gpu_raster"} <= keys


# --------------------------------------------------------------------------- #
#  The frozen corpus
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def manifest():
    return bc.load_manifest()


def test_the_frozen_files_are_what_the_manifest_says(manifest):
    """Every PNG listed, none extra, every sha256 right, under the budget."""
    assert bc.verify_files(manifest) == []


def test_the_budget_holds(manifest):
    total = sum((bc.CORPUS_DIR / rel).stat().st_size for rel in bc.frozen_files(manifest))
    assert total <= bc.BUDGET_BYTES


def test_each_baseline_is_stored_once(manifest):
    bases = {t["base"] for t in manifest["templates"].values()}
    assert len(bases) == len(bc.TEMPLATES)
    for c in manifest["cases"]:
        assert c["expected"] == manifest["templates"][c["template"]]["base"], c["name"]
        assert c["actual"] not in bases
    stored = sorted(p.name for p in bc.FRAMES_DIR.rglob("base.png"))
    assert stored == ["base.png"] * len(bc.TEMPLATES)


def test_every_pair_says_what_it_is(manifest):
    keys = {"name", "template", "split", "family", "magnitude", "label", "why",
            "expected", "actual", "sha256"}
    for c in manifest["cases"]:
        assert keys <= set(c), c["name"]
        assert c["label"] in (bc.SIGNAL, bc.NOISE, bc.DISPUTED)
        assert c["why"], c["name"]
        assert c["changed"]["pixels"] > 0, c["name"]


def test_the_labels_on_disk_are_the_codes(manifest):
    """Relabelling in the code without --regenerate would be a silent change."""
    assert manifest["families"] == json.loads(json.dumps(bc.families_record()))
    defs = {(f.key, m.key): m for f in bc.FAMILIES for m in f.magnitudes}
    for c in manifest["cases"]:
        if c["kind"] == "mutation":
            assert c["label"] == defs[(c["family"], c["magnitude"])].label, c["name"]
        else:
            assert c["label"] == bc.NOISE


def test_the_split_is_by_template(manifest):
    split = manifest["split"]
    assert split[bc.CALIBRATION] == ["table", "form", "cards", "article"]
    assert split[bc.HELD_OUT] == ["landing", "dark"]
    assert "never" in split["rule"] and "threshold" in split["rule"]
    for key, t in manifest["templates"].items():
        assert t["split"] == bc._split_of(key)
    for c in manifest["cases"]:
        assert c["split"] == manifest["templates"][c["template"]]["split"], c["name"]
    assert "never take part" in bc.__doc__


def test_a_configuration_that_changed_nothing_has_no_pairs(manifest):
    in_cases = {c["magnitude"] for c in manifest["cases"] if c["kind"] == "render"}
    for key, cfg in manifest["noise_configs"].items():
        assert cfg["in_corpus"] == (key in in_cases), key
        assert cfg["in_corpus"] == any(cfg["pixels_vs_baseline"].values()), key
        assert cfg["control_pixels"] == 0


def test_the_environment_is_recorded(manifest):
    env = manifest["environment"]
    for key in (*bc.ENV_KEYS, "browser_product", "distro", "viewport",
                "device_scale_factor", "container", "capture"):
        assert key in env, key
    for t in manifest["templates"].values():
        assert t["fonts"] and all(f["custom"] for f in t["fonts"])


def test_the_digest_covers_every_file(manifest, tmp_path):
    import shutil

    digest = bc.corpus_digest()
    assert len(digest) == 64
    copy = tmp_path / "c"
    shutil.copytree(bc.CORPUS_DIR, copy, ignore=shutil.ignore_patterns("templates"))
    assert bc.corpus_digest(copy / "manifest.json") == digest
    victim = copy / manifest["cases"][0]["actual"]
    victim.write_bytes(victim.read_bytes() + b"\0")
    assert bc.corpus_digest(copy / "manifest.json") != digest


def test_the_frozen_frames_are_what_this_environment_draws(pw, manifest):
    """Redrawn only where the manifest was drawn; elsewhere skipped, never red.

    A sample by default — every baseline, every rendering configuration, one
    magnitude of every family on every template. VISTEST_BROWSER_CORPUS_FULL=1
    redraws every pair.
    """
    import os

    full = os.environ.get("VISTEST_BROWSER_CORPUS_FULL") == "1"
    reasons, drifted = bc.drift(manifest, full=full, pw=pw)
    if reasons:
        pytest.skip("the browser corpus was drawn in another environment: "
                    + "; ".join(reasons))
    assert not drifted, (
        f"{len(drifted)} frame(s) no longer come out as frozen: "
        + ", ".join(drifted[:20]) + ". If the templates or the mutations changed "
        "on purpose: python scripts/browser_corpus.py --regenerate")


def test_another_environment_is_a_skip_with_the_reason(pw, manifest):
    other = dict(manifest, environment=dict(manifest["environment"],
                                            browser_build="chromium-0"))
    reasons, drifted = bc.drift(other, pw=pw)
    assert drifted == []
    assert reasons and "browser_build" in reasons[0]


# --------------------------------------------------------------------------- #
#  Noise from another machine
# --------------------------------------------------------------------------- #
def _corpus_copy(tmp_path: Path) -> Path:
    import shutil

    root = tmp_path / "corpus"
    shutil.copytree(bc.CORPUS_DIR, root, ignore=shutil.ignore_patterns("templates"))
    return root


def _fake_capture(tmp_path: Path, manifest: dict, *, os_name: str = "Windows",
                  changed: tuple[str, ...] = ("table", "landing"),
                  env: dict | None = None, name: str = "cap") -> Path:
    """What --capture-noise-only would write: the corpus bases, some of them
    with a few pixels changed, and a record of another machine."""
    import io

    import numpy as np
    from PIL import Image

    src = tmp_path / name
    src.mkdir()
    templates = {}
    for key, t in manifest["templates"].items():
        arr = np.array(bc.pixels((bc.CORPUS_DIR / t["base"]).read_bytes()))
        if key in changed:
            arr[10:13, 20:30] = 255 - arr[10:13, 20:30]
        buf = io.BytesIO()
        Image.fromarray(arr).save(buf, format="PNG")
        data = bc.pack(buf.getvalue())
        (src / f"{key}.png").write_bytes(data)
        templates[key] = {"file": f"{key}.png", "sha256": bc.sha256(data),
                          "split": t["split"], "fonts": t["fonts"]}
    base_env = manifest["environment"]
    record = {
        "format": bc.OS_NOISE_FORMAT, "kind": bc.OS_NOISE_KIND, "tag": "win11",
        "environment": env or dict(
            base_env, os=os_name, os_version="10.0.26100", arch="AMD64",
            distro="Windows-11-10.0.26100-SP0", python="3.13.7",
            host={"dpi": 144, "font_smoothing": {"enabled": True, "type": 2,
                                                 "contrast": 1200,
                                                 "orientation": 1,
                                                 "cleartype": True}}),
        "page": {"device_pixel_ratio": 1, "screen": [1280, 800], "user_agent": "x"},
        "templates": templates,
    }
    (src / bc.OS_ENV_FILE).write_text(json.dumps(record), encoding="utf-8")
    return src


def test_the_family_of_an_os_is_named_after_it():
    assert bc.os_family("Windows") == "os_windows"
    assert bc.os_family("Darwin") == "os_darwin"
    assert bc.os_frame_file("table", "os_windows") == "frames/table/os--windows.png"
    with pytest.raises(bc.CorpusError):
        bc.os_family("")


def test_imported_frames_are_one_noise_pair_per_changed_template(manifest, tmp_path):
    root = _corpus_copy(tmp_path)
    src = _fake_capture(tmp_path, manifest, changed=("table", "landing"))
    new = bc.import_os_noise(src, root=root, log=lambda *_: None)

    assert bc.verify_files(new, root) == []
    assert bc.load_manifest(root / "manifest.json") == json.loads(
        json.dumps(new, ensure_ascii=False))
    os_cases = [c for c in new["cases"] if c["kind"] == "os"]
    assert [c["name"] for c in os_cases] == ["table/os/windows", "landing/os/windows"]
    for c in os_cases:
        assert c["family"] == "os_windows" and c["label"] == bc.NOISE
        assert c["split"] == bc._split_of(c["template"])
        assert c["expected"] == new["templates"][c["template"]]["base"]
        assert c["changed"]["pixels"] == 30
        assert "Windows" in c["why"] and "Nobody changed the page" in c["why"]
    sec = new["os_noise"]["os_windows"]
    assert sec["tag"] == "win11" and sec["in_corpus"] is True
    assert sec["pixels_vs_baseline"] == {t.key: (30 if t.key in ("table", "landing")
                                                 else 0) for t in bc.TEMPLATES}
    differs = {d["key"] for d in sec["differs_from_corpus"]}
    assert {"os", "os_version", "arch", "host"} <= differs
    assert "chromium" not in differs  # the same build: not invented
    keys = list(new)
    assert keys.index("os_noise") == keys.index("noise_configs") + 1
    #  Everything that was there is still there, unchanged.
    assert new["cases"][:len(manifest["cases"])] == manifest["cases"]


def test_a_second_import_of_the_same_os_needs_replace(manifest, tmp_path):
    root = _corpus_copy(tmp_path)
    bc.import_os_noise(_fake_capture(tmp_path, manifest), root=root, log=lambda *_: None)
    again = _fake_capture(tmp_path, manifest, changed=("dark",), name="cap2")
    with pytest.raises(bc.CorpusError, match="already in the corpus"):
        bc.import_os_noise(again, root=root, log=lambda *_: None)
    new = bc.import_os_noise(again, root=root, replace=True, log=lambda *_: None)
    assert [c["name"] for c in new["cases"] if c["kind"] == "os"] == ["dark/os/windows"]
    assert not (root / "frames/table/os--windows.png").exists()
    assert bc.verify_files(new, root) == []


@pytest.mark.parametrize("spoil, match", [
    (lambda r, s: r["environment"].update(viewport={"width": 1920, "height": 1080}),
     "viewport"),
    (lambda r, s: r["environment"].update(capture={"via": "page.screenshot"}),
     "capture"),
    (lambda r, s: r["templates"]["form"].update(sha256="0" * 64), "sha256"),
    (lambda r, s: r["templates"]["cards"].update(
        fonts=[{"family": "Arial", "custom": False, "glyphs": 3}]), "shipped fonts"),
    (lambda r, s: r["templates"].pop("dark"), "templates"),
    (lambda r, s: r["page"].update(device_pixel_ratio=1.5), "devicePixelRatio"),
    (lambda r, s: r.update(kind="something else"), "not a --capture-noise-only"),
])
def test_a_capture_that_is_not_the_corpus_capture_is_refused(manifest, tmp_path,
                                                             spoil, match):
    root = _corpus_copy(tmp_path)
    before = (root / "manifest.json").read_bytes()
    src = _fake_capture(tmp_path, manifest)
    record = json.loads((src / bc.OS_ENV_FILE).read_text("utf-8"))
    spoil(record, src)
    (src / bc.OS_ENV_FILE).write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(bc.CorpusError, match=match):
        bc.import_os_noise(src, root=root, log=lambda *_: None)
    assert (root / "manifest.json").read_bytes() == before
    assert not list(root.rglob("os--*.png"))


def test_regenerate_keeps_other_machines_only_while_the_baselines_pair(manifest, tmp_path):
    import copy

    root = _corpus_copy(tmp_path)
    old = bc.import_os_noise(_fake_capture(tmp_path, manifest), root=root,
                             log=lambda *_: None)
    fresh = copy.deepcopy(manifest)             # what a redraw of the same pixels gives
    staging = tmp_path / "staging"
    staging.mkdir()
    kept, dropped = bc.carry_os_noise(old, root, fresh, staging, log=lambda *_: None)
    assert dropped == []
    assert kept["os_noise"] == old["os_noise"]
    assert [c for c in kept["cases"] if c["kind"] == "os"] == \
        [c for c in old["cases"] if c["kind"] == "os"]
    assert (staging / "frames/table/os--windows.png").read_bytes() == \
        (root / "frames/table/os--windows.png").read_bytes()

    moved = copy.deepcopy(manifest)
    moved["templates"]["form"]["sha256"] = "f" * 64   # a baseline came out otherwise
    said = []
    kept, dropped = bc.carry_os_noise(old, root, moved, tmp_path / "s2", log=said.append)
    assert dropped == ["os_windows"] and "os_noise" not in kept
    assert all(c["kind"] != "os" for c in kept["cases"])
    assert said and "capture them again" in said[0]


def test_the_machine_of_a_frame_is_compared_on_more_than_the_corpus_is():
    env = {"playwright": "1.56.0", "chromium": "141.0.7390.37",
           "browser_build": "chromium_headless_shell-1194", "os": "Windows",
           "arch": "AMD64", "os_version": "10.0.26100",
           "host": {"dpi": 96, "font_smoothing": {"type": 2}}}
    assert bc.os_env_mismatch(env, env) == []
    #  The DPI is recorded, not compared: the context draws at scale 1.
    assert bc.os_env_mismatch(env, dict(env, host={"dpi": 144,
                                                   "font_smoothing": {"type": 2}})) == []
    other = dict(env, os_version="10.0.22631",
                 host={"dpi": 96, "font_smoothing": {"type": 1}})
    said = bc.os_env_mismatch(env, other)
    assert any(s.startswith("os_version") for s in said)
    assert any(s.startswith("font smoothing") for s in said)
    assert bc.os_env_mismatch(env, dict(env, os="Linux"))[0].startswith("os:")


def test_the_os_noise_in_the_manifest_holds_together(manifest):
    sections = manifest.get("os_noise") or {}
    os_cases = [c for c in manifest["cases"] if c["kind"] == "os"]
    assert {c["family"] for c in os_cases} <= set(sections)
    for family, sec in sections.items():
        assert family == bc.os_family(sec["environment"]["os"])
        mine = [c for c in os_cases if c["family"] == family]
        assert {c["template"] for c in mine} == {
            k for k, n in sec["pixels_vs_baseline"].items() if n}
        assert sec["in_corpus"] == bool(mine)
        assert sec["control_pixels"] == 0
        for k in bc.CAPTURE_KEYS:
            assert sec["environment"][k] == manifest["environment"][k], (family, k)
        for c in mine:
            assert c["label"] == bc.NOISE
            assert c["split"] == manifest["templates"][c["template"]]["split"]
            assert c["changed"]["pixels"] == sec["pixels_vs_baseline"][c["template"]]
            assert c["actual"] == bc.os_frame_file(c["template"], family)
        for key, fonts in sec["fonts"].items():
            assert fonts and all(f["custom"] for f in fonts), (family, key)


def test_capture_noise_only_draws_the_six_baselines_and_nothing_else(pw, tmp_path):
    out = tmp_path / "osn"
    record = bc.capture_noise_only(out, "selfcheck", pw=pw, log=lambda *_: None)
    assert sorted(p.name for p in out.iterdir()) == sorted(
        [f"{t.key}.png" for t in bc.TEMPLATES] + [bc.OS_ENV_FILE])
    on_disk = json.loads((out / bc.OS_ENV_FILE).read_text("utf-8"))
    assert on_disk == json.loads(json.dumps(record, ensure_ascii=False))
    env = record["environment"]
    for key in (*bc.ENV_KEYS, "os_version", "host", "browser_product", *bc.CAPTURE_KEYS):
        assert key in env, key
    assert record["page"]["device_pixel_ratio"] == 1
    for t in bc.TEMPLATES:
        entry = record["templates"][t.key]
        assert bc.sha256((out / entry["file"]).read_bytes()) == entry["sha256"]
        assert entry["split"] == t.split
        assert entry["fonts"] and all(f["custom"] for f in entry["fonts"])
        assert "pixels" in entry["vs_corpus_base"]
    assert record["corpus"]["differences"] == bc.env_differences(
        bc.load_manifest()["environment"], env)
    with pytest.raises(bc.CorpusError, match="never mixed"):
        bc.capture_noise_only(out, "selfcheck", pw=pw, log=lambda *_: None)
    with pytest.raises(bc.CorpusError, match="inside the corpus"):
        bc.capture_noise_only(bc.CORPUS_DIR / "x", "selfcheck", log=lambda *_: None)
    with pytest.raises(bc.CorpusError, match="--tag"):
        bc.capture_noise_only(tmp_path / "y", "no spaces", log=lambda *_: None)


def test_the_os_frames_are_what_their_machine_draws(pw, manifest):
    """Redrawn only on the machine they came from; elsewhere skipped, never red."""
    sections = manifest.get("os_noise") or {}
    if not sections:
        pytest.skip("no frames from another machine in the browser corpus")
    skipped, drifted = bc.drift_os(manifest, pw=pw)
    assert not drifted, (
        f"{len(drifted)} frame(s) from another machine no longer come out as "
        f"frozen here: {', '.join(drifted)}. Capture them again: python "
        "scripts/browser_corpus.py --capture-noise-only, then --import-noise --replace")
    if len(skipped) == len(sections):
        pytest.skip("; ".join(f"{fam} was drawn on another machine: " + "; ".join(why)
                              for fam, why in skipped.items()))


def test_frames_of_another_machine_are_a_skip_here(pw, manifest, tmp_path):
    """drift_os on a record of another machine draws nothing and names why."""
    fake = json.loads(json.dumps(manifest))
    fake["os_noise"] = {"os_windows": {"environment": dict(
        manifest["environment"], os="Windows", os_version="10.0.26100")}}
    skipped, drifted = bc.drift_os(fake, pw=pw)
    assert drifted == []
    assert list(skipped) == ["os_windows"]
    assert any(s.startswith("os:") for s in skipped["os_windows"])
