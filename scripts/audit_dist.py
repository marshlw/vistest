# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""What is inside the built sdist and wheel, and what must not be.

    python scripts/audit_dist.py dist/ [--forbid TERM ...] [--forbid-file FILE]

Reads `dist/*.tar.gz` and `dist/*.whl` the way a download of them would be
read, without installing anything: lists every file, checks that what the
package needs at run time is inside, that the entry points are there, and
searches every byte of both archives for what a public index must not carry —
private keys, tokens, `.env` files, absolute paths of a developer's machine,
pictures in the wheel — and for the terms it is given.

The terms are an argument and not part of this file on purpose: a list of the
names that must not appear, committed to the repository, would be where they
appear. `--forbid-file` reads one term per line (case-insensitive); so does
the `VISTEST_AUDIT_FORBID` environment variable, comma-separated.

Exit status 0 when nothing forbidden was found and nothing required is missing.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tarfile
import zipfile
from pathlib import Path

#: What the wheel must carry for the installed package to work.
REQUIRED_IN_WHEEL = (
    "vistest/library/fonts/canary-sans.ttf",    # the renderer's fingerprint
    "vistest/bench/playwright_compare.mjs",     # `vistest bench`
    "vistest/ai/region_gate.json",              # the region gate's model
    "vistest/frontend/index.html",              # the review UI, `/ui/`
)
REQUIRED_GLOBS = (("vistest/bench/", ".mjs"), ("vistest/ai/", ".json"),
                  ("vistest/library/fonts/", ""))

ENTRY_POINTS = (
    ("console_scripts", "vistest = vistest.cli:main"),
    ("pytest11", "vistest = vistest.pytest_plugin"),
)

#: File names that do not belong on a public index, whichever archive.
FORBIDDEN_NAMES = (
    (re.compile(r"(^|/)\.env($|\.)"), ".env file"),
    (re.compile(r"(^|/)_private(/|$)"), "_private/"),
    (re.compile(r"\.(pem|key|p12|pfx|jks|keystore)$", re.I), "key file"),
    (re.compile(r"(^|/)id_(rsa|ed25519|ecdsa|dsa)(\.pub)?$"), "ssh key"),
    (re.compile(r"(^|/)\.pypirc$"), ".pypirc"),
    (re.compile(r"(^|/)\.vistest(/|$)"), ".vistest/ (a run's artifacts)"),
    (re.compile(r"(^|/)__vistest__(/|$)"), "__vistest__/ (baselines)"),
    (re.compile(r"(^|/)(\.git|\.venv|node_modules|\.idea)(/|$)"), "tooling directory"),
    (re.compile(r"\.(sqlite3?|db)$", re.I), "database file"),
)
#: Pictures: none at all in the wheel; the sdist may carry none either.
IMAGE = re.compile(r"\.(png|jpe?g|gif|webp|bmp|tiff?)$", re.I)
#: In the wheel, only the package.
WHEEL_OUTSIDE = re.compile(r"^(tests|examples|docker|clients|scripts|docs|corpus)/")

#: Content a public archive must not have, whatever the file.
FORBIDDEN_CONTENT = (
    (re.compile(rb"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY(?: BLOCK)?-----"),
     "private key block"),
    (re.compile(rb"\bpypi-AgE[A-Za-z0-9_\-]{20,}"), "PyPI token"),
    (re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{30,}"), "GitHub token"),
    (re.compile(rb"\bglpat-[A-Za-z0-9_\-]{15,}"), "GitLab token"),
    (re.compile(rb"\bAKIA[0-9A-Z]{16}\b"), "AWS access key id"),
    (re.compile(rb"\bxox[abpr]-[A-Za-z0-9\-]{10,}"), "Slack token"),
    (re.compile(rb"\bsk-[A-Za-z0-9]{32,}"), "API key (sk-)"),
    (re.compile(rb"(?i)\b(?:password|passwd|secret|api[_-]?key|token)\s*[:=]\s*"
                rb"['\"][^'\"\s]{12,}['\"]"),
     "a quoted secret assigned to a name"),
    (re.compile(rb"(?<![A-Za-z])[A-Z]:\\{1,2}(?:Users|project|work|src)\\{1,2}(?!\\)(?!\.\.\.|my-|<|your|example|path)[^\s\"']{3,}"),
     "an absolute Windows path of a developer machine"),
    (re.compile(rb"/(?:home|Users)/[a-z][a-z0-9_.-]{2,}/(?!\.\.)[^\s\"']{3,}"),
     "an absolute home path of a developer machine"),
)
#: Placeholder users that appear in documentation, not in somebody's tree.
PATH_OK = re.compile(rb"/(?:home|Users)/(?:user|you|me|name|runner|alice|bob|example|"
                     rb"username|vistest|app|your[-_a-z]*|<[^>]*>)/")


def _read_sdist(path: Path) -> dict[str, bytes]:
    out = {}
    with tarfile.open(path) as tar:
        for m in tar.getmembers():
            if m.isfile():
                out[m.name.split("/", 1)[1] if "/" in m.name else m.name] = \
                    tar.extractfile(m).read()                        # type: ignore[union-attr]
    return out


def _read_wheel(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as z:
        return {n: z.read(n) for n in z.namelist() if not n.endswith("/")}


def _terms(args) -> list[str]:
    terms = list(args.forbid or [])
    if args.forbid_file:
        terms += [t.strip() for t in Path(args.forbid_file).read_text("utf-8").splitlines()
                  if t.strip() and not t.startswith("#")]
    terms += [t.strip() for t in os.environ.get("VISTEST_AUDIT_FORBID", "").split(",")
              if t.strip()]
    return sorted(set(terms), key=str.lower)


def _scan(label: str, files: dict[str, bytes], terms: list[str]) -> list[str]:
    bad: list[str] = []
    for name in sorted(files):
        for rx, what in FORBIDDEN_NAMES:
            if rx.search(name):
                bad.append(f"{label}: {name}: {what}")
        data = files[name]
        for rx, what in FORBIDDEN_CONTENT:
            for m in rx.finditer(data):
                if what.startswith("an absolute home path") and PATH_OK.match(
                        data[m.start():m.start() + 80]):
                    continue
                line = data.count(b"\n", 0, m.start()) + 1
                bad.append(f"{label}: {name}:{line}: {what}")
                break
        low = data.lower()
        for t in terms:
            n = low.count(t.lower().encode("utf-8"))
            if n:
                bad.append(f"{label}: {name}: the term {t!r} x{n}")
        #  Names of files are searched for the terms too.
        for t in terms:
            if t.lower() in name.lower():
                bad.append(f"{label}: {name}: the term {t!r} in the file name")
    return bad


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dist", type=Path, help="directory with the sdist and the wheel")
    ap.add_argument("--forbid", action="append", metavar="TERM",
                    help="a term that must not appear (repeatable)")
    ap.add_argument("--forbid-file", metavar="FILE")
    ap.add_argument("--list", action="store_true", help="print every file of both archives")
    args = ap.parse_args(argv)

    sdists = sorted(args.dist.glob("*.tar.gz"))
    wheels = sorted(args.dist.glob("*.whl"))
    if len(sdists) != 1 or len(wheels) != 1:
        print(f"expected one sdist and one wheel in {args.dist}, found "
              f"{len(sdists)} and {len(wheels)}", file=sys.stderr)
        return 2
    sdist, wheel = _read_sdist(sdists[0]), _read_wheel(wheels[0])
    terms = _terms(args)
    problems: list[str] = []

    print(f"sdist  {sdists[0].name}: {len(sdist)} files, "
          f"{sum(map(len, sdist.values())) // 1024} KiB unpacked")
    print(f"wheel  {wheels[0].name}: {len(wheel)} files, "
          f"{sum(map(len, wheel.values())) // 1024} KiB unpacked")
    print(f"terms searched for: {len(terms)}"
          + ("" if terms else "  (none given: only the generic patterns ran)"))

    if args.list:
        for label, files in (("SDIST", sdist), ("WHEEL", wheel)):
            print(f"\n== {label} ==")
            for n in sorted(files):
                print(f"{len(files[n]):>9}  {n}")

    # ---- required ---------------------------------------------------------- #
    print("\n== required in the wheel ==")
    for need in REQUIRED_IN_WHEEL:
        ok = need in wheel
        print(f"  {'ok     ' if ok else 'MISSING'} {need}")
        if not ok:
            problems.append(f"wheel: required file missing: {need}")
    for prefix, suffix in REQUIRED_GLOBS:
        got = [n for n in wheel if n.startswith(prefix) and n.endswith(suffix)
               and not n.endswith(".py")]
        print(f"  {'ok     ' if got else 'MISSING'} {prefix}*{suffix}: {len(got)}")
        if not got:
            problems.append(f"wheel: nothing matches {prefix}*{suffix}")
    entry = next((v for k, v in wheel.items() if k.endswith(".dist-info/entry_points.txt")),
                 b"").decode()
    section = None
    seen: dict[str, set[str]] = {}
    for line in entry.splitlines():
        line = line.strip()
        if line.startswith("["):
            section = line.strip("[]")
        elif line and section:
            seen.setdefault(section, set()).add(line)
    for sec, line in ENTRY_POINTS:
        ok = line in seen.get(sec, set())
        print(f"  {'ok     ' if ok else 'MISSING'} entry point [{sec}] {line}")
        if not ok:
            problems.append(f"wheel: entry point [{sec}] {line} missing")
    for need in ("LICENSE", "NOTICE", "README.md", "pyproject.toml"):
        ok = need in sdist
        print(f"  {'ok     ' if ok else 'MISSING'} sdist {need}")
        if not ok:
            problems.append(f"sdist: {need} missing")

    # ---- forbidden --------------------------------------------------------- #
    print("\n== must not be inside ==")
    for n in sorted(wheel):
        if IMAGE.search(n):
            problems.append(f"wheel: {n}: a picture")
        if WHEEL_OUTSIDE.match(n):
            problems.append(f"wheel: {n}: outside the package")
    for n in sorted(sdist):
        if IMAGE.search(n):
            problems.append(f"sdist: {n}: a picture")
    problems += _scan("wheel", wheel, terms)
    problems += _scan("sdist", sdist, terms)
    if problems:
        for p in dict.fromkeys(problems):
            print("  FOUND  " + p)
    else:
        print("  nothing found: no key or token patterns, no .env or key files, no "
              "developer paths, no pictures, nothing outside the package in the wheel"
              + (", none of the given terms" if terms else ""))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
