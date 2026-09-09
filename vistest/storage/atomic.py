# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Writing a file so that nobody can ever read half of it.

Under `pytest -n` there is no single writer any more. Workers are separate
processes that share one working directory, and everything a run produces —
baselines under `--vistest-update`, the actual and diff pictures, the report's
per-test parts — is written by whichever worker happened to get that test.

The answer here is deliberately the cheap one: write to a temporary name in
the same directory, then `os.replace`. That is atomic on POSIX and on Windows,
it needs no lock, and it needs no shared index file that would have to be
locked in turn. A reader either sees the previous file or the new one.

Two details that are load-bearing:

* **The temporary name starts with a dot and carries both the pid and a
  random suffix.** The pid alone is not enough: one process can write the same
  path twice at once (a retry after a failure does exactly that), and two
  temporaries with the same name corrupt each other. The leading dot keeps the
  temporary out of `*.png` listings and out of `git status` while it exists.
* **No `fsync`.** `os.replace` is atomic with respect to other processes
  either way; `fsync` only buys durability across a power cut. These are test
  artifacts — paying a flush per snapshot in every run to protect against a
  power cut in the middle of one is the wrong trade.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

__all__ = ["write_bytes", "write_json", "write_text"]


def _temporary(path: Path) -> Path:
    return path.with_name(f".{path.name}.{os.getpid()}.{uuid4().hex[:8]}.tmp")


def write_bytes(path: str | Path, data: bytes, *, mkdir: bool = True) -> Path:
    """Put `data` at `path`, atomically for anyone reading it."""
    path = Path(path)
    if mkdir:
        path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _temporary(path)
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return path


def write_text(path: str | Path, text: str, *, mkdir: bool = True) -> Path:
    return write_bytes(path, text.encode("utf-8"), mkdir=mkdir)


def write_json(path: str | Path, payload, *, mkdir: bool = True) -> Path:
    """A JSON file with a trailing newline, so that git diffs stay quiet."""
    text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
    return write_text(path, text + "\n", mkdir=mkdir)
