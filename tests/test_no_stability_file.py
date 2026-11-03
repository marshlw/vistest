# VisTest - visual regression testing for Python and Playwright.
# Copyright 2026 Kirill Kulagin
# SPDX-License-Identifier: Apache-2.0
#
# This file is part of VisTest, licensed under the Apache License, Version 2.0.
# You may not use it except in compliance with the License. See LICENSE for
# the terms and NOTICE for attribution and the trademark.

"""Step l1: the library keeps no accumulated instability mask.

`FileBaselineStore` used to OR every approved check's instability mask into
`stability.png` next to the baseline and hand it back on every load, so a
pixel that shivered once stayed masked for good. The server stopped doing
that (s1b4); this holds the library to the same rule: a check's mask is that
check's, and it is stored nowhere. A `stability.png` an older version left
behind is neither read nor deleted. `BaselineRecord.stability_mask` is kept
for the server on the pin and ignored. `capture.stability_sticky`, the
setting of that accumulation, warns once and loads.
"""

from __future__ import annotations

import logging
from dataclasses import fields, replace

import numpy as np

from vistest.config import VisTestConfig
from vistest.core import noise as _noise
from vistest.library import context as _context
from vistest.storage import BaselineRecord, FileBaselineStore


def _record(name: str, mask: np.ndarray) -> BaselineRecord:
    """A record carrying `mask` if the record can carry one at all."""
    kw = {}
    if "stability_mask" in {f.name for f in fields(BaselineRecord)}:
        kw["stability_mask"] = mask
    return BaselineRecord(name=name, image=np.full((20, 30, 3), 200, np.uint8), **kw)


def _mask(x0: int, x1: int) -> np.ndarray:
    m = np.zeros((20, 30), dtype=bool)
    m[:, x0:x1] = True
    return m


def test_stability_mask_is_accepted_and_not_stored(tmp_path):
    """The field stays for the server on the pin: given, replaced, ignored."""
    rec = BaselineRecord(name="page", image=np.full((20, 30, 3), 200, np.uint8),
                         stability_mask=_mask(0, 5))
    assert replace(rec, stability_mask=None).stability_mask is None
    store = FileBaselineStore(tmp_path)
    store.save(rec)
    store.save(replace(rec, stability_mask=_mask(10, 15)))
    assert not (store.dir_for("page") / "stability.png").exists()
    assert store.load("page").stability_mask is None


def test_save_save_with_different_masks_leaves_no_stability_file(tmp_path):
    store = FileBaselineStore(tmp_path)
    store.save(_record("page", _mask(0, 5)))
    store.save(_record("page", _mask(10, 15)))
    d = store.dir_for("page")
    assert (d / "baseline.png").exists()
    assert not (d / "stability.png").exists()
    assert not any(p.name.startswith("stability") for p in d.rglob("*"))


def test_an_old_stability_file_is_not_applied_and_not_deleted(tmp_path):
    store = FileBaselineStore(tmp_path)
    store.save(_record("page", _mask(0, 0)))
    old = store.dir_for("page") / "stability.png"
    _noise.save_mask(old, np.ones((20, 30), dtype=bool))
    before = old.read_bytes()

    rec = store.load("page")
    assert getattr(rec, "stability_mask", None) is None
    # The mask a check is compared under: no zones drawn, so nothing.
    assert store.ignore_mask("page", (20, 30)) is None

    # A zone still counts, and only the zone.
    store.add_ignore_box("page", {"x": 0, "y": 0, "w": 3, "h": 3})
    m = store.ignore_mask("page", (20, 30))
    assert m is not None and 0 < int(m.sum()) < 20 * 30

    store.save(_record("page", _mask(0, 5)))
    assert old.read_bytes() == before


def test_stability_sticky_warns_once_and_loads(tmp_path, caplog):
    path = tmp_path / "vistest.yaml"
    path.write_text("capture:\n  stability_sticky: false\n  stability_shots: 4\n", "utf-8")
    with caplog.at_level(logging.WARNING):
        cfg = VisTestConfig.load(path)
    said = [r.getMessage() for r in caplog.records
            if "capture.stability_sticky" in r.getMessage()]
    assert len(said) == 1, said
    assert "capture.stability_sticky" in said[0] and "ignored" in said[0]
    assert cfg.capture.stability_shots == 4
    # The server warns on its own by `written`: the key stays there.
    assert "capture.stability_sticky" in cfg.written
    # Said once: the «library does not read» line does not repeat it.
    assert "capture.stability_sticky" not in _context.unread_text(cfg)
