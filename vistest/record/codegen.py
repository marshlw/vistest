# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Generation of a test file from a recorded session.

Principle: the code should look as though it was written manually. No
generated walls of text with magic constants, otherwise it will not pass
review and nobody will maintain it.

Snapshots are grouped by (url, viewport, steps): one test = one page in one
size with one path leading to it. Recorded actions (login, opening a modal)
turn into ordinary Playwright calls rather than a call into the internal
VisTest runtime, so the test stays readable and can be edited without docs.

Two output modes:

* default: a flat test with selectors right inside the steps. Shorter and
  clearer when there are few snapshots;
* `pom=True`: page objects in `pages/`, the test speaks about intent. More
  costly up front, but survives a re-layout: a locator is fixed in one place
  rather than in twenty tests. See `record/pom.py`.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from urllib.parse import urlparse

HEADER = '''"""Visual tests{project_title} — generated {date}.

The file can and should be edited manually: it is ordinary pytest + Playwright.
Baselines live in .vistest/baselines/{platform}/{project_dir} and already exist.

Run:          python run.py test {path}
Update:       python run.py update {path}
Regenerate:   python run.py codegen        (from baseline specs)
{secrets}"""
{imports}
{base_url_decl}{project_decl}'''

PROJECT_FIXTURE = '''

@pytest.fixture(autouse=True)
def _vistest_project(visual):
    """Prefix for snapshot names.

    Thanks to it, `login.png` of this project will not collapse into the
    `login.png` of a neighboring one: baselines live in different subdirs.
    """
    visual.project = PROJECT
'''

SECRETS_NOTE = '''
Secrets are taken from environment variables — they are not in the code:
{vars}
Put them in .vistest/secrets.env (a file in .gitignore) or in the CI environment.
'''

TEST_TEMPLATE = '''

{decorators}def {func_name}(page, visual):
    """{doc}"""
{viewport}{steps}{body}'''

def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_test_files(store, out_dir, *, platform: str = "",
                     project: str | None = None, pom: bool = False,
                     on_skip=None, on_link=None) -> list[tuple[str, int]]:
    """Build tests from baseline specs — one file per project.

    `on_link(path, links)` is called for every file actually written, with
    `links = [{"name": <snapshot>, "test": <function>}, …]`. Without it the
    connection between a snapshot and the test built from it exists only inside
    the generated source: the snapshot page keeps saying «no test», the test
    page keeps saying «no snapshots», and the person who has just pressed
    «Assemble» sees two halves that do not know about each other. The caller
    writes the link into the baseline passport — it knows the store and the
    scope, this function knows only names.

    The source of truth is the `meta.json` of each baseline (address, window
    size, selector, steps), not the last recording session. This is essential:
    otherwise recording a second project would overwrite the tests of the
    first, because the file is shared. A side benefit is that tests can be
    regenerated at any moment without re-capturing anything.

    Manual edits are not lost. Next to them lives `.vistest_codegen.json` with a
    fingerprint of the last generated version of each file. If the file on disk
    differs from that fingerprint, it was edited manually after generation, and
    regeneration does NOT touch it (it reports via `on_skip`). This way a test
    polished manually survives a repeated codegen. The previous version, during
    a legitimate overwrite, still goes to `*.py.bak`.
    """
    from pathlib import Path

    from ..storage.fs import split_project

    out_dir = Path(out_dir)
    fp_path = out_dir / ".vistest_codegen.json"
    fingerprints: dict = {}
    if fp_path.exists():
        try:
            fingerprints = json.loads(fp_path.read_text("utf-8")) or {}
        except Exception:
            fingerprints = {}
    groups: dict[str, list[dict]] = {}

    for name in store.list_names():
        proj, short = split_project(name)
        if project is not None and proj != project:
            continue
        rec = store.load(name)
        meta = (rec.meta if rec else None) or {}
        if not meta.get("url"):
            continue  # without an address the test cannot be built
        groups.setdefault(proj, []).append({
            "name": name,
            "short": short,
            "url": meta["url"],
            "selector": meta.get("selector"),
            "selector_element": meta.get("selector_element"),
            "viewport": _parse_viewport(meta.get("viewport")),
            "ignore_boxes": meta.get("ignore_boxes") or [],
            "steps": meta.get("steps") or [],
        })

    written: list[tuple[str, int]] = []
    for proj, items in sorted(groups.items()):
        path = out_dir / f"test_visual_{_module_name(proj)}.py"
        pages = None
        if pom:
            from . import pom as _pom

            pages = _pom.build_pages(items)
            _pom.write_page_objects(pages, out_dir / "pages")
            # The test imports page objects by a relative path
            # (`from .pages.checkout_page import CheckoutPage`), and for that
            # the tests directory must be a package.
            init = out_dir / "__init__.py"
            if not init.exists():
                out_dir.mkdir(parents=True, exist_ok=True)
                init.write_text(
                    '"""Tests directory as a package: needed to import pages/."""\n',
                    encoding="utf-8")

        links: list[dict] = []
        code = generate_test_file(items, platform=platform, path=str(path),
                                  project=proj, pages=pages, links=links)
        path.parent.mkdir(parents=True, exist_ok=True)
        name = path.name
        new_hash = _sha(code)

        if path.exists():
            current = path.read_text("utf-8")
            if current == code:
                fingerprints[name] = new_hash
                written.append((str(path), len(items)))
                # Файл тот же — связь всё равно объявляем: она могла не
                # записаться в прошлый раз, а «ничего не изменилось» не значит
                # «ничего не надо».
                if on_link:
                    on_link(str(path), links)
                continue
            last = fingerprints.get(name)
            if last is not None and _sha(current) != last:
                # The file was edited manually after the last generation — do not overwrite.
                if on_skip:
                    on_skip(str(path))
                continue
            # Untouched since the last generation (or no history) — safe to
            # overwrite, keeping the previous version alongside.
            path.with_suffix(".py.bak").write_text(current, encoding="utf-8")

        path.write_text(code, encoding="utf-8")
        fingerprints[name] = new_hash
        written.append((str(path), len(items)))
        if on_link:
            on_link(str(path), links)

    # Fingerprints are valid only for the files actually written in this run;
    # we do not touch other projects, so we extend the set rather than rewrite it.
    try:
        fp_path.write_text(json.dumps(fingerprints, ensure_ascii=False),
                           encoding="utf-8")
    except Exception:
        pass
    return written


def _module_name(project: str) -> str:
    if not project:
        return "recorded"
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", project).strip("_").lower()
    return slug or "recorded"


def _parse_viewport(value) -> tuple[int, int]:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return int(value[0]), int(value[1])
    try:
        w, h = str(value or "1440x900").lower().split("x")
        return int(w), int(h)
    except Exception:
        return 1440, 900


def generate_test_file(steps: list[dict], *, platform: str = "",
                       path: str = "tests/test_visual_recorded.py",
                       project: str = "", pages: dict | None = None,
                       links: list | None = None) -> str:
    groups = _group(steps)
    base_url = _common_base(steps)
    env_vars = _collect_env_vars(steps)

    # With page objects the secrets are substituted inside the object, so `os`
    # is not needed in the test itself — an unused import cannot be left in, the
    # linter would flag it first thing at review.
    need_os = bool(env_vars) and not pages
    imports = "\nimport os\n\nimport pytest\n" if need_os else "\nimport pytest\n"
    if pages:
        # Import page objects — one per page, in alphabetical order.
        used_classes = sorted({p.class_name: p for p in pages.values()}.items())
        imports += "\n" + "\n".join(
            f"from .pages.{po.module} import {cls}" for cls, po in used_classes)
        imports += "\n"

    parts = [HEADER.format(
        date=datetime.now().strftime("%Y-%m-%d %H:%M"),
        platform=platform or "<platform>",
        project_title=f": {project}" if project else "",
        project_dir=f"{project}/" if project else "",
        path=path,
        imports=imports,
        base_url_decl=f'BASE_URL = "{base_url}"\n' if base_url else "",
        project_decl=f'PROJECT = "{project}"\n' if project else "",
        secrets=SECRETS_NOTE.format(
            vars="".join(f"  {v}\n" for v in env_vars)) if env_vars else "",
    )]
    if project:
        parts.append(PROJECT_FIXTURE)

    used: set[str] = set()
    for (url, viewport, _key), items in groups.items():
        parts.append(_render_test(url, viewport, items, base_url, used, project,
                                  pages=pages, links=links))

    return "".join(parts).rstrip() + "\n"


# --------------------------------------------------------------------------- #
def _group(steps: list[dict]):
    groups: dict[tuple, list[dict]] = {}
    for s in steps:
        key = (
            s.get("url") or "",
            tuple(s.get("viewport") or (1440, 900)),
            json.dumps(s.get("steps") or [], sort_keys=True),
        )
        groups.setdefault(key, []).append(s)
    return groups


def _render_test(url, viewport, items, base_url, used, project="",
                 pages: dict | None = None, links: list | None = None) -> str:
    actions = items[0].get("steps") or []
    func = _unique(_func_name(url, viewport, actions), used)
    w, h = viewport
    if links is not None:
        # Имя функции известно здесь и только здесь: оно собирается из адреса
        # и размера окна и разводится по конфликтам. Восстанавливать его снаружи
        # значило бы завести вторую реализацию того же правила, и она разошлась
        # бы с первой на первом же переименовании.
        links.extend({"name": s["name"], "test": func} for s in items)

    url_expr = _url_expr(url, base_url)

    viewport_line = ""
    if (w, h) != (1440, 900):
        viewport_line = f'    page.set_viewport_size({{"width": {w}, "height": {h}}})\n'

    if pages:
        step_lines = _render_via_page_object(url, actions, base_url, pages)
    else:
        step_lines = _render_steps(actions, base_url, indent="    ")
        if not any(a.get("action") == "goto" for a in actions):
            step_lines = f"    page.goto({url_expr})\n" + step_lines

    body_lines = []
    for s in items:
        # The project is substituted by the fixture — the call keeps the short name.
        short = s.get("short") or s["name"]
        args = [f'"{short if project else s["name"]}"']
        if s.get("selector"):
            args.append(f'clip_selector="{_escape(s["selector"])}"')
        body_lines.append(f"    visual.assert_screenshot({', '.join(args)})")
        if s.get("ignore_boxes"):
            n = len(s["ignore_boxes"])
            body_lines.append(
                f"    # {n} ignore-zone{'' if n == 1 else 's'} recorded in the baseline "
                "(.vistest/.../meta.json)")

    decorators = "@pytest.mark.visual\n" if len(items) > 1 or actions else ""

    return TEST_TEMPLATE.format(
        decorators=decorators,
        func_name=func,
        doc=_doc(url, viewport, items, actions),
        viewport=viewport_line,
        steps=step_lines,
        body="\n".join(body_lines) + "\n",
    )


def _render_via_page_object(url, actions, base_url, pages: dict) -> str:
    """A test on top of a page object: intent instead of selectors.

    The point is exactly in these three lines. The test stops depending on the
    layout: when the button is moved, only one locator in the page object needs
    fixing, not twenty tests.
    """
    from . import pom as _pom

    po = pages.get(_pom._page_key(url))
    if po is None:
        return _render_steps(actions, base_url, indent="    ")

    var = _pom.ident(po.class_name.removesuffix("Page"), "page").lower()
    base = "BASE_URL" if base_url else '""'
    lines = [f"    {var} = {po.class_name}(page)",
             f"    {var}.open({base})"]
    for meth, _body, _doc in po.methods:
        lines.append(f"    {var}.{meth}()")
    return "\n".join(lines) + "\n"


def _render_steps(actions: list[dict], base_url: str, indent: str = "    ") -> str:
    """Steps → Playwright calls. Reads like an ordinary test."""
    lines: list[str] = []
    for a in actions:
        action = a.get("action")
        sel = _escape(a.get("selector") or "")
        val = a.get("value")

        if action == "goto":
            lines.append(f"{indent}page.goto({_url_expr(a.get('url') or '', base_url)})")
        elif action == "click":
            lines.append(f'{indent}page.click("{sel}")')
        elif action == "fill":
            lines.append(f'{indent}page.fill("{sel}", {_value_expr(val)})')
        elif action == "type":
            lines.append(f'{indent}page.type("{sel}", {_value_expr(val)})')
        elif action == "press":
            lines.append(f'{indent}page.press("{sel}", "{_escape(str(val or "Enter"))}")')
        elif action == "select":
            lines.append(f'{indent}page.select_option("{sel}", {_value_expr(val)})')
        elif action == "check":
            lines.append(f'{indent}page.check("{sel}")')
        elif action == "uncheck":
            lines.append(f'{indent}page.uncheck("{sel}")')
        elif action == "hover":
            lines.append(f'{indent}page.hover("{sel}")')
        elif action == "wait":
            lines.append(f"{indent}page.wait_for_timeout({int(a.get('ms') or 500)})")
        elif action == "wait_for":
            lines.append(f'{indent}page.wait_for_selector("{sel}")')
        elif action == "wait_for_url":
            lines.append(f'{indent}page.wait_for_url("{_escape(str(a.get("url") or val))}")')
        elif action == "scroll":
            lines.append(f'{indent}page.locator("{sel}").scroll_into_view_if_needed()'
                         if sel else
                         f"{indent}page.evaluate(\"() => window.scrollTo(0, {val or 0})\")")
        elif action == "eval":
            lines.append(f"{indent}page.evaluate({json.dumps(str(val))})")
        elif action == "flow":
            lines.append(f"{indent}# flow «{a.get('name')}» is described in vistest.yaml; "
                         "move it into a fixture if many tests need it")
    return ("\n".join(lines) + "\n") if lines else ""


def _value_expr(value) -> str:
    """`${VAR}` → os.environ["VAR"], so the secret does not end up in git."""
    if value is None:
        return '""'
    text = str(value)
    m = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", text.strip())
    if m:
        return f'os.environ["{m.group(1)}"]'
    if "${" in text:
        inner = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}",
                       lambda mm: f'{{os.environ["{mm.group(1)}"]}}', text)
        return f'f"{_escape(inner)}"'
    return f'"{_escape(text)}"'


def _url_expr(url: str, base_url: str) -> str:
    if base_url and url.startswith(base_url):
        return f'BASE_URL + "{url[len(base_url):] or "/"}"'
    return f'"{_escape(url)}"'


def _collect_env_vars(steps: list[dict]) -> list[str]:
    found: set[str] = set()
    for s in steps:
        for a in s.get("steps") or []:
            for m in re.finditer(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", str(a.get("value", ""))):
                found.add(m.group(1))
    return sorted(found)


def _doc(url, viewport, items, actions) -> str:
    path = urlparse(url).path or "/"
    what = ("page" if len(items) == 1 and not items[0].get("selector")
            else f"{len(items)} snapshot{'' if len(items) == 1 else 's'}")
    doc = f"{what} {path} at {viewport[0]}×{viewport[1]}"
    if actions:
        doc += f", after {len(actions)} action" + ("" if len(actions) == 1 else "s")
    return doc


def _func_name(url: str, viewport, actions=()) -> str:
    p = urlparse(url)
    path = (p.path or "/").strip("/")
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", path).strip("_").lower() or "index"
    if not re.match(r"^[a-z]", slug):
        slug = "page_" + slug
    w = viewport[0]
    suffix = {390: "_mobile", 768: "_tablet"}.get(w, "" if w >= 1200 else f"_{w}")
    prefix = "test_auth_" if _looks_like_login(actions) else "test_"
    return f"{prefix}{slug}{suffix}"[:60]


def _looks_like_login(actions) -> bool:
    return any(a.get("action") == "fill" and "PASSWORD" in str(a.get("value", ""))
               or a.get("action") == "flow" and "login" in str(a.get("name", ""))
               for a in actions or [])


def _unique(name: str, used: set[str]) -> str:
    if name not in used:
        used.add(name)
        return name
    i = 2
    while f"{name}_{i}" in used:
        i += 1
    used.add(f"{name}_{i}")
    return f"{name}_{i}"


def _common_base(steps: list[dict]) -> str:
    urls = [s.get("url") or "" for s in steps if s.get("url", "").startswith("http")]
    if len(urls) < 2:
        return ""
    first = urlparse(urls[0])
    base = f"{first.scheme}://{first.netloc}"
    return base if all(u.startswith(base) for u in urls) else ""


def _escape(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace('"', '\\"')
