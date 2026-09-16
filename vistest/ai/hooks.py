# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Registering an annotator from code.

The general mechanism is the plugin registry (`vistest.plugins`): installed
packages register annotators through an entry point. This module keeps the
older, code-only door open for a test suite that wants to describe regions
without packaging anything:

    class MyAnnotator:
        def annotate(self, regions, expected, actual, result) -> None:
            for r in regions:
                r.caption = "..."

    from vistest.ai import set_annotator
    set_annotator(MyAnnotator())

An object written against the plugin contract — ``annotate(region, ctx)``
returning `vistest.plugins.api.Annotation` objects — is accepted here as well.

Registration is from code, never from configuration. A class path as a string
in ``vistest.yaml`` or in the settings table would turn "change a setting"
into "run arbitrary code", and the settings table is writable over HTTP.

The rule is the same as for every annotator: it adds words and does not
change the outcome. It owns ``region.caption``, ``region.annotations`` and
``result.notes``; whatever it does to ``kind``, ``severity``, ``score`` or
``suppressed_by`` is rolled back before the result leaves the pipeline (see
`vistest.plugins.runtime.annotate`). A caption it sets is also recorded as an
annotation, so the report and the interface show it in one place.
"""

from __future__ import annotations

import inspect
from typing import Any, Protocol, runtime_checkable

import numpy as np

from ..models import CompareResult, DiffRegion, Verdict
from ..plugins.registry import PROGRAMMATIC, PluginRegistry, Registration

__all__ = ["RegionAnnotator", "as_registration", "get_annotator", "set_annotator"]


@runtime_checkable
class RegionAnnotator(Protocol):
    """The whole-comparison contract. Describes regions; no verdicts."""

    def annotate(
        self,
        regions: list[DiffRegion],
        expected: np.ndarray,
        actual: np.ndarray,
        result: CompareResult,
    ) -> None:
        ...


def _is_legacy(target: Any) -> bool:
    """Four positional parameters — the contract above. Two — the plugin one."""
    try:
        params = [p for p in inspect.signature(target.annotate).parameters.values()
                  if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    except (TypeError, ValueError):
        return True
    return len(params) != 2


class _WholeComparison:
    """Adapts the contract above to the annotator chain."""

    def __init__(self, target: Any, name: str):
        self.target = target
        self.name = name

    def annotate(self, region, ctx):          # the chain calls annotate_batch
        return ()

    def annotate_batch(self, regions, ctx, result) -> None:
        before = [r.caption for r in regions]
        if result is None:
            result = CompareResult(name=ctx.name, verdict=Verdict.PASS)
        self.target.annotate(regions, ctx.expected, ctx.actual, result)
        for region, old in zip(regions, before, strict=True):
            if region.caption and region.caption != old:
                region.annotations.append({
                    "text": str(region.caption)[:500], "kind": "caption",
                    "value": None, "source": self.name})


def as_registration(target: Any, *, name: str = PROGRAMMATIC) -> Registration:
    impl = _WholeComparison(target, name) if _is_legacy(target) else target
    return Registration(role="annotator", name=name, impl=impl, priority=0,
                        plugin=PROGRAMMATIC, order=0)


_annotator: Any = None
_registered: Any = None


def _registry() -> PluginRegistry:
    from ..plugins.loader import programmatic_registry

    return programmatic_registry()


def set_annotator(annotator: Any) -> None:
    """Register an annotator for this process. ``None`` removes it."""
    global _annotator, _registered
    registry = _registry()
    if _registered is not None:
        registry.remove(_registered)
    _annotator = _registered = None
    if annotator is None:
        return
    impl = (_WholeComparison(annotator, PROGRAMMATIC) if _is_legacy(annotator)
            else annotator)
    registry.add_annotator(impl, name=PROGRAMMATIC)
    _annotator, _registered = annotator, impl


def get_annotator() -> Any:
    return _annotator
