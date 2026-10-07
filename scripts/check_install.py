# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Does the package install and work on this machine — the way a user gets it.

One script for Linux, macOS and Windows, standard library only. In a fresh
virtual environment, with nothing from this checkout on the path:

    1. install vistest — from a wheel, or from an index;
    2. `playwright install chromium`;
    3. `vistest --help` and `vistest bench --help`;
    4. `import vistest` — from the environment, not from a source tree — and
       `vistest.__version__` is the version that was installed;
    5. the pytest plugin is registered (`--vistest-update` is a pytest option);
    6. `examples/test_library.py` — `expect_screenshot`, the library — laid out
       the way a project has it (`pyproject.toml`, `tests/`), four times:
       without baselines it is red with `BaselineMissing`; `--vistest-update`
       writes them to `tests/__vistest__/`; then it is green and leaves them
       as they were; after a change on the demo page it is red with
       `ScreenshotMismatch`, and still leaves them as they were.

From a wheel (a branch, before anything is uploaded):

    python scripts/check_install.py --wheel dist/vistest-0.2.0.dev1-py3-none-any.whl

From TestPyPI (dependencies come from the real index):

    python scripts/check_install.py --version 0.2.0.dev1 \\
        --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/

`--examples` is the `examples/` directory of a checkout (the wheel does not
carry it); the default is the one next to this script's repository.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
EXAMPLE = "test_library.py"
#  The page the example photographs, and the one change to it that the fourth
#  run must see: the prices' size, in the page's own style sheet — every one of
#  the example's pictures has the prices in it.
DEMO_PAGE = "demo_page.html"
DEMO_CHANGE = (".price{font-size:34px;", ".price{font-size:30px;")
#  What is installed besides the package: nothing. The example's `page` fixture
#  comes from pytest-playwright, and the `browser` extra brings it — the check
#  is of the install command the README gives.
EXTRA_PACKAGES: list[str] = []


class Failed(Exception):
    pass


def run(cmd, *, cwd=None, env=None, timeout=900) -> subprocess.CompletedProcess:
    return subprocess.run([str(c) for c in cmd], cwd=cwd, env=env, text=True,
                          encoding="utf-8", errors="replace", capture_output=True,
                          timeout=timeout)


def step(name: str):
    print(f"\n[{name}]", flush=True)


def must(done: subprocess.CompletedProcess, what: str) -> str:
    out = (done.stdout or "") + (done.stderr or "")
    if done.returncode != 0:
        tail = "\n".join(out.strip().splitlines()[-25:])
        raise Failed(f"{what}: exit {done.returncode}\n{tail}")
    return out


def tree_hash(root: Path) -> dict[str, str]:
    """Every baseline picture and passport under `root`, by relative path: its hash.

    The library's layout: `<platform>/<name>.png` next to `<name>.json`, and
    the renderers' canaries under `.renderers/`.
    """
    return {str(p.relative_to(root)).replace("\\", "/"):
            hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def junit_cases(path: Path) -> list[tuple[str, str, str]]:
    """(test, outcome, exception) for every test case of a pytest JUnit XML report.

    `outcome` is passed, failed, error or skipped; `exception` the qualified
    name of what a failed test raised (`vistest.library.errors.BaselineMissing`),
    '' otherwise. The outcomes are read from the report, not from pytest's
    terminal output: how often a message appears there depends on the width
    of the terminal — on a wide one the short summary repeats it in full —
    and counting lines of text counted that width (review A2).
    """
    import xml.etree.ElementTree as ET

    cases = []
    for case in ET.parse(path).getroot().iter("testcase"):
        outcome, exception = "passed", ""
        for kind in ("failure", "error", "skipped"):
            found = case.find(kind)
            if found is None:
                continue
            outcome = {"failure": "failed", "error": "error", "skipped": "skipped"}[kind]
            if kind != "skipped":
                exception = (found.get("message") or "").split(":", 1)[0].strip()
            break
        cases.append((case.get("name") or "?", outcome, exception))
    return cases


def example_outcomes(py, examples: Path, project: Path, env: dict) -> None:
    """Step 6: the library's example, as a project has it, four times.

    Red with `BaselineMissing` for every test and nothing written;
    `--vistest-update` green, one baseline per test under `tests/__vistest__/`;
    green with the baselines untouched; after a change in the demo page's style
    sheet, red with `ScreenshotMismatch` for every test, baselines untouched.
    Raises `Failed` with the run's last lines when an outcome is not that.
    """
    if project.exists():
        shutil.rmtree(project)
    tests = project / "tests"
    tests.mkdir(parents=True)
    for name in (EXAMPLE, DEMO_PAGE):
        shutil.copy2(examples / name, tests / name)
    #  A project has a configuration file, and that makes its root pytest's
    #  rootdir: the baselines then go where the README says they go.
    (project / "pyproject.toml").write_text(
        '[project]\nname = "check-install"\nversion = "0"\n\n'
        "[tool.pytest.ini_options]\n", encoding="utf-8")
    base = tests / "__vistest__"
    report = project / "junit.xml"

    def pictures() -> list[Path]:
        return sorted(p for p in base.rglob("*.png") if ".renderers" not in p.parts) \
            if base.is_dir() else []

    def ran(*extra: str) -> tuple[list[tuple[str, str, str]], str]:
        report.unlink(missing_ok=True)
        done = run([py, "-m", "pytest", f"tests/{EXAMPLE}", "-q", "-p", "no:cacheprovider",
                    f"--junitxml={report}", *extra], cwd=project, env=env, timeout=900)
        out = (done.stdout or "") + (done.stderr or "")
        if not report.is_file():
            raise Failed(f"pytest wrote no report (exit {done.returncode}):\n{out[-800:]}")
        return junit_cases(report), out

    def each(cases, outcome: str, exception: str = "") -> bool:
        return bool(cases) and all(o == outcome and (not exception or e.endswith(exception))
                                   for _, o, e in cases)

    def counted(cases) -> str:
        tally: dict[str, int] = {}
        for _, outcome, exception in cases:
            key = outcome + (f" ({exception.rsplit('.', 1)[-1]})" if exception else "")
            tally[key] = tally.get(key, 0) + 1
        return ", ".join(f"{n} {k}" for k, n in tally.items())

    cases, out = ran()
    if not each(cases, "failed", "BaselineMissing"):
        raise Failed("the first run, with no baselines, was not red with BaselineMissing "
                     f"for every test ({counted(cases)}):\n{out[-800:]}")
    if pictures():
        raise Failed("the first run wrote baselines without --vistest-update: "
                     + ", ".join(str(p) for p in pictures()[:5]))
    tests_n = len(cases)
    print(f"  1. no baselines    : {counted(cases)}")

    cases, out = ran("--vistest-update")
    if not each(cases, "passed"):
        raise Failed(f"--vistest-update did not end green ({counted(cases)}):\n{out[-800:]}")
    written = pictures()
    if len(written) != tests_n:
        raise Failed(f"--vistest-update wrote {len(written)} baselines under {base}, "
                     f"expected {tests_n}, one per test:\n{out[-800:]}")
    said = re.search(r"baselines written to (.+?) — commit them", out)
    if not said or Path(said.group(1)).resolve() != base.resolve():
        raise Failed(f"the run did not say it wrote the baselines to {base}:\n" + out[-800:])
    before = tree_hash(base)
    shown = ", ".join(str(p.relative_to(project)).replace("\\", "/") for p in written)
    print(f"  2. --vistest-update: {counted(cases)} — {shown}")

    cases, out = ran()
    if not each(cases, "passed"):
        raise Failed(f"the run after --vistest-update did not end green ({counted(cases)}):"
                     f"\n{out[-800:]}")
    if tree_hash(base) != before:
        raise Failed("the green run changed the baselines it compared with")
    print(f"  3. compared        : {counted(cases)} — baselines untouched")

    page = tests / DEMO_PAGE
    html = page.read_text(encoding="utf-8")
    old, new = DEMO_CHANGE
    if old not in html:
        raise Failed(f"{DEMO_PAGE} no longer has {old!r} to change")
    page.write_text(html.replace(old, new), encoding="utf-8")
    cases, out = ran()
    if not each(cases, "failed", "ScreenshotMismatch"):
        raise Failed("a changed page did not turn every test red with ScreenshotMismatch "
                     f"({counted(cases)}):\n{out[-800:]}")
    if tree_hash(base) != before:
        raise Failed("the red run changed the baselines it compared with")
    print(f"  4. page changed    : {counted(cases)} — baselines untouched")


def wheel_version(path: Path) -> str | None:
    m = re.match(r"[A-Za-z0-9_.]+?-([0-9][^-]*)-", path.name)
    return m.group(1) if m else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--wheel", type=Path, help="path to the wheel to install")
    src.add_argument("--version", help="version to install from an index")
    ap.add_argument("--index-url", help="index for --version (TestPyPI: .../simple/)")
    ap.add_argument("--extra-index-url", help="where the dependencies come from")
    ap.add_argument("--extra", default="browser",
                    help="extras to install, comma-separated (default: browser)")
    ap.add_argument("--examples", type=Path, default=HERE / "examples")
    ap.add_argument("--with-deps", action="store_true",
                    help="playwright install --with-deps (a Linux runner needs it)")
    ap.add_argument("--skip-browser-install", action="store_true",
                    help="use browsers that are already there (PLAYWRIGHT_BROWSERS_PATH)")
    ap.add_argument("--pip-arg", action="append", default=[], metavar="ARG",
                    help="an extra argument for every pip install (repeatable)")
    ap.add_argument("--workdir", type=Path, help="keep everything here (default: temporary)")
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args(argv)

    expected = wheel_version(args.wheel) if args.wheel else args.version
    if args.wheel and not args.wheel.is_file():
        print(f"no such wheel: {args.wheel}", file=sys.stderr)
        return 2
    if not (args.examples / EXAMPLE).is_file():
        print(f"no {EXAMPLE} in {args.examples}", file=sys.stderr)
        return 2

    work = args.workdir or Path(tempfile.mkdtemp(prefix="vistest-install-"))
    work.mkdir(parents=True, exist_ok=True)
    started = time.time()
    print(f"python {sys.version.split()[0]} on {sys.platform}; working in {work}")
    env = {**os.environ, "PYTHONUTF8": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1",
           "PYTHONDONTWRITEBYTECODE": "1"}
    for var in ("PYTHONPATH", "VIRTUAL_ENV"):
        env.pop(var, None)
    try:
        venv = work / "venv"
        step("1. a clean virtual environment, and the package installed into it")
        must(run([sys.executable, "-m", "venv", venv]), "creating the environment")
        bindir = venv / ("Scripts" if os.name == "nt" else "bin")
        exe = ".exe" if os.name == "nt" else ""
        py, vistest, pw = bindir / f"python{exe}", bindir / f"vistest{exe}", \
            bindir / f"playwright{exe}"
        extras = f"[{args.extra}]" if args.extra else ""
        if args.wheel:
            target = [f"{args.wheel.resolve()}{extras}"]
        else:
            target = [f"vistest{extras}=={args.version}"]
        pip = [py, "-m", "pip", "install", "--no-cache-dir", *args.pip_arg]
        if args.version and args.index_url:
            pip += ["--index-url", args.index_url]
            if args.extra_index_url:
                pip += ["--extra-index-url", args.extra_index_url]
        out = must(run([*pip, *target, *EXTRA_PACKAGES], env=env), "pip install")
        done = [ln for ln in out.splitlines() if ln.startswith("Successfully")]
        print("  installed:", (done[-1] if done else "(pip said nothing)")[:300])

        step("2. playwright install chromium")
        if args.skip_browser_install:
            print("  skipped (--skip-browser-install): using the browsers already present")
        else:
            cmd = [pw, "install", "chromium"] + (["--with-deps"] if args.with_deps else [])
            must(run(cmd, env=env, timeout=1500), "playwright install chromium")
            print("  ok")

        step("3. the command")
        must(run([vistest, "--help"], env=env), "vistest --help")
        print("  vistest --help: ok")
        must(run([vistest, "bench", "--help"], env=env), "vistest bench --help")
        print("  vistest bench --help: ok")

        step("4. the package, and where it was imported from")
        empty = work / "empty"
        empty.mkdir(exist_ok=True)
        got = must(run([py, "-c", "import vistest, sys; print(vistest.__version__); "
                        "print(vistest.__file__)"], cwd=empty, env=env),
                   "import vistest").strip().splitlines()
        version, where = got[0], Path(got[1]).resolve()
        print(f"  vistest.__version__ = {version}")
        print(f"  imported from {where}")
        if expected and version != expected:
            raise Failed(f"installed {version}, expected {expected}")
        if venv.resolve() not in where.parents:
            raise Failed("vistest was imported from outside the environment")

        step("5. the pytest plugin")
        helped = must(run([py, "-m", "pytest", "--help"], cwd=empty, env=env), "pytest --help")
        if "--vistest-update" not in helped:
            raise Failed("pytest does not know --vistest-update: the plugin is not registered")
        print("  --vistest-update is a pytest option: the pytest11 entry point works")

        step(f"6. examples/{EXAMPLE}: red, --vistest-update, green, changed page red")
        example_outcomes(py, args.examples, work / "project", env)
    except Failed as e:
        print(f"\nFAILED  {e}", file=sys.stderr)
        return 1
    except subprocess.TimeoutExpired as e:
        print(f"\nFAILED  timed out: {e.cmd}", file=sys.stderr)
        return 1
    finally:
        if not (args.keep or args.workdir):
            shutil.rmtree(work, ignore_errors=True)
    print(f"\nOK  vistest {version} installs and works on {sys.platform} "
          f"({int(time.time() - started)} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
