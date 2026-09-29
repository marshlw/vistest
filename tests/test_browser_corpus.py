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
    assert manifest["cross_render"] == json.loads(json.dumps(bc.cross_record()))
    defs = {(f.key, m.key): m for f in bc.FAMILIES for m in f.magnitudes}
    cross = {(fam.key, mag.key, cfg): label for fam, mag, cfg, label
             in bc.cross_plan(everything=True)}
    for c in manifest["cases"]:
        if c["kind"] == "mutation":
            assert c["label"] == defs[(c["family"], c["magnitude"])].label, c["name"]
        elif c["kind"] == "cross_render":
            d = c["detail"]
            assert c["label"] == cross[(d["mutation"], c["magnitude"], d["config"])]
        elif c["kind"] == "render":
            cfg = next(x for x in bc.NOISE_CONFIGS if x.key == c["magnitude"])
            assert c["label"] == cfg.label, c["name"]
            assert manifest["noise_configs"][cfg.key].get("label", bc.NOISE) == cfg.label
        else:
            assert c["label"] == bc.NOISE


def test_geometric_precision_is_disputed_by_decision_and_says_why(manifest):
    """The page's stylesheet, not the renderer: the canary proved it."""
    cfg = next(c for c in bc.NOISE_CONFIGS if c.key == "geometric_precision")
    assert cfg.label == bc.DISPUTED and "maintainer" in cfg.why and "canary" in cfg.why
    assert manifest["noise_configs"]["geometric_precision"]["why"] == cfg.why
    for c in manifest["cases"]:
        if c["family"] == "render:geometric_precision":
            assert c["label"] == bc.DISPUTED and c["why"] == cfg.why
    others = [c for c in bc.NOISE_CONFIGS if c.key != "geometric_precision"]
    assert all(c.label == bc.NOISE and not c.why for c in others)


# --------------------------------------------------------------------------- #
#  Real changes drawn by another renderer
# --------------------------------------------------------------------------- #
def test_the_cross_renderer_family_is_what_was_asked_for():
    signal = {k for k, _ in bc.CROSS_SIGNAL}
    assert signal == {"text_color", "link_color", "icon_color", "fill", "element_removed",
                      "icon_swap", "one_char", "word_swap", "border_added",
                      "border_removed", "underline"}
    for k, mags in bc.CROSS_SIGNAL:
        if k in ("text_color", "link_color", "icon_color", "fill"):
            assert mags == ("de8", "de15"), k
    assert {k for k, _ in bc.CROSS_DISPUTED} == {"padding", "offset", "letter_spacing",
                                                  "font_size"}
    assert bc.CROSS_CONFIGS == ("hinting_none", "full_chromium")
    fams = {f.key: {m.key: m for m in f.magnitudes} for f in bc.FAMILIES}
    for k, mags in (*bc.CROSS_SIGNAL, *bc.CROSS_DISPUTED):
        for m in mags:
            assert m in fams[k], (k, m)
    assert "maintainer" in bc.WHY_CROSS_DISPUTED and "never counted" in bc.WHY_CROSS_DISPUTED


def test_what_is_left_out_of_the_cross_family_is_named_with_the_reason(manifest):
    plan = {(f.key, m.key) for f, m, _, _ in bc.cross_plan()}
    everything = {(f.key, m.key) for f, m, _, _ in bc.cross_plan(everything=True)}
    assert plan | set(bc.CROSS_LEFT_OUT) == everything
    assert not plan & set(bc.CROSS_LEFT_OUT)
    left = manifest["cross_render"]["left_out"]
    assert left["magnitudes"] == sorted(f"{k}/{m}" for k, m in bc.CROSS_LEFT_OUT)
    assert "budget" in left["why"]


def test_every_cross_pair_is_a_change_drawn_by_another_renderer(manifest):
    cross = [c for c in manifest["cases"] if c["kind"] == "cross_render"]
    plan = bc.cross_plan()
    assert len(cross) == len(plan) * len(bc.TEMPLATES)
    for c in cross:
        d = c["detail"]
        assert c["family"] == bc.cross_family(d["mutation"], d["config"])
        assert c["expected"] == manifest["templates"][c["template"]]["base"]
        assert c["renderer"] == {"expected": bc.BASE_RENDERER, "actual": d["config"]}
        assert c["split"] == manifest["templates"][c["template"]]["split"]


# --------------------------------------------------------------------------- #
#  The renderer's fingerprint
# --------------------------------------------------------------------------- #
def test_every_frame_names_the_canary_of_its_renderer(manifest):
    fps = manifest["renderers"]["fingerprints"]
    assert set(fps) >= {bc.BASE_RENDERER, *(c.key for c in bc.NOISE_CONFIGS)}
    for t in manifest["templates"].values():
        assert t["renderer"] == bc.BASE_RENDERER
    for c in manifest["cases"]:
        ref = c["renderer"]
        assert ref["expected"] == bc.BASE_RENDERER, c["name"]
        if c["kind"] == "os" and ref["actual"] is None:
            continue                     # a machine that sent no canary: unknown
        assert ref["actual"] in fps, c["name"]
    by = {"mutation": bc.BASE_RENDERER}
    for c in manifest["cases"]:
        if c["kind"] in by:
            assert c["renderer"]["actual"] == by[c["kind"]]
        if c["kind"] == "render":
            assert c["renderer"]["actual"] == c["magnitude"]


def test_the_canaries_tell_the_renderers_apart_and_the_controls_do_not(manifest):
    fps = manifest["renderers"]["fingerprints"]
    assert manifest["renderers"]["canary"] == bc.canary_record()
    for key in bc.RENDERER_CONTROLS:
        assert fps[key]["pixels_vs_base"] == 0 and "control" in fps[key], key
    for c in bc.NOISE_CONFIGS:
        if c.launch.css:                 # the page's stylesheet, not the renderer
            assert fps[c.key]["pixels_vs_base"] == 0 and fps[c.key]["note"], c.key
    for key in ("hinting_none", "no_lcd_no_subpixel", "full_chromium"):
        assert fps[key]["pixels_vs_base"] > 0, key
    base = (bc.CORPUS_DIR / fps[bc.BASE_RENDERER]["file"]).read_bytes()
    for key, rec in fps.items():
        data = (bc.CORPUS_DIR / rec["file"]).read_bytes()
        assert bc.sha256(data) == rec["sha256"], key
        assert bc.differing_pixels(base, data) == rec["pixels_vs_base"], key


def test_the_canary_is_drawn_the_same_way_twice(pw):
    a, b = bc.Session(pw), bc.Session(pw)
    try:
        png = a.canary()
        assert bc.differing_pixels(png, b.canary()) == 0
    finally:
        a.close()
        b.close()


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
    """A copy of the corpus without frames from other machines, whatever the
    repository holds: these tests import their own."""
    import shutil

    root = tmp_path / "corpus"
    shutil.copytree(bc.CORPUS_DIR, root, ignore=shutil.ignore_patterns("templates"))
    m = bc.load_manifest(root / "manifest.json")
    for c in m["cases"]:
        if c["kind"] == "os":
            (root / c["actual"]).unlink()
    m = dict(m, cases=[c for c in m["cases"] if c["kind"] != "os"])
    m.pop("os_noise", None)
    fps = m["renderers"]["fingerprints"]
    for key in [k for k in fps if k.startswith("os_")]:
        (root / fps.pop(key)["file"]).unlink()
    bc.write_manifest(m, root / "manifest.json")
    assert bc.verify_files(m, root) == []
    return root


def _fake_capture(tmp_path: Path, manifest: dict, *, os_name: str = "Windows",
                  changed: tuple[str, ...] = ("table", "landing"),
                  env: dict | None = None, name: str = "cap",
                  canary: bool = False) -> Path:
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
    if canary:
        fp = manifest["renderers"]["fingerprints"][bc.BASE_RENDERER]
        arr = np.array(bc.pixels((bc.CORPUS_DIR / fp["file"]).read_bytes()))
        arr[2:4, 3:9] = 255 - arr[2:4, 3:9]
        buf = io.BytesIO()
        Image.fromarray(arr).save(buf, format="PNG")
        data = bc.pack(buf.getvalue())
        (src / bc.OS_CANARY_FILE).write_bytes(data)
        record["canary"] = {"file": bc.OS_CANARY_FILE, "sha256": bc.sha256(data),
                            **bc.canary_record()}
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
    before = bc.load_manifest(root / "manifest.json")
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
    assert new["cases"][:len(before["cases"])] == before["cases"]


def test_an_imported_canary_names_the_renderer_of_the_machine(manifest, tmp_path):
    root = _corpus_copy(tmp_path)
    new = bc.import_os_noise(_fake_capture(tmp_path, manifest, canary=True), root=root,
                             log=lambda *_: None)
    fp = new["renderers"]["fingerprints"]["os_windows"]
    assert fp["file"] == bc.renderer_file("os_windows") and fp["pixels_vs_base"] == 12
    assert fp["drawn_with"]["tag"] == "win11"
    for c in (c for c in new["cases"] if c["kind"] == "os"):
        assert c["renderer"] == {"expected": bc.BASE_RENDERER, "actual": "os_windows"}
    assert bc.verify_files(new, root) == []


def test_a_capture_without_a_canary_leaves_the_renderer_unknown(manifest, tmp_path):
    root = _corpus_copy(tmp_path)
    new = bc.import_os_noise(_fake_capture(tmp_path, manifest), root=root,
                             log=lambda *_: None)
    assert "os_windows" not in new["renderers"]["fingerprints"]
    assert all(c["renderer"]["actual"] is None for c in new["cases"] if c["kind"] == "os")


def test_the_same_pixels_again_bring_the_canary_without_replace(manifest, tmp_path):
    """The first capture had no canary; the same machine draws the same frames
    again with one. No frame changes, and --replace is not needed."""
    root = _corpus_copy(tmp_path)
    first = bc.import_os_noise(_fake_capture(tmp_path, manifest), root=root,
                               log=lambda *_: None)
    frames = {c["actual"]: (root / c["actual"]).read_bytes()
              for c in first["cases"] if c["kind"] == "os"}
    again = _fake_capture(tmp_path, manifest, name="cap2", canary=True)
    new = bc.import_os_noise(again, root=root, log=lambda *_: None)
    assert "os_windows" in new["renderers"]["fingerprints"]
    assert {rel: (root / rel).read_bytes() for rel in frames} == frames
    assert [c["name"] for c in new["cases"]] == [c["name"] for c in first["cases"]]
    assert bc.verify_files(new, root) == []


def test_a_canary_of_another_page_is_refused(manifest, tmp_path):
    root = _corpus_copy(tmp_path)
    src = _fake_capture(tmp_path, manifest, canary=True)
    record = json.loads((src / bc.OS_ENV_FILE).read_text("utf-8"))
    record["canary"]["version"] = 0
    (src / bc.OS_ENV_FILE).write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(bc.CorpusError, match="drawn from another page"):
        bc.import_os_noise(src, root=root, log=lambda *_: None)


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
    plain = bc.load_manifest(root / "manifest.json")
    old = bc.import_os_noise(_fake_capture(tmp_path, manifest), root=root,
                             log=lambda *_: None)
    fresh = copy.deepcopy(plain)                # what a redraw of the same pixels gives
    staging = tmp_path / "staging"
    staging.mkdir()
    kept, dropped = bc.carry_os_noise(old, root, fresh, staging, log=lambda *_: None)
    assert dropped == []
    assert kept["os_noise"] == old["os_noise"]
    assert [c for c in kept["cases"] if c["kind"] == "os"] == \
        [c for c in old["cases"] if c["kind"] == "os"]
    assert (staging / "frames/table/os--windows.png").read_bytes() == \
        (root / "frames/table/os--windows.png").read_bytes()

    moved = copy.deepcopy(plain)
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
        [f"{t.key}.png" for t in bc.TEMPLATES] + [bc.OS_ENV_FILE, bc.OS_CANARY_FILE])
    canary = record["canary"]
    assert canary["file"] == bc.OS_CANARY_FILE
    assert bc.sha256((out / bc.OS_CANARY_FILE).read_bytes()) == canary["sha256"]
    assert canary["version"] == bc.canary_record()["version"]
    assert canary["vs_corpus_base"] == 0          # this machine is the corpus's
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
