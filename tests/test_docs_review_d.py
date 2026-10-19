# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The documentation says what the code does (review v1, step D).

A README that names a flag the plugin does not have, a yaml key the config
does not know, or a command the CLI does not have is the first thing a new
user copies — and the first thing that fails. These tests read the documents
as text and the code as source (no import of the server, no browser), so they
run in any environment the suite runs in.

What is checked, in README.md, README.ru.md, docs/GUIDE.md and
examples/vistest.yaml:

* every `--vistest-*` flag exists in the pytest plugin or its server half;
* every `section.key` of vistest.yaml exists in the config;
* every `vistest COMMAND` and `python -m vistest.cli COMMAND` exists in the CLI;
* every key the library reads (`LIBRARY_READS`) is described in the guide;
* the test the README shows is `examples/test_library.py`, line for line;
* `engine=` of `vistest.compare` is not documented (phase 6 takes it away);
* the CI examples pin Playwright to the version of their image.

And every link between the repository's markdown files — relative, or
absolute to this repository on GitHub — points to a file that exists and, with
an anchor, to a heading that exists. The development journal is left out: it
moved to docs/dev/ word for word, and its history is not rewritten.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
DOCS = ("README.md", "README.ru.md", "docs/GUIDE.md", "examples/vistest.yaml")
REPO_URL = "https://github.com/marshlw/vistest/blob/main/"
RAW_URL = "https://raw.githubusercontent.com/marshlw/vistest/main/"

#  Named in the documents on purpose, as what is gone or refused — never as
#  something to use. Each is said next to its replacement.
GONE_FLAGS = {"--vistest-preset"}
REFUSED_KEYS = {"capture.restore_scroll", "diff.engine", "diff.preset"}
#  Spelled like a key and not one: an audit event, a field of a passport.
NOT_KEYS = {"auth.role_kept", "capture.version"}


def _read(rel: str) -> str:
    return (ROOT / rel).read_text("utf-8")


def _code(text: str) -> list[str]:
    """Inline code spans and the lines of fenced blocks: where names are named."""
    spans = []
    fenced = re.findall(r"^```[^\n]*\n(.*?)^```", text, re.M | re.S)
    for block in fenced:
        spans += block.splitlines()
    outside = re.sub(r"^```[^\n]*\n.*?^```", "", text, flags=re.M | re.S)
    spans += re.findall(r"`([^`\n]+)`", outside)
    return spans


# --------------------------------------------------------------------------- #
#  What the code has
# --------------------------------------------------------------------------- #
def _code_flags() -> set[str]:
    flags = set()
    for rel in ("vistest/pytest_plugin.py", "vistest_server/pytest_server.py"):
        flags |= set(re.findall(r'addoption\(\s*"(--vistest-[a-z-]+)"', _read(rel)))
    return flags


def _code_commands() -> set[str]:
    """`compare`, `check`, … and the nested ones: `project add`, `baselines export`."""
    #  The library's commands and the server's (o1).
    source = _read("vistest/cli.py") + _read("vistest_server/cli.py")
    parent = {"sub": "", "pr_sub": "project ", "u_sub": "user ", "bl_sub": "baselines "}
    found = set()
    for var, name in re.findall(r'(\w+)\.add_parser\(\s*"([\w-]+)"', source):
        if var in parent:
            found.add(parent[var] + name)
    return found


def _config_keys() -> set[str]:
    from dataclasses import fields

    from vistest.config import VisTestConfig

    keys = set()
    for section in fields(VisTestConfig):
        default = VisTestConfig()
        value = getattr(default, section.name)
        if hasattr(value, "__dataclass_fields__"):
            keys |= {f"{section.name}.{f.name}" for f in fields(value)}
    return keys


# --------------------------------------------------------------------------- #
#  Names in the documents
# --------------------------------------------------------------------------- #
def test_the_code_side_is_read():
    """A regex that stopped matching the source would make every test below vacuous."""
    assert {"--vistest-update", "--vistest-baselines", "--vistest-platform",
            "--vistest-report", "--vistest-config", "--vistest-api"} <= _code_flags()
    assert {"compare", "check", "bench", "doctor", "baselines export",
            "baselines import", "project add", "user add", "serve"} <= _code_commands()
    assert {"diff.fail_severity", "capture.keep_pointer", "paths.root",
            "service.api_url"} <= _config_keys()


@pytest.mark.parametrize("doc", DOCS)
def test_every_flag_named_exists(doc):
    named = set(re.findall(r"--vistest-[a-z]+(?:-[a-z]+)*", _read(doc)))
    missing = named - _code_flags() - GONE_FLAGS
    assert not missing, f"{doc} names flags the plugin does not have: {sorted(missing)}"


@pytest.mark.parametrize("doc", DOCS[:3])
def test_every_yaml_key_named_exists(doc):
    sections = "diff|capture|paths|plugins|ai|service|matrix|render|auth"
    named = set()
    for span in _code(_read(doc)):
        named |= {m.group(0) for m in re.finditer(
            rf"(?<![\w./-])(?:{sections})\.[a-z_]+(?![\w(])", span)}
    missing = named - _config_keys() - REFUSED_KEYS - NOT_KEYS
    assert not missing, (f"{doc} names vistest.yaml keys the config does not know: "
                         f"{sorted(missing)}")


@pytest.mark.parametrize("doc", DOCS[:3])
def test_every_key_in_a_yaml_block_exists(doc):
    """A key written the way a user copies it: nested, in a ```yaml block."""
    known = _config_keys() | REFUSED_KEYS
    sections = {k.split(".", 1)[0] for k in known}
    missing, parsed = set(), 0
    for block in re.findall(r"^```ya?ml\n(.*?)^```", _read(doc), re.M | re.S):
        try:
            data = yaml.safe_load(block)
        except yaml.YAMLError:
            continue                        # a fragment cut out of a larger file
        if not isinstance(data, dict):
            continue
        parsed += 1
        for section, body in data.items():
            if section in sections and isinstance(body, dict):
                missing |= {f"{section}.{key}" for key in body} - known
    assert parsed, f"{doc}: no yaml block was read — the test would prove nothing"
    assert not missing, (f"{doc}: yaml blocks set keys the config does not know: "
                         f"{sorted(missing)}")


def _uncommented(text: str) -> str:
    """The example with its commented-out keys switched on: they are copied too."""
    return re.sub(r"^(\s*)# ?(\s*[a-z_]+:(?: .*)?)$", r"\1\2", text, flags=re.M)


def test_every_key_of_the_example_config_is_read_by_the_library():
    from vistest.library.context import LIBRARY_READS

    reads = set(LIBRARY_READS)
    data = yaml.safe_load(_read("examples/vistest.yaml"))
    keys = {f"{s}.{k}" for s, v in data.items() for k in (v or {})}
    assert keys, "examples/vistest.yaml sets nothing"
    assert keys <= reads, sorted(keys - reads)
    #  The commented-out keys: a whole section the library reads (`plugins`,
    #  `ai`) takes any key the config knows in it.
    every = yaml.safe_load(_uncommented(_read("examples/vistest.yaml")))
    named = {f"{s}.{k}" for s, v in every.items() for k in (v or {})}
    assert {"diff.fail_severity", "plugins.fail_on", "ai.gate_enabled"} <= named
    wrong = {k for k in named
             if k not in reads and not (k.split(".")[0] in reads and k in _config_keys())}
    assert not wrong, f"examples/vistest.yaml names keys the library does not read: {wrong}"
    #  No threshold set: the comment says why, and the key is there, commented out.
    assert "fail_severity" not in data.get("diff", {})
    assert "max_changed_area_pct" not in data.get("diff", {})


@pytest.mark.parametrize("doc", DOCS[:3])
def test_every_command_named_exists(doc):
    commands = _code_commands()
    named = set()
    for span in _code(_read(doc)):
        for m in re.finditer(
                r"(?:^|[\s(])(?<!from )(?:vistest|python -m vistest\.cli) ([a-z][a-z-]*)"
                r"(?: ([a-z][a-z|-]*))?", span):
            top, nxt = m.group(1), m.group(2) or ""
            if top in ("project", "user", "baselines"):
                for one in nxt.split("|"):
                    named.add(f"{top} {one}")
            else:
                named.add(top)
    missing = named - commands
    assert not missing, f"{doc} names commands the CLI does not have: {sorted(missing)}"


def test_every_key_the_library_reads_is_in_the_guide():
    from vistest.library.context import LIBRARY_READS

    guide = _read("docs/GUIDE.md")
    missing = [k for k in sorted(LIBRARY_READS)
               if (f"`{k}`" not in guide if "." in k
                   else not re.search(rf"`{k}\.[a-z_]+`", guide))]
    assert not missing, f"docs/GUIDE.md does not describe: {missing}"


@pytest.mark.parametrize("doc", ["README.md", "README.ru.md"])
def test_the_readme_test_is_the_example(doc):
    """Its first Python block is examples/test_library.py, in order, nothing added."""
    block = re.search(r"```python\n(.*?)```", _read(doc), re.S).group(1)

    def lines(text):
        return [re.sub(r"\s+# noqa: \w+$", "", ln.rstrip())
                for ln in text.splitlines() if ln.strip()]

    example = lines(_read("examples/test_library.py"))
    shown = lines(block)
    at = 0
    for line in shown:
        try:
            at = example.index(line, at) + 1
        except ValueError:
            pytest.fail(f"{doc}: {line!r} is not in examples/test_library.py "
                        "(or not in this order)")
    assert "expect_screenshot" in block


@pytest.mark.parametrize("doc", [*DOCS, "CHANGELOG.md", "docs/server/GUIDE.ru.md",
                                 "docs/server/LICENSING.md", ".gitlab-ci.example.yml"])
def test_compare_engine_is_not_documented(doc):
    assert "engine=" not in _read(doc)


def test_the_ci_examples_pin_playwright_to_their_image():
    gitlab = _read(".gitlab-ci.example.yml")
    image = re.search(r"playwright/python:v([\d.]+)-", gitlab).group(1)
    pin = re.search(r'PLAYWRIGHT_VERSION: "([\d.]+)"', gitlab).group(1)
    assert image == pin
    assert '"playwright==$PLAYWRIGHT_VERSION"' in gitlab
    assert 'PIP_BREAK_SYSTEM_PACKAGES: "1"' in gitlab
    readme = _read("README.md")
    assert re.search(r"runs-on: ubuntu-\d\d\.\d\d\n", readme), "the runner's system is pinned"
    assert re.search(r'pip install "vistest\[browser\]" "playwright==\d+\.\d+\.\d+"', readme)


def test_the_root_config_says_it_is_not_a_template():
    first = _read("vistest.yaml").splitlines()[0]
    assert first == "# not a template — for a project, start from examples/vistest.yaml"


# --------------------------------------------------------------------------- #
#  Links
# --------------------------------------------------------------------------- #
def _slug(heading: str) -> str:
    """GitHub's anchor for a heading."""
    text = re.sub(r"`|\*\*|\*", "", heading.strip().lower())
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def _anchors(path: Path) -> set[str]:
    text = re.sub(r"^```.*?^```", "", path.read_text("utf-8"), flags=re.M | re.S)
    seen: dict[str, int] = {}
    out = set()
    for heading in re.findall(r"^#{1,6} (.+)$", text, re.M):
        slug = _slug(heading)
        n = seen.get(slug, 0)
        seen[slug] = n + 1
        out.add(slug if n == 0 else f"{slug}-{n}")
    return out


def _markdown_files() -> list[str]:
    try:
        listed = subprocess.run(["git", "ls-files", "*.md"], cwd=ROOT, check=True,
                                capture_output=True, text=True).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        listed = [str(p.relative_to(ROOT)) for p in ROOT.rglob("*.md")]
    skip = ("node_modules", "docs/dev/journal.md")
    return [f for f in listed if not any(s in f for s in skip) and (ROOT / f).is_file()]


def test_the_markdown_files_are_found():
    files = _markdown_files()
    assert {"README.md", "README.ru.md", "docs/GUIDE.md",
            "docs/server/GUIDE.ru.md"} <= set(files)


@pytest.mark.parametrize("doc", _markdown_files())
def test_links_between_the_documents_lead_somewhere(doc):
    path = ROOT / doc
    text = re.sub(r"^```.*?^```", "", path.read_text("utf-8"), flags=re.M | re.S)
    broken = []
    for target in re.findall(r"\]\(([^)\s]+)\)", text):
        if target.startswith(REPO_URL):
            file, _, anchor = target[len(REPO_URL):].partition("#")
            where = ROOT / file
        elif target.startswith(RAW_URL):
            file, anchor = target[len(RAW_URL):], ""
            where = ROOT / file
        elif re.match(r"^[a-z]+:", target):
            continue                        # another site, mailto:
        else:
            file, _, anchor = target.partition("#")
            where = (path.parent / file) if file else path
        if not where.exists():
            shown = where.relative_to(ROOT) if where.is_relative_to(ROOT) else where
            broken.append(f"{target} (no {shown})")
            continue
        if anchor and where.suffix == ".md" and anchor not in _anchors(where):
            broken.append(f"{target} (no heading #{anchor})")
    assert not broken, f"{doc}: links that lead nowhere: {broken}"
