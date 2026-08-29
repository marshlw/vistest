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
from .core.comparator import compare, strip_internal
from .models import CompareResult, Verdict
from .render.artifacts import render_all
from .storage import BaselineRecord, FileBaselineStore


def slug(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name).strip("_")


# Пороги, которые снимок может держать в собственном паспорте. Список короткий
# намеренно: это те же две ручки, что настраиваются глобально и на проект, —
# третий уровень должен управлять тем же, чем первые два, иначе он не третий
# уровень, а отдельная система с теми же словами.
SNAPSHOT_THRESHOLDS = ("fail_severity", "max_changed_area_pct")


def _snapshot_thresholds(meta: dict | None) -> dict:
    """Пороги из паспорта эталона. Мусор молча игнорируется.

    Уронить сравнение из-за строки в поле порога значило бы, что одна кривая
    правка паспорта останавливает весь прогон, а не один снимок.
    """
    raw = (meta or {}).get("thresholds") or {}
    if not isinstance(raw, dict):
        return {}
    out = {}
    for name in SNAPSHOT_THRESHOLDS:
        value = raw.get(name)
        if value is None:
            continue
        try:
            out[name] = float(value)
        except (TypeError, ValueError):
            continue
    return out


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
    ) -> CompareResult:
        """Compare a snapshot against a baseline.

        frames  — additional frames of the same page; discrepancies between them
                  turn into an instability mask (auto-suppression of motion).
        unstable— a ready-made mask, if the client computed it itself.
        recapture — как снять ещё один кадр той же страницы. Вызывается ТОЛЬКО
                  при падении: движок решает, что делать, а вызывающий умеет
                  снимать — сюда одинаково подходят и наш раннер, и адаптер
                  чужих тестов, и съёмка по адресу.
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
            ignore = _noise.merge_masks(
                ignore, _noise.mask_from_boxes(rgb.shape[:2], baseline.ignore_boxes)
            )

        # Пороги: конфиг → проект (уже учтён в `self.cfg`) → **снимок** → вызов.
        #
        # Третий уровень появился потому, что двух не хватало: один шумный
        # дашборд заставлял ослаблять порог для всего набора, то есть чинить
        # один снимок ценой чувствительности всех остальных. Порог снимка живёт
        # в его паспорте, а не в базе, и это осознанно — он обязан ехать вместе
        # с эталоном: в наложение ветки, в спутник чужого проекта, в архив.
        #
        # Вызов сильнее паспорта: `assert_screenshot(fail_severity=...)` написан
        # рядом с конкретной проверкой и о ней знает больше, чем настройка,
        # выставленная когда-то в интерфейсе.
        #
        # Слои складываются в один словарь, а не раскрываются двумя `**`: у них
        # общие ключи, и Python на таком вызове падает. Заодно это единственное
        # место, где видно правило — `None` в вызове ничего не стирает, он
        # означает «не задано», и тогда действует паспорт.
        layered = dict(_snapshot_thresholds(baseline.meta))
        layered.update({k: v for k, v in (diff_overrides or {}).items()
                        if v is not None})
        diff_cfg = self.cfg.diff.merged(**layered)
        ai_hooks = self._make_ai(baseline.dom, dom)

        res = compare(baseline.image, rgb, cfg=diff_cfg, name=name,
                      ignore_mask=ignore, ai_hooks=ai_hooks)

        # ---------- упало? снимем ещё кадр и посмотрим, что не повторяется ----
        if res.failed and recapture is not None and self.cfg.capture.retry_on_fail:
            extra, ignore, res = self._retry(
                name, rgb, baseline, ignore, diff_cfg, ai_hooks, res, recapture)
            unstable = _noise.merge_masks(unstable, extra, sticky=True) \
                if extra is not None else unstable

        res.notes = notes + res.notes

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
               recapture):
        """Второй кадр той же страницы — и пересчёт вердикта по нему.

        Сравнивается при этом **первый** кадр, а не второй. Разница
        принципиальная: взять второй значило бы выбирать снимок поудачнее, пока
        не позеленеет. Второй кадр здесь — не замена картинки, а **свидетель**:
        он говорит, какие пиксели на этой странице не держатся на месте, и
        ровно они уходят в маску.

        Отсюда главное свойство: подавляется не вердикт, а конкретные области.
        Дрожал спиннер — уйдёт в маску только спиннер, а съехавшая шапка
        останется красной, потому что между двумя кадрами она никуда не дрожала.
        Устойчивый регресс спрятать таким образом нельзя: устойчивый — это
        ровно тот, который воспроизводится.

        А вот **мерцающий** — можно, и об этом стоит сказать прямо, а не
        умолчать. Если поломка проявляется через раз, второй кадр может застать
        страницу целой, область попадёт в маску, и вердикт станет зелёным. Двух
        кадров, чтобы отличить «мерцающий шум» от «мерцающей поломки», не
        хватает никому — ни человеку, ни движку.

        Поэтому зелёный здесь никогда не бывает молчаливым. «Упало на первом
        кадре, прошло на втором» — это не «всё хорошо», это «страница здесь
        нестабильна», и так и написано в заметке, с долей дрожащей площади и
        прямым советом чинить в источнике. Для мерцающей поломки это ровно тот
        текст, который нужен: чинить надо недетерминированный рендер, а не
        картинку. А снимок, который лечится пересъёмкой из раза в раз, копит
        маску и всплывает в отчёте о шуме.
        """
        try:
            second = recapture()
        except Exception as e:
            first.notes.append(
                f"Could not take a second capture ({type(e).__name__}: {e}) — "
                "the verdict is from a single frame.")
            return None, ignore, first
        if second is None:
            return None, ignore, first

        extra = _noise.stability_mask([rgb, second])
        if not extra.any():
            # Ни один пиксель не дрогнул между двумя кадрами. Это сильное
            # утверждение в пользу падения, и сказать его стоит вслух: дальше
            # разбирать будут уже не «а вдруг моргнуло».
            first.notes.append(
                "Confirmed on a second capture: nothing on this page moved "
                "between the two frames.")
            return extra, ignore, first

        wider = _noise.merge_masks(ignore, extra, sticky=True)
        again = compare(baseline.image, rgb, cfg=diff_cfg, name=name,
                        ignore_mask=wider, ai_hooks=ai_hooks)
        share = float(extra.mean()) * 100

        if again.failed:
            again.notes.append(
                f"A second capture was taken: {share:.1f}% of the page is "
                "unstable and was suppressed, but the difference remains.")
            return extra, wider, again

        again.notes.append(
            f"Failed on the first capture and passed on the second: "
            f"{share:.1f}% of the page does not hold still. The difference did "
            "not reproduce, so it is noise — but this snapshot is unstable, and "
            "that is worth fixing at the source.")
        return extra, wider, again

    # ------------------------------------------------------------------ #
    def save_baseline(self, name: str, rgb: np.ndarray, **kw) -> None:
        self.store.save(BaselineRecord(name=name, image=rgb, **kw))

    def _make_ai(self, dom_expected, dom_actual):
        ai = self.cfg.ai
        if not (ai.attribution_enabled or ai.perceptual_enabled or ai.captioner_enabled):
            return None
        from .ai import AIPipeline

        return AIPipeline(ai, dom_expected=dom_expected, dom_actual=dom_actual)
