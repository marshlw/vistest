# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""What a baseline store is, in two shapes.

`BaselineStore` is the service's: it speaks in decoded images and carries
everything the review screen needs — stability masks, the DOM snapshot taken
at approval, ignore boxes, versions.

`SnapshotStore` is the library's: bytes in, bytes out, and a passport beside
them. It exists because the library mode has a different owner for the same
data. There, baselines are files in somebody else's repository, reviewed in
their pull requests and versioned by their git — so the store must not decode
anything it was not asked about, must not keep a history of its own, and must
not need numpy to answer «is there a baseline for this».

Both live here rather than in two files because the pair is the interesting
part: `SnapshotKey` below is the mapping between them, and the archive that
`vistest baselines export` writes is the format they share.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Protocol, runtime_checkable

import numpy as np

from ..core.settings import ConfigError
from ..core.thresholds import EDITABLE, ThresholdError, validate
from .paths import safe_name, split_project

__all__ = [
    "BaselineRecord", "BaselineStore",
    "SnapshotKey", "SnapshotMeta", "SnapshotStore", "platforms_with",
]


# --------------------------------------------------------------------------- #
#  The service's store: decoded images and everything around them
# --------------------------------------------------------------------------- #
@dataclass
class BaselineRecord:
    name: str
    image: np.ndarray
    stability_mask: np.ndarray | None = None
    dom: dict | None = None
    ignore_boxes: list[dict] | None = None
    version: int = 1
    meta: dict | None = None


class BaselineStore(ABC):
    @abstractmethod
    def exists(self, name: str) -> bool: ...

    @abstractmethod
    def load(self, name: str) -> BaselineRecord | None: ...

    @abstractmethod
    def save(self, record: BaselineRecord) -> None: ...

    @abstractmethod
    def list_names(self) -> list[str]: ...

    @abstractmethod
    def add_ignore_box(self, name: str, box: dict) -> None: ...


# --------------------------------------------------------------------------- #
#  The library's store: bytes, and the key that addresses them
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SnapshotKey:
    """Everything that decides which file a check is compared against.

    Three parts, and the middle one is the one people get wrong. `platform` is
    the browser together with the window size — `chromium-1440x900` — because
    a baseline is only comparable against a screenshot taken the same way.
    It is NOT a `NamingProfile`: that word is taken in this package, and it
    means the rules for reading somebody else's file names.

    `project` is a prefix inside the name, not a separate level. It is spelled
    out as a field because `SnapshotKey("login.png", project="shop")` is how a
    library user says it, but it is folded into the name at construction, so
    that spelling and `SnapshotKey("shop/login.png")` are the same key and the
    same file. Without that folding the two would compare unequal and address
    one path, which is the sort of thing that stays hidden until a store is
    asked to deduplicate.
    """

    name: str
    platform: str = ""
    project: str = ""

    def __post_init__(self) -> None:
        name = str(self.name).replace("\\", "/").strip("/")
        project = str(self.project or "").replace("\\", "/").strip("/")
        if project and not name.startswith(f"{project}/"):
            name = f"{project}/{name}"
        if not name.lower().endswith(".png"):
            #  `login` and `login.png` are one snapshot. The suffix is part of
            #  the name everywhere else in VisTest — in the store, in the
            #  history, on the review screen — and a name without it would show
            #  up as a second, empty snapshot next to the real one.
            name = f"{name}.png"
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "project", split_project(name)[0])
        object.__setattr__(self, "platform", str(self.platform or "").strip("/"))

    @property
    def folder(self) -> str:
        """The name as a sanitised path without the suffix: `shop/login`.

        This is the unit the export archive is built from and the directory the
        service's store uses, so it is what makes the two layouts one format.
        """
        return safe_name(self.name)

    @property
    def relpath(self) -> PurePosixPath:
        """Where the baseline file sits under a flat store's root."""
        parts = [p for p in (self.platform, f"{self.folder}.png") if p]
        return PurePosixPath(*parts)

    def as_str(self) -> str:
        """A stable id: used in artifact paths and as the report's row key."""
        return f"{self.platform}/{self.folder}" if self.platform else self.folder

    def __str__(self) -> str:
        return self.as_str()

    @classmethod
    def parse(cls, text: str) -> SnapshotKey:
        """The inverse of `as_str`, with the first segment read as the platform.

        A key built this way has `project` where `as_str` put it and not
        necessarily where the original key had it — but `folder`, `relpath` and
        `as_str` all come back identical, and those are what address the file.
        """
        parts = [p for p in str(text).replace("\\", "/").split("/") if p]
        if len(parts) < 2:
            return cls(name="/".join(parts) or "snapshot")
        return cls(name="/".join(parts[1:]), platform=parts[0])


#  What a passport may say. Anything else in the file is a mistake, and saying
#  so is the point: the file sits in the user's repository and is edited by
#  hand there.
_META_KEYS = (
    "version", "width", "height", "sha256", "updated_at", "tool_version",
    "thresholds", "ignore_boxes",
)


@dataclass(frozen=True)
class SnapshotMeta:
    """The passport written next to a baseline. Data, and nothing else.

    Optional by design. A baseline is a PNG file, and a PNG file on its own is
    a complete answer to «what should this look like» — a person may drop one
    into the directory by hand, and that has to work. Everything here is either
    reconstructible from the picture (`width`, `height`, `sha256`) or an
    addition somebody made deliberately (`thresholds`, `ignore_boxes`).

    A missing passport is therefore not an error. A passport that is there and
    says something impossible is, loudly and by name — that is the difference
    between absent data and invalid data, and it is the whole of the rule this
    package follows about configuration.
    """

    version: int = 1
    width: int = 0
    height: int = 0
    sha256: str = ""
    updated_at: str = ""
    tool_version: str = ""
    thresholds: dict[str, float] = field(default_factory=dict)
    ignore_boxes: tuple[dict, ...] = ()

    def to_dict(self) -> dict:
        return {
            "version": int(self.version),
            "width": int(self.width),
            "height": int(self.height),
            "sha256": self.sha256,
            "updated_at": self.updated_at,
            "tool_version": self.tool_version,
            "thresholds": dict(self.thresholds),
            "ignore_boxes": [dict(b) for b in self.ignore_boxes],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False,
                          sort_keys=True) + "\n"

    def with_(self, **changes) -> SnapshotMeta:
        return replace(self, **changes)

    # ------------------------------------------------------------------ #
    @classmethod
    def from_dict(cls, raw: dict, *, source: str = "") -> SnapshotMeta:
        where = f"{source}: " if source else ""
        if not isinstance(raw, dict):
            raise ConfigError(
                f"{where}a snapshot passport must be an object, "
                f"got {type(raw).__name__}")

        unknown = sorted(set(raw) - set(_META_KEYS))
        if unknown:
            raise ConfigError(
                f"{where}unknown keys {', '.join(repr(k) for k in unknown)}. "
                f"Known keys: {', '.join(_META_KEYS)}.")

        def number(key: str, cast) -> int | float:
            value = raw.get(key, 0)
            try:
                return cast(value)
            except (TypeError, ValueError):
                raise ConfigError(
                    f"{where}{key}: {value!r} is not a number") from None

        def text(key: str) -> str:
            value = raw.get(key, "")
            if value is None:
                return ""
            if not isinstance(value, str):
                raise ConfigError(f"{where}{key}: {value!r} is not a string")
            return value

        thresholds_raw = raw.get("thresholds") or {}
        if not isinstance(thresholds_raw, dict):
            raise ConfigError(
                f"{where}thresholds must be an object, "
                f"got {type(thresholds_raw).__name__}")
        unknown = sorted(set(thresholds_raw) - set(EDITABLE))
        if unknown:
            raise ConfigError(
                f"{where}thresholds: unknown {', '.join(repr(k) for k in unknown)}. "
                f"Editable: {', '.join(sorted(EDITABLE))}.")
        thresholds = {}
        for name, value in thresholds_raw.items():
            try:
                thresholds[name] = validate(name, value)
            except ThresholdError as e:
                raise ConfigError(f"{where}thresholds.{e}") from None

        boxes = raw.get("ignore_boxes") or []
        if not isinstance(boxes, (list, tuple)):
            raise ConfigError(
                f"{where}ignore_boxes must be a list, "
                f"got {type(boxes).__name__}")
        for box in boxes:
            if not isinstance(box, dict):
                raise ConfigError(
                    f"{where}ignore_boxes: {box!r} is not an object")

        return cls(
            version=int(number("version", int) or 1),
            width=int(number("width", int)),
            height=int(number("height", int)),
            sha256=text("sha256"),
            updated_at=text("updated_at"),
            tool_version=text("tool_version"),
            thresholds=thresholds,
            ignore_boxes=tuple(dict(b) for b in boxes),
        )

    @classmethod
    def from_json(cls, text: str, *, source: str = "") -> SnapshotMeta:
        try:
            raw = json.loads(text)
        except ValueError as e:
            raise ConfigError(
                f"{source or 'snapshot passport'}: not valid JSON ({e})") from None
        return cls.from_dict(raw, source=source)


def platforms_with(store: SnapshotStore, key: SnapshotKey) -> list[str]:
    """Which other platforms hold a baseline for this same snapshot.

    Written against `list()` alone, so any store answers it. Called only on the
    failure path — it is a walk, and it buys the one sentence that turns the
    most confusing message this tool produces into an obvious one: «there is no
    baseline» in a repository that visibly contains one.

    The empty string is a platform like any other here: it is where a run whose
    target is `bytes` puts its baselines, and «you approved these from a page
    and are now comparing from bytes» is exactly the case worth naming.
    """
    try:
        every = store.list()
    except Exception:
        #  This runs while a failure message is being built. A store that
        #  cannot enumerate itself must not replace that message with its own
        #  traceback.
        return []
    return sorted({other.platform for other in every
                   if other.folder == key.folder
                   and other.platform != key.platform})


@runtime_checkable
class SnapshotStore(Protocol):
    """Byte-level baseline storage. No numpy, no database, no service.

    Six methods, and two of them are here for reasons worth writing down.

    `meta` exists so that a snapshot's own thresholds can be read without
    decoding its picture: they are consulted for every check, and unfolding a
    full-page screenshot to reach a two-key dictionary is a cost paid once per
    snapshot per run for nothing.

    `path_of` exists because every message this package prints about a baseline
    has to name the file. A store that is not file-backed returns `None`, and
    the caller says what it can instead.
    """

    def get(self, key: SnapshotKey) -> bytes | None:
        """The baseline picture, or `None` if there is none yet."""
        ...

    def put(self, key: SnapshotKey, png: bytes,
            meta: SnapshotMeta | None = None) -> SnapshotMeta:
        """Write a baseline. Returns the passport as it now stands."""
        ...

    def meta(self, key: SnapshotKey) -> SnapshotMeta | None:
        """The passport, reconstructed from the picture when there is no file."""
        ...

    def history(self, key: SnapshotKey) -> list[SnapshotMeta]:
        """Previous versions, newest first. Empty where version control is the history."""
        ...

    def list(self, prefix: str = "") -> list[SnapshotKey]:
        """Every baseline whose `as_str()` starts with `prefix`, sorted."""
        ...

    def path_of(self, key: SnapshotKey) -> Path | None:
        """Where this baseline lives, for a message a human will read."""
        ...
