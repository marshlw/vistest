# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The database VisTest d048b25 creates, frozen.

`sqlite3.Connection.iterdump()` of a database that commit's `Database()` made
from nothing, with the SQL comments stripped. A test that opens an old
database with new code needs the old code's database, and the tests cannot ask
git for it: CI checks out one commit. The `migrations` job in `ci.yml` does
the same thing against the previous commit; this one stays pinned to the last
schema before `user.role_manual`.
"""

USER_VERSION = 24

SCHEMA = """
BEGIN TRANSACTION;
CREATE TABLE audit (
  id        INTEGER PRIMARY KEY,
  at        TEXT NOT NULL DEFAULT (datetime('now')),
  who       TEXT NOT NULL DEFAULT '',
  action    TEXT NOT NULL,
  target    TEXT NOT NULL DEFAULT '',
  details   TEXT
);
CREATE TABLE baseline (
  id          INTEGER PRIMARY KEY,
  snapshot_id INTEGER REFERENCES snapshot(id) ON DELETE SET NULL,
  version     INTEGER NOT NULL,
  image_uri   TEXT,
  git_sha     TEXT,
  branch      TEXT,
  approved_by TEXT,
  approved_at TEXT NOT NULL DEFAULT (datetime('now')),
  is_current  INTEGER NOT NULL DEFAULT 1,
  scope       TEXT NOT NULL DEFAULT 'global',
  project_key TEXT,
  platform    TEXT NOT NULL DEFAULT '',
  name        TEXT NOT NULL DEFAULT '',
  from_comparison INTEGER,
  note        TEXT
);
CREATE TABLE ci_token (
  id           INTEGER PRIMARY KEY,
  name         TEXT NOT NULL DEFAULT '',
  prefix       TEXT NOT NULL,
  token_hash   TEXT NOT NULL,
  project_key  TEXT NOT NULL DEFAULT '',
  role         TEXT NOT NULL DEFAULT 'reviewer',
  created_by   TEXT NOT NULL DEFAULT '',
  created_at   TEXT NOT NULL DEFAULT (datetime('now')),
  expires_at   TEXT,
  last_used_at TEXT,
  revoked_at   TEXT
);
CREATE TABLE comparison (
  id          INTEGER PRIMARY KEY,
  run_id      INTEGER NOT NULL REFERENCES run(id) ON DELETE CASCADE,
  snapshot_id INTEGER NOT NULL REFERENCES snapshot(id) ON DELETE CASCADE,
  verdict     TEXT NOT NULL,
  review      TEXT,
  reviewed_by TEXT, reviewed_at TEXT,
  ssim        REAL, de_mean REAL, de_p95 REAL,
  changed_area_pct REAL, max_severity REAL,
  size_changed INTEGER DEFAULT 0,
  duration_ms INTEGER,
  artifacts   TEXT,
  meta        TEXT,
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE ignore_box (
  id          INTEGER PRIMARY KEY,
  snapshot_id INTEGER NOT NULL REFERENCES snapshot(id) ON DELETE CASCADE,
  x INTEGER, y INTEGER, w INTEGER, h INTEGER,
  reason TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE invite (
  token      TEXT PRIMARY KEY,
  role       TEXT NOT NULL DEFAULT 'viewer',
  name       TEXT NOT NULL DEFAULT '',
  created_by TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  expires_at TEXT NOT NULL,
  used_at    TEXT,
  used_by    TEXT
);
CREATE TABLE job (
  id          TEXT PRIMARY KEY,
  kind        TEXT NOT NULL DEFAULT '',
  title       TEXT NOT NULL DEFAULT '',
  status      TEXT NOT NULL DEFAULT 'queued',
  progress    REAL NOT NULL DEFAULT 0,
  owner       TEXT NOT NULL DEFAULT '',
  lock_key    TEXT NOT NULL DEFAULT '',
  error       TEXT,
  result      TEXT,
  log         TEXT,
  dropped     INTEGER NOT NULL DEFAULT 0,
  queued_at   REAL,
  started_at  REAL,
  finished_at REAL,
  heartbeat_at REAL
);
CREATE TABLE plugin_schema_version (
  plugin     TEXT PRIMARY KEY,
  version    INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE presence (
  user_id   INTEGER PRIMARY KEY,
  login     TEXT NOT NULL DEFAULT '',
  name      TEXT NOT NULL DEFAULT '',
  viewing   TEXT NOT NULL DEFAULT '',
  last_seen TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE project (
  id       INTEGER PRIMARY KEY,
  name     TEXT UNIQUE NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE project_grant (
  login       TEXT NOT NULL,
  project_key TEXT NOT NULL,
  role        TEXT NOT NULL DEFAULT 'viewer',
  granted_by  TEXT NOT NULL DEFAULT '',
  granted_at  TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (login, project_key)
);
CREATE TABLE region (
  id            INTEGER PRIMARY KEY,
  comparison_id INTEGER NOT NULL REFERENCES comparison(id) ON DELETE CASCADE,
  x INTEGER, y INTEGER, w INTEGER, h INTEGER,
  kind TEXT, severity REAL, de_mean REAL, ssim_local REAL,
  moved_dx INTEGER, moved_dy INTEGER,
  selector TEXT, element_text TEXT, caption TEXT,
  region_index INTEGER,
  score REAL,
  annotations TEXT NOT NULL DEFAULT '[]',
  suppressed_by TEXT
);
CREATE TABLE review_claim (
  comparison_id INTEGER PRIMARY KEY,
  user_id    INTEGER NOT NULL,
  login      TEXT NOT NULL DEFAULT '',
  name       TEXT NOT NULL DEFAULT '',
  claimed_at TEXT NOT NULL DEFAULT (datetime('now')),
  expires_at TEXT NOT NULL
);
CREATE TABLE run (
  id         INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  run_key    TEXT UNIQUE NOT NULL,
  branch     TEXT, git_sha TEXT, ci_url TEXT,
  platform   TEXT, browser TEXT,
  started_at TEXT NOT NULL DEFAULT (datetime('now')),
  finished_at TEXT,
  total  INTEGER DEFAULT 0,
  passed INTEGER DEFAULT 0,
  failed INTEGER DEFAULT 0,
  new_baselines INTEGER DEFAULT 0,
  project_key    TEXT,
  baseline_scope TEXT NOT NULL DEFAULT 'global',
  baseline_dir   TEXT
);
CREATE TABLE session (
  token      TEXT PRIMARY KEY,
  user_id    INTEGER NOT NULL REFERENCES user(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  expires_at TEXT NOT NULL,
  agent      TEXT
);
CREATE TABLE setting (
  scope       TEXT NOT NULL DEFAULT 'global',
  project_key TEXT NOT NULL DEFAULT '',
  name        TEXT NOT NULL,
  value       TEXT NOT NULL,
  updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
  updated_by  TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (scope, project_key, name)
);
CREATE TABLE snapshot (
  id         INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  name       TEXT NOT NULL,
  platform   TEXT NOT NULL DEFAULT '',
  browser    TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(project_id, name, platform, browser)
);
CREATE TABLE team (
  id          INTEGER PRIMARY KEY CHECK (id = 1),
  name        TEXT NOT NULL DEFAULT '',
  brand_color TEXT NOT NULL DEFAULT '#C2410C',
  updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE user (
  id         INTEGER PRIMARY KEY,
  login      TEXT UNIQUE NOT NULL,
  name       TEXT NOT NULL DEFAULT '',
  password   TEXT NOT NULL,
  role       TEXT NOT NULL DEFAULT 'viewer',
  active     INTEGER NOT NULL DEFAULT 1,
  status     TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  last_login TEXT
, source TEXT NOT NULL DEFAULT 'local', external_dn TEXT);
CREATE INDEX ix_baseline_snapshot ON baseline(snapshot_id, is_current);
CREATE INDEX ix_run_project ON run(project_id, started_at DESC);
CREATE INDEX ix_comparison_run ON comparison(run_id);
CREATE INDEX ix_comparison_snapshot ON comparison(snapshot_id, created_at DESC);
CREATE INDEX ix_region_comparison ON region(comparison_id);
CREATE INDEX ix_session_user ON session(user_id);
CREATE INDEX ix_grant_project ON project_grant(project_key);
CREATE INDEX ix_audit_at ON audit(at DESC);
CREATE INDEX ix_invite_expires ON invite(expires_at);
CREATE UNIQUE INDEX ix_ci_token_prefix ON ci_token(prefix);
CREATE INDEX ix_ci_token_project ON ci_token(project_key);
CREATE INDEX ix_claim_expires ON review_claim(expires_at);
CREATE INDEX ix_job_status ON job(status, queued_at DESC);
CREATE INDEX ix_baseline_key ON baseline(scope, project_key, platform, name, version DESC);
CREATE INDEX ix_run_scope ON run(project_key, baseline_scope);
CREATE INDEX ix_comparison_review ON comparison(review, verdict, created_at DESC);
CREATE INDEX ix_user_status ON user(status);
CREATE INDEX ix_audit_who_action ON audit(who, action, at DESC);
CREATE INDEX ix_audit_target_action ON audit(target, action, at DESC);
COMMIT;
"""
