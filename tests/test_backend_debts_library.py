# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.


"""Storage names: two different snapshots cannot be one (o1c: split from
server/tests/test_backend_debts.py, which keeps the server's debts)."""

from __future__ import annotations


def test_deep_names_with_a_common_tail_do_not_collide(tmp_path):
    """`"/".join(parts[-4:])` молча отбрасывал НАЧАЛО имени.

    Значит и различаться имена должны именно в начале — там, где различие
    выбрасывалось. Два биллинга разных продуктов ложились в один каталог и
    перезаписывали эталон друг друга; человек видел дифф между чужими
    страницами и не понимал, откуда он взялся.
    """
    from vistest.storage.fs import _safe

    billing = _safe("billing/eu/checkout/payment/form.png")
    shop = _safe("shop/eu/checkout/payment/form.png")
    assert billing != shop, "разные снимки не могут делить один каталог эталонов"


def test_ordinary_names_are_untouched(tmp_path):
    """Иначе правка утащила бы за собой все существующие эталоны."""
    from vistest.storage.fs import _safe

    assert _safe("login.png") == "login"
    assert _safe("shop.example/login.png") == "shop.example/login"
    assert _safe("a/b/c/d.png") == "a/b/c/d"


def test_the_path_still_cannot_escape_and_stays_bounded():
    from vistest.storage.fs import MAX_DEPTH, _safe

    assert ".." not in _safe("../../../etc/passwd.png")
    deep = _safe("/".join(f"s{i}" for i in range(20)) + ".png")
    assert len(deep.split("/")) == MAX_DEPTH
