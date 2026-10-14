# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The live corpus (plan, v13): its templates, its photos, its frozen frames.

What is read from disk is checked everywhere. What is drawn is redrawn only
in the environment the manifest records, and skipped elsewhere with the
difference named; the photos are remade only on the OpenCV that made them.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import live_corpus as lc  # noqa: E402
import live_photos as lp  # noqa: E402

_REMOTE = re.compile(r"""(?:src|href)\s*=\s*["']?\s*(?:https?:)?//""", re.IGNORECASE)


@pytest.fixture(scope="module")
def manifest() -> dict:
    return lc.load_manifest()


# --------------------------------------------------------------------------- #
#  Templates and photos
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("key", [t.key for t in lc.TEMPLATES])
def test_a_template_loads_only_its_own_files(key):
    html = (lc.TEMPLATE_DIR / f"{key}.html").read_text("utf-8")
    assert not _REMOTE.search(html)
    for ref in re.findall(r'(?:src|href)="([^"]+)"', html):
        assert (lc.TEMPLATE_DIR / ref).resolve().is_file(), f"{key}.html: {ref}"


@pytest.mark.parametrize("key", [t.key for t in lc.TEMPLATES])
def test_every_target_is_one_element(key):
    html = (lc.TEMPLATE_DIR / f"{key}.html").read_text("utf-8")
    tokens = [t for v in re.findall(r'data-m="([^"]+)"', html) for t in v.split()]
    assert sorted(tokens) == ["part", "photo"]


def test_every_photo_a_family_names_is_there():
    for t in lc.TEMPLATES:
        for f in lc.families(t):
            for m in f.magnitudes:
                if isinstance(m.value, str):
                    assert (lc.TEMPLATE_DIR / m.value).is_file(), m.value


def test_a_tint_lands_on_its_delta_e():
    for name, de in lp.TINTS:
        a = lc.read(lp.PHOTO_DIR / f"{name}.webp")
        b = lc.read(lp.PHOTO_DIR / f"{name}.tint{de}.webp")
        assert abs(lp.median_de(a, b) - de) < 0.05, (name, de)


def test_the_photos_are_what_the_script_makes():
    import cv2

    if cv2.__version__ != lp.MADE_WITH_OPENCV:
        pytest.skip(f"the photos were made with OpenCV {lp.MADE_WITH_OPENCV}, "
                    f"here {cv2.__version__}")
    made = lp.build()
    on_disk = {p.name: p.read_bytes() for p in lp.PHOTO_DIR.glob("*.webp")}
    assert sorted(made) == sorted(on_disk)
    assert [n for n in made if made[n] != on_disk[n]] == []


def test_a_stored_frame_has_the_pixels_of_the_png():
    from vistest.core import pngio

    rng = np.random.default_rng(13)
    rgb = rng.integers(0, 256, (37, 53, 3), dtype=np.uint8)
    assert np.array_equal(lc.read_bytes(lc.to_webp(pngio.encode(rgb))), rgb)


# --------------------------------------------------------------------------- #
#  The frozen frames
# --------------------------------------------------------------------------- #
def test_the_frozen_files_are_what_the_manifest_says(manifest):
    assert lc.verify_files(manifest) == []


def test_the_budget_holds(manifest):
    total = sum((lc.LIVE_DIR / r).stat().st_size for r in lc.frozen_files(manifest))
    assert total <= lc.BUDGET_BYTES


def test_the_split_is_by_template_and_the_held_out_one_is_spent(manifest):
    split = manifest["split"]
    assert split[lc.CALIBRATION] == ["history"]
    assert split[lc.HELD_OUT] == ["gallery"]
    assert "gallery" in split["spent"]
    for c in manifest["cases"]:
        assert c["split"] == manifest["templates"][c["template"]]["split"]


def test_the_labels_are_the_maintainers(manifest):
    """< 0.5 px NOISE, 0.5 px DISPUTED, ΔE00 2 DISPUTED, the rest SIGNAL."""
    for c in manifest["cases"]:
        if c["family"] in ("part_shift", "image_shift"):
            want = "DISPUTED" if c["value"] >= 0.5 else "NOISE"
            assert "2026-10-14" in c["why"], c["name"]
        elif c["name"].endswith("image_tint/de2"):
            want = "DISPUTED"
        else:
            want = "SIGNAL"
        assert c["label"] == want, c["name"]
        assert c["why"], c["name"]


def test_every_pair_changed_pixels_and_names_its_renderer(manifest):
    assert manifest["renderer"]["file"] == lc.CANARY
    names = [c["name"] for c in manifest["cases"]]
    assert len(names) == len(set(names)) == 34
    for c in manifest["cases"]:
        assert c["changed"]["pixels"] > 0, c["name"]
        assert c["renderer"] == {"expected": "base", "actual": "base"}


# --------------------------------------------------------------------------- #
#  Redrawn — only in the environment the manifest records
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


def test_the_frozen_frames_are_what_this_environment_draws(pw, manifest):
    reasons, drifted = lc.drift(manifest, pw=pw)
    if reasons:
        pytest.skip("the live corpus was drawn in another environment: "
                    + "; ".join(reasons))
    assert drifted == [], ("no longer drawn as frozen: " + ", ".join(drifted)
                           + ". On purpose: python scripts/live_corpus.py --regenerate")


def test_another_environment_is_a_skip_with_the_reason(pw, manifest):
    other = dict(manifest, environment=dict(manifest["environment"],
                                            browser_build="chromium-0"))
    reasons, drifted = lc.drift(other, pw=pw)
    assert drifted == [] and "browser_build" in reasons[0]


def test_the_benchmark_reads_every_pair():
    import types

    from tests import benchmark_live

    m = lc.load_manifest()
    tool = benchmark_live.run(m, "v2", ai=False)
    assert sorted(tool.failed) == sorted(c["name"] for c in m["cases"])
    out = benchmark_live.report(m, [tool])
    assert "calibration (17 pairs)" in out and "held out (17 pairs)" in out
    assert benchmark_live.main(types.SimpleNamespace(engine="v2", no_ai=True)) == 0
