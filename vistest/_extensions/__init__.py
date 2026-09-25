# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Temporary: the extension modules that still live in this repository,
registered the way any installed plugin is.

This package exists to prove the wiring before the repositories are split.
It is reached only through the ``vistest.plugins`` entry point declared in
``pyproject.toml`` — nothing in the core imports it or the modules below by
name, and `tests/test_plugin_boundary.py` fails if that changes. When the
split happens, this package and the modules it registers leave together, and
the core does not notice.

    vistest.ai.gate, vistest.ai.perceptual  -> RegionScorer, RegionAnnotator
    vistest.api.directory                   -> AuthProvider
    vistest.transfer.ArchiveSyncBackend     -> BaselineSyncBackend

`vistest.transfer` itself is core: `vistest baselines export|import` call it
directly and work with every plugin switched off. What is registered here is
the server's side of it — the HTTP routes that move a set between running
installations.
"""

from __future__ import annotations

API_VERSION = 1


class CombinedScorer:
    """The gate, and the perceptual filter when it is switched on.

    Both only ever lower a score: a region is as likely real as the more
    sceptical of the two says. A model with no opinion on a region (too small
    to crop, switched off) defers to the other. Neither with an opinion —
    abstain.
    """

    name = "gate"

    def __init__(self, gate, perceptual):
        self.gate = gate
        self.perceptual = perceptual

    def score(self, regions, ctx):
        from ..ai.perceptual import score_of_distance

        gated = self.gate.score(regions, ctx)
        distances = self.perceptual.distances_for(regions, ctx)
        if distances is None:
            return gated
        tol = float(getattr(ctx.settings, "perceptual_tolerance", 0.06))
        out = []
        for i, d in enumerate(distances):
            opinions = [] if gated is None else [float(gated[i])]
            if d is not None:
                opinions.append(score_of_distance(d, tol))
            out.append(min(opinions) if opinions else 1.0)
        if gated is None and all(d is None for d in distances):
            return None
        return out


def register(registry) -> None:
    from ..ai.gate import GateScorer
    from ..ai.perceptual import PerceptualScorer
    from ..api.directory import DirectoryAuthProvider
    from ..transfer import ArchiveSyncBackend

    gate = GateScorer()
    perceptual = PerceptualScorer()
    registry.set_scorer(CombinedScorer(gate, perceptual), name="gate")
    registry.add_annotator(gate, name="gate")
    registry.add_annotator(perceptual, name="perceptual")
    registry.set_auth_provider(DirectoryAuthProvider(registry.host), name="ldap")
    registry.set_sync_backend(ArchiveSyncBackend(), name="archive")
