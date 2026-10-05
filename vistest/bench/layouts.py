# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Where the pairs are: three layouts, told apart by what is on disk.

* **Playwright's test-results** — a failed `toHaveScreenshot` leaves
  `<test>/<name>-expected.png` and `<name>-actual.png` (and its own
  `-diff.png`) in the test's folder.
* **A VisTest run** — the library writes the run's pictures under
  `.vistest/actual/<platform>/<name>.png`, the baselines under
  `tests/__vistest__/<platform>/<name>.png` with their passports, and a row
  per check under `.vistest/report/parts/`.
* **Two folders** with the same file names (`--expected A --actual B`).

What a layout does not account for is said, one line per file — a picture
that is not part of a pair, a pair missing its other half — and never
dropped in silence. Nothing is copied or written here; paths are only read.

**The renderer.** A pair is compared with the canaries of the renderers that
drew its two pictures when both are known (core/renderer.py), and with
«unknown» otherwise:

* a VisTest run: the baseline's passport names its canary
  (`<baselines>/.renderers/<sha>.png`); the run's is the one the library kept
  under `.vistest/renderers/` when the check drew one, or the baseline's when
  the check said the renderer was the same;
* two folders: `<name>.canary.png` next to a picture is the canary of the
  renderer that drew it (what `.renderers/<sha>.png` holds);
* Playwright's test-results carry none.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["CANARY_SUFFIX", "Found", "LayoutError", "Pair", "discover"]

PLAYWRIGHT = "Playwright test-results"
VISTEST = "VisTest run"
FOLDERS = "two folders"

EXPECTED_SUFFIX = "-expected.png"
ACTUAL_SUFFIX = "-actual.png"
#: Playwright's own pictures next to a pair: recognised, not compared.
PLAYWRIGHT_OWN = ("-diff.png", "-previous.png")
#: In the two-folder layout: the canary of the renderer that drew `<name>.png`.
CANARY_SUFFIX = ".canary.png"
#: The library's default baseline root (library/context.py).
DEFAULT_BASELINES = Path("tests") / "__vistest__"


class LayoutError(ValueError):
    """The input is none of the three layouts; the text says what was looked for."""


@dataclass(frozen=True)
class Pair:
    """One pair to compare: the baseline, the screenshot, and their canaries."""

    id: str
    expected: Path
    actual: Path
    canary_expected: Path | None = None
    canary_actual: Path | None = None
    #: Why a canary is missing, when one is — for the renderer line.
    canary_note: str = ""


@dataclass
class Found:
    layout: str
    root: Path
    pairs: list[Pair] = field(default_factory=list)
    #: One line per file the layout does not account for.
    unrecognised: list[str] = field(default_factory=list)
    #: Recognised and left out on purpose, counted: Playwright's diff pictures,
    #: files that are not pictures.
    skipped: list[str] = field(default_factory=list)


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _not_pictures(paths: list[Path]) -> list[str]:
    if not paths:
        return []
    kinds = Counter(p.suffix.lower() or "(no extension)" for p in paths)
    said = ", ".join(f"{ext} {n}" for ext, n in sorted(kinds.items()))
    return [f"not pictures, left out: {len(paths)} file{'s' if len(paths) != 1 else ''} "
            f"({said})"]


def _files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file())


# --------------------------------------------------------------------------- #
#  Playwright's test-results
# --------------------------------------------------------------------------- #
def _is_playwright(root: Path) -> bool:
    return any(p.name.endswith((EXPECTED_SUFFIX, ACTUAL_SUFFIX)) for p in root.rglob("*.png"))


def _playwright(root: Path) -> Found:
    found = Found(PLAYWRIGHT, root)
    expected: dict[str, Path] = {}
    actual: dict[str, Path] = {}
    own = 0
    others: list[Path] = []
    for path in _files(root):
        name = path.name
        if name.endswith(EXPECTED_SUFFIX):
            expected[_rel(path.parent / name[:-len(EXPECTED_SUFFIX)], root)] = path
        elif name.endswith(ACTUAL_SUFFIX):
            actual[_rel(path.parent / name[:-len(ACTUAL_SUFFIX)], root)] = path
        elif name.endswith(PLAYWRIGHT_OWN):
            own += 1
        elif path.suffix.lower() == ".png":
            found.unrecognised.append(
                f"{_rel(path, root)}: a picture that is not <name>-expected.png "
                "or <name>-actual.png")
        else:
            others.append(path)
    for pid in sorted(set(expected) | set(actual)):
        if pid in expected and pid in actual:
            found.pairs.append(Pair(pid, expected[pid], actual[pid],
                                    canary_note="Playwright's test-results carry no canary"))
        elif pid in expected:
            found.unrecognised.append(f"{_rel(expected[pid], root)}: no {Path(pid).name}"
                                      f"{ACTUAL_SUFFIX} next to it")
        else:
            found.unrecognised.append(
                f"{_rel(actual[pid], root)}: no {Path(pid).name}{EXPECTED_SUFFIX} next "
                "to it (Playwright writes only the actual when there is no baseline)")
    if own:
        found.skipped.append(f"Playwright's own -diff.png / -previous.png, left out: {own}")
    found.skipped += _not_pictures(others)
    return found


# --------------------------------------------------------------------------- #
#  A VisTest run
# --------------------------------------------------------------------------- #
def _vistest_dirs(folder: Path) -> tuple[Path, Path] | None:
    """(project root, .vistest folder) when `folder` is one or holds one."""
    if (folder / ".vistest" / "actual").is_dir():
        return folder, folder / ".vistest"
    if (folder / "actual").is_dir() and (folder / "report").is_dir():
        return folder.parent, folder
    return None


def _rows(vistest_dir: Path) -> dict[str, dict]:
    """The report rows, newest per picture, by the picture's path under actual/."""
    out: dict[str, dict] = {}
    for path in sorted((vistest_dir / "report" / "parts").glob("*.json")):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(row, dict) or not row.get("name"):
            continue
        rel = "/".join(filter(None, [str(row.get("platform") or ""), str(row["name"])]))
        if rel not in out or row.get("written_at", 0) >= out[rel].get("written_at", 0):
            out[rel] = row
    return out


def _canary_of_baseline(baseline: Path, stop: Path) -> tuple[Path | None, str]:
    passport = baseline.with_suffix(".json")
    try:
        meta = json.loads(passport.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, "the baseline has no passport"
    sha = str(((meta or {}).get("renderer") or {}).get("sha256") or "")
    if not sha:
        return None, "the baseline names no canary"
    for folder in (baseline.parent, *baseline.parent.parents):
        candidate = folder / ".renderers" / f"{sha}.png"
        if candidate.is_file():
            return candidate, ""
        if folder == stop:
            break
    return None, f"the baseline's canary .renderers/{sha[:12]}….png is not there"


def _default_baselines(root: Path) -> Path:
    """`tests/__vistest__`; `__vistest__` for a project with no `tests/` folder that has one."""
    from ..storage.file import FALLBACK_BASELINE_ROOT

    usual = root / DEFAULT_BASELINES
    other = root / FALLBACK_BASELINE_ROOT
    if not (root / "tests").is_dir() and other.is_dir():
        return other
    return usual


def _vistest(root: Path, vistest_dir: Path, baselines: Path | None) -> Found:
    found = Found(VISTEST, root)
    actual_dir = vistest_dir / "actual"
    base_root = baselines or _default_baselines(root)
    rows = _rows(vistest_dir)
    others: list[Path] = []
    for path in _files(actual_dir):
        rel = _rel(path, actual_dir)
        if path.suffix.lower() != ".png":
            others.append(path)
            continue
        row = rows.get(rel) or {}
        named = Path(str((row.get("images") or {}).get("baseline") or ""))
        baseline = named if named.is_file() else base_root / rel
        if not baseline.is_file():
            found.unrecognised.append(
                f".vistest/actual/{rel}: no baseline at {_rel(baseline, root)}")
            continue
        canary_e, why_e = _canary_of_baseline(baseline, base_root)
        renderer = row.get("renderer") or {}
        run_sha = str(renderer.get("run_sha256") or "")
        canary_a, why_a = None, ""
        if run_sha and (vistest_dir / "renderers" / f"{run_sha}.png").is_file():
            canary_a = vistest_dir / "renderers" / f"{run_sha}.png"
        elif renderer.get("status") == "same" and canary_e is not None:
            canary_a = canary_e                   # the check found them the same
        else:
            why_a = ("the run kept no canary (a check that passed draws none)"
                     if row else "no report row for this picture")
        found.pairs.append(Pair(rel[:-len(".png")], baseline, path, canary_e, canary_a,
                                "; ".join(filter(None, [why_e, why_a]))))
    found.skipped += _not_pictures(others)
    return found


# --------------------------------------------------------------------------- #
#  Two folders
# --------------------------------------------------------------------------- #
def _sidecar(picture: Path) -> Path | None:
    candidate = picture.with_name(picture.name[:-len(".png")] + CANARY_SUFFIX)
    return candidate if candidate.is_file() else None


def _pictures(root: Path) -> tuple[dict[str, Path], list[Path]]:
    pictures, others = {}, []
    for path in _files(root):
        if path.name.endswith(CANARY_SUFFIX):
            continue
        if path.suffix.lower() == ".png":
            pictures[_rel(path, root)] = path
        else:
            others.append(path)
    return pictures, others


def _folders(expected: Path, actual: Path) -> Found:
    found = Found(FOLDERS, expected)
    left, others_l = _pictures(expected)
    right, others_r = _pictures(actual)
    for rel in sorted(set(left) | set(right)):
        if rel in left and rel in right:
            ce, ca = _sidecar(left[rel]), _sidecar(right[rel])
            note = "; ".join(filter(None, [
                "" if ce else f"no {Path(rel).stem}{CANARY_SUFFIX} in --expected",
                "" if ca else f"no {Path(rel).stem}{CANARY_SUFFIX} in --actual"]))
            found.pairs.append(Pair(rel[:-len(".png")], left[rel], right[rel], ce, ca, note))
        elif rel in left:
            found.unrecognised.append(f"{rel}: in --expected, not in --actual")
        else:
            found.unrecognised.append(f"{rel}: in --actual, not in --expected")
    found.skipped += _not_pictures(others_l + others_r)
    return found


# --------------------------------------------------------------------------- #
def _sorted(found: Found) -> Found:
    """By path, so the same folder gives the same lines in the same order."""
    found.pairs.sort(key=lambda p: p.id)
    found.unrecognised.sort()
    return found


def discover(folder: str | Path | None = None, *, expected: str | Path | None = None,
             actual: str | Path | None = None,
             baselines: str | Path | None = None) -> Found:
    """The pairs in `folder` (a layout told by what is in it), or in two folders."""
    if expected is not None or actual is not None:
        if expected is None or actual is None:
            raise LayoutError("--expected and --actual go together")
        e, a = Path(expected), Path(actual)
        for what, p in (("--expected", e), ("--actual", a)):
            if not p.is_dir():
                raise LayoutError(f"{what} {p}: not a folder")
        return _sorted(_folders(e, a))
    if folder is None:
        raise LayoutError("give a folder, or --expected and --actual")
    root = Path(folder)
    if not root.is_dir():
        raise LayoutError(f"{root}: not a folder")
    dirs = _vistest_dirs(root)
    if dirs is not None:
        return _sorted(_vistest(*dirs, Path(baselines) if baselines else None))
    if _is_playwright(root):
        return _sorted(_playwright(root))
    raise LayoutError(
        f"{root}: no layout recognised — looked for Playwright's test-results "
        "(<name>-expected.png next to <name>-actual.png) and a VisTest run "
        "(.vistest/actual/); for two folders with the same file names use "
        "--expected and --actual")
