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
    6. an example from `examples/`, twice: the first run writes the baselines,
       the second compares against them and must pass without touching them.

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
EXAMPLE = "test_demo_visual.py"
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
    """Every baseline under `root`, by relative path: its hash.

    `baseline.png` only: next to it every run rewrites `stability.png`, the map
    of what moved between the frames of that run — which depends on the load of
    the machine and is not what the second run compares with.
    """
    return {str(p.relative_to(root)).replace("\\", "/"):
            hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("baseline.png"))}


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

        step(f"6. examples/{EXAMPLE}, twice")
        project = work / "project"
        if project.exists():
            shutil.rmtree(project)
        shutil.copytree(args.examples, project / "examples",
                        ignore=shutil.ignore_patterns("__pycache__", ".vistest"))
        pytest = [py, "-m", "pytest", f"examples/{EXAMPLE}", "-q", "-p", "no:cacheprovider"]
        first = must(run([*pytest, "--vistest-update"], cwd=project, env=env, timeout=900),
                     "the first run (writes the baselines)")
        base = project / ".vistest" / "baselines"
        shots = list(base.rglob("baseline.png")) if base.is_dir() else []
        if not shots:
            raise Failed("the first run wrote no baselines under .vistest/baselines")
        before = tree_hash(base)
        print("  first run :", first.strip().splitlines()[-1],
              f"- {len(shots)} baselines written")
        second = must(run(pytest, cwd=project, env=env, timeout=900),
                      "the second run (compares)")
        print("  second run:", second.strip().splitlines()[-1])
        after = tree_hash(base)
        if after != before:
            names = sorted(n for n in {*before, *after} if before.get(n) != after.get(n))
            raise Failed("the second run changed the baselines it was meant to compare "
                         "with: " + ", ".join(names[:10]))
        if " passed" not in second or " failed" in second:
            raise Failed("the second run did not end green:\n" + second[-600:])
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
