# VisTest - visual regression testing for Python and Playwright.
# Copyright 2026 Kirill Kulagin
# SPDX-License-Identifier: Apache-2.0
#
# This file is part of VisTest, licensed under the Apache License, Version 2.0.
# You may not use it except in compliance with the License. See LICENSE for
# the terms and NOTICE for attribution and the trademark.


"""The baselines archive, the library's half (o1c: split from
server/tests/test_transfer.py, where the merges run against the server's store)."""

from __future__ import annotations

import json

import pytest

from vistest import transfer


def test_an_archive_cannot_write_outside_the_baselines(tmp_path):
    """Архив мог приехать по почте, а tar умеет пути вида `../..`."""
    import tarfile

    evil = tmp_path / "evil.tar.gz"
    payload = tmp_path / "payload"
    payload.write_text("owned", encoding="utf-8")
    manifest = tmp_path / transfer.MANIFEST
    manifest.write_text(json.dumps({"kind": "baselines", "snapshots": []}),
                        encoding="utf-8")
    with tarfile.open(evil, "w:gz") as tar:
        tar.add(payload, arcname="../escaped.txt")
        tar.add(manifest, arcname=transfer.MANIFEST)

    with pytest.raises(ValueError, match="Unsafe path"):
        transfer.import_(evil, baselines_root=tmp_path / "dst")
    assert not (tmp_path / "escaped.txt").exists()
