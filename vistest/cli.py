# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""CLI: `check|snap|record|doctor|project|report|evidence|compare|serve|push|list|rm|prune`."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .config import VisTestConfig, platform_key


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="vistest")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("compare", help="compare two PNGs")
    c.add_argument("expected")
    c.add_argument("actual")
    c.add_argument("-o", "--out", default="vistest-diff")
    c.add_argument("--preset", choices=["strict", "balanced", "loose"], default="balanced")
    c.add_argument("--json", action="store_true", help="print result.json to stdout")

    ch = sub.add_parser(
        "check", help="check a ready PNG against a baseline (for foreign tests)")
    ch.add_argument("name", help="snapshot name, e.g. checkout.png")
    ch.add_argument("image", help="path to the actual screenshot")
    ch.add_argument("--frames", nargs="*", default=[],
                    help="extra frames of the same page, to detect motion")
    ch.add_argument("--dom", help="DOM snapshot JSON")
    ch.add_argument("--platform", help="platform key; defaults to the current OS")
    ch.add_argument("--browser", default="chromium")
    ch.add_argument("--run-key", help="groups checks into one run")
    ch.add_argument("--preset", choices=["strict", "balanced", "loose"])
    ch.add_argument("--update", action="store_true", help="overwrite the baseline")
    ch.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="override a threshold, e.g. --set fail_severity=40")
    ch.add_argument("--json", action="store_true")
    ch.add_argument("--api", help="check through the service instead of locally")

    sn = sub.add_parser("snap", help="capture baselines by URL, without writing a test")
    sn.add_argument("url", nargs="?", help="page address")
    sn.add_argument("--name", help="snapshot name; taken from the URL by default")
    sn.add_argument("--suite", help="YAML with a list of pages instead of a single URL")
    sn.add_argument("--viewport", default="1440x900", action="append",
                    help="may be given more than once: --viewport 390x844")
    sn.add_argument("--selector", help="capture only this element")
    sn.add_argument("--wait", type=int, default=0, help="extra pause, ms")
    sn.add_argument("--browser", default="chromium",
                    choices=["chromium", "firefox", "webkit"])
    sn.add_argument("--update", action="store_true", help="overwrite existing ones")
    # Матрица. `--viewport` остаётся тем, чем был — размером ЭТОГО кадра, — а
    # матрица описывает набор целиком. Смешивать их в одном флаге нельзя:
    # повторный `--viewport` годами приклеивал размер к ИМЕНИ снимка, и люди на
    # это опираются. Здесь другой механизм и другое хранение, поэтому и флаг
    # другой; что важнее — старые эталоны остаются на месте.
    sn.add_argument("--matrix", action="store_true",
                    help="снять во всех вариантах матрицы из vistest.yaml")
    sn.add_argument("--browsers",
                    help="матрица браузеров через запятую: chromium,firefox")
    sn.add_argument("--viewports",
                    help="матрица размеров через запятую: 1440x900,390x844")

    mx = sub.add_parser(
        "matrix", help="во что раскрывается матрица браузеров и разрешений")
    mx.add_argument("--browsers", help="переопределить список браузеров")
    mx.add_argument("--viewports", help="переопределить список размеров")

    rec = sub.add_parser(
        "record", help="open a browser with a panel and capture baselines by hand")
    rec.add_argument("url", nargs="?", help="starting address")
    rec.add_argument("--cdp", help="attach to an open browser, e.g. http://localhost:9222")
    rec.add_argument("--browser", default="chromium",
                     choices=["chromium", "firefox", "webkit"])
    rec.add_argument("--viewport", default="1440x900")
    rec.add_argument("--out", default="tests/test_visual_recorded.py",
                     help="where to save the generated test")
    rec.add_argument("--no-code", action="store_true", help="baselines only, no code")
    rec.add_argument("--pom", action="store_true",
                     help="generate page objects instead of selectors in the test")
    rec.add_argument("--shots", type=int, default=2,
                     help="frames per snapshot, to detect motion (2 by default)")
    rec.add_argument("--no-scroll", action="store_true",
                     help="do not warm up lazy-load: faster on endless feeds")
    rec.add_argument("--project", default="",
                     help="project name; the domain from the address by default. "
                          "Keeps baselines and tests of different apps apart")
    rec.add_argument("--no-freeze-time", action="store_true",
                     help="do not fake the clock: the page will see the real "
                          "date. The block with the date then has to be masked, "
                          "otherwise there is a diff every day")

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

    cg = sub.add_parser(
        "codegen", help="rebuild test files from the baselines' passports")
    cg.add_argument("--out", default="tests", help="directory for the tests")
    cg.add_argument("--platform", help="the current one by default")
    cg.add_argument("--project", help="a single project only")
    cg.add_argument("--pom", action="store_true",
                    help="page objects in tests/pages/ instead of selectors in the test")

    pr_ = sub.add_parser(
        "project", help="test suites connected from outside")
    pr_sub = pr_.add_subparsers(dest="sub", required=True)

    pr_sub.add_parser("list", help="what is connected")

    p_add = pr_sub.add_parser("add", help="inspect a directory and connect it")
    p_add.add_argument("root", help="root of the repository with the tests")
    p_add.add_argument("--key", help="short name; taken from the directory by default")
    p_add.add_argument("--dry-run", action="store_true",
                       help="only show what was found")

    p_run = pr_sub.add_parser("run", help="run the tests of a connected project")
    p_run.add_argument("key")
    p_run.add_argument("--env", action="append", default=[], metavar="K=V")
    p_run.add_argument("--update", action="store_true",
                       help="accept the current state as the baselines")
    p_run.add_argument("--json", action="store_true")
    p_run.add_argument("--no-history", action="store_true",
                       help="do not record the run in the shared history")
    p_run.add_argument("rest", nargs=argparse.REMAINDER,
                       help="extra arguments for their pytest")

    p_chk = pr_sub.add_parser(
        "check", help="collect their tests without running anything")
    p_chk.add_argument("key")
    p_chk.add_argument("--env", action="append", default=[], metavar="K=V")

    p_deps = pr_sub.add_parser(
        "deps", help="install the missing dependencies into the VisTest environment")
    p_deps.add_argument("key")
    p_deps.add_argument("--all", action="store_true",
                        help="the whole requirements.txt, not only what is missing")

    p_bl = pr_sub.add_parser(
        "baselines", help="the project's baselines: show, switch, reset")
    p_bl.add_argument("key")
    p_bl.add_argument("--use", choices=["project", "vistest"],
                      help="which set the engine compares against")
    p_bl.add_argument("--reset", action="store_true",
                      help="delete VisTest's own set")
    p_bl.add_argument("--platform", help="a single platform when resetting")

    p_rm = pr_sub.add_parser("rm", help="disconnect (the project's files are untouched)")
    p_rm.add_argument("key")

    rp = sub.add_parser(
        "report", help="build an offline HTML report about a run")
    rp.add_argument("run", nargs="?",
                    help="run directory; the last one by default")
    rp.add_argument("-o", "--out", help="where to save it; next to the run by default")
    rp.add_argument("--title", default="", help="report title")
    rp.add_argument("--open", action="store_true", help="open it in a browser")

    ev = sub.add_parser(
        "evidence", help="quality statistics accumulated over the run history")
    ev.add_argument("--days", type=int, default=90)
    ev.add_argument("--project", default="*", help="all of them by default")
    ev.add_argument("--format", choices=["text", "md", "html", "json"],
                    default="text")
    ev.add_argument("-o", "--out", help="write to a file")

    u = sub.add_parser("user", help="users of a shared installation")
    u_sub = u.add_subparsers(dest="sub", required=True)

    u_sub.add_parser("list", help="who exists")

    u_add = u_sub.add_parser("add", help="create a user")
    u_add.add_argument("login")
    u_add.add_argument("--role", default="viewer",
                       choices=["viewer", "reviewer", "admin"])
    u_add.add_argument("--name", default="", help="how to show them in the interface")
    u_add.add_argument("--password", help="if not given, we ask")

    u_pw = u_sub.add_parser("passwd", help="change the password")
    u_pw.add_argument("login")
    u_pw.add_argument("--password")

    u_role = u_sub.add_parser("role", help="change the role")
    u_role.add_argument("login")
    u_role.add_argument("role", choices=["viewer", "reviewer", "admin"])

    u_off = u_sub.add_parser(
        "disable", help="disable (not delete — the audit would lose the author)")
    u_off.add_argument("login")

    u_sub.add_parser(
        "pending", help="access requests waiting for approval")

    u_ok = u_sub.add_parser("approve", help="approve an access request")
    u_ok.add_argument("login")
    u_ok.add_argument("--role", default="viewer",
                      choices=["viewer", "reviewer", "admin"])

    u_sub.add_parser(
        "setup-reopen",
        help="allow the first administrator to be created again — "
             "the way back in when the only admin has lost access")

    # Право на один проект. Из командной строки — потому что это ровно то, что
    # понадобится, когда интерфейс недоступен или когда права раздаются
    # скриптом при развёртывании.
    u_gr = u_sub.add_parser(
        "grant", help="raise the role in ONE project (never lowers)")
    u_gr.add_argument("login")
    u_gr.add_argument("project", help="project key, or the service own project")
    u_gr.add_argument("role", choices=["viewer", "reviewer", "admin"])

    u_rv = u_sub.add_parser("revoke", help="take a per-project right away")
    u_rv.add_argument("login")
    u_rv.add_argument("project")

    u_rt = u_sub.add_parser("rights", help="who may what, and where")
    u_rt.add_argument("login", nargs="?", help="everyone by default")

    s = sub.add_parser("serve", help="start the service")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8420)
    s.add_argument("--reload", action="store_true")

    sub.add_parser("push", help="upload runs made offline")
    sub.add_parser("list", help="list the baselines")

    a = sub.add_parser("approve", help="accept the latest actual as the baseline")
    a.add_argument("name")
    a.add_argument("--actual", required=True)

    rm = sub.add_parser("rm", help="delete baselines")
    rm.add_argument("names", nargs="*", help="snapshot names")
    rm.add_argument("--platform", help="the current one by default")
    rm.add_argument("--all", action="store_true", help="every baseline of the platform")
    rm.add_argument("--yes", "-y", action="store_true", help="no confirmation")

    cm = sub.add_parser(
        "comment", help="a run summary in markdown — for a PR/MR comment")
    cm.add_argument("--run", help="run directory or its key; "
                                  "the last one by default")
    cm.add_argument("--out", help="a file instead of standard output")

    nt = sub.add_parser(
        "notify", help="a digest of unreviewed failures, to a webhook")
    nt.add_argument("--dry-run", action="store_true",
                    help="show the text and send nothing")
    nt.add_argument("--force", action="store_true",
                    help="send even if the list is the same")
    nt.add_argument("--after-hours", type=float,
                    help="treat a failure older than this many hours as forgotten")

    g = sub.add_parser(
        "gate", help="a decision for CI on the last run: exit code 0 or 1")
    g.add_argument("--run", help="run directory or its key; "
                                 "the last one in .vistest/runs by default")
    g.add_argument("--junit", help="where to put the JUnit XML (junit.xml next "
                                   "to the run by default)")
    g.add_argument("--fail-on-new", action="store_true",
                   help="treat a new baseline as a reason to stop the pipeline")
    g.add_argument("--ignore-review", action="store_true",
                   help="ignore that a failure has already been accepted as normal")

    pr = sub.add_parser("prune", help="delete old runs and their artifacts")
    pr.add_argument("--days", type=int, help="delete runs older than N days")
    pr.add_argument("--keep-last", type=int, default=20,
                    help="how many recent runs to always keep")
    pr.add_argument("--runs-only", action="store_true",
                    help="run files only, no database rows")
    pr.add_argument("--yes", "-y", action="store_true")

    args = p.parse_args(argv)

    if args.cmd == "compare":
        return _compare(args)
    if args.cmd == "check":
        return _check(args)
    if args.cmd == "snap":
        return _snap(args)
    if args.cmd == "matrix":
        return _matrix(args)
    if args.cmd == "record":
        from .record.session import run_recorder

        return run_recorder(args)
    if args.cmd == "doctor":
        from .doctor import run_doctor

        if args.runs < 2:
            print("--runs needs 2 at least: there is nothing to compare",
                  file=sys.stderr)
            return 2
        return run_doctor(args)
    if args.cmd == "report":
        return _report(args)
    if args.cmd == "evidence":
        return _evidence(args)
    if args.cmd == "user":
        return _user(args)
    if args.cmd == "project":
        return _project(args)
    if args.cmd == "codegen":
        return _codegen(args)
    if args.cmd == "serve":
        from .api.main import serve

        serve(args.host, args.port, args.reload)
        return 0
    if args.cmd == "push":
        from .client import push_pending

        cfg = VisTestConfig.load()
        print("runs pushed:", push_pending(cfg.runs_path(), cfg.service))
        return 0
    if args.cmd == "list":
        from .storage import FileBaselineStore

        cfg = VisTestConfig.load()
        for key_dir in sorted((cfg.root_path / cfg.paths.baselines).glob("*")):
            if not key_dir.is_dir():
                continue
            names = FileBaselineStore(key_dir).list_names()
            print(f"{key_dir.name}: {len(names)}")
            for n in names:
                print(f"  {n}")
        return 0
    if args.cmd == "approve":
        return _approve(args)
    if args.cmd == "rm":
        return _rm(args)
    if args.cmd == "comment":
        return _comment(args)
    if args.cmd == "notify":
        return _notify(args)
    if args.cmd == "gate":
        return _gate(args)
    if args.cmd == "prune":
        return _prune(args)
    return 1


def _compare(args) -> int:
    from .capture.playwright_capture import read_png
    from .core.comparator import compare, strip_internal
    from .render.artifacts import render_all

    cfg = VisTestConfig.preset_of(args.preset)
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
        print(res.summary())
        print(f"\nArtifacts: {out.resolve()}")
    return 1 if res.failed else 0


def _check(args) -> int:
    """Проверка готового PNG — точка подключения тестов на любом языке.

    Работает и локально (движок в этом же процессе), и через сервис (--api),
    результат в обоих случаях одинаковый: код возврата 0 — прошло, 1 — регресс.
    """
    overrides = _parse_set(args.set)

    if args.api:
        return _check_via_api(args, overrides)

    from .capture.playwright_capture import read_png
    from .service import CheckService

    cfg = VisTestConfig.preset_of(args.preset) if args.preset else VisTestConfig.load()
    run_dir = cfg.runs_path() / (args.run_key or "cli")
    svc = CheckService(cfg, platform=args.platform, browser=args.browser,
                       run_dir=run_dir)

    frames = [read_png(args.image)] + [read_png(f) for f in args.frames]
    dom = json.loads(Path(args.dom).read_text("utf-8")) if args.dom else None

    res = svc.check(
        args.name, frames[0],
        frames=frames if len(frames) > 1 else None,
        dom=dom,
        diff_overrides=overrides or None,
        update_baseline=args.update or None,
    )

    if args.json:
        print(res.to_json())
    else:
        print(res.summary() if res.failed else
              f"{res.verdict.value}: {res.name}"
              + (f"  severity={res.max_severity:.1f}" if res.regions else ""))
        for n in res.notes:
            print(f"  note: {n}")
        if res.artifacts.get("boxes"):
            print(f"  markup: {res.artifacts['boxes']}")
    return 1 if res.failed else 0


def _check_via_api(args, overrides: dict) -> int:
    import requests

    files = [("image", (Path(args.image).name, open(args.image, "rb"), "image/png"))]
    for f in args.frames:
        files.append(("frames", (Path(f).name, open(f, "rb"), "image/png")))
    if args.dom:
        files.append(("dom", ("dom.json", open(args.dom, "rb"), "application/json")))

    data = {"name": args.name, "browser": args.browser}
    for key, val in (("platform", args.platform), ("run_key", args.run_key),
                     ("preset", args.preset)):
        if val:
            data[key] = val
    if args.update:
        data["update_baseline"] = "true"
    if overrides:
        data["options"] = json.dumps(overrides)

    try:
        r = requests.post(f"{args.api.rstrip('/')}/api/check", data=data,
                          files=files, timeout=120)
        r.raise_for_status()
        body = r.json()
    finally:
        for _, (_, fh, _) in files:
            fh.close()

    print(json.dumps(body, indent=2, ensure_ascii=False) if args.json
          else f"{body['verdict']}: {body['name']}  "
               f"severity={body['metrics'].get('max_severity', 0):.1f}")
    return 0 if body.get("passed") else 1


def _snap(args) -> int:
    """Снять эталоны по URL — без написания тестов вообще."""
    from .record.snap import snap_urls

    targets = _snap_targets(args)
    if not targets:
        print("Nothing to capture: give a URL or --suite", file=sys.stderr)
        return 2

    variants = _variants_from_args(args)
    if variants and len(variants) > 1 and len(args.viewport) > 1:
        # Оба механизма сразу — это почти наверняка недоразумение, и молча
        # выбрать один значило бы снять половину набора не в тех размерах.
        print("--viewport и матрица заданы одновременно: размеры берутся из "
              "матрицы, повторный --viewport игнорируется.", file=sys.stderr)
    return snap_urls(targets, browser=args.browser, update=args.update,
                     variants=variants)


def _split_list(text: str | None) -> list[str] | None:
    if text is None:
        return None
    return [x.strip() for x in str(text).split(",") if x.strip()]


def _variants_from_args(args):
    """Матрица для этого запуска, или `None`, если её не просили.

    `None`, а не «один вариант по умолчанию»: `snap_urls` умеет обходиться без
    матрицы и делает это ровно как раньше. Возвращать сюда всегда список
    значило бы прогонять старый путь через новый код без всякой на то причины.
    """
    from .matrix import MatrixError, from_config

    browsers = _split_list(getattr(args, "browsers", None))
    viewports = _split_list(getattr(args, "viewports", None))
    if not (getattr(args, "matrix", False) or browsers or viewports):
        return None
    cfg = VisTestConfig.load()
    if browsers is None and getattr(args, "browser", None):
        # `--browser` без списка означает «этот браузер», и при матрице
        # размеров он остаётся единственным. Иначе включение `--viewports`
        # молча приводило бы ещё и firefox с webkit из конфига.
        browsers = list(cfg.matrix.browsers or ()) or [args.browser]
    try:
        return from_config(cfg, browsers=browsers, viewports=viewports)
    except MatrixError as e:
        raise SystemExit(f"matrix: {e}") from None


def _matrix(args) -> int:
    """Показать, во что раскрывается матрица, — до того, как её запустят.

    Шесть строк в конфиге превращаются в восемнадцать прогонов и восемнадцать
    наборов эталонов. Узнать это заранее дешевле, чем из времени ожидания;
    заодно видно, где именно лежит эталон каждого варианта — вопрос, который
    задают первым.
    """
    from .matrix import MatrixError, from_config

    cfg = VisTestConfig.load()
    try:
        variants = from_config(cfg,
                               browsers=_split_list(args.browsers),
                               viewports=_split_list(args.viewports))
    except MatrixError as e:
        print(f"matrix: {e}", file=sys.stderr)
        return 2

    declared = bool(cfg.matrix.browsers or cfg.matrix.viewports
                    or args.browsers or args.viewports)
    if not declared:
        print("Матрица не объявлена — один вариант, как и было.\n"
              "Опишите её в vistest.yaml:\n\n"
              "  matrix:\n"
              "    browsers: [chromium, firefox]\n"
              "    viewports: [\"1440x900\", \"390x844\"]\n"
              "    base_viewport: \"1440x900\"   # его эталоны остаются на месте\n")

    width = max((len(v.label) for v in variants), default=0)
    print(f"{len(variants)} "
          f"{'вариант' if len(variants) == 1 else 'варианта(ов)'}:\n")
    for v in variants:
        mark = "  ← базовый, эталоны остаются на месте" if v.base and v.viewport else ""
        print(f"  {v.label:<{width}}   {v.platform}{mark}")
    root = cfg.baselines_path()
    print(f"\nЭталоны: {root}{os.sep}<ключ варианта>{os.sep}<снимок>{os.sep}baseline.png")
    return 0


def _snap_targets(args) -> list[dict]:
    if args.suite:
        import yaml

        raw = yaml.safe_load(Path(args.suite).read_text("utf-8")) or {}
        return raw.get("snapshots") or raw.get("pages") or []

    if not args.url:
        return []

    # --viewport с action="append" оставляет дефолт первым элементом
    viewports = args.viewport[1:] if len(args.viewport) > 1 else [args.viewport[0]]
    base = args.name or _name_from_url(args.url)
    out = []
    for vp in viewports:
        suffix = f"-{vp}" if len(viewports) > 1 else ""
        out.append({
            "url": args.url,
            "name": f"{base}{suffix}.png",
            "viewport": vp,
            "selector": args.selector,
            "wait": args.wait,
        })
    return out


def _name_from_url(url: str) -> str:
    from urllib.parse import urlparse

    p = urlparse(url)
    path = (p.path or "/").strip("/").replace("/", "-") or "index"
    return f"{p.netloc.replace(':', '_')}-{path}" if p.netloc else path


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


def _codegen(args) -> int:
    """Пересобрать тесты из паспортов эталонов.

    Источник правды — meta.json каждого эталона, а не сессия записи. Поэтому
    тесты можно пересобрать когда угодно: после правки адресов и шагов в UI,
    после удаления снимков, на новой машине.
    """
    from .record.codegen import write_test_files
    from .storage import FileBaselineStore

    cfg = VisTestConfig.load()
    platform = args.platform or platform_key()
    store = FileBaselineStore(cfg.baselines_path(platform))

    names = store.list_names()
    if not names:
        print(f"No baselines on platform {platform}", file=sys.stderr)
        return 1

    written = write_test_files(store, args.out, platform=platform,
                               project=args.project, pom=args.pom)
    if not written:
        print("Nothing to build from: not one baseline has an address.\n"
              "Set a url on the «Baselines» tab, or capture with "
              "`vistest snap URL`.", file=sys.stderr)
        return 1

    total = sum(n for _, n in written)
    print(f"Platform {platform}: {total} snapshot(s) in {len(written)} file(s)")
    for path, count in written:
        print(f"  {path}  ({count})")
    if args.pom:
        print(f"\nPage objects: {Path(args.out) / 'pages'}")
        print("  Edit the locators there — the marked block survives a rebuild.")

    skipped = len(names) - total
    if skipped > 0:
        print(f"\nSkipped for want of an address: {skipped}. "
              "Give them a url so they reach the tests.")
    return 0


def _report(args) -> int:
    """Оффлайн-отчёт о прогоне.

    Разбор регресса не заканчивается в интерфейсе: заводится тикет, и в него
    надо что-то вложить. Ссылка на localhost в тикете бесполезна — её не
    откроет ни разработчик на другой машине, ни тестировщик через полгода,
    когда прогон уже вычищен ретенцией.
    """
    from .report import build_report

    cfg = VisTestConfig.load()

    if args.run:
        run_dir = Path(args.run)
    else:
        runs_root = cfg.runs_path()
        candidates = [d for d in runs_root.glob("*") if d.is_dir()] \
            if runs_root.exists() else []
        if not candidates:
            print("No runs. Give the directory explicitly: vistest report DIR",
                  file=sys.stderr)
            return 1
        run_dir = max(candidates, key=lambda d: d.stat().st_mtime)
        print(f"Last run: {run_dir.name}")

    if not run_dir.exists():
        print(f"Directory not found: {run_dir}", file=sys.stderr)
        return 1

    path = build_report(run_dir, args.out, title=args.title)
    size = path.stat().st_size / 1048576
    print(f"Report: {path.resolve()}  ({size:.1f} MB)")
    print("The file is self-contained — attach it to a ticket and open it offline.")

    if args.open:
        import webbrowser

        webbrowser.open(path.resolve().as_uri())
    return 0


def _user(args) -> int:
    """Пользователи общей инсталляции.

    Первый администратор заводится только отсюда — намеренно. Окно «создайте
    администратора» при первом заходе было бы дырой ровно до момента, пока его
    не заполнили, и в сети её найдут быстрее вас.
    """
    import getpass

    from .api.auth import ROLES, create_user, deactivate, list_users, set_password, set_role
    from .api.db import Database

    cfg = VisTestConfig.load()
    db = Database(cfg.root_path / "vistest.db")

    def ask_password() -> str:
        first = getpass.getpass("Password (8 characters at least): ")
        if first != getpass.getpass("Again: "):
            raise SystemExit("The passwords do not match")
        return first

    if args.sub == "list":
        users = list_users(db)
        if not users:
            print("No users — the service runs in local mode:")
            print("  access is open, decisions are recorded as «local».")
            print("\nCreate an administrator:")
            print("  vistest user add <login> --role admin")
            return 0
        print(f"{'login':20s} {'role':10s} {'status':10s} last sign-in")
        for u in users:
            print(f"{u['login']:20s} {u['role']:10s} "
                  f"{'active' if u['active'] else 'disabled':10s} "
                  f"{u['last_login'] or '—'}")
        if not any(u["role"] == "admin" and u["active"] for u in users):
            print("\n⚠ Not one active administrator — the installation cannot "
                  "be managed through the interface.")
        return 0

    if args.sub == "add":
        password = args.password or ask_password()
        try:
            create_user(db, args.login, password, role=args.role,
                        name=args.name)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 1
        print(f"Created: {args.login} ({args.role})")
        if args.role == "admin":
            print("From now on everyone has to sign in: local mode turns "
                  "itself off as soon as the first user exists.")
        return 0

    if args.sub == "passwd":
        password = args.password or ask_password()
        try:
            ok = set_password(db, args.login, password)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 1
        if not ok:
            print(f"No such user: {args.login}", file=sys.stderr)
            return 1
        print("Password changed. Open sessions keep working — to drop them, "
              "disable the user and create them again.")
        return 0

    if args.sub == "role":
        if not set_role(db, args.login, args.role):
            print(f"No such user: {args.login}", file=sys.stderr)
            return 1
        print(f"{args.login}: role {args.role}")
        print(f"Roles: {', '.join(ROLES)}")
        return 0

    if args.sub in ("grant", "revoke", "rights"):
        return _rights_cmd(args, db)

    if args.sub == "pending":
        from vistest.api.auth import pending_users

        rows = pending_users(db)
        if not rows:
            print("No access requests waiting.")
            return 0
        print(f"{'login':20s} {'name':24s} requested")
        for r in rows:
            print(f"{r['login']:20s} {(r['name'] or ''):24s} {r['created_at']}")
        print("\nApprove: vistest user approve <login> --role viewer")
        return 0

    if args.sub == "approve":
        from vistest.api.auth import approve_user

        if not approve_user(db, args.login, args.role):
            print(f"No request from {args.login}", file=sys.stderr)
            return 1
        print(f"{args.login} can sign in now, role {args.role}")
        return 0

    if args.sub == "setup-reopen":
        # Единственный способ вернуться, когда единственный администратор
        # потерял доступ. Живёт в CLI, а не в интерфейсе, ровно потому, что в
        # интерфейсе это была бы кнопка «сделать меня администратором».
        from vistest.api.auth import any_users, reopen_setup

        if any_users(db):
            print("This installation already has an active user. Reopening the "
                  "setup form would let anyone who reaches the port create a "
                  "second administrator.\n"
                  "Disable the accounts first if you really mean it: "
                  "vistest user disable <login>", file=sys.stderr)
            return 1
        reopen_setup(db, "cli")
        print("The setup form is open again — create the administrator in the "
              "interface.")
        return 0

    if args.sub == "disable":
        if not deactivate(db, args.login):
            print(f"No such user: {args.login}", file=sys.stderr)
            return 1
        print(f"{args.login} is disabled and their sessions are closed. "
              "The audit records are kept.")
        return 0

    return 1


def _rights_cmd(args, db) -> int:
    """Права на отдельные проекты.

    Глобальная роль отвечает на «что человек может везде». Со вторым
    подключённым набором `reviewer` начинает означать «может переписать эталон
    любого проекта», а утверждение необратимо. Право поднимает роль в одном
    проекте и никогда её не понижает — понижающее право означало бы, что
    администратора можно лишить доступа, а чинить это стало бы некому.
    """
    from .api import rights

    if args.sub == "rights":
        rows = rights.list_grants(db, login=args.login) if args.login \
            else rights.list_grants(db)
        if not rows:
            print("No per-project rights — the global role applies everywhere.")
            print("\nGrant one:")
            print("  vistest user grant <login> <project> reviewer")
            print("\nProjects that can be named here:")
            for name in rights.known(db) or ["— none yet —"]:
                print(f"  {name}")
            return 0
        print(f"{'login':20s} {'project':24s} {'role':10s} granted by")
        for r in rows:
            print(f"{r['login']:20s} {r['project_key']:24s} "
                  f"{r['role']:10s} {r['granted_by'] or '—'}")
        return 0

    if not db.one("SELECT id FROM user WHERE login=?",
                  (args.login.strip().lower(),)):
        # Опечатка в логине иначе выглядит как выданное право: строка есть,
        # человек уверен, что доступ дал, а коллега по-прежнему не может ничего.
        print(f"No such user: {args.login}", file=sys.stderr)
        return 1

    if args.sub == "grant":
        rights.grant(db, args.login.strip().lower(), args.project, args.role,
                     by="cli")
        print(f"{args.login}: {args.role} in «{args.project}»")
        print("The right only raises the role — the global one stays as it is.")
        return 0

    if not rights.revoke(db, args.login.strip().lower(), args.project):
        print(f"{args.login} had no right on «{args.project}»", file=sys.stderr)
        return 1
    print(f"{args.login}: the right on «{args.project}» is revoked")
    return 0


def _evidence(args) -> int:
    """Накопленная статистика качества.

    Отвечает на вопрос, который задаёт покупатель: «а у вас правда меньше
    ложных падений?». Синтетический бенчмарк отвечает на него про движок,
    эта команда — про живой проект за реальный период.
    """
    from .api.db import Database
    from .report.evidence import collect_evidence, to_html, to_markdown

    cfg = VisTestConfig.load()
    db_path = cfg.root_path / "vistest.db"
    if not db_path.exists():
        print("There is no run history: the database was never created. The "
              "statistics accumulate from ordinary runs — run the tests first.",
              file=sys.stderr)
        return 1

    data = collect_evidence(Database(db_path), args.project, args.days)

    if args.format == "json":
        text = json.dumps(data, ensure_ascii=False, indent=2)
    elif args.format == "html":
        text = to_html(data)
    elif args.format == "md":
        text = to_markdown(data)
    else:
        text = _evidence_text(data)

    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"Written: {path.resolve()}")
    else:
        print(text)
    return 0


def _evidence_text(d: dict) -> str:
    ff, v = d["false_fail"], d["volume"]
    lines = [
        "=== VisTest quality statistics ===",
        f"  period:       {d['period']['from'] or '—'} — {d['period']['to'] or '—'}"
        f"  (last {d['days']} days)",
        f"  runs:         {v['runs']}",
        f"  comparisons:  {v['comparisons']}  "
        f"(passed {v['passed']}, failed {v['failed']}, new {v['new_baselines']})",
        "",
        f"  FALSE FAILURES: {ff['rate'] * 100:.1f}%"
        f"   95% CI {ff['ci95'][0] * 100:.1f}–{ff['ci95'][1] * 100:.1f}%",
        f"  measured over {ff['reviewed']} reviewed of {v['failed']} "
        f"({ff['coverage'] * 100:.0f}% reviewed)",
        f"  {ff['confidence']}",
        "",
        "  Measurement conditions:",
    ]
    for k, val in d["conditions"].items():
        lines.append(f"    {k:24s} {val}")

    lines.append("")
    lines.append("  What this number does not prove:")
    for note in d["caveats"]:
        lines.append(f"    • {note}")
    lines.append("")
    lines.append("  A document to publish:  vistest evidence --format md "
                 "-o EVIDENCE.md")
    return "\n".join(lines)


def _project(args) -> int:
    """Подключённые наборы тестов из командной строки.

    То же, что вкладка «Проекты», но пригодное для CI: `project run` возвращает
    1, если хоть один снимок разошёлся с эталоном.
    """
    from .projects import ProjectRegistry, discover, suggest

    cfg = VisTestConfig.load()
    reg = ProjectRegistry(cfg)

    if args.sub == "list":
        projects = reg.list()
        if not projects:
            print("Nothing is connected. Connect one: vistest project add <path>")
            return 0
        for p in projects:
            print(f"{p.key}  [{p.mode()}]  {p.root}")
            print(f"    tests: {p.tests or '(the whole root)'}")
            for b in p.baselines:
                cond = (" when " + ", ".join(f"{k}={v}" for k, v in b.when.items())
                        if b.when else " by default")
                print(f"    baselines: {b.dir}{cond}")
            for problem in p.validate():
                print(f"    ⚠ {problem}")
        return 0

    if args.sub == "add":
        info = discover(args.root)
        if not info["exists"]:
            print(f"Directory not found: {args.root}", file=sys.stderr)
            return 1

        proposal = suggest(args.root, key=args.key or "")
        print(f"Project: {proposal.name}  ({proposal.root})")
        print(f"  key:          {proposal.key}")
        print(f"  tests:        {proposal.tests or '(the whole root)'}")
        print(f"  interpreter:  {proposal.resolve_python()}")
        print(f"  mode:         {proposal.mode()}")
        if proposal.adapter.target:
            print(f"  hook point:   {proposal.adapter.target}")
        for b in proposal.baselines:
            cond = (", ".join(f"{k}={v}" for k, v in b.when.items())
                    if b.when else "by default")
            print(f"  baselines:    {b.dir}  ({cond})")
        if info["markers"]:
            print(f"  markers:      {', '.join(info['markers'])}")
        if info["env_vars"]:
            print(f"  variables:    {', '.join(info['env_vars'][:10])}")
        for n in info["notes"]:
            print(f"  ! {n}")

        others = [c["target"] for c in info["adapter_candidates"]
                  if c["target"] != proposal.adapter.target]
        if others:
            print("\n  Other candidates for the hook point:")
            for t in others[:8]:
                print(f"    {t}")
            print("  To change it: edit .vistest/projects.yaml or the "
                  "«Projects» tab.")

        if args.dry_run:
            return 0
        reg.save(proposal)
        print(f"\nConnected. Description: {reg.path}")
        print(f"Run it: vistest project run {proposal.key}")
        return 0

    if args.sub == "check":
        from .external import preflight

        project = reg.get(args.key)
        if project is None:
            print(f"Project {args.key!r} is not connected", file=sys.stderr)
            return 1

        overrides = {}
        for pair in args.env:
            if "=" in pair:
                k, v = pair.split("=", 1)
                overrides[k.strip()] = v

        for problem in project.validate():
            print(f"⚠ {problem}")

        res = preflight(project, cfg=cfg, env_overrides=overrides)
        print(f"\ninterpreter:      {res['interpreter']}"
              + ("  (the VisTest environment)" if res["own_interpreter"] else ""))
        print(f"pytest:           {'yes' if res['pytest'] else 'NO'}")
        print(f"collected:        {res['collected']}")
        if res["missing"]:
            print(f"missing modules:  {', '.join(res['missing'])}")
        if res["missing_fixtures"]:
            print(f"missing fixtures: {', '.join(res['missing_fixtures'])}")
        if res["plugins"]:
            print(f"packages needed:  {', '.join(res['plugins'])}")
        if not res["ok"]:
            print("\n--- pytest output ---")
            for line in res["output"][-40:]:
                print(line)
        if res["hint"]:
            print(f"\n{res['hint']}")
        return 0 if res["ok"] else 1

    if args.sub == "deps":
        from .external import install_requirements, preflight

        project = reg.get(args.key)
        if project is None:
            print(f"Project {args.key!r} is not connected", file=sys.stderr)
            return 1

        if args.all:
            return install_requirements(project, all_requirements=True)

        res = preflight(project, cfg=cfg)
        if res["ok"]:
            print("Everything is in place — the tests collect and the fixtures resolve.")
            return 0
        if not res["missing"] and not res["plugins"]:
            print("Collection fails, but no package is missing: the cause is in "
                  "their conftest, not in the dependencies. Output above.",
                  file=sys.stderr)
            return 1
        return install_requirements(project, only=res["missing"],
                                    packages=res["plugins"])

    if args.sub == "baselines":
        from dataclasses import replace as _replace

        from .external import baseline_status, reset_baselines

        project = reg.get(args.key)
        if project is None:
            print(f"Project {args.key!r} is not connected", file=sys.stderr)
            return 1

        if args.use:
            reg.save(_replace(project, baseline_store=args.use))
            print(f"The engine will compare against the set: {args.use}")
            if args.use == "vistest":
                print("The first run captures the baselines and will be green "
                      "— there is nothing to compare against yet. The "
                      "project's folder is not touched.")
            return 0

        if args.reset:
            reset_baselines(project, cfg=cfg, platform=args.platform or "")
            return 0

        st = baseline_status(project, cfg)
        used = st["store"]
        where = ("VisTest's own set" if used == "vistest"
                 else "the project's baselines")
        print(f"Right now the engine compares against: {where}")
        print(f"Current platform: {st['current_platform']}\n")

        print("In the project:")
        for b in st["project"]:
            cond = (", ".join(f"{k}={v}" for k, v in b["when"].items())
                    if b["when"] else "by default")
            mark = "" if b["exists"] else "  (no such folder)"
            print(f"  {b['dir']}  {b['count']} pcs.  ({cond}){mark}")

        print("\nVisTest's own:")
        if not st["vistest"]:
            print("  empty — captured on the first run in vistest mode")
        for v in st["vistest"]:
            here = ("  ← current platform"
                    if v["platform"] == st["current_platform"] else "")
            print(f"  {v['platform']}  {v['count']} pcs.{here}")

        print("\nSwitch it:  vistest project baselines "
              f"{args.key} --use vistest")
        return 0

    if args.sub == "rm":
        if not reg.delete(args.key):
            print(f"Project {args.key!r} is not connected", file=sys.stderr)
            return 1
        print(f"Disconnected: {args.key}. The project's files are untouched.")
        return 0

    if args.sub == "run":
        from .external import publish, run_project

        project = reg.get(args.key)
        if project is None:
            print(f"Project {args.key!r} is not connected", file=sys.stderr)
            return 1

        overrides = {}
        for pair in args.env:
            if "=" not in pair:
                print(f"--env expects K=V, got {pair!r}", file=sys.stderr)
                return 2
            k, v = pair.split("=", 1)
            overrides[k.strip()] = v

        extra = [a for a in (args.rest or []) if a != "--"]
        run = run_project(project, cfg=cfg, env_overrides=overrides,
                          update_baselines=args.update, extra_args=extra)

        if not args.no_history:
            try:
                publish(run, project, cfg=cfg,
                        log=lambda t: None if args.json else print(t))
            except Exception as e:
                print(f"could not write to the history: {e}", file=sys.stderr)

        if args.json:
            print(json.dumps(run.to_dict(), indent=2, ensure_ascii=False))
            return 1 if run.failed else 0

        s = run.summary()
        print(f"\nSnapshots: {s['total']}, failed: {s['failed']}, "
              f"new baselines: {s['new']}, passed: {s['passed']}")
        for r in run.results:
            if r["verdict"] != "pass":
                print(f"  {r['verdict']:12s} {r['name']}  "
                      f"severity={r.get('max_severity', 0):.1f}")
        for e in run.errors:
            print(f"  ! {e}", file=sys.stderr)
        print(f"Artifacts: {run.run_dir}")
        return 1 if run.failed else 0

    return 1


def _rm(args) -> int:
    """Удаление эталонов из командной строки."""
    import shutil

    from .storage import FileBaselineStore

    cfg = VisTestConfig.load()
    platform = args.platform or platform_key()
    store = FileBaselineStore(cfg.baselines_path(platform))

    names = store.list_names() if args.all else args.names
    if not names:
        print("Nothing to delete: give names or --all", file=sys.stderr)
        return 2

    print(f"Platform {platform}, {len(names)} to delete:")
    for n in names[:30]:
        print(f"  {n}")
    if len(names) > 30:
        print(f"  … and {len(names) - 30} more")

    if not args.yes and input("Delete? [y/N] ").strip().lower() not in ("y", "yes"):
        print("Cancelled")
        return 1

    removed = 0
    for name in names:
        d = store.dir_for(name)
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
            removed += 1
    print(f"Baselines deleted: {removed}")
    return 0


def _prune(args) -> int:
    """Уборка истории прогонов.

    Каждый полностраничный дифф — это несколько мегабайт PNG. Без ретенции
    каталог `.vistest` за пару месяцев съедает десятки гигабайт.
    """
    import shutil

    cfg = VisTestConfig.load()
    runs_root = cfg.runs_path()
    if not runs_root.exists():
        print("No runs")
        return 0

    dirs = sorted((d for d in runs_root.iterdir() if d.is_dir()),
                  key=lambda d: d.stat().st_mtime, reverse=True)
    keep = set(dirs[: max(0, args.keep_last)])

    victims = []
    cutoff = None
    if args.days is not None:
        import time

        cutoff = time.time() - args.days * 86400
    for d in dirs:
        if d in keep:
            continue
        if cutoff is not None and d.stat().st_mtime >= cutoff:
            continue
        victims.append(d)

    if not victims:
        print(f"Nothing to delete (keeping the last {args.keep_last})")
        return 0

    total = sum(_dir_size(d) for d in victims)
    print(f"{len(victims)} runs to delete, {total / 1_048_576:.1f} MB")
    for d in victims[:15]:
        print(f"  {d.name}")
    if len(victims) > 15:
        print(f"  … and {len(victims) - 15} more")

    if not args.yes and input("Delete? [y/N] ").strip().lower() not in ("y", "yes"):
        print("Cancelled")
        return 1

    for d in victims:
        shutil.rmtree(d, ignore_errors=True)

    if not args.runs_only:
        _prune_db(cfg, {d.name for d in victims})

    print(f"Deleted: {len(victims)} runs, freed "
          f"{total / 1_048_576:.1f} MB")
    return 0


def _prune_db(cfg, run_keys: set[str]) -> None:
    """Убрать те же прогоны из БД сервиса, если она есть."""
    db_path = cfg.root_path / "vistest.db"
    if not db_path.exists():
        return
    try:
        from .api.db import Database

        db = Database(db_path)
        for key in run_keys:
            db.execute("DELETE FROM run WHERE run_key=?", (key,))
    except Exception as e:
        print(f"  the database rows were not deleted: {e}", file=sys.stderr)


def _dir_size(path: Path) -> int:
    try:
        return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    except Exception:
        return 0


def _approve(args) -> int:
    from .capture.playwright_capture import read_png
    from .storage import BaselineRecord, FileBaselineStore

    cfg = VisTestConfig.load()
    store = FileBaselineStore(cfg.baselines_path(platform_key()))
    store.save(BaselineRecord(name=args.name, image=read_png(args.actual),
                              meta={"approved_by": "cli"}))
    print(f"Baseline updated: {store.dir_for(args.name)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())


def _gate(args) -> int:
    """Пройден ли прогон — для шага CI, который обязан покраснеть.

    Существует потому, что код возврата есть только у pytest. Прогон
    подключённого проекта в режиме наблюдения и прогон, запущенный кнопкой в
    интерфейсе, никакого pytest не имеют: их результат для конвейера не
    существовал вовсе.

    Читает каталог прогона на диске, а не сервис: в CI сервиса может не быть, а
    `run.json` пишется всегда.
    """
    from .report.junit import gate, render_junit

    cfg = VisTestConfig.load()
    run_dir, payload = _load_run(args.run, cfg)
    if payload is None:
        return 2

    comparisons = payload.get("comparisons") or payload.get("results") or []
    verdict = gate(comparisons,
                   honor_review=not args.ignore_review,
                   allow_new=not args.fail_on_new)

    git = payload.get("git") or {}
    target = Path(args.junit) if args.junit else run_dir / "junit.xml"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_junit(
            comparisons, suite=payload.get("project") or cfg.service.project,
            run_key=payload.get("run_id") or run_dir.name,
            platform=payload.get("platform") or "",
            branch=git.get("branch") or "", git_sha=git.get("sha") or "",
            honor_review=not args.ignore_review), encoding="utf-8")
        print("JUnit:", target)
    except OSError as e:
        print(f"could not write {target}: {e}")

    print(("PASSED: " if verdict["ok"] else "STOP: ") + verdict["reason"])
    for item in verdict["blocking"][:20]:
        print(f"  {item['name']} — {item['why']}")
    if len(verdict["blocking"]) > 20:
        print(f"  … and {len(verdict['blocking']) - 20} more")

    _github_summary(payload, comparisons, run_dir)
    _github_annotations(verdict)

    return 0 if verdict["ok"] else 1


# --------------------------------------------------------------------------- #
#  Отчёт в интерфейсе сборки
#
#  JUnit показывают все, но по-разному и не на первом экране: в GitHub Actions
#  до списка тестов надо провалиться в шаг. А смотрят на страницу сборки —
#  и видели там ровно «Process completed with exit code 1».
#
#  GitHub даёт для этого два готовых места, и оба — просто файл и просто
#  stdout, без токенов и без сети:
#
#  * `$GITHUB_STEP_SUMMARY` — markdown, попадающий на страницу сборки;
#  * `::error` — аннотация в списке проблем и в диффе.
#
#  У GitLab аналога нет — он читает JUnit и показывает его сам, поэтому там
#  ничего и не печатается. Определяем по переменным окружения: печатать
#  `::error` в чужом конвейере значит сорить в лог тем, что там никто не
#  разберёт.
# --------------------------------------------------------------------------- #
def _clusters_for(comparisons: list) -> list:
    """Группировка падений по общей причине. Молча пустая, если не вышло."""
    try:
        from .cluster import summarize

        failed = [c for c in comparisons if c.get("verdict") == "fail"]
        return summarize(failed).get("clusters") or []
    except Exception:
        return []


def _github_summary(payload: dict, comparisons: list, run_dir) -> None:
    """Та же сводка, что уходит в комментарий PR, — на страницу сборки.

    Именно та же, а не похожая: две почти одинаковые сводки расходятся на
    третьем изменении, и человек начинает сверять их между собой вместо того,
    чтобы смотреть на дифф.
    """
    target = os.getenv("GITHUB_STEP_SUMMARY")
    if not target:
        return
    try:
        from .report.comment import render_comment

        git = payload.get("git") or {}
        body = render_comment(
            comparisons, run_key=payload.get("run_id") or run_dir.name,
            platform=payload.get("platform") or "",
            branch=git.get("branch") or "", git_sha=git.get("sha") or "",
            base_url=(os.getenv("VISTEST_PUBLIC_URL") or "").strip(),
            clusters=_clusters_for(comparisons))
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(body + "\n")
    except Exception:
        # Отчёт для глаз не может стоить вердикта: код возврата уже посчитан.
        pass


def _ann_message(text) -> str:
    """Текст аннотации.

    Перевод строки обрывает команду на середине, и вместо аннотации в лог
    попадает её хвост открытым текстом. Имя снимка с процентом сделает то же
    самое, поэтому экранируется и он — иначе экранирование ломает себя само.
    """
    return (str(text if text is not None else "")
            .replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
            .strip())


def _ann_prop(text) -> str:
    """Значение свойства (`title=…`) — правила строже, чем у текста.

    Свойства разделены запятой, а от текста отбиты двоеточием: имя снимка
    `shop.example/a,b.png` без экранирования превратится в два свойства, второе
    из которых GitHub не знает.
    """
    return _ann_message(text).replace(":", "%3A").replace(",", "%2C")


def _github_annotations(verdict: dict) -> None:
    if not os.getenv("GITHUB_ACTIONS"):
        return
    if verdict["ok"]:
        print(f"::notice title=VisTest::{_ann_message(verdict['reason'])}")
        return
    # Двадцать — предел не наш: лог с сотней строк `::error` нечитаем ровно так
    # же, как таблица на сотню строк в комментарии.
    for item in verdict["blocking"][:20]:
        print(f"::error title=VisTest: {_ann_prop(item['name'])}"
              f"::{_ann_message(item['why'])}")
    if len(verdict["blocking"]) > 20:
        print(f"::error title=VisTest::"
              f"{len(verdict['blocking']) - 20} more snapshots are blocking")


def _notify(args) -> int:
    """Дайджест непросмотренных падений — то же, что делают часы внутри сервиса.

    Существует отдельной командой ради двух случаев: проверить, что именно уйдёт
    в чат, не отправляя (`--dry-run`), и повесить на чужой планировщик там, где
    сервис поднимают по требованию, а не держат постоянно.
    """
    from .api.db import Database
    from .api.notify import AFTER_HOURS, run_once, set_text

    cfg = VisTestConfig.load()
    db = Database(cfg.root_path / "vistest.db")
    if args.after_hours is not None:
        set_text(db, AFTER_HOURS, str(args.after_hours), "cli")

    result = run_once(db, base_url=(os.getenv("VISTEST_PUBLIC_URL") or "").strip(),
                      force=args.force or args.dry_run, dry_run=args.dry_run)
    if result.get("text"):
        print(result["text"])
    if result.get("sent"):
        print(f"sent: {result['count']}")
    else:
        print(f"not sent: {result.get('reason')}"
              + (f" — {result['error']}" if result.get("error") else ""))
    # Код возврата — про доставку, а не про наличие падений: «нечего слать» это
    # нормальный день, а не сбой.
    return 1 if result.get("error") else 0


def _resolve_run_dir(value, cfg) -> Path | None:
    """Каталог прогона: путь, ключ или «последний»."""
    runs = cfg.runs_path()
    if value:
        direct = Path(value)
        if direct.is_dir():
            return direct
        by_key = runs / value
        return by_key if by_key.is_dir() else None
    candidates = [d for d in runs.glob("*") if d.is_dir()]
    if not candidates:
        return None
    # По времени изменения, а не по имени: ключи внешних прогонов начинаются с
    # «ext-» и сортируются не по дате.
    return max(candidates, key=lambda d: d.stat().st_mtime)


def _load_run(value, cfg):
    """Каталог прогона и его содержимое: общее для `gate` и `comment`."""
    import json as _json

    run_dir = _resolve_run_dir(value, cfg)
    if run_dir is None:
        print("no runs found: not one directory in", cfg.runs_path())
        return None, None
    for name in ("run.json", "external.json"):
        candidate = run_dir / name
        if candidate.exists():
            try:
                return run_dir, _json.loads(candidate.read_text("utf-8"))
            except Exception as e:
                print(f"{candidate}: could not parse it — {e}")
                return None, None
    print(f"{run_dir} has neither run.json nor external.json")
    return None, None


def _comment(args) -> int:
    """Сводка прогона в markdown. Публикует её CI, не мы.

    Учить сервис ходить в GitHub и GitLab самому значило бы держать по клиенту
    на площадку и просить токен с правом писать в чужие репозитории — взамен
    ничего, чего не делает `gh pr comment --body-file`.
    """
    from .report.comment import render_comment

    cfg = VisTestConfig.load()
    run_dir, payload = _load_run(args.run, cfg)
    if payload is None:
        return 2

    comparisons = payload.get("comparisons") or payload.get("results") or []
    git = payload.get("git") or {}

    body = render_comment(
        comparisons, run_key=payload.get("run_id") or run_dir.name,
        platform=payload.get("platform") or "",
        branch=git.get("branch") or "", git_sha=git.get("sha") or "",
        base_url=(os.getenv("VISTEST_PUBLIC_URL") or "").strip(),
        # Группировка по общей причине — то, ради чего этот комментарий вообще
        # читают: двадцать красных снимков от одного отступа в шапке это один
        # вопрос, а не двадцать.
        clusters=_clusters_for(comparisons))

    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        print("comment:", target)
    else:
        print(body, end="")
    return 0
