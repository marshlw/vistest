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
"""

from __future__ import annotations

import fnmatch
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


def _store_root(root: Path, platform: str) -> Path:
    """Where one platform's baselines live on disk."""
    return root / platform if platform else root


def platforms_of(baselines_root: Path) -> list[str]:
    """Platform directories under the baselines root.

    A baseline directory is recognised by its passport: `meta.json` next to a
    `baseline.png`. Anything else down there is not ours to move.
    """
    if not baselines_root.exists():
        return []
    out = []
    for child in sorted(baselines_root.iterdir()):
        if not child.is_dir():
            continue
        if any(child.rglob("meta.json")):
            out.append(child.name)
    return out


def _snapshot_dirs(platform_root: Path) -> list[Path]:
    return sorted(p.parent for p in platform_root.rglob("meta.json"))


def _name_of(platform_root: Path, snapshot_dir: Path) -> str:
    return snapshot_dir.relative_to(platform_root).as_posix()


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

    entries: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp, tarfile.open(archive, "w:gz") as tar:
        for platform in platforms_of(baselines_root) or [""]:
            platform_root = _store_root(baselines_root, platform)
            for snapshot_dir in _snapshot_dirs(platform_root):
                folder = _name_of(platform_root, snapshot_dir)

                meta = {}
                try:
                    meta = json.loads((snapshot_dir / "meta.json")
                                      .read_text("utf-8"))
                except (OSError, ValueError):
                    # Снимок без читаемого паспорта не переносим: на той
                    # стороне он стал бы эталоном без версии, то есть
                    # выключил бы защиту от гонки решений.
                    continue

                # Имя снимка живёт в паспорте: каталог называется без
                # расширения (`checkout.png` → `checkout/`), а человеку в
                # плане импорта нужно то имя, которое он видит в интерфейсе.
                name = str(meta.get("name") or folder)
                if not selection.matches(name, platform, folder):
                    continue

                arc_base = f"baselines/{platform}/{folder}" if platform \
                    else f"baselines/{folder}"
                for item in sorted(snapshot_dir.iterdir()):
                    if item.name == "history" and not with_history:
                        continue
                    tar.add(item, arcname=f"{arc_base}/{item.name}")

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


def plan(archive: str | Path, *, baselines_root: str | Path,
         mode: str = "new", selection: Selection | None = None) -> list[dict]:
    """What the import would do to each snapshot — without doing it.

    Exists because the answer to «what happens to my baselines» must be
    available before the answer becomes irreversible.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of: {', '.join(MODES)}")

    selection = selection or Selection()
    baselines_root = Path(baselines_root)
    manifest = inspect(archive)

    out = []
    for entry in manifest.get("snapshots", []):
        name, platform = entry["name"], entry.get("platform", "")
        folder = entry.get("dir") or name
        if not selection.matches(name, platform, folder):
            continue
        target = _store_root(baselines_root, platform) / folder
        exists = (target / "meta.json").exists()

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
                    "action": action, "reason": why})
    return out


def import_(archive: str | Path, *, baselines_root: str | Path,
            mode: str = "new", selection: Selection | None = None,
            who: str = "") -> dict:
    """Merge an archive into this installation's baselines."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of: {', '.join(MODES)}")

    selection = selection or Selection()
    baselines_root = Path(baselines_root)
    baselines_root.mkdir(parents=True, exist_ok=True)

    from .storage import BaselineRecord, FileBaselineStore

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

                target = target_platform / folder
                exists = (target / "meta.json").exists()
                try:
                    if exists and mode == "new":
                        result["skipped"].append(name)
                        continue

                    if exists and mode == "replace":
                        shutil.rmtree(target, ignore_errors=True)
                        shutil.copytree(snapshot_dir, target)
                        result["replaced"].append(name)
                        continue

                    if not exists:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copytree(snapshot_dir, target)
                        result["added"].append(name)
                        continue

                    # mode == "update": через `store.save`, а не копированием.
                    #
                    # Копирование поверх стёрло бы то, что здесь уже решили:
                    # версию, историю и — главное — маски игнорирования,
                    # которые ставила ЭТА команда под свои условия съёмки.
                    # `save` архивирует предыдущую версию и поднимает номер,
                    # то есть импорт становится обратимым.
                    store = FileBaselineStore(target_platform)
                    from .capture.playwright_capture import read_png

                    image = read_png(snapshot_dir / "baseline.png")
                    dom = None
                    dom_path = snapshot_dir / "dom.json"
                    if dom_path.exists():
                        try:
                            dom = json.loads(dom_path.read_text("utf-8"))
                        except ValueError:
                            dom = None

                    store.save(BaselineRecord(
                        name=name, image=image, dom=dom,
                        meta={"imported_by": who or "import",
                              "imported_from_version":
                                  int(incoming_meta.get("version", 1))}))
                    result["updated"].append(name)
                except Exception as e:
                    result["failed"].append(
                        {"name": name, "error": f"{type(e).__name__}: {e}"})

    result["counts"] = {k: len(v) for k, v in result.items() if isinstance(v, list)}
    result["mode"] = mode
    return result
