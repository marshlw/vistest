# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The layer after the cascade, as one hook for `compare(ai_hooks=...)`.

Order matters:

  1. attribution — cheap, offline, part of the engine: names the elements;
  2. scorer      — the active `RegionScorer` plugin, if any: estimates how
                   likely each region is real; the core applies the estimate
                   under `plugins.fail_on` (see `vistest.plugins.runtime`);
  3. annotators  — the annotator chain, if any, plus the one registered from
                   code with `set_annotator`: words only, never the verdict.

Every step may be missing, and none of them is allowed to take a run down.
With no plugin installed this is attribution and nothing else — the
deterministic path.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from ..config import AIConfig
from ..models import CompareResult, DiffRegion
from ..plugins import runtime as _runtime
from ..plugins.api import RegionContext
from ..plugins.registry import PluginRegistry, Registration
from .attribution import attribute, diagnose, dom_changes
from .hooks import RegionAnnotator, as_registration

log = logging.getLogger("vistest.ai")


def _read_only(image: Any) -> Any:
    if not isinstance(image, np.ndarray):
        return image
    view = image.view()
    view.flags.writeable = False
    return view


class AIPipeline:
    """Attribution, then plugins.

    `registry` defaults to the process registry with installed plugins loaded.
    `plugins` is the `plugins:` section (a `PluginsConfig`); its `fail_on`
    decides what a score does to the outcome. `annotator` is an extra
    annotator for this pipeline only, in the `set_annotator` contract.
    """

    def __init__(
        self,
        cfg: AIConfig | None = None,
        *,
        dom_expected: dict | None = None,
        dom_actual: dict | None = None,
        annotator: RegionAnnotator | None = None,
        registry: PluginRegistry | None = None,
        plugins: Any = None,
    ):
        self.cfg = cfg or AIConfig()
        self.dom_expected = dom_expected
        self.dom_actual = dom_actual
        if registry is None:
            from ..plugins.loader import active_registry

            registry = active_registry()
        self.registry = registry
        self.policy = _runtime.ScoringPolicy.of(plugins)
        self._options = dict(getattr(plugins, "options", None) or {})
        self._extra: Registration | None = (
            as_registration(annotator) if annotator is not None else None)

    # ------------------------------------------------------------------ #
    def context(self, regions: list[DiffRegion], expected: Any, actual: Any,
                result: CompareResult) -> RegionContext:
        page_h = max(1, max((r.y + r.h) for r in regions)) if regions else 1
        return RegionContext(
            name=result.name,
            expected=_read_only(expected),
            actual=_read_only(actual),
            total_pixels=int(result.total_pixels or page_h),
            page_height=page_h,
            aligned=bool(result.aligned),
            size_changed=bool(result.size_changed),
            region_count=len(regions),
            dom_expected=self.dom_expected,
            dom_actual=self.dom_actual,
            options=self._options,
            settings=self.cfg,
        )

    def refine(
        self,
        regions: list[DiffRegion],
        expected: np.ndarray,
        actual: np.ndarray,
        result: CompareResult,
    ) -> list[DiffRegion]:
        if self.cfg.attribution_enabled:
            try:
                regions = attribute(
                    regions, self.dom_expected, self.dom_actual,
                    min_coverage=self.cfg.attribution_min_iou,
                )
                self._describe_dom(regions, result)
            except Exception as e:
                log.warning("attribution: %s", e)

        if not regions:
            return regions
        ctx = self.context(regions, expected, actual, result)

        outcome = _runtime.score(self.registry.scorer(), regions, ctx, self.policy)
        if outcome.error:
            result.notes.append(
                f"Region scorer skipped ({outcome.error}); the verdict is the "
                "engine's own.")
        result.notes.extend(outcome.notes)

        chain = self.registry.annotators()
        if self._extra is not None:
            chain = [*chain, self._extra]
        if chain:
            _runtime.annotate(chain, regions, ctx, result=result)
        return regions

    # ------------------------------------------------------------------ #
    def _describe_dom(self, regions, result) -> None:
        changes = dom_changes(self.dom_expected, self.dom_actual)
        if changes.get("counts"):
            c = changes["counts"]
            #  Into `maps`, like every other non-string the cascade produces.
            #  Nothing reads this yet — the counts reach a person through the
            #  note below — but a dict parked in a field typed
            #  `dict[str, str]` is the same trap that took a run down once
            #  already.
            result.maps["dom_changes"] = changes
            if any(c.values()):
                result.notes.append(
                    f"DOM: +{c['added']} elements, -{c['removed']}, "
                    f"moved {c['moved']}"
                )
        # A diagnosis beats a statement: "the region changed" a person sees
        # for themselves; "this is a date, here is what to do with it" they
        # do not.
        result.notes.extend(diagnose(regions))
