# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Which baseline store a decision applies to.

This module exists because the answer used to be guessed in four different
places, and three of them guessed wrong.

A snapshot named `login.png` can live in three different stores at the same
time:

  * **global**  — the service's own set, `.vistest/baselines/<platform>/`.
    Local runs, `POST /api/check`, mouse recording and `/api/suite/run` all
    compare against it.
  * **project** — the PNGs committed in the connected project's own
    repository. Their pipeline made them, their tests read them, and VisTest
    only borrows them for a comparison.
  * **vistest** — the set VisTest captured *itself* for that connected
    project, kept outside their repository in
    `.vistest/external/<key>/baselines/<platform>/`.

The three are different files with the same name, and the choice between them
is a property of **the run**, not of the project: the same suite is launched
against its own PNGs by one button and against the VisTest set by another.

Before this module, `approve` always wrote into `global`, and
`external.approve` chose by the project's *saved setting*. So the sequence
that any user would try first — run on VisTest baselines, see a failure,
accept it as the new baseline, run again — changed a file the run never reads
and failed again on exactly the same diff.

Everything that writes a baseline now goes through `store_for_run` or
`store_for`. There is no second place to get this wrong.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..config import VisTestConfig, platform_key
from ..storage import BaselineStore, ExternalBaselineStore, FileBaselineStore

SCOPES = ("global", "project", "vistest")

# The platform of a run comes out of the database, and it got there from
# whatever a runner posted. Here it becomes a directory name, so it is checked
# like one rather than trusted like a value we wrote ourselves.
_PLATFORM_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}")


class ScopeError(RuntimeError):
    """The requested store cannot be resolved — said in words a person can act on."""


def store_for(scope: str, *, cfg: VisTestConfig, platform: str = "",
              project=None, baseline_dir: str | Path | None = None
              ) -> tuple[BaselineStore, Path]:
    """Resolve (store, directory) for a scope.

    `baseline_dir` wins when it is given: it is what the run actually compared
    against, recorded at the moment of the run. Recomputing the directory from
    the project rules months later can land somewhere else — the rules match on
    the environment, and the environment has moved on.
    """
    scope = (scope or "global").lower()
    if scope not in SCOPES:
        raise ScopeError(f"Unknown baseline scope: {scope!r}")

    platform = platform or platform_key()
    if not _PLATFORM_RE.fullmatch(platform):
        raise ScopeError(
            f"Invalid platform key {platform!r} on this run — it is used as a "
            "directory name and may only contain letters, digits, dot, dash "
            "and underscore.")

    if scope == "global":
        directory = Path(baseline_dir) if baseline_dir else cfg.baselines_path(platform)
        return FileBaselineStore(directory), directory

    if project is None:
        raise ScopeError(
            f"Scope {scope!r} refers to a connected project, but the project is "
            "no longer connected — reconnect it, or review this run as history "
            "only.")

    if scope == "vistest":
        directory = (Path(baseline_dir) if baseline_dir
                     else project.vistest_baselines_path(cfg, platform))
        return FileBaselineStore(directory), directory

    # scope == "project": their own committed PNGs, flat layout, sidecar for
    # everything VisTest wants to keep but must not put into their repository.
    directory = Path(baseline_dir) if baseline_dir else project.baseline_dir()
    if directory is None:
        raise ScopeError(
            "No baselines folder selected for this project: no rule in the "
            "project description matched the run environment.")
    return ExternalBaselineStore(directory, project.sidecar_dir(cfg)), Path(directory)


def run_scope(db, run_id: int) -> dict:
    """What this run compared against. Everything needed to resolve a store."""
    row = db.one(
        "SELECT r.baseline_scope, r.baseline_dir, r.project_key, r.platform,"
        "       r.branch, p.name AS project_name"
        "  FROM run r JOIN project p ON p.id = r.project_id WHERE r.id = ?",
        (run_id,))
    if not row:
        return {"scope": "global", "baseline_dir": None, "project_key": None,
                "platform": "", "branch": ""}
    return {
        "scope": row.get("baseline_scope") or "global",
        "baseline_dir": row.get("baseline_dir"),
        "project_key": row.get("project_key"),
        "platform": row.get("platform") or "",
        "branch": row.get("branch") or "",
        "project_name": row.get("project_name") or "",
    }


def branch_layer(store: BaselineStore, directory: Path, *, db, cfg,
                 platform: str, branch: str, scope: str,
                 project_key: str | None = None
                 ) -> tuple[BaselineStore, Path, str]:
    """Наложение ветки поверх готового стора — если режим включён.

    Возвращает (стор, каталог записи, метка ветки). Метка пустая, когда ветки
    нет: прогон основной, и никакого наложения быть не должно.

    Здесь же и вся проверка «а надо ли»: режим выключен, ветка совпадает с
    основной, ветка неизвестна — во всех трёх случаях отдаём то, что пришло,
    ничего не оборачивая. Развести два набора там, где человек видел один, —
    худшее, что эта функция может сделать.
    """
    from ..storage.branch import BranchBaselineStore, safe_branch
    from .prefs import branch_baselines_on, default_branch, is_base_branch

    if not branch_baselines_on(db) or is_base_branch(db, branch):
        return store, directory, ""

    slug = safe_branch(branch)
    if not slug:
        return store, directory, ""

    # Наложения лежат отдельным деревом, а не внутри основного набора: иначе
    # `list_names()` основного стора начал бы возвращать чужие ветки, а чистка
    # веток задевала бы main.
    root = cfg.root_path / "branch-baselines" / slug
    if project_key:
        root = root / "project" / project_key
    overlay_dir = root / (scope or "global") / platform
    overlay = FileBaselineStore(overlay_dir)
    return (BranchBaselineStore(overlay, store, branch=branch,
                                base_label=default_branch(db)),
            overlay_dir, branch)


def store_for_run(db, run_id: int, *, cfg: VisTestConfig | None = None,
                  platform: str = "") -> tuple[BaselineStore, Path, dict]:
    """The store a decision on this run's comparison must be applied to.

    Returns (store, directory, scope_info). `scope_info` is handed back so the
    caller can report *where* it wrote — the interface has to say this out
    loud, otherwise «accepted as baseline» is an unverifiable claim.
    """
    cfg = cfg or VisTestConfig.load()
    info = run_scope(db, run_id)
    project = _project(info.get("project_key"), cfg)
    platform = platform or info.get("platform") or ""

    store, directory = store_for(
        info["scope"], cfg=cfg, platform=platform,
        project=project, baseline_dir=info.get("baseline_dir"))

    # Ветка накладывается ПОСЛЕ выбора набора, а не вместо него. Набор отвечает
    # на вопрос «чьи эталоны» (наши, проекта, снятые нами для проекта), ветка —
    # на вопрос «чья версия этих эталонов». Смешать эти два вопроса значит
    # вернуться ровно к тому классу ошибок, из-за которого апрув годами уходил
    # не в тот каталог.
    store, directory, branch = branch_layer(
        store, directory, db=db, cfg=cfg, platform=platform,
        branch=info.get("branch") or "", scope=info["scope"],
        project_key=info.get("project_key"))

    info["directory"] = str(directory)
    info["branch"] = branch
    return store, directory, info


def _project(key: str | None, cfg: VisTestConfig):
    if not key:
        return None
    from ..projects import ProjectRegistry
    try:
        return ProjectRegistry(cfg).get(key)
    except Exception:
        return None


def record_approval(db, *, scope: str, name: str, platform: str,
                    project_key: str | None, version: int, approved_by: str,
                    image_uri: str = "", snapshot_id: int | None = None,
                    from_comparison: int | None = None,
                    git_sha: str = "", branch: str = "", note: str = "") -> None:
    """Write the approval into the journal.

    Best-effort by design: an approval that succeeded on disk must not be
    reported as a failure because the journal write did. But it is not silent
    either — the caller can read the journal back and see whether the row is
    there.
    """
    try:
        db.execute(
            "UPDATE baseline SET is_current=0"
            " WHERE scope=? AND COALESCE(project_key,'')=COALESCE(?,'')"
            "   AND platform=? AND name=?",
            (scope, project_key, platform, name))
        db.execute(
            "INSERT INTO baseline(snapshot_id, version, image_uri, git_sha,"
            " branch, approved_by, is_current, scope, project_key, platform,"
            " name, from_comparison, note)"
            " VALUES(?,?,?,?,?,?,1,?,?,?,?,?,?)",
            (snapshot_id, int(version), image_uri, git_sha, branch, approved_by,
             scope, project_key, platform, name, from_comparison, note))
    except Exception:
        pass


def approval_history(db, *, scope: str, name: str, platform: str,
                     project_key: str | None = None, limit: int = 50) -> list[dict]:
    return db.query(
        "SELECT id, version, approved_by, approved_at, is_current, image_uri,"
        "       git_sha, branch, from_comparison, note"
        "  FROM baseline"
        " WHERE scope=? AND COALESCE(project_key,'')=COALESCE(?,'')"
        "   AND platform=? AND name=?"
        " ORDER BY version DESC LIMIT ?",
        (scope, project_key, platform, name, limit))
