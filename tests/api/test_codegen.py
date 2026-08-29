# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""«Умный» codegen: пересборка не затирает ручные правки теста."""

from __future__ import annotations

from vistest.record.codegen import write_test_files


class _Rec:
    def __init__(self, meta):
        self.meta = meta


class FakeStore:
    """Мини-хранилище эталонов: имя → meta.json."""
    def __init__(self, metas):
        self.metas = metas

    def list_names(self):
        return list(self.metas)

    def load(self, name):
        return _Rec(self.metas[name])


def _store():
    return FakeStore({
        "login": {"url": "https://app.local/login", "viewport": "1440x900"},
    })


def test_first_generation_writes(tmp_path):
    written = write_test_files(_store(), tmp_path, platform="linux-chromium")
    f = tmp_path / "test_visual_recorded.py"
    assert f.exists() and written
    assert (tmp_path / ".vistest_codegen.json").exists()
    assert "def " in f.read_text("utf-8")


def test_manual_edits_preserved_on_regen(tmp_path):
    store = _store()
    write_test_files(store, tmp_path, platform="linux-chromium")
    f = tmp_path / "test_visual_recorded.py"

    edited = f.read_text("utf-8") + "\n\ndef test_моя_правка(page, visual):\n    pass\n"
    f.write_text(edited, encoding="utf-8")

    skipped = []
    write_test_files(store, tmp_path, platform="linux-chromium",
                     on_skip=lambda p: skipped.append(p))

    # правка на месте, файл не перезаписан, о пропуске сообщено
    assert "test_моя_правка" in f.read_text("utf-8")
    assert skipped and str(f) in skipped


def test_untouched_regen_backs_up_not_skips(tmp_path):
    store = _store()
    write_test_files(store, tmp_path, platform="linux-chromium")
    skipped = []
    # без ручных правок пересборка проходит штатно, без «пропущено»
    write_test_files(store, tmp_path, platform="linux-chromium",
                     on_skip=lambda p: skipped.append(p))
    assert not skipped
