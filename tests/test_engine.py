# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Бенчмарк движка сравнения.

Две группы, и обе одинаково важны:
  * NOISE  — движок НЕ должен падать (ложные срабатывания дороже пропусков:
             от них команда перестаёт верить тестам и начинает всё аппрувить).
  * SIGNAL — движок ДОЛЖЕН падать и правильно классифицировать изменение.
"""

from __future__ import annotations

import numpy as np
import pytest

from vistest.config import DiffConfig, VisTestConfig
from vistest.core.comparator import compare
from vistest.models import ChangeKind, Verdict

from . import synthetic as syn

CFG = VisTestConfig.preset_of("balanced").diff


def _c(exp, act, cfg: DiffConfig | None = None):
    return compare(exp, act, cfg=cfg or CFG, name="t")


@pytest.fixture(scope="module")
def base():
    return syn.page()


# --------------------------------------------------------------------------- #
#  NOISE: должно проходить
# --------------------------------------------------------------------------- #
def test_identical_passes(base):
    r = _c(base, base.copy())
    assert r.verdict is Verdict.PASS
    assert r.changed_pixels == 0
    assert r.max_severity == 0.0


def test_sensor_noise_passes(base):
    """±2 единицы шума — ниже порога различимости, ΔE00 < 1."""
    r = _c(base, syn.add_sensor_noise(base, sigma=1.6))
    assert r.verdict is Verdict.PASS, r.summary()


def test_antialias_passes(base):
    """Субпиксельный сдвиг рендера текста — самый частый ложный дифф."""
    r = _c(base, syn.resample_antialias(base, amount=0.4))
    assert r.verdict is Verdict.PASS, r.summary()


def test_jpeg_recompression_passes(base):
    r = _c(base, syn.recompress(base, quality=92))
    assert r.verdict is Verdict.PASS, r.summary()


def test_one_pixel_global_shift_passes(base):
    """Сдвиг всей страницы на 1px компенсируется выравниванием."""
    r = _c(base, syn.shift(base, dx=1, dy=1))
    assert r.aligned, r.summary()
    assert r.verdict is Verdict.PASS, r.summary()


def test_dynamic_clock_suppressed_by_stability_mask(base):
    """Живые часы гасятся stability-маской, а не ручным селектором."""
    from vistest.core.noise import stability_mask

    frames = [syn.add_clock(base, t) for t in ("12:41:07", "12:41:08", "12:41:09")]
    mask = stability_mask(frames)
    assert mask.any(), "маска нестабильности должна поймать часы"

    r = compare(frames[0], syn.add_clock(base, "13:02:55"),
                cfg=CFG, name="clock", ignore_mask=mask)
    assert r.verdict is Verdict.PASS, r.summary()


def test_combined_noise_passes(base):
    """Всё сразу: шум + AA + сдвиг + пережатие."""
    act = syn.recompress(
        syn.add_sensor_noise(syn.resample_antialias(syn.shift(base, 1, 0)), 1.2),
        quality=94,
    )
    r = _c(base, act)
    assert r.verdict is Verdict.PASS, r.summary()


# --------------------------------------------------------------------------- #
#  SIGNAL: должно падать
# --------------------------------------------------------------------------- #
def test_button_color_change_detected(base):
    r = _c(base, syn.regress_button_color(base))
    assert r.verdict is Verdict.FAIL, r.summary()
    hit = [x for x in r.regions if x.y > 400 and x.x < 400]
    assert hit, r.summary()
    assert hit[0].kind in (ChangeKind.COLOR, ChangeKind.CONTENT, ChangeKind.TEXT)


def test_button_removed_detected(base):
    r = _c(base, syn.regress_button_removed(base))
    assert r.verdict is Verdict.FAIL, r.summary()
    kinds = {x.kind for x in r.regions}
    assert ChangeKind.REMOVED in kinds or ChangeKind.CONTENT in kinds, r.summary()


def test_promo_block_removed_detected(base):
    r = _c(base, syn.regress_promo_gone(base))
    assert r.verdict is Verdict.FAIL, r.summary()
    assert r.max_severity >= 25, r.summary()


def test_layout_shift_detected_not_swallowed(base):
    """Сдвиг блока на 40px — больше лимита выравнивания, обязан быть виден."""
    r = _c(base, syn.regress_layout_moved(base, dy=40))
    assert r.verdict is Verdict.FAIL, r.summary()
    assert not r.aligned or abs(r.align_dy) < 8, r.summary()


def test_page_height_change_detected(base):
    """Обрезка до min(h,w) спрятала бы это — самый частый реальный регресс."""
    r = _c(base, syn.regress_taller_page(base, extra=160))
    assert r.size_changed
    assert r.verdict is Verdict.FAIL, r.summary()


def test_small_icon_change_detected(base):
    """24×24 не должно теряться: фильтр по площади bbox такое пропускал."""
    cfg = VisTestConfig.preset_of("strict").diff
    r = _c(base, syn.regress_tiny_icon(base), cfg)
    assert r.regions, r.summary()
    assert any(x.w <= 40 and x.h <= 40 for x in r.regions), r.summary()


def test_moved_block_classified_as_moved():
    """Блок уехал вниз на 60px, содержимое то же → MOVED, не CONTENT."""
    exp = syn.blank(600, 600)
    import cv2

    cv2.rectangle(exp, (60, 80), (420, 200), (52, 120, 246), -1)
    cv2.putText(exp, "PANEL", (110, 155), 0, 1.2, (255, 255, 255), 3, cv2.LINE_AA)

    act = syn.blank(600, 600)
    cv2.rectangle(act, (60, 140), (420, 260), (52, 120, 246), -1)
    cv2.putText(act, "PANEL", (110, 215), 0, 1.2, (255, 255, 255), 3, cv2.LINE_AA)

    r = _c(exp, act)
    assert r.regions, r.summary()
    assert any(x.kind is ChangeKind.MOVED for x in r.regions), r.summary()


# --------------------------------------------------------------------------- #
#  Инварианты
# --------------------------------------------------------------------------- #
def test_color_change_on_flat_fill_not_hidden_by_consensus():
    """Смена цвета плоской заливки не меняет структуру (SSIM≈1).

    Проверяем, что «сильный цвет» проходит мимо консенсуса — иначе фильтр
    сам себя перехитрил бы и спрятал очевидный регресс.
    """
    exp = np.full((300, 300, 3), (240, 240, 240), np.uint8)
    act = np.full((300, 300, 3), (240, 200, 200), np.uint8)
    r = _c(exp, act)
    assert r.changed_pixels > 0, r.summary()
    assert r.verdict is Verdict.FAIL, r.summary()


def test_antialias_filter_cannot_hide_flat_change():
    """АА-фильтр физически не должен подавлять изменения на плоскости."""
    from vistest.core.antialias import antialias_mask

    a = np.full((200, 200), 128, np.uint8)
    b = np.full((200, 200), 160, np.uint8)
    assert not antialias_mask(a, b).any()


def test_severity_is_monotonic_in_area():
    """Больше изменённая площадь → не меньшая severity."""
    import cv2

    prev = -1.0
    for size in (30, 70, 140, 260):
        exp = syn.blank(600, 600)
        act = exp.copy()
        cv2.rectangle(act, (100, 100), (100 + size, 100 + size), (220, 40, 40), -1)
        r = _c(exp, act)
        assert r.max_severity >= prev - 1e-6, f"size={size}"
        prev = r.max_severity


def test_delta_e_zero_for_identical_colors():
    from vistest.core.color import delta_e_ciede2000, srgb_to_lab

    img = np.random.default_rng(0).integers(0, 256, (64, 64, 3), dtype=np.uint8)
    lab = srgb_to_lab(img)
    de = delta_e_ciede2000(lab, lab)
    assert float(de.max()) < 1e-3


def test_delta_e_matches_reference_pairs():
    """Контрольные пары из CIE Technical Report (Sharma et al., 2005)."""
    from vistest.core.color import delta_e_ciede2000

    cases = [
        ((50.0000, 2.6772, -79.7751), (50.0000, 0.0000, -82.7485), 2.0425),
        ((50.0000, 3.1571, -77.2803), (50.0000, 0.0000, -82.7485), 2.8615),
        ((50.0000, 2.8361, -74.0200), (50.0000, 0.0000, -82.7485), 3.4412),
        ((50.0000, -1.3802, -84.2814), (50.0000, 0.0000, -82.7485), 1.0000),
        ((60.2574, -34.0099, 36.2677), (60.4626, -34.1751, 39.4387), 1.2644),
        ((22.7233, 20.0904, -46.6940), (23.0331, 14.9730, -42.5619), 2.0373),
    ]
    for lab1, lab2, expected in cases:
        a = np.array(lab1, np.float32).reshape(1, 1, 3)
        b = np.array(lab2, np.float32).reshape(1, 1, 3)
        got = float(delta_e_ciede2000(a, b)[0, 0])
        assert abs(got - expected) < 0.02, f"{lab1} vs {lab2}: {got} != {expected}"


def test_result_is_json_serializable(base):
    import json

    from vistest.core.comparator import strip_internal

    r = strip_internal(_c(base, syn.regress_button_color(base)))
    json.loads(r.to_json())


# --------------------------------------------------------------------------- #
#  What a result is allowed to contain
#
#  This section exists because of one live run: eleven tests on somebody else's
#  project, a screenshot that legitimately differed on the fifth, and the whole
#  session gone with `TypeError: Object of type ndarray is not JSON
#  serializable` raised from inside a report hook.
#
#  The cause was not a metric. `compare` hands the renderer four full-frame
#  arrays, and it used to hand them over inside `CompareResult.artifacts` —
#  a field declared `dict[str, str]`, serialised into every report, with a
#  `# type: ignore[assignment]` on each write. Every caller in the package
#  stripped them before serialising; the library-mode caller, added later, did
#  not, because nothing in the type said it had to.
#
#  So the checks below are about the shape of a result, not about one field:
#  every metric a plain Python number, `artifacts` only strings, and the whole
#  thing serialisable with a bare `json.dumps` — no fallback encoder involved.
# --------------------------------------------------------------------------- #
_REGION_NUMBERS = ("x", "y", "w", "h", "severity", "de_mean", "de_max",
                   "ssim_local", "pixel_count", "fill_ratio", "moved_dx",
                   "moved_dy", "match_score", "edge_density")
_RESULT_NUMBERS = ("ssim_global", "de_mean", "de_p95", "changed_pixels",
                   "total_pixels", "changed_area_pct", "max_severity",
                   "align_dx", "align_dy", "duration_ms")


def _plain(value) -> bool:
    """A Python int or float, and not a numpy anything wearing that face."""
    return type(value) in (int, float, bool)


@pytest.mark.parametrize("mutate", [
    syn.regress_button_color,
    syn.regress_button_removed,
    syn.regress_layout_moved,          # the MOVED path: dx, dy, match_score
    syn.regress_tiny_icon,
    syn.regress_taller_page,           # sizes differ, so padding is involved
])
def test_every_metric_of_a_real_comparison_is_a_plain_number(base, mutate):
    """A metric holding an array is a wrong metric, not a serialisation detail.

    Everything that compares one of these against a threshold would be
    comparing something else — so this is checked on the values, before any
    question of JSON comes up.
    """
    r = _c(base, mutate(base))

    for field in _RESULT_NUMBERS:
        assert _plain(getattr(r, field)), (field, type(getattr(r, field)))
    for pair in (r.size_expected, r.size_actual):
        assert all(_plain(v) for v in pair), pair

    for region in [*r.regions, *r.suppressed]:
        for field in _REGION_NUMBERS:
            value = getattr(region, field)
            assert _plain(value), (field, type(value), region)
        for field in ("perceptual_distance", "gate_probability", "region_index"):
            value = getattr(region, field)
            assert value is None or _plain(value), (field, type(value))


@pytest.mark.parametrize("mutate", [syn.regress_button_color,
                                    syn.regress_promo_gone])
def test_a_result_serialises_with_a_bare_json_dumps(base, mutate):
    """No fallback encoder, no cleanup call, nothing to remember.

    `to_json` has a `default=` safety net, and it must stay — but a test that
    went through it would pass just as happily with arrays in the result, which
    is exactly what nobody noticed the first time.
    """
    import json

    r = _c(base, mutate(base))

    json.dumps(r.to_dict())            # deliberately without `default=`
    assert json.loads(r.to_json())["name"] == "t"

    assert all(isinstance(v, str) for v in r.artifacts.values()), r.artifacts
    assert not any(k.startswith("_") for k in r.artifacts), r.artifacts


def test_the_maps_are_handed_over_and_can_be_released(base):
    """The renderer needs them; everyone else needs them gone.

    `strip_internal` is no longer what makes a result serialisable — it is what
    stops four screenshot-sized arrays being held for the lifetime of the
    result, which in a suite of two hundred is the difference between a run and
    an out-of-memory kill.
    """
    from vistest.core.comparator import strip_internal

    r = _c(base, syn.regress_button_color(base))
    assert set(r.maps) >= {"expected", "aligned_actual", "de_map", "mask"}
    assert r.maps["de_map"].shape[:2] == r.maps["mask"].shape[:2]

    import json

    json.dumps(r.to_dict())            # serialisable even with the maps present

    strip_internal(r)
    assert r.maps == {}
    json.dumps(r.to_dict())
