# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""CLI: the library's commands, and the server's when it is installed.

`vistest --help` lists them in two groups (review v1, R6, 3.1): what a project
using `expect_screenshot` needs — `compare`, `check`, `bench`, `doctor`,
`baselines` — and the server's, which another package offers through the
`vistest.commands` entry point (`vistest_server.cli`; o1). A server command
without the server's dependencies says so in one line, with the line to
install them, and exits 2 — it used to die on `ModuleNotFoundError: No module
named 'fastapi'`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from . import __version__
from .config import VisTestConfig

#: The library's commands, in the order `--help` lists them. Every other
#: command is the server's.
LIBRARY_COMMANDS = ("compare", "check", "bench", "doctor", "baselines")

#: The server's commands, as `vistest.commands` registered them in `main`.
_SERVER = None


def _server_commands(sub):
    """Let every `vistest.commands` entry point add its commands to `sub`.

    Each is a `register(sub)` that returns what runs them: an object with
    `run(args)` and `check_via_api(args, overrides)`. One is expected — the
    server's — and none is fine: the library's commands are all there is.
    """
    from importlib.metadata import entry_points

    found = None
    for ep in entry_points(group="vistest.commands"):
        found = ep.load()(sub)
    return found


def _grouped(sub) -> str:
    """The commands for `--help`: the library's, then the server's."""
    helps = {a.dest: a.help or "" for a in getattr(sub, "_choices_actions", ())}
    width = max(len(n) for n in helps) + 2
    from ._extras import SERVER_INSTALL

    def line(name: str) -> str:
        import textwrap

        return textwrap.fill(helps[name], width=79, initial_indent=f"  {name:<{width}}",
                             subsequent_indent=" " * (width + 2))

    library = [line(n) for n in LIBRARY_COMMANDS]
    server = [line(n) for n in helps if n not in LIBRARY_COMMANDS]
    return "\n".join(["library:", *library, "",
                      f"server (needs vistest[server]: {SERVER_INSTALL}):", *server])


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="vistest", usage="vistest [-h] [--version] COMMAND ...",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="`vistest COMMAND --help` for the options of one command.")
    p.add_argument("--version", action="version", version=f"vistest {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="COMMAND",
                           help=argparse.SUPPRESS)

    c = sub.add_parser("compare", help="compare two PNGs")
    c.add_argument("expected")
    c.add_argument("actual")
    c.add_argument("-o", "--out", default="vistest-diff")
    c.add_argument("--json", action="store_true", help="print result.json to stdout")

    ch = sub.add_parser(
        "check", help="check a PNG against its baseline, as expect_screenshot does: "
                      "exit 0 matches, 1 differs, 2 no baseline yet",
        description="Check a ready PNG against its baseline — expect_screenshot "
                    "from any language. Without a server: the library's layout "
                    "(tests/__vistest__), the same rules for the name and the "
                    "platform; exit 0 when it matches, 1 when it differs, 2 when "
                    "there is no baseline yet (--update writes it). With --api, "
                    "or a server configured (VISTEST_API_URL, service.api_url), "
                    "the server's layout and its options.")
    ch.add_argument("name", help="snapshot name, e.g. checkout.png")
    ch.add_argument("actual", metavar="ACTUAL", help="path to the actual screenshot (PNG)")
    ch.add_argument("--baselines", metavar="DIR",
                    help="where the baselines are (tests/__vistest__ by default, "
                         "__vistest__ without a tests/ folder)")
    ch.add_argument("--platform", help="the platform directory of the baseline, e.g. "
                                       "linux-chromium-1x-1440x900 (without the "
                                       "system every machine shares one set); "
                                       "none by default")
    ch.add_argument("--update", action="store_true",
                    help="accept the picture as the baseline when it is missing or differs")
    ch.add_argument("--json", action="store_true",
                    help="print one JSON object: verdict, message, baseline, actual, diff")
    server = ch.add_argument_group("the server's (with --api or a configured server)")
    server.add_argument("--api", help="check through this service, in its layout")
    server.add_argument("--frames", nargs="*", default=[],
                        help="extra frames of the same page, to detect motion")
    server.add_argument("--dom", help="DOM snapshot JSON")
    server.add_argument("--browser", default=None, help="chromium by default")
    server.add_argument("--run-key", help="groups checks into one run")
    server.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                        help="override a threshold, e.g. --set fail_severity=40")

    doc = sub.add_parser(
        "doctor", help="measure the stand's own noise on an unchanged page")
    doc.add_argument("url", nargs="?", help="page address")
    doc.add_argument("--name", help="take the address from an existing baseline's passport")
    doc.add_argument("--runs", type=int, default=3,
                     help="how many times to load the page (2 at least)")
    doc.add_argument("--browser", default="chromium",
                     choices=["chromium", "firefox", "webkit"])
    doc.add_argument("--viewport", help="1440x900 by default")
    doc.add_argument("--selector", help="measure only this element")
    doc.add_argument("--platform", help="for --name; the current one by default")
    doc.add_argument("--out", help="where to put the noise map and report.json")
    doc.add_argument("--json", action="store_true")

    bn = sub.add_parser(
        "bench", help="the engine on your own failures: v2 against Playwright's "
                      "comparator, disagreements collected for a person")
    bn.add_argument("folder", nargs="?",
                    help="Playwright's test-results, or a project with a VisTest run "
                         "(.vistest/actual/)")
    bn.add_argument("--expected", help="two folders with the same file names: the baselines")
    bn.add_argument("--actual", help="... and the screenshots")
    bn.add_argument("--baselines",
                    help="a VisTest run: where its baselines are "
                         "(tests/__vistest__ by default, __vistest__ without a tests/ folder)")
    bn.add_argument("--out", default="vistest-bench-out",
                    help="where the report, labels.csv and the diff pictures go "
                         "(./vistest-bench-out by default)")
    bn.add_argument("--labels", help="labels.csv with the label column filled in: "
                                     "count each tool's false failures and misses")
    bn.add_argument("--playwright", help="a folder of a project with @playwright/test "
                                         "installed, for Playwright's own comparator")
    bn.add_argument("--no-playwright", action="store_true",
                    help="leave the Playwright columns empty")
    bn.add_argument("--no-ai", action="store_true", help="v2 without the AI layer")

    bl = sub.add_parser("baselines", help="move baselines between installations")
    bl_sub = bl.add_subparsers(dest="sub", required=True)

    bl_ex = bl_sub.add_parser("export", help="pack selected baselines")
    bl_ex.add_argument("archive")
    bl_ex.add_argument("--project", default="", help="only this project's set")
    bl_ex.add_argument("--platform", default="",
                       help="only this platform key, for example linux-chromium-1x")
    bl_ex.add_argument("--name", action="append", default=[], dest="names",
                       help="glob of snapshot names; may be repeated")
    bl_ex.add_argument("--with-history", action="store_true",
                       help="include previous versions, several times the size")
    bl_ex.add_argument("--baselines", default="", metavar="PATH",
                       help="directory to read from; the installation's own set "
                            "by default. A library project passes its committed "
                            "one, e.g. tests/__vistest__")

    bl_im = bl_sub.add_parser("import", help="merge an archive into this installation")
    bl_im.add_argument("archive")
    bl_im.add_argument("--mode", default="new", choices=["new", "update", "replace"],
                       help="new: keep what is here; update: bring incoming in as "
                            "a new version; replace: incoming wins, history included")
    bl_im.add_argument("--project", default="")
    bl_im.add_argument("--platform", default="")
    bl_im.add_argument("--name", action="append", default=[], dest="names")
    bl_im.add_argument("--dry-run", action="store_true",
                       help="say what would happen and change nothing")
    bl_im.add_argument("--who", default="", help="name to record on imported versions")
    bl_im.add_argument("--baselines", default="", metavar="PATH",
                       help="directory to write into; the installation's own set "
                            "by default")
    bl_im.add_argument("--layout", default="auto", choices=["auto", "server", "flat"],
                       help="shape to write in. auto: whatever is already there, "
                            "server when the target is empty. flat: one PNG per "
                            "snapshot, as a project using vistest as a library "
                            "keeps them")

    global _SERVER
    _SERVER = _server_commands(sub)

    p.description = _grouped(sub)
    args = p.parse_args(argv)
    _safe_streams()
    if args.cmd not in LIBRARY_COMMANDS or (args.cmd == "check" and args.api):
        refused = _needs_server(args.cmd)
        if refused:
            return refused
    try:
        return _run(args)
    except _expected_errors() as e:
        #  A wrong path, an unreadable PNG, a broken vistest.yaml: one line that
        #  says which, and exit 2 — not a traceback that reads like a bug in
        #  the package. A bug still gets its traceback: only these are caught.
        print(f"vistest {args.cmd}: {_one_line(e)}", file=sys.stderr)
        return 2


def _needs_server(command: str) -> int:
    """2 and one line when the server's extra is not installed; 0 when it is."""
    from ._extras import SERVER_INSTALL, server_missing

    if not server_missing():
        return 0
    print(f"vistest {command}: this is the server's command and needs "
          f"vistest[server] — {SERVER_INSTALL}", file=sys.stderr)
    return 2


def _expected_errors() -> tuple[type[BaseException], ...]:
    """What a command fails with when the input, not the code, is wrong."""
    from .core.comparator import ImageTooLarge
    from .core.pngio import PngError
    from .core.settings import ConfigError
    from .core.thresholds import ThresholdError

    return (OSError, PngError, ImageTooLarge, ConfigError, ThresholdError)


def _one_line(error: BaseException) -> str:
    text = " ".join(str(error).split())
    return text or type(error).__name__


def _safe_streams() -> None:
    """Never die printing «ΔE00» where the console or the pipe cannot hold it.

    On Windows a redirected stdout is in the ANSI code page (cp1252 and the
    like) unless PYTHONUTF8 is set, and `vistest compare` crashed on its first
    «Δ» with UnicodeEncodeError. A character the stream cannot encode is
    written the way `library/words.py` spells it there — «->» for «→», «dE»
    for «ΔE» (review v1, item 9 of step C) — and anything else it has no
    spelling for as «?»; a stream that can hold them is left alone.
    """
    from .library.words import ERRORS

    for stream in (sys.stdout, sys.stderr):
        encoding = getattr(stream, "encoding", None)
        reconfigure = getattr(stream, "reconfigure", None)
        if not encoding or not callable(reconfigure):
            continue
        try:
            "ΔE00 → — × «» ≈".encode(encoding)
        except (UnicodeEncodeError, LookupError):
            try:
                reconfigure(errors=ERRORS)
            except (ValueError, OSError, LookupError):  # pragma: no cover - closed
                pass


def _run(args) -> int:
    """The command, once the arguments are read."""
    if args.cmd == "compare":
        return _compare(args)
    if args.cmd == "check":
        return _check(args)
    if args.cmd == "doctor":
        from .doctor import run_doctor

        if args.runs < 2:
            print("--runs needs 2 at least: there is nothing to compare",
                  file=sys.stderr)
            return 2
        return run_doctor(args)
    if args.cmd == "bench":
        from .bench import main as run_bench

        return run_bench(args)
    if args.cmd == "baselines":
        return _baselines_transfer(args)
    #  Every other command is the server's (`vistest.commands`).
    return _SERVER.run(args)


def _baselines_root() -> Path:
    cfg = VisTestConfig.load()
    root = Path(os.getenv("VISTEST_ROOT", ".vistest"))
    return root / cfg.paths.baselines


def _baselines_transfer(args) -> int:
    """`vistest baselines export|import` — the archive, in the core.

    Straight to `vistest.transfer`, not through the plugin registry: packing a
    set of baselines and merging one in is what a project needs to leave the
    library mode or hand its set over, and it works with every plugin switched
    off. What an extension may add on top is orchestration between running
    installations; that is `BaselineSyncBackend`, and this command does not
    need one.
    """
    from .transfer import Selection, export, import_, inspect, plan

    selection = Selection(project=args.project, platform=args.platform,
                          names=tuple(args.names))
    root = Path(args.baselines) if getattr(args, "baselines", "") \
        else _baselines_root()

    if args.sub == "export":
        try:
            info = export(args.archive, baselines_root=root,
                          selection=selection, with_history=args.with_history)
        except FileNotFoundError as e:
            print(str(e))
            return 1
        if not info["count"]:
            print("Nothing matched the selection — the archive would be empty.")
            return 1
        print(f"{info['count']} snapshots -> {info['archive']} "
              f"({info['size_kb']} KB)"
              + (", with history" if info["with_history"] else ""))
        return 0

    # import
    try:
        manifest = inspect(args.archive)
    except (OSError, ValueError) as e:
        print(str(e))
        return 1
    print(f"Made {manifest.get('created_at', '?')} by VisTest "
          f"{manifest.get('version', '?')}: "
          f"{len(manifest.get('snapshots') or [])} snapshots")

    steps = plan(args.archive, baselines_root=root, mode=args.mode,
                 selection=selection, layout=args.layout)
    if not steps:
        print("Nothing in this archive matches the selection.")
        return 1

    for step in steps:
        where = f" [{step['platform']}]" if step["platform"] else ""
        print(f"  {step['action']:<12} {step['name']}{where}  - {step['reason']}")

    if args.dry_run:
        print("\nNothing was changed (--dry-run).")
        return 0

    result = import_(args.archive, baselines_root=root, mode=args.mode,
                     selection=selection, who=args.who, layout=args.layout)
    counts = result["counts"]
    print(f"\nadded {counts['added']}, new versions {counts['updated']}, "
          f"replaced {counts['replaced']}, skipped {counts['skipped']}")
    if result["failed"]:
        for bad in result["failed"]:
            print(f"  failed: {bad['name']} - {bad['error']}")
        return 1
    return 0


def _compare(args) -> int:
    from .capture.playwright_capture import read_png
    from .core.comparator import compare, strip_internal
    from .render.artifacts import render_all

    #  vistest.yaml and the environment, like every other command: the preset
    #  that stood here used to replace them all (review v1, 2.1).
    cfg = VisTestConfig.load()
    res = compare(read_png(args.expected), read_png(args.actual),
                  cfg=cfg.diff, name=Path(args.actual).stem)
    res.artifacts.update(render_all(res, args.out, cfg=cfg.render))
    strip_internal(res)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "result.json").write_text(res.to_json(), encoding="utf-8")

    if args.json:
        print(res.to_json())
    else:
        print("\n".join(_compare_words(args, res, out)))
    return 1 if res.failed else 0


def _compare_words(args, res, out: Path) -> list[str]:
    """What `vistest compare` says: the failure message's words, for two files.

    No «renderer: unknown» (two files carry no canary, review v1, 4.8), no SSIM
    or mean ΔE — those are in result.json — and the changes in the words the
    library's message and report use (library/words.py).
    """
    from .library import words
    from .library.errors import CHANGED_SHOWN, SUPPRESSED_SHOWN
    from .plugins.runtime import count_suppressed, say_suppressed

    cfg = res.threshold or {}
    limits = {"engine": "v2", **({"threshold_source": cfg.get("source"),
                                  "fail_severity": cfg.get("value", 0),
                                  "max_changed_area_pct": cfg.get("area_limit", 0),
                                  "area_source": cfg.get("area_source")}
                                 if cfg.get("source") else {})}
    if not res.failed:
        lines = [f"vistest compare: {args.actual} matches {args.expected}"]
        lines += words.labelled("reason", words.reason(res))
    else:
        lines = [f"vistest compare: {args.actual} differs from {args.expected}",
                 *words.limits_lines(res, limits)]
        lines += words.labelled("reason", words.reason(res))
    shown = [words.region(r) for r in res.regions[:CHANGED_SHOWN]]
    for i, text in enumerate(shown):
        lines += words.labelled("changed" if i == 0 else "", text)
    if len(res.regions) > CHANGED_SHOWN:
        lines += words.labelled("", f"... and {len(res.regions) - CHANGED_SHOWN} more in "
                                    "result.json")
    if res.failed and (below := words.below(res)):
        shown.append(below)
        lines += words.labelled("let through", below)
    if scale := words.scale_for(shown):
        lines += words.labelled("", f"({scale})")
    if res.suppressed:
        lines += words.labelled(
            "also", "; ".join(say_suppressed(count_suppressed(res.suppressed))))
        for r in res.suppressed[:SUPPRESSED_SHOWN]:
            lines += words.labelled("", words.suppressed(r))
    lines.append(f"  {'artifacts:':<{words.COLUMN - 2}}{out.resolve()}")
    return lines


def _check(args) -> int:
    """`vistest check NAME ACTUAL` — the library's check, or the server's.

    Without a server it is `expect_screenshot` on a file (review v1, R4): the
    library's layout, its rules for the name and the platform, and a first
    run without a baseline is a failure (exit 2), not a baseline written with
    exit 0, which it used to be — in the server's layout, which a library
    project does not read. With `--api` or a configured server it goes to
    that server, which keeps its own layout and options.
    """
    overrides = _parse_set(args.set)
    from .config import V1_REMOVED, ConfigError

    for key in ("engine", "preset"):
        if key in overrides:
            raise ConfigError(f"--set {key}: {V1_REMOVED}")

    api = args.api or VisTestConfig.load().service.api_url
    if api:
        if _needs_server("check"):
            return 2
        args.api = api
        return _SERVER.check_via_api(args, overrides)

    server_only = [flag for flag, used in (
        ("--frames", args.frames), ("--dom", args.dom), ("--browser", args.browser),
        ("--run-key", args.run_key), ("--set", args.set)) if used]
    if server_only:
        print(f"vistest check: {', '.join(server_only)} is the server's — pass --api URL "
              "or configure one (VISTEST_API_URL); without a server the check takes "
              "NAME ACTUAL [--baselines DIR] [--platform P] [--update] [--json]",
              file=sys.stderr)
        return 2
    return _check_library(args)


def _check_library(args) -> int:
    """`expect_screenshot(ACTUAL, NAME)` with a context made of the flags."""
    import warnings

    from . import expect_screenshot
    from .config import env_text
    from .library import context as _context
    from .library.errors import BaselineMissing, ScreenshotMismatch, VisTestWarning

    baselines = args.baselines or env_text("VISTEST_BASELINES")
    root = Path.cwd()
    ctx = _context.LibraryContext(
        root=root, baselines=(root / baselines) if baselines else None,
        platform_override=args.platform or env_text("VISTEST_PLATFORM"),
        update="changed" if args.update else None,
        origin="cli", command=_check_command(args))
    _context.install(ctx)
    started = time.time()
    try:
        with warnings.catch_warnings(record=True) as said:
            #  The library's warnings, as a command says them (review v1, 8a):
            #  one line on stderr each, not the Python warning with a path into
            #  cli.py and a line of its source — and --json keeps one object
            #  on stdout.
            warnings.simplefilter("always")
            try:
                result = expect_screenshot(Path(args.actual), args.name)
            except BaselineMissing as e:
                verdict, code, message, files = "missing", 2, str(e), e.artifacts
            except ScreenshotMismatch as e:
                verdict, code, message, files = "fail", 1, str(e), e.artifacts
            except (TypeError, ValueError) as e:
                if not str(e).startswith("expect_screenshot"):
                    raise
                print(f"vistest check: {str(e).removeprefix('expect_screenshot: ')}",
                      file=sys.stderr)
                return 2
            else:
                files = _row_images(ctx, started)
                if result.verdict.value == "new_baseline":
                    verdict, code = "new_baseline", 0
                    message = f"vistest: {args.name!r} accepted as the baseline (--update)"
                else:
                    verdict, code = "pass", 0
                    message = f"vistest: {args.name!r} matches its baseline"
    finally:
        _context.uninstall()
    for w in said:
        if not issubclass(w.category, VisTestWarning):
            warnings.showwarning(w.message, w.category, w.filename, w.lineno)
            continue
        text = " ".join(str(w.message).split()).removeprefix("vistest: ")
        print(f"vistest check: warning: {text}", file=sys.stderr)

    out = {"verdict": verdict, "message": message,
           "baseline": files.get("baseline"), "actual": files.get("actual"),
           "diff": files.get("diff")}
    if args.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        print(message)
    return code


def _check_command(args) -> str:
    """The command line of this `vistest check`, to repeat with --update."""
    def arg(value) -> str:
        value = str(value)
        return f'"{value}"' if not value or any(c.isspace() for c in value) else value

    parts = ["vistest", "check", arg(args.name), arg(args.actual)]
    for flag in ("baselines", "platform"):
        if getattr(args, flag, None):
            parts += [f"--{flag}", arg(getattr(args, flag))]
    return " ".join(parts)


def _row_images(ctx, since: float) -> dict:
    """The files of the check that just ran, from the row it left for the report."""
    rows = []
    for part in ctx.parts_dir.glob("*.json"):
        try:
            if part.stat().st_mtime >= since - 1:
                rows.append(json.loads(part.read_text("utf-8")))
        except (OSError, ValueError):
            continue
    rows.sort(key=lambda r: str(r.get("written_at") or ""))
    return dict(rows[-1].get("images") or {}) if rows else {}


def _parse_set(pairs: list[str]) -> dict:
    out: dict = {}
    for item in pairs:
        if "=" not in item:
            raise SystemExit(f"--set expects KEY=VALUE, got: {item!r}")
        k, v = item.split("=", 1)
        v = v.strip()
        if v.lower() in ("true", "false"):
            out[k.strip()] = v.lower() == "true"
        elif "," in v:
            out[k.strip()] = tuple(x.strip() for x in v.split(","))
        else:
            try:
                out[k.strip()] = float(v) if "." in v else int(v)
            except ValueError:
                out[k.strip()] = v
    return out


if __name__ == "__main__":
    sys.exit(main())
