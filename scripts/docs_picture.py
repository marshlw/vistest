# VisTest - visual regression testing for Python and Playwright.
# Copyright 2026 Kirill Kulagin
# SPDX-License-Identifier: Apache-2.0
#
# This file is part of VisTest, licensed under the Apache License, Version 2.0.
# You may not use it except in compliance with the License. See LICENSE for
# the terms and NOTICE for attribution and the trademark.

"""Take the README's picture of a failed check again, from a real run.

    python scripts/docs_picture.py

The README shows one failure: its diff picture (`docs/img/failure.png`) and
its message (`docs/img/failure.txt`). Neither is drawn by hand. This script
makes a project the way a reader of the README would — `examples/test_library.py`
and `examples/demo_page.html` copied under `tests/` of an empty directory —
and runs pytest in it with the installed `vistest`:

1. `--vistest-update`: the baseline of `landing.png`;
2. one change in the demo page's style sheet — the Team plan's button,
   `#3478f6` → `#1d4ed8` (`CHANGE` below);
3. the same test again: red. Its diff picture is copied to
   `docs/img/failure.png` byte for byte, and the message, with the temporary
   project's path taken out (as the README says, «paths are shortened to the
   project's root»), to `docs/img/failure.txt`.

Only `test_landing_page` runs. It needs Playwright and its Chromium; the
picture depends on the browser build and the fonts of the machine, so take it
again when the README's run changes (`tests/test_readme_top.py` checks that
the README quotes `failure.txt` word for word). Two runs on one machine give
the same bytes.

`--out DIR` writes the two files elsewhere (to compare a new take with the
committed one).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
OUT = ROOT / "docs" / "img"
TEST = "test_landing_page"
SNAPSHOT = "landing.png"

#  The change: one button's fill. Inserted before the end of the style sheet.
CHANGE = "  [data-testid=cta-team]{background:#1d4ed8}\n"
ANCHOR = "</style>"


class Failed(RuntimeError):
    pass


def _pytest(project: Path, *extra: str) -> tuple[int, str, list[str]]:
    """Run the example's landing test; the exit code, the output, the failure messages."""
    report = project / "junit.xml"
    report.unlink(missing_ok=True)
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    #  The picture must come from this run, not from a setting of the machine.
    for name in list(env):
        if name.startswith("VISTEST_"):
            del env[name]
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_library.py", "-k", TEST, "-q",
         "-p", "no:cacheprovider", f"--junitxml={report}", *extra],
        cwd=project, env=env, capture_output=True, text=True, encoding="utf-8",
        timeout=600)
    out = (done.stdout or "") + (done.stderr or "")
    if not report.is_file():
        raise Failed(f"pytest wrote no report (exit {done.returncode}):\n{out[-1500:]}")
    messages = [_message(f.text or "") for f in ET.parse(report).iter("failure")]
    return done.returncode, out, messages


def _message(longrepr: str) -> str:
    """The exception's whole text out of a JUnit failure's body.

    The `message` attribute is cut short by pytest (9 and later) after a few
    lines; the body has the whole report, the exception's lines prefixed with
    `E` and the indentation of the source line they are under."""
    block: list[str] = []
    for line in longrepr.splitlines():
        if line.startswith("E "):
            block.append(line)
        elif block and line.strip():
            break
    if not block:
        return ""
    #  «E» and the spaces up to the first character of the exception's name.
    indent = len(block[0]) - len(block[0][1:].lstrip())
    return "\n".join(line[indent:].rstrip() for line in block)


def take(project: Path, out: Path) -> tuple[Path, Path]:
    """Make the project in `project`, run it, write the picture and the text to `out`."""
    tests = project / "tests"
    tests.mkdir(parents=True)
    for name in ("test_library.py", "demo_page.html"):
        shutil.copy2(EXAMPLES / name, tests / name)
    (project / "pyproject.toml").write_text(
        '[project]\nname = "docs-picture"\nversion = "0"\n\n'
        "[tool.pytest.ini_options]\n", encoding="utf-8")

    code, said, _ = _pytest(project, "--vistest-update")
    if code != 0:
        raise Failed(f"--vistest-update did not pass (exit {code}):\n{said[-1500:]}")

    page = tests / "demo_page.html"
    html = page.read_text("utf-8")
    if html.count(ANCHOR) != 1:
        raise Failed(f"demo_page.html has {html.count(ANCHOR)} «{ANCHOR}», expected one")
    page.write_text(html.replace(ANCHOR, CHANGE + ANCHOR), encoding="utf-8")

    code, said, messages = _pytest(project)
    if code != 1 or len(messages) != 1:
        raise Failed(f"the changed page did not fail once (exit {code}):\n{said[-1500:]}")
    message = messages[0]
    prefix = "vistest.library.errors.ScreenshotMismatch: "
    if not message.startswith(prefix):
        raise Failed(f"not a ScreenshotMismatch: {message[:300]}")
    message = message[len(prefix):]
    #  The paths, shortened to the project's root, as the README prints them.
    for spelled in {str(project.resolve()), str(project)}:
        message = message.replace(spelled + os.sep, "").replace(spelled + "/", "")

    diffs = sorted((project / ".vistest" / "diff").rglob(SNAPSHOT))
    if len(diffs) != 1:
        raise Failed(f"expected one diff picture of {SNAPSHOT}, found {len(diffs)}")
    out.mkdir(parents=True, exist_ok=True)
    picture, text = out / "failure.png", out / "failure.txt"
    shutil.copyfile(diffs[0], picture)
    text.write_text(message.rstrip("\n") + "\n", encoding="utf-8", newline="\n")
    return picture, text


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=OUT,
                    help="where failure.png and failure.txt go "
                         f"(default {OUT.relative_to(ROOT)})")
    args = ap.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="vistest-docs-picture-") as tmp:
        try:
            picture, text = take(Path(tmp) / "project", args.out)
        except Failed as e:
            print(f"docs_picture: {e}", file=sys.stderr)
            return 1
    print(f"written: {picture} ({picture.stat().st_size} bytes), {text}")
    print(text.read_text("utf-8"), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
