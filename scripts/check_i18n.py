#!/usr/bin/env python3
# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""A ratchet on Russian text, not a translation.

The repository is going public in English, and roughly three and a half
thousand lines of it are still in Russian — comments, docstrings, a few user
facing strings. Translating that is a week of work and it is not this week's.

What cannot wait is the direction. Every day without a check adds new Russian
lines to files that already have them and to files that do not, and the number
nobody is measuring only goes up. So: the existing debt is written down, file
by file, with its current line count, and this script fails if a file grows
past its entry or if a file that is not on the list acquires any Russian at
all.

The effect is narrow and worth having on its own. New debt stops appearing.
Old debt has a number. The number can only go down, and `--update` is how it is
recorded going down after a file is translated.

    python scripts/check_i18n.py            # check; non-zero if it grew
    python scripts/check_i18n.py --update   # rewrite the debt file
    python scripts/check_i18n.py --list     # what is left, biggest first

**What is not debt.** A file whose Russian is deliberate — `README.ru.md`,
`docs/ARCHITECTURE.ru.md`, anything with a `.ru.` infix — is skipped entirely.
Recording those as debt would say they ought to shrink to nothing, which is
what they are for.

**What is scanned.** The repository's tracked files, as `git ls-files` reports
them — which is also what excludes `.git`, `node_modules`, `.venv`, the caches
and everything else `.gitignore` covers. Without git, a filesystem walk with
the same exclusions is used instead. Binary files are skipped by extension and,
failing that, by the first null byte: baselines are PNGs and have nothing to
say about language.

A file that is added but not yet committed is not tracked and is therefore not
checked locally. In CI it always is, because there it is committed.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEBT = ROOT / "scripts" / "i18n_debt.txt"

#  Used only when git is unavailable; with git, `.gitignore` decides.
SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".idea", ".vscode",
    ".cache", "build", "dist", "htmlcov", ".vistest", "_private",
})

#  Files whose Russian is the point, not a debt. `README.ru.md` and
#  `docs/ARCHITECTURE.ru.md` are the Russian versions of documents that also
#  exist in English; listing them as debt would say they ought to shrink to
#  nothing, which is the opposite of why they exist. The `.ru.` infix is the
#  rule, so a future `docs/GUIDE.ru.md` needs no edit here.
TRANSLATIONS = ("*.ru.md", "*.ru.txt", "*.ru.html")

SKIP_SUFFIXES = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg", ".pdf",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".zip", ".gz", ".tar", ".whl", ".so", ".pyd", ".dll", ".exe",
    ".db", ".sqlite", ".sqlite3", ".onnx", ".pem", ".mp4", ".webm",
})


def is_cyrillic(char: str) -> bool:
    #  The Cyrillic block (U+0400-U+04FF) plus its supplement (U+0500-U+052F).
    #  Written as escapes rather than as characters so that the checker is not
    #  the first entry in its own debt file — and deliberately not
    #  `str.isalpha()` with a locale guess, which would answer differently on
    #  different machines.
    return "\u0400" <= char <= "\u04ff" or "\u0500" <= char <= "\u052f"


def count_lines(path: Path) -> int:
    """Lines holding at least one Cyrillic character. Binary files count zero."""
    if path.suffix.lower() in SKIP_SUFFIXES:
        return 0
    try:
        raw = path.read_bytes()
    except OSError:
        return 0
    if b"\x00" in raw[:8192]:
        return 0
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return 0
    return sum(1 for line in text.splitlines()
               if any(is_cyrillic(c) for c in line))


def tracked_files() -> list[Path]:
    """Every file in the repository as it stands, ignored ones excluded.

    `--cached --others --exclude-standard` is tracked files plus the ones that
    exist but are not committed yet, minus everything `.gitignore` covers. The
    middle group is the one that matters: a file written five minutes ago and
    not yet added is exactly where a new Russian comment appears, and checking
    only `ls-files` would wave it through until CI — which is late, and on a
    branch that already looks finished.
    """
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others",
             "--exclude-standard"],
            cwd=ROOT, capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return walked_files()
    names = out.stdout.decode("utf-8", "replace").split("\0")
    return sorted({ROOT / name for name in names
                   if name and (ROOT / name).is_file()})


def walked_files() -> list[Path]:
    import os

    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIRS and not d.endswith(".egg-info")]
        for name in filenames:
            found.append(Path(dirpath) / name)
    return found


def is_translation(relative: str) -> bool:
    from fnmatch import fnmatch

    name = relative.rsplit("/", 1)[-1]
    return any(fnmatch(name, pattern) for pattern in TRANSLATIONS)


def scan() -> dict[str, int]:
    """Every file with Russian in it, as `relative path -> line count`."""
    out: dict[str, int] = {}
    for path in tracked_files():
        relative = path.relative_to(ROOT).as_posix()
        if is_translation(relative):
            continue
        count = count_lines(path)
        if count:
            out[relative] = count
    return out


def read_debt() -> dict[str, int]:
    if not DEBT.exists():
        return {}
    debt: dict[str, int] = {}
    for number, line in enumerate(DEBT.read_text("utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        count, _, path = line.partition("\t")
        if not path:
            count, _, path = line.partition(" ")
        try:
            debt[path.strip()] = int(count)
        except ValueError:
            raise SystemExit(
                f"{DEBT}:{number}: expected '<count><tab><path>', "
                f"got {line!r}") from None
    return debt


def write_debt(current: dict[str, int]) -> None:
    total = sum(current.values())
    header = [
        "# Lines of Russian text still in this repository, by file.",
        "#",
        "# Written by scripts/check_i18n.py, which fails when a file grows past",
        "# its number here or when a file that is not listed acquires any. The",
        "# list is a debt, so entries are expected to shrink and disappear;",
        "# `python scripts/check_i18n.py --update` records that after a file is",
        "# translated.",
        "#",
        f"# {total} lines in {len(current)} files.",
        "",
    ]
    body = [f"{count}\t{path}" for path, count in sorted(current.items())]
    DEBT.write_text("\n".join([*header, *body, ""]), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--update", action="store_true",
                        help="record the current state as the debt")
    parser.add_argument("--list", action="store_true",
                        help="show what is left, biggest first")
    args = parser.parse_args()

    current = scan()

    if args.update:
        write_debt(current)
        print(f"recorded {sum(current.values())} lines in {len(current)} files "
              f"-> {DEBT.relative_to(ROOT)}")
        return 0

    if args.list:
        for path, count in sorted(current.items(), key=lambda kv: -kv[1]):
            print(f"{count:>6}  {path}")
        print(f"{sum(current.values()):>6}  TOTAL in {len(current)} files")
        return 0

    debt = read_debt()
    if not debt:
        print(f"{DEBT.relative_to(ROOT)} is empty or missing. Create it with:\n"
              f"    python scripts/check_i18n.py --update")
        return 1

    new_files = sorted(set(current) - set(debt))
    grown = sorted((path, debt[path], current[path]) for path in current
                   if path in debt and current[path] > debt[path])
    shrunk = sorted((path, debt[path], current.get(path, 0)) for path in debt
                    if current.get(path, 0) < debt[path])

    for path in new_files:
        print(f"NEW    {path}: {current[path]} Russian line(s) in a file that "
              "had none")
    for path, was, now in grown:
        print(f"GREW   {path}: {was} -> {now} Russian lines")

    if new_files or grown:
        print()
        print("The repository is going out in English and this is the ratchet "
              "that keeps new Russian from arriving.")
        print("Write the new comment or message in English, or — if the file "
              "is being translated — run:")
        print("    python scripts/check_i18n.py --update")
        return 1

    if shrunk:
        gone = sum(was - now for _, was, now in shrunk)
        print(f"{gone} line(s) of Russian went away in {len(shrunk)} file(s). "
              "Record it with:")
        print("    python scripts/check_i18n.py --update")

    print(f"ok: {sum(current.values())} Russian lines in {len(current)} files, "
          "none of them new")
    return 0


if __name__ == "__main__":
    sys.exit(main())
