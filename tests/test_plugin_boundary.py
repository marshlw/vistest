# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The line between the core and the extensions, checked statically.

The extension modules still live in this repository, and the day the
repositories are split they leave. That day must be uneventful, which means
that today nothing in the core may import them by name — the only door is the
``vistest.plugins`` entry point. This file reads every module's imports (the
AST, not a text search, so a name in a comment or a docstring does not count)
and fails on any path from the core to an extension module.

It also pins the two other promises of this stage: the public code does not
name the closed package, and the bundle is reachable only through the entry
point declared in ``pyproject.toml``.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "vistest"

#  The modules that go behind the protocols, and the package that registers
#  them. Everything under `vistest/` that is not in this set is the core.
EXTENSION_MODULES = {
    "vistest._extensions",
    "vistest.ai.gate",
    "vistest.ai.perceptual",
    "vistest.ai.train",           # trains the gate; leaves with it
    "vistest.api.directory",
    "vistest.api.directory_routes",
    "vistest.transfer",
}


def _module_name(path: Path) -> str:
    rel = path.relative_to(ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _is_extension(name: str) -> bool:
    return any(name == m or name.startswith(m + ".") for m in EXTENSION_MODULES)


def _imports(path: Path) -> set[str]:
    """Absolute names of everything this module imports, at any depth."""
    tree = ast.parse(path.read_text("utf-8"))
    here = _module_name(path)
    package = here if path.name == "__init__.py" else here.rpartition(".")[0]
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                base = base[: len(base) - node.level + 1]
                prefix = ".".join(base)
                module = f"{prefix}.{node.module}" if node.module else prefix
            else:
                module = node.module or ""
            out.add(module)
            #  `from . import directory` names a module, not an attribute.
            out.update(f"{module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Call):
            #  importlib.import_module("vistest.ai.gate") and friends.
            func = node.func
            name = getattr(func, "attr", None) or getattr(func, "id", None)
            if name in ("import_module", "__import__") and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    out.add(arg.value)
    return out


def _core_files() -> list[Path]:
    return sorted(p for p in PACKAGE.rglob("*.py")
                  if "__pycache__" not in p.parts
                  and not _is_extension(_module_name(p)))


@pytest.mark.parametrize("path", _core_files(),
                         ids=lambda p: str(p.relative_to(ROOT)))
def test_the_core_does_not_import_extension_modules(path):
    reached = sorted(name for name in _imports(path) if _is_extension(name))
    assert not reached, (
        f"{path.relative_to(ROOT)} imports {reached}. The core reaches "
        "extensions only through the plugin registry "
        "(vistest.plugins.loader.active_registry); an import by name would "
        "break the day these modules move to their own repository.")


def test_the_extension_list_matches_the_tree():
    """A renamed extension module must not silently fall out of the check."""
    present = {_module_name(p) for p in PACKAGE.rglob("*.py")
               if "__pycache__" not in p.parts}
    missing = sorted(m for m in EXTENSION_MODULES
                     if m not in present)
    assert not missing, f"listed as extensions but not found: {missing}"


def test_the_bundle_is_reached_through_the_entry_point_only():
    try:
        import tomllib
    except ImportError:                                   # pragma: no cover
        import tomli as tomllib

    raw = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
    group = raw["project"]["entry-points"]["vistest.plugins"]
    assert group == {"bundled-extensions": "vistest._extensions"}

    mentions = []
    for path in list(PACKAGE.rglob("*.py")) + list((ROOT / "frontend").rglob("*.js")):
        if "__pycache__" in path.parts or _is_extension(
                _module_name(path) if path.suffix == ".py" else ""):
            continue
        if "_extensions" in path.read_text("utf-8"):
            mentions.append(str(path.relative_to(ROOT)))
    assert not mentions, f"the bundle is named in core files: {mentions}"


def test_the_bundle_is_discoverable_in_this_environment():
    """Declared is not registered: entry points come from installed metadata."""
    from importlib.metadata import entry_points

    found = [e for e in entry_points(group="vistest.plugins")
             if e.value == "vistest._extensions"]
    if not found:
        pytest.fail(
            "the bundled extensions are declared in pyproject.toml but not "
            "registered in this environment. Reinstall the package so the "
            "entry point is written: pip install -e .")
    assert found[0].load().API_VERSION == 1


#  The closed package's name, assembled so that this file does not contain it.
_CLOSED = re.compile(r"vistest[-_ ]?" + "pro" + r"\b", re.IGNORECASE)


def test_public_code_does_not_name_the_closed_package():
    roots = [PACKAGE, ROOT / "frontend", ROOT / "tests", ROOT / "clients",
             ROOT / "scripts", ROOT / "docker", ROOT / "examples"]
    files = [ROOT / "pyproject.toml", ROOT / "README.md", ROOT / "vistest.yaml"]
    for root in roots:
        if root.exists():
            files += [p for p in root.rglob("*") if p.is_file()
                      and "__pycache__" not in p.parts
                      and "node_modules" not in p.parts
                      and p.suffix in (".py", ".js", ".mjs", ".html", ".css",
                                       ".md", ".toml", ".yaml", ".yml", ".txt",
                                       ".json", ".java", ".sh", "")]
    hits = []
    for path in files:
        try:
            text = path.read_text("utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if _CLOSED.search(text):
            hits.append(str(path.relative_to(ROOT)))
    assert not hits, f"the closed package is named in: {hits}"


def test_the_ui_has_no_upsell():
    """No extension — no column, no card, and no words about why."""
    words = re.compile(r"upgrade|premium|paid (plan|version|edition)|buy |"
                       r"unlock|enterprise edition|available in", re.IGNORECASE)
    hits = []
    for path in (ROOT / "frontend").rglob("*"):
        if path.suffix in (".js", ".html") and words.search(path.read_text("utf-8")):
            hits += [f"{path.name}: {m.group(0)}"
                     for m in words.finditer(path.read_text("utf-8"))]
    assert not hits, hits
