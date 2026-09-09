# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Baselines as plain files in the user's own repository.

    tests/__vistest__/
      chromium-1440x900/
        login.png           the baseline
        login.json          its passport, optional
        checkout/summary.png

One picture, one file, named after the snapshot. That is the whole layout, and
it is chosen for a reason that has nothing to do with storage: in the library
mode a baseline is reviewed by people, in a pull request, next to the change
that moved it. `git diff` on `login.png` shows a picture; `git diff` on a
directory holding `baseline.png`, `meta.json` and a `history/` folder shows a
change of bookkeeping with a picture somewhere inside it.

**What this store deliberately does not do.** It keeps no history — git is the
history, and a second one beside it would be a second answer to «what did this
look like last week». It keeps no stability mask and no DOM snapshot: both are
produced by the capture layer, which the library mode does not require. And it
never writes anything but a PNG and its passport into the user's tree, so the
directory stays something a person can read.

**The passport is optional.** A PNG dropped in by hand is a valid baseline;
its version, size and checksum are read back out of the file. A passport that
is present and says something impossible fails loudly and by name — see
`SnapshotMeta.from_dict`.

The mapping from a name to a path is `storage.paths.safe_name`, the same one
the service's store uses, which is what lets `vistest baselines export` carry
a set from here to an installation and back.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from ..core import pngio
from . import atomic
from .base import SnapshotKey, SnapshotMeta

__all__ = ["DEFAULT_BASELINE_ROOT", "FileStore"]

#  Next to the tests, and named so that it sorts and reads as machinery rather
#  than as a test package: `tests/__vistest__/` cannot be imported by accident
#  and cannot be mistaken for fixtures.
DEFAULT_BASELINE_ROOT = "tests/__vistest__"


def _sha256(png: bytes) -> str:
    return hashlib.sha256(png).hexdigest()


class FileStore:
    """`SnapshotStore` over a directory of PNG files."""

    def __init__(self, root: str | Path = DEFAULT_BASELINE_ROOT) -> None:
        self.root = Path(root)

    def __repr__(self) -> str:  # pragma: no cover - diagnostics
        return f"FileStore({str(self.root)!r})"

    # ------------------------------------------------------------------ #
    #  Paths
    # ------------------------------------------------------------------ #
    def path_of(self, key: SnapshotKey) -> Path:
        return self.root / Path(str(key.relpath))

    def meta_path_of(self, key: SnapshotKey) -> Path:
        return self.path_of(key).with_suffix(".json")

    # ------------------------------------------------------------------ #
    #  Reading
    # ------------------------------------------------------------------ #
    def get(self, key: SnapshotKey) -> bytes | None:
        path = self.path_of(key)
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as e:
            #  Not a missing baseline: a directory in the way, a permission
            #  problem, a half-synced network share. Answering `None` here
            #  would turn that into «no baseline yet», and the next
            #  `--vistest-update` would write over whatever is actually there.
            raise OSError(f"cannot read the baseline {path}: {e}") from None

    def meta(self, key: SnapshotKey) -> SnapshotMeta | None:
        png = self.get(key)
        if png is None:
            return None

        path = self.meta_path_of(key)
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return self._derive_meta(key, png)
        except OSError as e:
            raise OSError(f"cannot read the passport {path}: {e}") from None
        return SnapshotMeta.from_json(raw, source=str(path))

    def _derive_meta(self, key: SnapshotKey, png: bytes) -> SnapshotMeta:
        """A passport for a baseline that has none — read out of the file itself."""
        width, height = pngio.dimensions(png, source=self.path_of(key))
        try:
            stamp = datetime.fromtimestamp(
                self.path_of(key).stat().st_mtime, timezone.utc).isoformat()
        except OSError:  # pragma: no cover - the file was just read
            stamp = ""
        return SnapshotMeta(version=1, width=width, height=height,
                            sha256=_sha256(png), updated_at=stamp)

    def history(self, key: SnapshotKey) -> list[SnapshotMeta]:
        """Always empty: in this mode the history of a baseline is its git log.

        Not a gap and not a degraded answer. A baseline here is a file in the
        user's repository — every previous version of it is already recorded,
        with an author, a date and the change that caused it, by a tool far
        better at that than we would be. Keeping a second copy under
        `history/` would put a second, worse answer next to it.
        """
        return []

    def list(self, prefix: str = "") -> list[SnapshotKey]:
        if not self.root.exists():
            return []
        out: list[SnapshotKey] = []
        for path in self.root.rglob("*.png"):
            if path.name.startswith("."):
                #  A half-written file from `atomic.write_bytes`. It exists for
                #  microseconds, but a listing that catches one would report a
                #  baseline that is about to have a different name.
                continue
            rel = path.relative_to(self.root).as_posix()
            key = SnapshotKey.parse(rel)
            if prefix and not key.as_str().startswith(prefix):
                continue
            out.append(key)
        return sorted(out, key=lambda k: k.as_str())

    # ------------------------------------------------------------------ #
    #  Writing
    # ------------------------------------------------------------------ #
    def put(self, key: SnapshotKey, png: bytes,
            meta: SnapshotMeta | None = None) -> SnapshotMeta:
        """Write a baseline and its passport. Returns the passport as stored.

        **An identical picture is not rewritten.** `--vistest-update` is run
        over a whole suite to accept two snapshots, and if every file were
        touched the result would be a pull request with three hundred changed
        baselines, three of which mean anything. So bytes are compared first,
        and when they match nothing on disk moves — not the file, not its
        mtime, not the version number.

        The picture is written before the passport, and that order is the
        recoverable one: a baseline without a passport is a working baseline
        (the passport is derived from it), a passport without a picture is a
        claim about a file that does not exist.
        """
        if not isinstance(png, (bytes, bytearray, memoryview)):
            raise TypeError(
                f"a baseline is written as PNG bytes, got {type(png).__name__}")
        png = bytes(png)
        #  Refuse to store anything that is not a PNG, here rather than at the
        #  next run's comparison: the file lands in somebody's repository and
        #  is reviewed as a picture.
        width, height = pngio.dimensions(png, source=self.path_of(key))

        previous = self._read_meta_file(key)
        current = self.get(key)
        unchanged = current is not None and current == png

        if unchanged and previous is not None and meta is None:
            #  Nothing to write and nothing to say. `meta is not None` is the
            #  exception and it matters: setting a snapshot's own thresholds
            #  does not change its picture, and returning early here would
            #  accept the passport and store none of it — a setting that reads
            #  back as saved and is never applied.
            return previous

        version = 1 if current is None else int((previous.version if previous
                                                 else 1) + (0 if unchanged else 1))
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        from .. import __version__ as tool_version

        merged = (meta or previous or SnapshotMeta()).with_(
            version=version,
            width=width,
            height=height,
            sha256=_sha256(png),
            updated_at=(previous.updated_at if unchanged and previous else stamp),
            tool_version=tool_version,
        )
        if meta is not None and previous is not None:
            #  A caller that passes only thresholds should not lose the boxes
            #  somebody drew, and vice versa.
            merged = merged.with_(
                thresholds=meta.thresholds or previous.thresholds,
                ignore_boxes=meta.ignore_boxes or previous.ignore_boxes,
            )

        if not unchanged:
            atomic.write_bytes(self.path_of(key), png)
        atomic.write_text(self.meta_path_of(key), merged.to_json())
        return merged

    def _read_meta_file(self, key: SnapshotKey) -> SnapshotMeta | None:
        path = self.meta_path_of(key)
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as e:
            raise OSError(f"cannot read the passport {path}: {e}") from None
        return SnapshotMeta.from_json(raw, source=str(path))
