# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Single point for screenshot checking.

The same function serves four entry points:
  * the pytest fixture `visual`
  * the HTTP endpoint `POST /api/check` (for tests in any language)
  * the CLI `vistest check`
  * baseline recording mode

This rules out behavior divergence: the engine, thresholds, masks and artifacts
are the same regardless of where the snapshot came from.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .config import VisTestConfig, platform_key
from .core import noise as _noise
from .core import thresholds as _thresholds
from .core.comparator import compare, strip_internal
from .models import CompareResult, Verdict
from .render.artifacts import render_all
from .storage import BaselineRecord, FileBaselineStore


def slug(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name).strip("_")


#  Kept as module-level names because they have always been importable from
#  here. The rules themselves live in `core.thresholds`, which is also where
#  the layering they take part in is written down.
SNAPSHOT_THRESHOLDS = _thresholds.SNAPSHOT_THRESHOLDS
_snapshot_thresholds = _thresholds.from_meta


#: Later frames the second look asks a caller's `recapture` for, at most.
RETRY_FRAMES = 4


def _old_way(then: dict | None, now: dict | None) -> list[str]:
    """A failure against a baseline taken an older way says so (capture/ready.py).

    Only when this picture was taken by our capture — its meta carries
    `capture_version` — and the baseline's is lower or absent: a picture
    uploaded from elsewhere has no way of being taken to compare.
    """
    from .capture.ready import old_way

    try:
        version = int((now or {}).get("capture_version") or 0)
        before = int((then or {}).get("capture_version") or 1)
    except (TypeError, ValueError):
        return []
    if not version or before >= version:
        return []
    return [old_way("in the review screen, or with --update")]


class CheckService:
    """Comparison of a snapshot against a baseline + artifacts. No browser and no network."""

    def __init__(
        self,
        cfg: VisTestConfig | None = None,
        *,
        platform: str | None = None,
        browser: str = "chromium",
        run_dir: str | Path | None = None,
        store=None,
    ):
        """store — your own baseline storage.

        Needed for projects connected from outside: their baselines live in git next to
        the code, and swapping the path is not enough — reading and writing must happen in
        place. See `storage.ExternalBaselineStore`.
        """
        self.cfg = cfg or VisTestConfig.load()
        self.browser = browser
        self.platform = platform or platform_key(
            browser, self.cfg.capture.device_scale_factor
        )
        self.store = store or FileBaselineStore(
            self.cfg.baselines_path(self.platform))
        self.run_dir = Path(run_dir) if run_dir else self.cfg.runs_path() / "adhoc"
        self.run_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ #
    def check(
        self,
        name: str,
        rgb: np.ndarray,
        *,
        frames: list[np.ndarray] | None = None,
        unstable: np.ndarray | None = None,
        dom: dict | None = None,
        diff_overrides: dict | None = None,
        update_baseline: bool | None = None,
        render: bool = True,
        notes: list[str] | None = None,
        meta: dict | None = None,
        recapture=None,
        ready: bool | None = None,
        not_ready: str = "",
    ) -> CompareResult:
        """Compare a snapshot against a baseline.

        frames  — additional frames of the same page; discrepancies between them
                  turn into an instability mask (auto-suppression of motion).
        unstable— a ready-made mask, if the client computed it itself.
        recapture — как снять ещё один кадр той же страницы. Вызывается ТОЛЬКО
                  при падении: движок решает, что делать, а вызывающий умеет
                  снимать — сюда одинаково подходят и наш раннер, и адаптер
                  чужих тестов, и съёмка по адресу.
        ready   — was the page ready when `rgb` was taken (capture/ready.py)?
                  False: the second look masks nothing (`not_ready` says why);
                  None: not known.
        """
        notes = list(notes or [])

        if unstable is None:
            if frames and len(frames) >= 2:
                unstable = _noise.stability_mask(frames)
                ratio = _noise.instability_score(unstable)
                if ratio > self.cfg.capture.max_unstable_ratio:
                    notes.append(
                        f"WARNING: {ratio * 100:.1f}% of pixels are unstable — "
                        "the page is too dynamic"
                    )
            else:
                unstable = np.zeros(rgb.shape[:2], dtype=bool)
                if not frames:
                    notes.append(
                        "A single frame was sent: auto-detection of motion is off. "
                        "Send 2–3 frames in a row so that noise is suppressed automatically."
                    )

        want_update = self.cfg.update_baselines if update_baseline is None else update_baseline

        # ---------- the baseline does not exist or an overwrite is requested ----------
        if not self.store.exists(name) or want_update:
            existed = self.store.exists(name)
            self.store.save(BaselineRecord(
                name=name, image=rgb, stability_mask=unstable, dom=dom,
                meta={"platform": self.platform, "browser": self.browser, **(meta or {})},
            ))
            res = CompareResult(name=name, verdict=Verdict.NEW_BASELINE)
            res.size_expected = res.size_actual = (int(rgb.shape[1]), int(rgb.shape[0]))
            res.total_pixels = int(rgb.shape[0] * rgb.shape[1])
            res.notes = notes + [
                "Baseline updated" if existed else "Baseline created (first run)"
            ]
            if render:
                out = self.run_dir / slug(name)
                out.mkdir(parents=True, exist_ok=True)
                from .capture.playwright_capture import _write_png

                _write_png(out / "actual.png", rgb)
                res.artifacts = {"actual": str(out / "actual.png")}
                (out / "result.json").write_text(res.to_json(), encoding="utf-8")
            return res

        # ---------- comparison ----------
        baseline = self.store.load(name)
        assert baseline is not None

        ignore = _noise.merge_masks(baseline.stability_mask, unstable, sticky=True)
        if baseline.ignore_boxes:
            # Зона может держаться за ЭЛЕМЕНТ, а не за прямоугольник, и тогда
            # маска встаёт туда, где элемент сейчас, — в обоих кадрах сразу.
            # Единственное место во всём движке, где на руках есть оба DOM'а:
            # эталонный лежит в хранилище, текущий приехал вместе с кадром.
            # Дальше по дороге их уже нет, и опознавать будет нечем.
            from .core import regions as _zones

            boxes_mask, resolved = _zones.mask(
                rgb.shape[:2], baseline.ignore_boxes,
                baseline_dom=baseline.dom, actual_dom=dom)
            ignore = _noise.merge_masks(ignore, boxes_mask)
            # Маска, потерявшая цель, не отличима от работающей — ни на
            # картинке, ни по вердикту. Поэтому она говорит о себе сама, и
            # говорит в заметках сравнения: их человек видит в разборе ровно
            # тогда, когда смотрит на этот снимок.
            notes.extend(resolved.notes)

        # Thresholds: config → project (already folded into `self.cfg`) →
        # **snapshot** → call. The last two are what this one comparison adds;
        # `core.thresholds.patch_for` is where that fold is written down, and
        # why each layer is where it is.
        layered = _thresholds.patch_for(baseline.meta, diff_overrides)
        diff_cfg = self.cfg.diff.merged(**layered)
        ai_hooks = self._make_ai(baseline.dom, dom)

        res = compare(baseline.image, rgb, cfg=diff_cfg, name=name,
                      ignore_mask=ignore, ai_hooks=ai_hooks)

        # ---------- упало? снимем ещё кадр и посмотрим, что не повторяется ----
        if res.failed and recapture is not None and self.cfg.capture.retry_on_fail:
            extra, res = self._retry(
                name, rgb, baseline, ignore, diff_cfg, ai_hooks, res, recapture,
                ready=ready, not_ready=not_ready)
            unstable = _noise.merge_masks(unstable, extra, sticky=True) \
                if extra is not None else unstable

        res.notes = notes + res.notes
        if res.failed:
            res.notes.extend(_old_way(baseline.meta, meta))

        # The noise profile accumulates even on a successful run.
        if unstable.any() and self.cfg.capture.stability_sticky:
            _noise.save_mask(
                self.store.dir_for(name) / "stability.png",
                _noise.merge_masks(baseline.stability_mask, unstable, sticky=True),
            )

        if render:
            out = self.run_dir / slug(name)
            out.mkdir(parents=True, exist_ok=True)
            # Artifacts (heatmap, onion, blink, close-ups) are
            # decorative: the verdict is already computed. Their rendering must NOT bring
            # down the check. Previously, if images of different sizes produced a
            # numpy broadcast error in the renderer, the whole run crashed, and the verdict
            # itself (for example «size changed») was lost. Now the render is wrapped —
            # like the AI layer, it may fail, but it does not kill the check.
            try:
                res.artifacts.update(render_all(res, out, cfg=self.cfg.render))
            except Exception as e:
                res.notes.append(
                    f"Artifacts were not rendered ({type(e).__name__}: {e}). "
                    "The verdict is computed, images for this snapshot are unavailable."
                )
            strip_internal(res)
            (out / "result.json").write_text(res.to_json(), encoding="utf-8")
        else:
            strip_internal(res)
        return res

    # ------------------------------------------------------------------ #
    def _retry(self, name, rgb, baseline, ignore, diff_cfg, ai_hooks, first,
               recapture, *, ready=None, not_ready=""):
        """More frames of the same page, and the verdict from the last of them.

        The logic is `core.retry.second_look`, shared with the library mode;
        what is here is only how this service compares — the stored baseline,
        the masks it already had, the thresholds of this snapshot — and how it
        gets the later frames: `recapture` gives one, so it is asked until two
        in a row are identical, `RETRY_FRAMES` at most, within the capture's
        `stable_timeout_ms`.
        """
        import time

        from .core.retry import Later, second_look

        def later():
            frames, previous = [], rgb
            started = time.monotonic()
            limit = max(self.cfg.capture.stable_timeout_ms, 1) / 1000
            while True:
                frame = recapture()
                if frame is None:
                    break
                frames.append(frame)
                if frame.shape == previous.shape and np.array_equal(frame, previous):
                    return Later(frames, settled=True)
                previous = frame
                if len(frames) >= RETRY_FRAMES or time.monotonic() - started >= limit:
                    break
            return Later(frames, settled=False) if frames else None

        def recompare(frame, live):
            wider = (_noise.merge_masks(ignore, live, sticky=True)
                     if live is not None else ignore)
            return compare(baseline.image, frame, cfg=diff_cfg, name=name,
                           ignore_mask=wider, ai_hooks=ai_hooks)

        look = second_look(first, rgb, later, recompare, ready=ready,
                           not_ready=not_ready)
        return look.unstable, look.result

    # ------------------------------------------------------------------ #
    def save_baseline(self, name: str, rgb: np.ndarray, **kw) -> None:
        self.store.save(BaselineRecord(name=name, image=rgb, **kw))

    def _make_ai(self, dom_expected, dom_actual):
        """Attribution and whatever plugins are installed; None if neither."""
        from .plugins.loader import active_registry

        registry = active_registry()
        if not self.cfg.ai.attribution_enabled and registry.is_empty():
            return None
        from .ai import AIPipeline

        return AIPipeline(self.cfg.ai, dom_expected=dom_expected,
                          dom_actual=dom_actual, registry=registry,
                          plugins=self.cfg.plugins)
