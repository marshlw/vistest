# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Moving baselines between installations.

Different from a backup, and the difference is the whole point. A backup answers
«put this installation back the way it was» — everything, into an empty volume.
This answers «take these snapshots to that other installation, which already has
its own» — a subset, merged into something live.

Three real situations, and they are why the options look the way they do:

* dev → staging → production. Same product, three installations, and a set of
  baselines that has to travel forward without carrying the other two's
  history.
* a contractor hands the work over. They ran the tests, they approved the
  snapshots, and now the customer needs those baselines inside their own
  perimeter, where the contractor never had access.
* one team splits into two. The same baselines start life in two places and
  then diverge on purpose.

**Merging is the hard part, so it is explicit.** Three modes, and each is a
different answer to «what if this snapshot already exists here»:

* `new` — leave what is here, take only what is missing. The safe default.
* `update` — bring the incoming picture in as a NEW VERSION of the existing
  baseline. Nothing is lost: the previous version stays in the history and can
  be rolled back to from the interface. This is what «take the update» means.
* `replace` — the incoming set wins, existing history included.

Approval history does not travel. A signature under «I looked at this and it is
correct» belongs to the person who gave it, in the installation where they gave
it — copying it into another installation would fabricate a decision nobody
made there. What travels is the picture, its passport, its masks and its
version number; the receiving installation records the import itself, under the
name of whoever performed it.

**Two layouts, one archive.** Baselines live on disk in one of two shapes. The
service keeps a directory per snapshot — `baseline.png` with `meta.json`, masks
and `history/` beside it. The library mode keeps plain files in the user's
repository — `login.png` with an optional `login.json` — because there they are
reviewed in pull requests, and a pull request showing a picture is worth more
than one showing a directory of bookkeeping.

The archive is the same either way, and that is what makes this module the
migration path between them: a project that started with the library and
outgrew it runs `vistest baselines export` and imports the result into an
installation, with no re-approval and no renaming. The layout is detected from
what is on disk; `layout=` overrides the guess when the target is empty and
there is nothing to detect.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import shutil
import tarfile
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

MANIFEST = "vistest-baselines.json"
MODES = ("new", "update", "replace")


@dataclass
class Selection:
    """What to take. Empty everywhere means «everything»."""

    project: str = ""
    platform: str = ""
    names: tuple[str, ...] = ()

    def matches(self, name: str, platform: str, folder: str = "") -> bool:
        """Совпадает ли снимок с выборкой.

        Имя и каталог проверяются оба, и это не перестраховка. Хранилище
        режет расширение из имени каталога (`checkout.png` лежит в
        `checkout/`), поэтому `--name '*.png'` не нашёл бы ничего, если
        сверять только с каталогом, а `--name 'shop/checkout'` — если только
        с именем. Человек вправе написать и так, и так.
        """
        candidates = [c for c in (name, folder) if c]
        if self.platform and platform != self.platform:
            return False
        if self.project:
            if not any(c.partition("/")[0] == self.project for c in candidates):
                return False
        if self.names:
            return any(fnmatch.fnmatch(c, pattern)
                       for c in candidates for pattern in self.names)
        return True


SERVER, FLAT, EMPTY = "server", "flat", "empty"
LAYOUTS = (SERVER, FLAT)


def _store_root(root: Path, platform: str) -> Path:
    """Where one platform's baselines live on disk."""
    return root / platform if platform else root


def detect_layout(baselines_root: str | Path) -> str:
    """Which of the two shapes this directory is in.

    `server` is recognised by a passport next to a picture — `meta.json` and
    `baseline.png` in one directory. `flat` is any other PNG. The order matters
    and not the other way round: a server store also contains PNGs, so looking
    for those first would call every installation flat.
    """
    root = Path(baselines_root)
    if not root.exists():
        return EMPTY
    for meta in root.rglob("meta.json"):
        if (meta.parent / "baseline.png").exists():
            return SERVER
    for png in root.rglob("*.png"):
        if png.name != "baseline.png" and not png.name.startswith("."):
            return FLAT
    return EMPTY


def platforms_of(baselines_root: Path, layout: str | None = None) -> list[str]:
    """Platform directories under the baselines root, in either layout."""
    root = Path(baselines_root)
    if not root.exists():
        return []
    layout = layout or detect_layout(root)
    out = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if layout == SERVER and any(child.rglob("meta.json")):
            out.append(child.name)
        elif layout == FLAT and any(
                p.name != "baseline.png" and not p.name.startswith(".")
                for p in child.rglob("*.png")):
            out.append(child.name)
    return out


def _snapshot_dirs(platform_root: Path) -> list[Path]:
    return sorted(p.parent for p in platform_root.rglob("meta.json"))


def _name_of(platform_root: Path, snapshot_dir: Path) -> str:
    return snapshot_dir.relative_to(platform_root).as_posix()


# --------------------------------------------------------------------------- #
#  Reading a flat store
# --------------------------------------------------------------------------- #
def _flat_pngs(platform_root: Path) -> list[Path]:
    return sorted(p for p in platform_root.rglob("*.png")
                  if p.name != "baseline.png" and not p.name.startswith("."))


def _flat_meta(png: Path, name: str) -> dict:
    """The passport for one flat baseline, in the archive's shape.

    Reconstructed from the picture when there is no sidecar, because in this
    layout the sidecar is optional by design: a PNG a person dropped into the
    directory by hand is a valid baseline, and it has to survive the move.
    """
    from .core import pngio
    from .storage.base import SnapshotMeta

    sidecar = png.with_suffix(".json")
    meta = None
    if sidecar.exists():
        meta = SnapshotMeta.from_json(sidecar.read_text("utf-8"),
                                      source=str(sidecar))
    if meta is None:
        raw = png.read_bytes()
        width, height = pngio.dimensions(raw, source=png)
        meta = SnapshotMeta(version=1, width=width, height=height)
    return {**meta.to_dict(), "name": name}


def _archive_meta_to_passport(raw: dict):
    """A server passport -> the library's, keeping only what it can hold.

    The service records things the library mode has nowhere to put — who
    approved a snapshot, against which commit, the accumulated masks. Those are
    dropped on the way in rather than refused: the picture and its thresholds
    are what a comparison needs, and stopping an import over a field we do not
    store would make the migration path unusable in the one direction people
    actually take it.
    """
    from .storage.base import SnapshotMeta

    known = {"version", "width", "height", "sha256", "updated_at",
             "tool_version", "thresholds", "ignore_boxes"}
    return SnapshotMeta.from_dict(
        {k: v for k, v in (raw or {}).items() if k in known and v is not None},
        source="the archive")


# --------------------------------------------------------------------------- #
#  Export
# --------------------------------------------------------------------------- #
def export(archive: str | Path, *, baselines_root: str | Path,
           selection: Selection | None = None,
           with_history: bool = False) -> dict:
    """Pack the selected baselines into an archive. Returns the manifest.

    `with_history` is off by default: the history of previous versions is
    usually several times the size of the current picture, and the receiving
    side gets its own history from the moment the set arrives. Turn it on when
    the point of the move is precisely to keep the past — a handover, say.
    """
    baselines_root = Path(baselines_root)
    if not baselines_root.exists():
        raise FileNotFoundError(f"There are no baselines at {baselines_root}")

    selection = selection or Selection()
    archive = Path(archive).resolve()
    archive.parent.mkdir(parents=True, exist_ok=True)

    import vistest

    layout = detect_layout(baselines_root)
    entries: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp, tarfile.open(archive, "w:gz") as tar:
        staged = Path(tmp)
        for platform, folder, name, meta, files in _scan(
                baselines_root, layout, with_history=with_history,
                staged=staged):
            if not selection.matches(name, platform, folder):
                continue

            arc_base = f"baselines/{platform}/{folder}" if platform \
                else f"baselines/{folder}"
            for arcname, source in files:
                tar.add(source, arcname=f"{arc_base}/{arcname}")

            entries.append({
                "name": name,
                "dir": folder,
                "platform": platform,
                "version": int(meta.get("version", 1)),
                "width": meta.get("width"),
                "height": meta.get("height"),
                "updated_at": meta.get("updated_at", ""),
                "ignore_boxes": len(meta.get("ignore_boxes") or []),
            })

        manifest = {
            "tool": "vistest",
            "kind": "baselines",
            "version": getattr(vistest, "__version__", "?"),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "with_history": bool(with_history),
            "layout": layout,
            "selection": {"project": selection.project,
                          "platform": selection.platform,
                          "names": list(selection.names)},
            "snapshots": entries,
        }
        info = Path(tmp) / MANIFEST
        info.write_text(json.dumps(manifest, indent=2, ensure_ascii=False),
                        encoding="utf-8")
        tar.add(info, arcname=MANIFEST)

    manifest["archive"] = str(archive)
    manifest["size_kb"] = round(archive.stat().st_size / 1024)
    manifest["count"] = len(entries)
    return manifest


def _scan(baselines_root: Path, layout: str, *, with_history: bool,
          staged: Path):
    """Every baseline under the root, in the archive's terms, whatever the layout.

    Yields `(platform, folder, name, meta, files)`, where `files` are the pairs
    that go into the archive under `baselines/<platform>/<folder>/`. Both
    layouts produce the same pairs — `baseline.png` and `meta.json` — which is
    the whole reason a set can be moved from one to the other.
    """
    for platform in platforms_of(baselines_root, layout) or [""]:
        platform_root = _store_root(baselines_root, platform)

        if layout == FLAT:
            for png in _flat_pngs(platform_root):
                folder = png.relative_to(platform_root).with_suffix("").as_posix()
                name = f"{folder}.png"
                meta = _flat_meta(png, name)
                #  The generated passport is staged under the same relative
                #  path, so two snapshots cannot collide over one temp file.
                target = staged / (platform or "_") / f"{folder}.json"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(meta, indent=2, ensure_ascii=False),
                                  encoding="utf-8")
                yield platform, folder, name, meta, [("baseline.png", png),
                                                     ("meta.json", target)]
            continue

        for snapshot_dir in _snapshot_dirs(platform_root):
            folder = _name_of(platform_root, snapshot_dir)
            try:
                meta = json.loads((snapshot_dir / "meta.json").read_text("utf-8"))
            except (OSError, ValueError):
                # A snapshot with no readable passport is not moved: on the
                # other side it would become a baseline without a version, that
                # is, with the protection against racing decisions switched off.
                continue

            # The snapshot's name lives in the passport: the directory is named
            # without the extension (`checkout.png` -> `checkout/`), and the
            # import plan has to show a person the name they see in the
            # interface.
            name = str(meta.get("name") or folder)
            files = [(item.name, item) for item in sorted(snapshot_dir.iterdir())
                     if not (item.name == "history" and not with_history)]
            yield platform, folder, name, meta, files


def _exists_at(root: Path, layout: str, platform: str, folder: str) -> bool:
    """Is this snapshot already in the target store."""
    where = _store_root(root, platform)
    if layout == FLAT:
        return (where / f"{folder}.png").exists()
    return (where / folder / "meta.json").exists()


def _open(archive: Path) -> tarfile.TarFile:
    """Открыть архив, переведя отказ tar на человеческий.

    `tarfile.ReadError` не наследуется ни от `OSError`, ни от `ValueError`, и
    из-за этого файл, который прислали не тот, доезжал до обработчика ошибок
    сервиса и возвращался пятисоткой. Человек в этот момент узнавал про
    «internal error» вместо «это не наш архив» — то есть про нашу поломку
    вместо своей опечатки.
    """
    try:
        return tarfile.open(archive, "r:gz")
    except tarfile.TarError as e:
        raise ValueError(
            f"This file is not a VisTest baselines archive ({e})") from None


def inspect(archive: str | Path) -> dict:
    """What is inside, without unpacking."""
    with _open(Path(archive)) as tar:
        try:
            member = tar.extractfile(MANIFEST)
        except KeyError:
            member = None
        if member is None:
            raise ValueError(
                "This archive was not made by `vistest baselines export`")
        try:
            return json.loads(member.read().decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            raise ValueError(f"The archive manifest is damaged: {e}") from None


# --------------------------------------------------------------------------- #
#  Import
# --------------------------------------------------------------------------- #
def _safe_extract(tar: tarfile.TarFile, dest: Path) -> None:
    """Unpack, refusing anything that would land outside `dest`."""
    base = dest.resolve()
    members = []
    for member in tar.getmembers():
        target = (dest / member.name).resolve()
        if target != base and base not in target.parents:
            raise ValueError(f"Unsafe path in the archive: {member.name}")
        if member.issym() or member.islnk():
            raise ValueError(f"Links are not imported: {member.name}")
        members.append(member)
    tar.extractall(dest, members=members)


def _target_layout(baselines_root: Path, layout: str) -> str:
    """Which shape to write in. Detected, unless the caller insisted.

    An empty target has nothing to detect, and the answer there is `server`:
    this function is reached from the service's CLI and its API, and quietly
    laying out an installation's baselines the library's way would leave the
    review screen looking at an empty directory. A library user passes
    `layout="flat"` — the CLI has a flag for it.
    """
    if layout and layout != "auto":
        if layout not in LAYOUTS:
            raise ValueError(f"layout must be one of: {', '.join(LAYOUTS)}")
        return layout
    found = detect_layout(baselines_root)
    return SERVER if found == EMPTY else found


def plan(archive: str | Path, *, baselines_root: str | Path,
         mode: str = "new", selection: Selection | None = None,
         layout: str = "auto") -> list[dict]:
    """What the import would do to each snapshot — without doing it.

    Exists because the answer to «what happens to my baselines» must be
    available before the answer becomes irreversible.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of: {', '.join(MODES)}")

    selection = selection or Selection()
    baselines_root = Path(baselines_root)
    target_layout = _target_layout(baselines_root, layout)
    manifest = inspect(archive)

    out = []
    for entry in manifest.get("snapshots", []):
        name, platform = entry["name"], entry.get("platform", "")
        folder = entry.get("dir") or name
        if not selection.matches(name, platform, folder):
            continue
        exists = _exists_at(baselines_root, target_layout, platform, folder)

        if not exists:
            action, why = "add", "not here yet"
        elif mode == "new":
            action, why = "skip", "already here; mode «new» keeps what is here"
        elif mode == "update":
            action, why = "new version", "kept as a version you can roll back to"
        else:
            action, why = "replace", "existing history is discarded"

        out.append({"name": name, "dir": folder, "platform": platform,
                    "version": entry.get("version", 1),
                    "layout": target_layout,
                    "action": action, "reason": why})
    return out


def import_(archive: str | Path, *, baselines_root: str | Path,
            mode: str = "new", selection: Selection | None = None,
            who: str = "", layout: str = "auto") -> dict:
    """Merge an archive into this installation's baselines.

    `layout` decides the shape written on this side and defaults to whatever is
    already there. It is the other half of the migration path: an archive made
    from a repository's `tests/__vistest__/` imports into a service store
    unchanged, and an archive made from a service imports into a repository the
    same way.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of: {', '.join(MODES)}")

    selection = selection or Selection()
    baselines_root = Path(baselines_root)
    target_layout = _target_layout(baselines_root, layout)
    baselines_root.mkdir(parents=True, exist_ok=True)

    result = {"added": [], "updated": [], "replaced": [], "skipped": [],
              "failed": []}

    with tempfile.TemporaryDirectory() as tmp:
        staged = Path(tmp)
        with _open(Path(archive)) as tar:
            _safe_extract(tar, staged)

        incoming_root = staged / "baselines"
        if not incoming_root.exists():
            raise ValueError("The archive holds no baselines")

        for platform in platforms_of(incoming_root) or [""]:
            incoming_platform = _store_root(incoming_root, platform)
            target_platform = _store_root(baselines_root, platform)

            for snapshot_dir in _snapshot_dirs(incoming_platform):
                folder = _name_of(incoming_platform, snapshot_dir)
                incoming_meta = {}
                try:
                    incoming_meta = json.loads(
                        (snapshot_dir / "meta.json").read_text("utf-8"))
                except (OSError, ValueError):
                    pass
                name = str(incoming_meta.get("name") or folder)
                if not selection.matches(name, platform, folder):
                    continue

                exists = _exists_at(baselines_root, target_layout, platform,
                                    folder)
                try:
                    if exists and mode == "new":
                        result["skipped"].append(name)
                        continue

                    action = "replaced" if exists and mode == "replace" \
                        else ("added" if not exists else "updated")
                    writer = (_write_flat if target_layout == FLAT
                              else _write_server)
                    writer(target_platform, folder, name, snapshot_dir,
                           incoming_meta, action=action, who=who)
                    result[action].append(name)
                except Exception as e:
                    result["failed"].append(
                        {"name": name, "error": f"{type(e).__name__}: {e}"})

    result["counts"] = {k: len(v) for k, v in result.items() if isinstance(v, list)}
    result["mode"] = mode
    result["layout"] = target_layout
    return result


# --------------------------------------------------------------------------- #
#  Writing one snapshot into either shape
# --------------------------------------------------------------------------- #
def _write_server(platform_root: Path, folder: str, name: str,
                  snapshot_dir: Path, incoming_meta: dict, *, action: str,
                  who: str) -> None:
    target = platform_root / folder
    if action == "replaced":
        shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(snapshot_dir, target)
        return
    if action == "added":
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(snapshot_dir, target)
        return

    # action == "updated": through `store.save`, not by copying.
    #
    # Copying over would erase what has already been decided here: the version,
    # the history and — above all — the ignore masks this team drew for its own
    # capture conditions. `save` archives the previous version and raises the
    # number, which is what makes the import reversible.
    from .capture.playwright_capture import read_png
    from .storage import BaselineRecord, FileBaselineStore

    store = FileBaselineStore(platform_root)
    dom = None
    dom_path = snapshot_dir / "dom.json"
    if dom_path.exists():
        try:
            dom = json.loads(dom_path.read_text("utf-8"))
        except ValueError:
            dom = None

    store.save(BaselineRecord(
        name=name, image=read_png(snapshot_dir / "baseline.png"), dom=dom,
        meta={"imported_by": who or "import",
              "imported_from_version": int(incoming_meta.get("version", 1))}))


def _write_flat(platform_root: Path, folder: str, name: str,
                snapshot_dir: Path, incoming_meta: dict, *, action: str,
                who: str = "") -> None:
    """Into a repository: one PNG and one passport, and nothing else.

    `name` and `who` are taken and not used: the two writers are called through
    one variable and a repository records neither. The name is the file name
    here, and who imported a file is what `git log` is for.

    Masks, the DOM snapshot and the previous versions do not come along. There
    is nowhere for them to go that a person reviewing a pull request would
    thank us for, and the history they carry is the sending installation's, not
    this repository's.
    """
    from .storage import atomic
    from .storage.base import SnapshotKey
    from .storage.file import FileStore

    png = (snapshot_dir / "baseline.png").read_bytes()
    passport = _archive_meta_to_passport(incoming_meta)

    if action == "updated":
        #  Through the store, so the version becomes this repository's own
        #  count rather than the sender's: what the file looked like before is
        #  already recorded by git, and two numbering schemes in one passport
        #  would answer «which version is this» twice.
        FileStore(platform_root).put(SnapshotKey(name=f"{folder}.png"), png,
                                     meta=passport)
        return

    #  Added or replaced: nothing here to count from, so the sender's version
    #  is kept as it stands. It is the only record of where this picture came
    #  from that survives the move.
    target = platform_root / f"{folder}.png"
    atomic.write_bytes(target, png)
    atomic.write_text(
        target.with_suffix(".json"),
        passport.with_(sha256=hashlib.sha256(png).hexdigest()).to_json())
