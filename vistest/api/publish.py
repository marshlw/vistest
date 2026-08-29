# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""External run into the shared run history.

Why. A connected project is executed by a separate process, and until now its
result landed in the «Projects» card as a flat table: verdict, severity,
numbers. There was nothing to look at to inspect the divergence.

Meanwhile the whole viewer is already written and works for local runs — the
slider, onion, blink, heatmap, the region table with highlighting. It makes
sense not to build a second one just like it, but to put the external run into
the same database. Then it opens on the «Runs» tab alongside the local ones,
and the question «what changed there» is answered with a single click.

A side benefit, which is exactly what was requested: the history now captures
**all** snapshots, not only the failed ones. A passing snapshot is a result
too: it shows what exactly was accepted as the norm. Previously it stayed a
«pass» line without a single picture.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


def _safe(text: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in str(text))


def publish_external_run(db, artifacts_root: Path, run: dict, *,
                         project: str, platform: str = "",
                         browser: str = "chromium", log=print) -> int | None:
    """Write the run into the DB and lay out artifacts. Returns the run id."""
    comparisons = [c for c in (run.get("results") or []) if c.get("name")]
    if not comparisons:
        log("nothing to write into history: not a single comparison")
        return None

    totals = {
        "total": len(comparisons),
        "passed": sum(c.get("verdict") == "pass" for c in comparisons),
        "failed": sum(c.get("verdict") == "fail" for c in comparisons),
        "new": sum(c.get("verdict") == "new_baseline" for c in comparisons),
    }

    scope = run.get("baseline_scope") or "project"
    if scope not in ("project", "vistest"):
        scope = "project"

    payload = {
        "run_id": Path(run.get("run_dir") or "").name or _new_key(),
        "platform": platform or _platform(),
        "browser": browser,
        "created_at": datetime.now(timezone.utc).isoformat(),
        # Branch and commit of THEIR repository, taken at the moment of the
        # run. An empty dict stood here, so the whole history of connected
        # projects lay without git: «—» in the list, «—» in the report, and
        # nothing to correlate with their build.
        "git": run.get("git") or {},
        "totals": totals,
        "comparisons": comparisons,
        "external": True,
        # The three fields that let a later approval find the right store.
        # Without them `approve` had no choice but to guess, and it guessed the
        # service's own global store — which this run never read.
        "project_key": run.get("project") or project,
        "baseline_scope": scope,
        "baseline_dir": run.get("baseline_dir") or "",
    }

    run_id = db.ingest_run(payload, project)

    # A re-sent run replaces the old row and gets a new id; the artifacts of
    # the old id would otherwise stay on disk forever, owned by nothing.
    replaced = getattr(db, "replaced_run_id", None)
    if replaced and replaced != run_id:
        _drop_dir(artifacts_root / str(replaced), log=log)

    _copy_artifacts(db, artifacts_root, run_id, comparisons, log=log)
    log(f"run written into history: #{run_id}, snapshots {totals['total']} "
        f"· baselines: {scope}")
    return run_id


def _drop_dir(path: Path, *, log) -> None:
    try:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
            log(f"artifacts of the replaced run removed: {path.name}")
    except OSError:
        pass


def _copy_artifacts(db, artifacts_root: Path, run_id: int,
                    comparisons: list[dict], *, log) -> None:
    """Move the pictures to where the service serves them from, and rewrite links.

    Local runs upload artifacts over HTTP, but here we are already inside the
    service — pushing files through our own port would be odd. We copy directly
    and normalize the paths to the same `/files/...` that the interface expects.
    """
    limit = 25 * 1024 * 1024

    # One run can hold two comparisons under the same name: a parametrized test
    # checks the same screen twice. The rows were written in the order of
    # `comparisons`, so we hand out ids in that same order — otherwise both
    # comparisons would get the pictures of the first one.
    rows_by_name: dict[str, list[int]] = {}
    for row in db.query(
            "SELECT c.id AS id, s.name AS name FROM comparison c"
            " JOIN snapshot s ON s.id=c.snapshot_id"
            " WHERE c.run_id=? ORDER BY c.id", (run_id,)):
        rows_by_name.setdefault(row["name"], []).append(row["id"])

    for index, comp in enumerate(comparisons):
        name = comp.get("name") or ""
        uris: dict[str, str] = {}
        # A separate folder per comparison, not per name: two snapshots with the
        # same name would otherwise overwrite each other's pictures.
        slot = f"{_safe(name)}-{index}" if len(rows_by_name.get(name, [])) > 1 \
            else _safe(name)

        for kind, path in (comp.get("artifacts") or {}).items():
            if not isinstance(path, str) or kind.startswith("_"):
                continue
            src = Path(path)
            try:
                if not src.is_file() or src.stat().st_size > limit:
                    continue
            except OSError:
                continue

            dest_dir = artifacts_root / str(run_id) / slot
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / f"{_safe(kind)}{src.suffix or '.png'}"
            try:
                shutil.copy2(src, dest)
            except OSError as e:
                log(f"  artifact {kind} for {name} not copied: {e}")
                continue
            uris[kind] = f"/files/{run_id}/{slot}/{dest.name}"

        ids = rows_by_name.get(name) or []
        if not uris or not ids:
            continue
        db.execute("UPDATE comparison SET artifacts=? WHERE id=?",
                   (json.dumps(uris, ensure_ascii=False), ids.pop(0)))


def _platform() -> str:
    from ..config import platform_key

    return platform_key()


def _new_key() -> str:
    return "ext-" + datetime.now().strftime("%Y%m%d-%H%M%S")
