# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Connecting existing test suites.

The problem. A team already has visual tests: their own `conftest`, their own
page objects, their own baselines in git, their own way of comparing. Nobody
will rewrite all that for the sake of a new tool -- and rightly so. That means
we have to connect to what already exists.

The key decision: **someone else's code is not parsed.** Trying to understand
the steps of a foreign test statically is hopeless -- it is arbitrary Python,
fixtures, inheritance, conditions. Instead, their pytest is run as is, and
VisTest wedges into exactly one point -- the place where a screenshot is
compared with the baseline. Everything else (login, navigation, waits,
markers, parametrization) keeps working through their own code, and we do not
need to know anything about it.

Two modes:

* **adapter** -- the project description specifies a comparison point, for
  example `UiTests.pages.base_page.BasePage.assert_screenshot`. The plugin
  replaces it: the snapshot is taken by their own driver, while the VisTest
  engine does the comparison. The full picture: severity, regions, change
  classes, DOM attribution, artifacts;
* **observe** -- no point is specified. Their tests are run as is, and then
  the «baseline / actual» pairs lying on disk are matched up. Poorer, but it
  connects without any knowledge of the foreign code at all.

The project registry is stored in `.vistest/projects.yaml` -- plain text that
can be committed to git and shared with the team.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Method names that test suites usually use for comparison with the baseline.
# They are used only as a hint during auto-detection -- the decision always
# stays with a human, and it is written into the config explicitly.
SCREENSHOT_METHOD_HINTS = (
    "assert_screenshot", "check_screenshot", "compare_screenshot",
    "assert_snapshot", "match_screenshot", "verify_screenshot",
)

BASELINE_DIR_HINTS = (
    "snapshots", "snapshots_ci", "baseline", "baselines", "screenshots",
    "__screenshots__", "expected", "reference",
)

ACTUAL_PREFIXES = ("actual_", "actual-", "current_", "received_", "test_")
DIFF_PREFIXES = ("diff_", "diff-", "combined_", "combined-")


# --------------------------------------------------------------------------- #
#  Model
# --------------------------------------------------------------------------- #
@dataclass
class BaselineDir:
    """A baseline folder and the condition under which it is chosen.

    The condition is the environment variables of the run. Projects that run
    tests both locally and in a container almost always have two sets of
    baselines: font rendering on Windows and Linux is physically different,
    and a shared baseline between them is impossible. The selection rule is
    described here, not guessed.
    """

    dir: str
    when: dict[str, str] = field(default_factory=dict)
    label: str = ""

    def matches(self, env: dict[str, str]) -> bool:
        return all(str(env.get(k, "")) == str(v) for k, v in self.when.items())


@dataclass
class Adapter:
    """The point of insertion into the foreign code."""

    target: str = ""            # UiTests.pages.base_page.BasePage.assert_screenshot
    name_arg: int = 0           # position of the argument with the snapshot name
    page_attr: str = "page"     # where the page driver lives on the object
    passthrough_on_error: bool = True   # could not intercept -> call the original

    @property
    def enabled(self) -> bool:
        return bool(self.target)


@dataclass
class Project:
    key: str
    name: str = ""
    root: str = ""                       # repository root (rootdir for pytest)
    tests: str = ""                      # what to pass to pytest; empty = whole root
    python: str = ""                     # interpreter; empty = auto-detection
    pytest_args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    baselines: list[BaselineDir] = field(default_factory=list)
    adapter: Adapter = field(default_factory=Adapter)
    # Where the baselines that the VisTest engine compares against live:
    #   "project" -- the project folder, as is (an approval lands in their git)
    #   "vistest" -- our own set, taken by our capture
    baseline_store: str = "project"
    note: str = ""
    # How their tests are started.
    #
    #   "pytest"  -- <interpreter> -m pytest <tests> <pytest_args>. The only mode
    #                in which the adapter can be attached: it is a Python plugin
    #                and lives inside their process.
    #   "command" -- an arbitrary argv, run from the project root. Their stack
    #                does not have to be Python at all.
    #
    # The second one exists because the review mode never needed Python. It
    # compares the «baseline / actual» PNG pairs left on disk, and it does not
    # care who drew them — Playwright on TypeScript, Cypress, Selenium on Java,
    # a shell script. The only Python-shaped thing in that path was the hardcoded
    # `python -m pytest` in the command builder.
    runner: str = "pytest"
    command: list[str] = field(default_factory=list)

    # ---------------- paths ----------------
    @property
    def root_path(self) -> Path:
        return Path(self.root).expanduser().resolve()

    def tests_path(self) -> Path:
        return (self.root_path / self.tests) if self.tests else self.root_path

    def baseline_dir(self, env: dict[str, str] | None = None) -> Path | None:
        """Which baseline folder applies under this environment.

        The first matching rule wins -- the order in the config matters, so
        rules with conditions must be placed above the unconditional one.
        """
        merged = {**self.env, **(env or {})}
        for b in self.baselines:
            if b.matches(merged):
                return self.root_path / b.dir
        return None

    def sidecar_dir(self, cfg) -> Path:
        """VisTest service data for this project -- outside the foreign repository."""
        return cfg.root_path / "external" / self.key

    def vistest_baselines_path(self, cfg, platform: str = "") -> Path:
        """VisTest's own baseline set for this project.

        Why it is needed, if the project already has baselines. Their
        baselines were taken by their pipeline: their way of waiting, their
        screenshot parameters, their full-page assembly. Our capture works
        differently -- it freezes animations at the first render, substitutes
        the sources of time and randomness, takes a series of frames.
        Comparing a snapshot from one pipeline with a baseline from another is
        pointless: fonts, shadows, and the moment of capture will diverge, and
        the engine will honestly show discrepancies that are not in the
        application.

        That is why there is a mode in which VisTest takes the baselines
        itself. Their folder is not touched at all in this case: their own
        tests keep being compared with their own baselines their own way.

        The split by platform is mandatory here too: text rendering on Windows
        and in a container is physically different.
        """
        base = self.sidecar_dir(cfg) / "baselines"
        return base / platform if platform else base

    def uses_own_baselines(self) -> bool:
        return self.baseline_store == "vistest"

    def resolve_python(self) -> str:
        """The interpreter to run their tests with.

        By default -- **ours**. This is a deliberate decision, not a shortcut.

        A foreign virtual environment is not an asset but a liability: it may
        not exist at all, it may be built for a different Python version, it
        may not contain pytest (a common case: the venv was set up for a
        linter), and it certainly does not contain `vistest`, without which
        the adapter plugin will not connect. Demanding that the team first fix
        their venv just to try the tool is a sure way to lose the pilot.

        That is why everything runs in the VisTest environment: it already has
        pytest, Playwright and the browsers. Their code is imported as is --
        that is handled by the project root in `PYTHONPATH`, not by installing
        a package.

        Field values:
            ""          our interpreter (by default)
            "project"   the venv in their root, if one is found
            path        a specific interpreter
        """
        if self.python and self.python != "project":
            return self.python
        if self.python == "project":
            found = self.detect_venv()
            if found:
                return found
        return sys.executable

    def detect_venv(self) -> str:
        """The virtual environment in their root, if there is one there."""
        for rel in (".venv/Scripts/python.exe", ".venv/bin/python",
                    "venv/Scripts/python.exe", "venv/bin/python",
                    "env/Scripts/python.exe", "env/bin/python"):
            candidate = self.root_path / rel
            if candidate.exists():
                return str(candidate)
        return ""

    def uses_own_interpreter(self) -> bool:
        return self.resolve_python() == sys.executable

    def requirements_files(self) -> list[Path]:
        """The project's dependency files.

        They can be installed into the VisTest environment.
        """
        out = []
        for name in ("requirements.txt", "requirements-dev.txt",
                     "requirements/base.txt", "requirements/test.txt"):
            p = self.root_path / name
            if p.exists():
                out.append(p)
        return out

    def python_path_entries(self) -> list[str]:
        """What to add to PYTHONPATH so that their modules get imported.

        The repository root -- because `from UiTests.pages...` is written
        relative to it. The tests directory -- because some projects count on
        being run from inside it.
        """
        entries = [str(self.root_path)]
        tests = self.tests_path()
        if tests != self.root_path:
            entries.append(str(tests))
        return entries

    def uses_pytest(self) -> bool:
        return (self.runner or "pytest") == "pytest"

    def mode(self) -> str:
        """adapter -- comparison inside their test; observe -- ready PNG pairs.

        Interception is only possible in a Python process: the adapter is a
        pytest plugin. A project started by an arbitrary command is therefore
        always in review mode, even if a comparison point is filled in — better
        to say so by the mode than to promise DOM attribution that cannot happen.
        """
        return "adapter" if (self.adapter.enabled and self.uses_pytest()) \
            else "observe"

    # ---------------- serialization ----------------
    def to_dict(self) -> dict:
        d = asdict(self)
        d["mode"] = self.mode()
        d["uses_pytest"] = self.uses_pytest()
        d["python_resolved"] = self.resolve_python()
        d["own_interpreter"] = self.uses_own_interpreter()
        d["own_baselines"] = self.uses_own_baselines()
        d["project_venv"] = self.detect_venv()
        d["requirements"] = [str(p) for p in self.requirements_files()]
        return d

    @classmethod
    def from_dict(cls, key: str, raw: dict) -> Project:
        raw = dict(raw or {})
        baselines = [
            BaselineDir(dir=b["dir"], when=dict(b.get("when") or {}),
                        label=b.get("label", ""))
            for b in (raw.get("baselines") or []) if b.get("dir")
        ]
        adapter_raw = dict(raw.get("adapter") or {})
        return cls(
            key=key,
            name=raw.get("name") or key,
            root=raw.get("root", ""),
            tests=raw.get("tests", ""),
            python=raw.get("python", ""),
            pytest_args=list(raw.get("pytest_args") or []),
            env={str(k): str(v) for k, v in (raw.get("env") or {}).items()},
            baselines=baselines,
            adapter=Adapter(
                target=adapter_raw.get("target", ""),
                name_arg=int(adapter_raw.get("name_arg", 0)),
                page_attr=adapter_raw.get("page_attr", "page"),
                passthrough_on_error=bool(
                    adapter_raw.get("passthrough_on_error", True)),
            ),
            baseline_store=(raw.get("baseline_store") or "project").lower(),
            note=raw.get("note", ""),
            runner=(raw.get("runner") or "pytest").lower(),
            command=list(raw.get("command") or []),
        )

    def validate(self) -> list[str]:
        """What will get in the way of a run. Better to say so at once than to fail midway."""
        problems: list[str] = []
        if not self.root:
            problems.append("project root is not specified")
            return problems
        if not self.root_path.exists():
            problems.append(f"directory not found: {self.root_path}")
            return problems
        if not self.tests_path().exists():
            problems.append(f"tests directory not found: {self.tests_path()}")
        if self.baseline_store not in ("project", "vistest"):
            problems.append(
                f"unknown baseline store: {self.baseline_store!r} "
                "(project | vistest)")
        # Our own set does not need their folders -- it is captured by itself.
        if not self.uses_own_baselines():
            if not self.baselines:
                problems.append("not a single baseline folder is specified")
            for b in self.baselines:
                if not (self.root_path / b.dir).exists():
                    problems.append(f"baseline folder not found: {b.dir}")
        if self.python and self.python != "project" \
                and not Path(self.python).exists():
            problems.append(f"interpreter not found: {self.python}")
        if self.runner not in ("pytest", "command"):
            problems.append(
                f"unknown runner: {self.runner!r} (pytest | command)")
        if self.runner == "command" and not self.command:
            problems.append(
                "runner=command, but the command itself is not specified")
        return problems


# --------------------------------------------------------------------------- #
#  Registry
# --------------------------------------------------------------------------- #
class ProjectRegistry:
    def __init__(self, cfg):
        self.cfg = cfg
        self.path = cfg.root_path / "projects.yaml"

    def _load_raw(self) -> dict:
        if not self.path.exists():
            return {}
        import yaml

        data = yaml.safe_load(self.path.read_text("utf-8")) or {}
        return data.get("projects") or {}

    def list(self) -> list[Project]:
        return [Project.from_dict(k, v) for k, v in sorted(self._load_raw().items())]

    def get(self, key: str) -> Project | None:
        raw = self._load_raw().get(key)
        return Project.from_dict(key, raw) if raw else None

    def save(self, project: Project) -> Project:
        import yaml

        raw = self._load_raw()
        data = asdict(project)
        data.pop("key", None)
        raw[project.key] = data

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            "# Connected test suites.\n"
            "# The file can be committed to git: there are no paths to secrets here,\n"
            "# variable values are taken from the environment and .vistest/secrets.env.\n"
            + yaml.safe_dump({"projects": raw}, allow_unicode=True, sort_keys=True),
            encoding="utf-8")
        return project

    def delete(self, key: str) -> bool:
        import yaml

        raw = self._load_raw()
        if key not in raw:
            return False
        raw.pop(key)
        self.path.write_text(
            yaml.safe_dump({"projects": raw}, allow_unicode=True, sort_keys=True),
            encoding="utf-8")
        return True


# --------------------------------------------------------------------------- #
#  Auto-detection
# --------------------------------------------------------------------------- #
def discover(root: str | Path) -> dict:
    """Inspect the foreign repository and propose a description.

    This is precisely a proposal: everything found is shown to a human and
    edited before saving. Guessing silently in a foreign project is a bad
    idea, but sparing someone from filling in six fields by hand is a good one.
    """
    root = Path(root).expanduser().resolve()
    out: dict = {
        "root": str(root),
        "exists": root.exists(),
        "tests": "",
        "baselines": [],
        "markers": [],
        "project_venv": "",
        "requirements": [],
        "adapter_candidates": [],
        "env_vars": [],
        "docker": [],
        "notes": [],
    }
    if not root.exists():
        out["notes"].append("Directory not found")
        return out

    skip = {".git", ".venv", "venv", "env", "node_modules", "__pycache__",
            ".pytest_cache", ".idea", ".vscode", "dist", "build", ".tox"}

    py_files: list[Path] = []
    baseline_dirs: list[Path] = []
    test_dirs: set[Path] = set()

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip]
        here = Path(dirpath)
        if here.name in BASELINE_DIR_HINTS and any(
                f.lower().endswith(".png") for f in filenames):
            baseline_dirs.append(here)
        for f in filenames:
            if not f.endswith(".py"):
                continue
            p = here / f
            py_files.append(p)
            if re.match(r"(test_.*|.*_test)\.py$", f):
                test_dirs.add(here)
        if len(py_files) > 4000:            # a huge monorepo
            out["notes"].append("The project is large, the inspection is limited")
            break

    # --- baseline folders ---
    for d in sorted(baseline_dirs):
        rel = d.relative_to(root).as_posix()
        n = len(list(d.glob("*.png")))
        entry = {"dir": rel, "count": n, "when": {}, "label": ""}
        # `snapshots_ci` next to `snapshots` is almost always «baselines for
        # the container»: font rendering on Linux and Windows differs, and
        # there is no shared baseline between them.
        if re.search(r"(_ci|_docker|_linux)$", d.name):
            entry["when"] = {"CI": "true"}
            entry["label"] = "run in a container / CI"
        else:
            entry["label"] = "local run"
        out["baselines"].append(entry)
    # Conditional rules must stand above the unconditional one, otherwise the
    # unconditional one will intercept everything.
    out["baselines"].sort(key=lambda b: (not b["when"], b["dir"]))

    # --- tests directory ---
    visual = [d for d in test_dirs
              if re.search(r"screen|visual|snapshot", d.name, re.I)]
    chosen = sorted(visual or test_dirs, key=lambda p: len(p.parts))
    if chosen:
        out["tests"] = chosen[0].relative_to(root).as_posix()

    # --- environment ---
    # We find the project venv, but by default we do NOT use it: tests are run
    # by the VisTest interpreter. We show it as an alternative, in case a
    # person wants the opposite.
    for rel in (".venv/Scripts/python.exe", ".venv/bin/python",
                "venv/Scripts/python.exe", "venv/bin/python"):
        if (root / rel).exists():
            out["project_venv"] = str(root / rel)
            break
    out["requirements"] = [
        (root / n).as_posix() for n in
        ("requirements.txt", "requirements-dev.txt",
         "requirements/base.txt", "requirements/test.txt")
        if (root / n).exists()
    ]

    # --- markers, insertion points, variables ---
    method_re = re.compile(
        r"^\s*def\s+(" + "|".join(SCREENSHOT_METHOD_HINTS) + r")\s*\(", re.M)
    class_re = re.compile(r"^\s*class\s+(\w+)", re.M)
    marker_re = re.compile(r"pytest\.mark\.([a-zA-Z_]\w*)")
    env_re = re.compile(r"(?:environ(?:\.get)?\s*[\[(]\s*|getenv\s*\(\s*)"
                        r"['\"]([A-Z][A-Z0-9_]{2,})['\"]")

    markers: set[str] = set()
    env_vars: set[str] = set()

    for p in py_files:
        try:
            text = p.read_text("utf-8", errors="ignore")
        except OSError:
            continue
        markers |= set(marker_re.findall(text))
        env_vars |= set(env_re.findall(text))

        for m in method_re.finditer(text):
            cls = _enclosing_class(text, m.start(), class_re)
            dotted = _dotted_path(root, p)
            target = f"{dotted}.{cls}.{m.group(1)}" if cls else f"{dotted}.{m.group(1)}"
            out["adapter_candidates"].append({
                "target": target,
                "file": p.relative_to(root).as_posix(),
                "method": m.group(1),
                "class": cls or "",
            })

    known_noise = {"parametrize", "skip", "skipif", "xfail", "usefixtures"}
    out["markers"] = sorted(markers - known_noise)
    out["env_vars"] = sorted(env_vars)

    for name in ("Dockerfile.screenshots", "Dockerfile", "docker-compose.yml"):
        if (root / name).exists():
            out["docker"].append(name)

    if not out["baselines"]:
        out["notes"].append(
            "No baseline folders found. Specify the path manually -- we looked for "
            + ", ".join(BASELINE_DIR_HINTS))
    if not out["adapter_candidates"]:
        out["notes"].append(
            "The baseline comparison method was not found automatically. Without it "
            "the project will connect in the mode of parsing ready-made PNGs.")
    if out["requirements"]:
        out["notes"].append(
            "The tests will be run by the VisTest interpreter -- the project "
            "does not need its own venv. If their dependencies are not found, install "
            "them with the «Install dependencies» button or the command "
            "`vistest project deps <key>`.")
    return out


def _enclosing_class(text: str, pos: int, class_re: re.Pattern) -> str:
    last = ""
    for m in class_re.finditer(text, 0, pos):
        last = m.group(1)
    return last


def _dotted_path(root: Path, file: Path) -> str:
    """A path to a file -> an importable module name relative to the project root."""
    rel = file.relative_to(root).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def suggest(root: str | Path, key: str = "") -> Project:
    """Auto-detection -> a ready project object that can be saved right away."""
    info = discover(root)
    root_path = Path(info["root"])
    key = key or re.sub(r"[^a-z0-9_]+", "_", root_path.name.lower()).strip("_") \
        or "project"

    pytest_args: list[str] = []
    for marker in ("screenshot", "visual", "screen"):
        if marker in info["markers"]:
            pytest_args = ["-m", marker]
            break

    candidate = _best_candidate(info["adapter_candidates"])

    return Project(
        key=key,
        name=root_path.name,
        root=str(root_path),
        tests=info["tests"],
        python="",                       # our interpreter -- see resolve_python
        pytest_args=pytest_args,
        env={},
        baselines=[BaselineDir(dir=b["dir"], when=b["when"], label=b["label"])
                   for b in info["baselines"]],
        adapter=Adapter(target=candidate["target"] if candidate else ""),
        note="; ".join(info["notes"])[:500],
    )


def _best_candidate(candidates: list[dict]) -> dict | None:
    """From the found methods, choose the most plausible one.

    We prefer the one declared in the base page class: that is usually where
    the comparison is placed, and specific pages rarely override it.
    """
    if not candidates:
        return None
    return sorted(candidates, key=lambda c: (
        "base" not in c["file"].lower(),
        "page" not in c["file"].lower(),
        len(c["target"]),
    ))[0]


# --------------------------------------------------------------------------- #
def classify_png(path: Path) -> str:
    """Whether this is a baseline, an actual or a diff. Needed by the ready-PNG parsing mode."""
    name = path.name.lower()
    if any(name.startswith(p) for p in DIFF_PREFIXES):
        return "diff"
    if any(name.startswith(p) for p in ACTUAL_PREFIXES):
        return "actual"
    return "baseline"


def baseline_name_of(path: Path) -> str:
    """`actual_login_filled.png` -> `login_filled.png`."""
    name = path.name
    for prefix in ACTUAL_PREFIXES:
        if name.lower().startswith(prefix):
            return name[len(prefix):]
    return name


def which_python(project: Project) -> str | None:
    exe = project.resolve_python()
    return exe if Path(exe).exists() or shutil.which(exe) else None
