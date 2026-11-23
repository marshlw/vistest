# VisTest - visual regression testing for Python and Playwright.
# Copyright 2026 Kirill Kulagin
# SPDX-License-Identifier: Apache-2.0
#
# This file is part of VisTest, licensed under the Apache License, Version 2.0.
# You may not use it except in compliance with the License. See LICENSE for
# the terms and NOTICE for attribution and the trademark.

"""Publish one selected pytest baseline after all workers have reported."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PureWindowsPath
from uuid import UUID

from ..storage import atomic
from ..storage.base import SnapshotKey
from ..storage.file import FileStore
from .fingerprint import RENDERERS_DIR, of_baseline


def target(path: str) -> str:
    if (not path.endswith(".png") or PureWindowsPath(path).drive or "\\" in path
            or any(part in ("", ".", "..") for part in path.split("/"))):
        raise ValueError("expected a baseline-relative PNG path, such as chromium/home.png")
    return path


def baseline_digest(png: bytes | None) -> str | None:
    return hashlib.sha256(png).hexdigest() if png is not None else None


def publish(directory: Path, store: FileStore, entry: dict) -> None:
    candidate = entry.get("update_candidate")
    if not candidate:
        return
    key = SnapshotKey(name=entry["name"], platform=entry["platform"])
    candidate_dir = directory / "updates" / UUID(candidate).hex
    staged = FileStore(candidate_dir / "baselines")
    data = json.loads((candidate_dir / "update.json").read_text("utf-8"))
    png = staged.get(key)
    meta = staged.meta(key)
    if png is None or meta is None or meta.sha256 != baseline_digest(png):
        raise ValueError("the staged screenshot is missing or incomplete")

    # Serialize targeted acceptance in this checkout. The second session
    # rechecks the baseline it compared, rather than replacing the first.
    root = store.path_of(key).parent
    root.mkdir(parents=True, exist_ok=True)
    lock = store.path_of(key).with_suffix(".png.vistest-lock")
    try:
        with lock.open("xb"):
            pass
    except FileExistsError as exc:
        raise ValueError(f"another acceptance may be using {lock}") from exc
    try:
        if baseline_digest(store.get(key)) != data["before"]:
            raise ValueError("the baseline changed during the run; review it and retry")
        if meta.renderer:
            canary, why = of_baseline(staged, meta)
            if canary is not None:
                path = store.root / RENDERERS_DIR / f"{meta.renderer['sha256']}.png"
                if not path.is_file():
                    atomic.write_bytes(path, canary)
            elif "renderer" in data["passport_fields"]:
                raise ValueError(why)
        # Pass only the changed fields. FileStore retains the real version,
        # thresholds and ignore boxes from the passport in the project.
        current = store.meta(key)
        if data["passport_fields"]:
            current = (current or meta).with_(**{
                field: getattr(meta, field) for field in data["passport_fields"]
            })
        store.put(key, png, meta=current if data["passport_fields"] else None)
    finally:
        lock.unlink()
