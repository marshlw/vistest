# VisTest - visual regression testing for Python and Playwright.
# Copyright 2026 Kirill Kulagin
# SPDX-License-Identifier: Apache-2.0
#
# This file is part of VisTest, licensed under the Apache License, Version 2.0.
# You may not use it except in compliance with the License. See LICENSE for
# the terms and NOTICE for attribution and the trademark.


"""The generated corpus the region gate is trained on (o1c: split from
server/tests/test_gate.py — the corpus is tests/corpus.py, and needs no server)."""

from __future__ import annotations

from . import corpus


def test_families_group_the_same_phenomenon_across_layouts():
    """Прятать при проверке надо явление, а не случай.

    Иначе у спрятанной «изменившейся цены» останутся пять близнецов с соседних
    вёрсток, и проверка измерит память, а не обобщение.
    """
    generated = corpus.generate(layout_count=3)
    families = {c.family for c in generated}

    assert "jpeg q=80" in families
    assert len({c.name for c in generated if c.family == "jpeg q=80"}) == 3


def test_generated_corpus_has_no_empty_pairs():
    """Случай, где ничего не изменилось, не может быть регрессом."""
    for case in corpus.generate(layout_count=3):
        if case.group != "SIGNAL":
            continue
        same = (case.expected.shape == case.actual.shape
                and not (case.expected != case.actual).any())
        assert not same, f"{case.name}: помечен как регресс, но картинки совпадают"
