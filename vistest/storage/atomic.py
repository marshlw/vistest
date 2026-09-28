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
the same directory, then `os.replace`. It needs no lock, and it needs no shared
index file that would have to be locked in turn. A reader either sees the
previous file or the new one.

On POSIX that is the whole story: `rename(2)` swaps the directory entry, and
nobody holding the old file open can stop it. Windows is different. There
`os.replace` is `MoveFileEx`, and it fails with `ERROR_ACCESS_DENIED`
(`PermissionError`, WinError 5) while the target is open in another process
without delete sharing, or while another writer is replacing the same target
at that very moment. Two tests using one snapshot name under `pytest -n` do
exactly that to `.vistest/actual/<name>.png`, and an antivirus scanner or the
search indexer opening a PNG that has just appeared does the same with nobody
else involved. The refusal lasts milliseconds. So on Windows, and only there,
a `PermissionError` from the replace is retried with a short, growing pause
for up to `REPLACE_RETRY_SECONDS`, and after that the error is raised as it
came. The replace itself is still one call: a reader never sees half a file,
retry or not.

On POSIX a `PermissionError` means the permissions are wrong, and waiting will
not change them; it is raised on the first attempt.

Three details that are load-bearing:

* **The temporary name starts with a dot and carries both the pid and a
  random suffix.** The pid alone is not enough: one process can write the same
  path twice at once (a retry after a failure does exactly that), and two
  temporaries with the same name corrupt each other. The leading dot keeps the
  temporary out of `*.png` listings and out of `git status` while it exists.
* **No `fsync`.** `os.replace` is atomic with respect to other processes
  either way; `fsync` only buys durability across a power cut. These are test
  artifacts — paying a flush per snapshot in every run to protect against a
  power cut in the middle of one is the wrong trade.
* **The temporary is removed whatever happens**, including when the retries
  run out: it is our file, nobody else will ever clean it up.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from uuid import uuid4

__all__ = ["write_bytes", "write_json", "write_text"]


#: Whether a refused replace is worth another attempt. A module variable and
#: not a check inside the function, so a test can switch the Windows behaviour
#: on (or off) on any platform.
RETRY_REPLACE = sys.platform == "win32"

#: How long, in total, a refused replace is retried before the error is raised.
REPLACE_RETRY_SECONDS = 2.0

#: The first pause between attempts, and the most any one pause may grow to.
_FIRST_PAUSE = 0.005
_LONGEST_PAUSE = 0.2


def _temporary(path: Path) -> Path:
    return path.with_name(f".{path.name}.{os.getpid()}.{uuid4().hex[:8]}.tmp")


def _replace(tmp: Path, path: Path) -> None:
    """`os.replace`, patient on Windows with a target somebody else is holding.

    Only `PermissionError` is retried — that is the shape `ERROR_ACCESS_DENIED`
    and `ERROR_SHARING_VIOLATION` take. Anything else (a missing directory, a
    full disk) is not going to go away in two seconds and is raised at once.
    """
    if not RETRY_REPLACE:
        os.replace(tmp, path)
        return
    deadline = time.monotonic() + REPLACE_RETRY_SECONDS
    pause = _FIRST_PAUSE
    while True:
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            left = deadline - time.monotonic()
            if left <= 0:
                raise
            time.sleep(min(pause, left))
            pause = min(pause * 2, _LONGEST_PAUSE)


def write_bytes(path: str | Path, data: bytes, *, mkdir: bool = True) -> Path:
    """Put `data` at `path`, atomically for anyone reading it."""
    path = Path(path)
    if mkdir:
        path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _temporary(path)
    try:
        tmp.write_bytes(data)
        _replace(tmp, path)
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
