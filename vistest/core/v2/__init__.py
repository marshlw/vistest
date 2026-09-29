# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The second engine path: catch everything first, take out only proven noise.

`compare(..., engine="v2")`. It stands next to the v1 cascade, not instead of
it, so that both can be measured in one table on the same pairs.

Why a second path and not a repair of the first. v1 filters before it
explains: consensus of colour and structure, a morphological opening, a
per-pixel tolerance that grows with local contrast. On pages drawn by a
browser those filters throw a signal away before it has become a region
(`scripts/diagnose_browser.py` measures where): a new ink colour on text is
"within tolerance", an underline is opened away, a recoloured button never
passes consensus. A region that was never formed cannot be explained, and
cannot be seen either.

v2 turns the order round:

1. **Base** (`base.py`). A pixel is a candidate when ΔE00 between the two
   frames is above the threshold of discernibility. Candidates are grouped
   by proximity — dilated by a couple of pixels, then connected components —
   with no opening. Every candidate pixel belongs to exactly one region:
   nothing is left unassigned, and there is no threshold in percent of the
   frame; the smallest region is a number of pixels.
2. **Explanations** — each a separate, named test that a region must pass in
   full to be called noise, and each writing its measurements into
   `suppressed_by`. The base itself explains nothing. So far two:
   `pageshift.py`, the page moved by a fraction of a pixel — proven on the
   page's box edges and on most of its changed pixels before any region is
   looked at, whatever the renderer; and `rerender.py`, text
   re-rasterisation, looked for only when the renderer's canary proves the
   renderer changed (`core/renderer.py`) — the ink, the shape to a pixel,
   the paper, and not a block that moved; the page's share of changed text
   is printed with them.
3. **The AI layer**, when there is one, sees what no rule explained. A region
   it does not hand back is suppressed under its name, never dropped.

A region that no explanation takes out fails the comparison. There is no
severity threshold between "found" and "failed": severity orders the report,
it does not decide the verdict.
"""

from .engine import compare
from .settings import V2Config

__all__ = ["compare", "V2Config"]
