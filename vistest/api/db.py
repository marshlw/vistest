# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Service storage. Plain sqlite3 — no ORM and no extra dependencies.

The schema is built around the two questions asked most often:
  «what broke in this run?» and «is this snapshot even stable?».
The second one needs history, so a comparison is always stored, not only on
failure.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS project (
  id       INTEGER PRIMARY KEY,
  name     TEXT UNIQUE NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS snapshot (
  id         INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  name       TEXT NOT NULL,
  platform   TEXT NOT NULL DEFAULT '',
  browser    TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(project_id, name, platform, browser)
);

-- The approval journal. Until now this table existed in the schema and was
-- never written to or read from: baseline versions lived only in the store's
-- `meta.json`, which answers «what version is it now» and nothing else.
-- «Who changed this baseline, when, from which run, and what was there
-- before» had no answer at all — and that is the first question asked after
-- an approval that should not have happened.
--
-- `scope` + `project_key` + `platform` identify the store, because one
-- snapshot name can exist in three of them at once (the service's own set,
-- the project's PNGs, the VisTest set for that project).
CREATE TABLE IF NOT EXISTS baseline (
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
CREATE INDEX IF NOT EXISTS ix_baseline_snapshot ON baseline(snapshot_id, is_current);

-- `baseline_scope` / `baseline_dir` / `project_key` answer the question
-- «against what was this run compared». Without them `approve` had nowhere to
-- look and always wrote into the service's own global store — so accepting a
-- failure from a «Run on VisTest baselines» of a connected project changed a
-- file that run never read, and the next run failed again on the same diff.
--
-- The source of baselines is a property of the RUN, not of the project: the
-- same project is run against its own PNGs today and against the VisTest set
-- tomorrow, by two different buttons.
CREATE TABLE IF NOT EXISTS run (
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
  project_key    TEXT,          -- connected suite key, NULL for our own runs
  baseline_scope TEXT NOT NULL DEFAULT 'global',   -- global | project | vistest
  baseline_dir   TEXT           -- the directory actually compared against
);
CREATE INDEX IF NOT EXISTS ix_run_project ON run(project_id, started_at DESC);

CREATE TABLE IF NOT EXISTS comparison (
  id          INTEGER PRIMARY KEY,
  run_id      INTEGER NOT NULL REFERENCES run(id) ON DELETE CASCADE,
  snapshot_id INTEGER NOT NULL REFERENCES snapshot(id) ON DELETE CASCADE,
  verdict     TEXT NOT NULL,
  review      TEXT,                    -- approved | rejected | NULL
  reviewed_by TEXT, reviewed_at TEXT,
  ssim        REAL, de_mean REAL, de_p95 REAL,
  changed_area_pct REAL, max_severity REAL,
  size_changed INTEGER DEFAULT 0,
  duration_ms INTEGER,
  artifacts   TEXT,                    -- json {kind: uri}
  meta        TEXT,                    -- json full result
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_comparison_run ON comparison(run_id);
CREATE INDEX IF NOT EXISTS ix_comparison_snapshot ON comparison(snapshot_id, created_at DESC);

CREATE TABLE IF NOT EXISTS region (
  id            INTEGER PRIMARY KEY,
  comparison_id INTEGER NOT NULL REFERENCES comparison(id) ON DELETE CASCADE,
  x INTEGER, y INTEGER, w INTEGER, h INTEGER,
  kind TEXT, severity REAL, de_mean REAL, ssim_local REAL,
  moved_dx INTEGER, moved_dy INTEGER,
  selector TEXT, element_text TEXT, caption TEXT,
  region_index INTEGER          -- номер крупного плана (`region_<N>`), если он есть
);
CREATE INDEX IF NOT EXISTS ix_region_comparison ON region(comparison_id);

-- DEPRECATED, kept only so an existing database still opens.
--
-- Ignore zones used to be written both here and into the store's `meta.json`.
-- The engine reads only `meta.json` (see `service.check`), so this table was a
-- second copy that nothing consulted and `DELETE /api/baselines/ignore-boxes`
-- did not clean — the two drifted apart within a week. The store is the single
-- source of truth now; nothing writes here any more.
CREATE TABLE IF NOT EXISTS ignore_box (
  id          INTEGER PRIMARY KEY,
  snapshot_id INTEGER NOT NULL REFERENCES snapshot(id) ON DELETE CASCADE,
  x INTEGER, y INTEGER, w INTEGER, h INTEGER,
  reason TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- --------------------------------------------------------------------------
--  People
--
--  These appeared when the installation moved from an engineer's machine to a
--  server inside the perimeter. Before that «who did this» was a meaningless
--  question: it was done by whoever was sitting at the computer.
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user (
  id         INTEGER PRIMARY KEY,
  login      TEXT UNIQUE NOT NULL,
  name       TEXT NOT NULL DEFAULT '',
  password   TEXT NOT NULL,            -- pbkdf2$iterations$salt$hash
  role       TEXT NOT NULL DEFAULT 'viewer',  -- viewer | reviewer | admin
  active     INTEGER NOT NULL DEFAULT 1,
  -- pending | active | disabled. Отдельно от `active`, потому что «ещё не
  -- одобрен» и «отключён администратором» — это два разных «войти нельзя», и
  -- различить их надо не ради красоты: первое ждёт действия админа, второе уже
  -- является его действием.
  status     TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  last_login TEXT
);

-- Sessions are server-side, not signed tokens: otherwise logout and access
-- revocation do not work, and in a closed perimeter that is exactly what will
-- be asked for.
CREATE TABLE IF NOT EXISTS session (
  token      TEXT PRIMARY KEY,
  user_id    INTEGER NOT NULL REFERENCES user(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  expires_at TEXT NOT NULL,
  agent      TEXT
);
CREATE INDEX IF NOT EXISTS ix_session_user ON session(user_id);

-- Роль, которая действует не везде.
--
-- `user.role` — одна на инсталляцию, и пока проект был один, этого хватало.
-- Как только к сервису подключили второй набор тестов, `reviewer` стал
-- означать «может переписать эталон ЛЮБОГО проекта»: человек, отвечающий за
-- витрину, одним нажатием менял точку отсчёта биллинга — не со зла, а потому
-- что кнопка была активной, а снимок красным.
--
-- Здесь лежит ДОБАВКА к глобальной роли, а не замена ей: действующая роль —
-- максимум из двух. Право не умеет отнимать (иначе администратор мог бы
-- перестать быть администратором), а изоляция достигается тем, что глобально
-- все `viewer`, и `reviewer` выдаётся на конкретные проекты. Пока в таблице
-- пусто, поведение совпадает с прежним до мелочей — обновление не должно
-- приводить в команду, где утром никто не может утвердить ничего.
--
-- `login`, а не `user_id`: пользователя могут удалить и завести заново, и
-- унаследовать чужие права по номеру строки было бы худшим из возможных
-- сюрпризов. Внешнего ключа поэтому нет; висячие записи убирает удаление
-- пользователя.
CREATE TABLE IF NOT EXISTS project_grant (
  login       TEXT NOT NULL,
  project_key TEXT NOT NULL,
  role        TEXT NOT NULL DEFAULT 'viewer',
  granted_by  TEXT NOT NULL DEFAULT '',
  granted_at  TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (login, project_key)
);
CREATE INDEX IF NOT EXISTS ix_grant_project ON project_grant(project_key);

-- Who, when, what. A separate table, because «just look in the logs» stops
-- working exactly when you need it.
CREATE TABLE IF NOT EXISTS audit (
  id        INTEGER PRIMARY KEY,
  at        TEXT NOT NULL DEFAULT (datetime('now')),
  who       TEXT NOT NULL DEFAULT '',
  action    TEXT NOT NULL,
  target    TEXT NOT NULL DEFAULT '',
  details   TEXT
);
CREATE INDEX IF NOT EXISTS ix_audit_at ON audit(at DESC);

-- --------------------------------------------------------------------------
--  Team
--
--  The installation profile (a single row) and invites. A «team» is the people
--  of one instance; isolation between teams is at the deployment level
--  (a dedicated container and volume per team), so there is nothing about org
--  here.
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS team (
  id          INTEGER PRIMARY KEY CHECK (id = 1),
  name        TEXT NOT NULL DEFAULT '',
  brand_color TEXT NOT NULL DEFAULT '#C2410C',
  updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- An invite is a one-time link with a role. Better than «the admin made up a
-- password and sent it in chat»: only the invited person knows the password.
CREATE TABLE IF NOT EXISTS invite (
  token      TEXT PRIMARY KEY,
  role       TEXT NOT NULL DEFAULT 'viewer',
  name       TEXT NOT NULL DEFAULT '',
  created_by TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  expires_at TEXT NOT NULL,
  used_at    TEXT,
  used_by    TEXT
);
CREATE INDEX IF NOT EXISTS ix_invite_expires ON invite(expires_at);

-- --------------------------------------------------------------------------
--  Токены CI
--
--  Токен приёма прогонов был один на всю инсталляцию — переменная окружения
--  `VISTEST_INGEST_TOKEN`. Пока в сервис писал один пайплайн, этого хватало.
--  Дальше начинается арифметика, которая не сходится: подрядчику надо выдать
--  доступ, а есть только тот самый общий токен; увольнение одного человека
--  означает смену секрета во ВСЕХ пайплайнах разом, и делать это надо
--  одномоментно, иначе половина сборок краснеет.
--
--  Здесь токен принадлежит ПРОЕКТУ и живёт своей жизнью: у него есть имя, срок,
--  роль и отметка последнего использования. Отзыв одного не трогает остальные,
--  а перекат пайплайнов делается двумя действующими токенами сразу.
--
--  Хранится хеш, а не сам токен: база лежит рядом с эталонами и попадает в
--  бэкапы, и секрет в открытом виде пережил бы там любую ротацию.
--
--  Почему SHA-256, а не PBKDF2, которым хешируются пароли. PBKDF2 в 480 000
--  раундов существует потому, что пароль придумал человек: его можно
--  перебрать, и каждая попытка должна быть дорогой. Токен — 24 случайных
--  байта, перебирать его нечем, а предъявляется он на КАЖДЫЙ запрос приёма.
--  Полмиллиона раундов на запрос — это отказ в обслуживании, устроенный
--  самому себе. Плюс по хешу с солью нельзя искать: пришлось бы перебирать все
--  токены базы на каждый запрос, и с солью это стало бы N × 480 000.
--
--  `prefix` — первые символы самого токена. По ним идёт индексированный поиск
--  одной строки, и по ним же токен опознаётся в списке, не будучи показанным.
CREATE TABLE IF NOT EXISTS ci_token (
  id           INTEGER PRIMARY KEY,
  name         TEXT NOT NULL DEFAULT '',
  prefix       TEXT NOT NULL,
  token_hash   TEXT NOT NULL,
  -- Пустая строка — вся инсталляция. Это путь совместимости и осознанный
  -- выбор администратора, а не значение по умолчанию для новых токенов.
  project_key  TEXT NOT NULL DEFAULT '',
  role         TEXT NOT NULL DEFAULT 'reviewer',
  created_by   TEXT NOT NULL DEFAULT '',
  created_at   TEXT NOT NULL DEFAULT (datetime('now')),
  expires_at   TEXT,
  last_used_at TEXT,
  revoked_at   TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_ci_token_prefix ON ci_token(prefix);
CREATE INDEX IF NOT EXISTS ix_ci_token_project ON ci_token(project_key);

-- --------------------------------------------------------------------------
--  Collaboration
--
--  presence — who is online right now and what they are looking at (a
--  short-lived row per person, refreshed by a heartbeat). review_claim is a
--  soft lock «I am reviewing this snapshot»: it does not forbid anything, but
--  it shows a colleague that someone is already busy with it. The hard backstop
--  against a race of decisions — a version lock on approve — stays in place.
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS presence (
  user_id   INTEGER PRIMARY KEY,
  login     TEXT NOT NULL DEFAULT '',
  name      TEXT NOT NULL DEFAULT '',
  viewing   TEXT NOT NULL DEFAULT '',
  last_seen TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS review_claim (
  comparison_id INTEGER PRIMARY KEY,
  user_id    INTEGER NOT NULL,
  login      TEXT NOT NULL DEFAULT '',
  name       TEXT NOT NULL DEFAULT '',
  claimed_at TEXT NOT NULL DEFAULT (datetime('now')),
  expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_claim_expires ON review_claim(expires_at);

-- --------------------------------------------------------------------------
--  Настройки, которые правятся из интерфейса
--
--  Пороги вердикта жили только в `vistest.yaml`. Дашборд при этом считает
--  гистограмму severity против порога — самую полезную картинку во всём
--  интерфейсе, потому что она отвечает ровно на вопрос «куда его ставить», — а
--  поставить было некуда: в настройках стоял ползунок, который никуда ничего
--  не отправлял. Человек двигал, видел число и уходил уверенный, что настроил.
--
--  Почему БД, а не запись в yaml: конфиг лежит в git и описывает проект, а не
--  инсталляцию, и сервис, переписывающий чужой версионируемый файл, — источник
--  конфликтов на ровном месте. Здесь же значение принадлежит инсталляции и
--  переживает и обновление образа, и несколько реплик на одном томе.
--
--  `scope` = 'global' | 'project'. Для проекта `project_key` — его ключ, для
--  глобального — пустая строка (в PRIMARY KEY NULL не годится).
CREATE TABLE IF NOT EXISTS setting (
  scope       TEXT NOT NULL DEFAULT 'global',
  project_key TEXT NOT NULL DEFAULT '',
  name        TEXT NOT NULL,
  value       TEXT NOT NULL,
  updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
  updated_by  TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (scope, project_key, name)
);

-- --------------------------------------------------------------------------
--  Фоновые задачи
--
--  Задача жила в словаре в памяти процесса, и это было честно ровно до первого
--  перезапуска. Дальше сценарий такой: человек нажал «Прогнать», ушёл пить
--  чай, в это время выкатили обновление образа — и задача **исчезла**. Не
--  «упала», не «прервана», а перестала существовать: опрос получал 404, а
--  интерфейс писал «connection to the task lost», то есть винил сеть в том, к
--  чему сеть отношения не имеет.
--
--  Воскресить задачу нельзя и не нужно: внутри неё крутится чужой pytest или
--  браузер, и после смерти процесса продолжать нечего. Нужно другое — чтобы
--  она **не пропадала бесследно**: осталась в списке, получила понятный статус
--  и сохранила хвост лога, потому что причина написана именно там.
--
--  `heartbeat_at` отвечает на вопрос «а вдруг она всё-таки жива». Процесс,
--  который ведёт задачу, обновляет отметку, пока та идёт. При старте сервис
--  помечает прерванными только те задачи, чья отметка протухла, — иначе второй
--  воркер, стартовав, убил бы работающие задачи первого. Проверять по PID
--  нельзя: на Windows `os.kill(pid, 0)` не спрашивает, а завершает процесс.
CREATE TABLE IF NOT EXISTS job (
  id          TEXT PRIMARY KEY,
  kind        TEXT NOT NULL DEFAULT '',
  title       TEXT NOT NULL DEFAULT '',
  status      TEXT NOT NULL DEFAULT 'queued',
  progress    REAL NOT NULL DEFAULT 0,
  owner       TEXT NOT NULL DEFAULT '',
  lock_key    TEXT NOT NULL DEFAULT '',
  error       TEXT,
  result      TEXT,                    -- json
  log         TEXT,                    -- json, только хвост
  dropped     INTEGER NOT NULL DEFAULT 0,
  queued_at   REAL,
  started_at  REAL,
  finished_at REAL,
  heartbeat_at REAL
);
CREATE INDEX IF NOT EXISTS ix_job_status ON job(status, queued_at DESC);
"""

_local = threading.local()

SCOPES = ("global", "project", "vistest")


# --------------------------------------------------------------------------- #
#  Migrations
#
#  `CREATE TABLE IF NOT EXISTS` does nothing to a table that already exists, so
#  a new column never reaches an installation that has been running for a
#  month — and the failure looks like «no such column» deep inside a query.
#  The list below is applied on every open; each step must be safe to run
#  against a database where it has already been applied.
# --------------------------------------------------------------------------- #
MIGRATIONS: list[tuple[str, str]] = [
    ("run", "ALTER TABLE run ADD COLUMN project_key TEXT"),
    ("run", "ALTER TABLE run ADD COLUMN baseline_scope TEXT NOT NULL DEFAULT 'global'"),
    ("run", "ALTER TABLE run ADD COLUMN baseline_dir TEXT"),
    ("baseline", "ALTER TABLE baseline ADD COLUMN scope TEXT NOT NULL DEFAULT 'global'"),
    ("baseline", "ALTER TABLE baseline ADD COLUMN project_key TEXT"),
    ("baseline", "ALTER TABLE baseline ADD COLUMN platform TEXT NOT NULL DEFAULT ''"),
    ("baseline", "ALTER TABLE baseline ADD COLUMN name TEXT NOT NULL DEFAULT ''"),
    ("baseline", "ALTER TABLE baseline ADD COLUMN from_comparison INTEGER"),
    ("baseline", "ALTER TABLE baseline ADD COLUMN note TEXT"),
    # Indexes over migrated columns come last: on a database created before
    # this release the columns do not exist until the statements above have
    # run, and an index in SCHEMA would fail the very first open.
    ("baseline", "CREATE INDEX IF NOT EXISTS ix_baseline_key"
                 " ON baseline(scope, project_key, platform, name, version DESC)"),
    ("region", "ALTER TABLE region ADD COLUMN region_index INTEGER"),
    ("run", "CREATE INDEX IF NOT EXISTS ix_run_scope"
            " ON run(project_key, baseline_scope)"),
    ("comparison", "CREATE INDEX IF NOT EXISTS ix_comparison_review"
                   " ON comparison(review, verdict, created_at DESC)"),
    # `active` отвечал на вопрос «может ли войти», и двух разных «нет» в нём не
    # помещалось: заявка, ждущая одобрения, и отключённый администратором
    # выглядели одинаково — то есть заявку нельзя было ни найти, ни одобрить.
    # `status`: pending | active | disabled.
    ("user", "ALTER TABLE user ADD COLUMN status TEXT NOT NULL DEFAULT 'active'"),
    ("user", "CREATE INDEX IF NOT EXISTS ix_user_status ON user(status)"),
    # Колонка добавляется со значением 'active' — иначе ALTER TABLE не умеет.
    # Отключённые до обновления получили бы «активен» рядом с невозможностью
    # войти: в списке команды это выглядит как поломка прав, а не как решение
    # администратора. Условие идемпотентно и заявок не трогает: у них
    # `status='pending'`.
    ("user", "UPDATE user SET status='disabled'"
             " WHERE active=0 AND status='active'"),
    # Цвет команды по умолчанию сменился вместе с интерфейсом. Меняется ТОЛЬКО
    # прежнее значение по умолчанию: цвет, который администратор выбрал сам, —
    # его решение, и переписывать его при обновлении нельзя.
    ("team", "UPDATE team SET brand_color='#C2410C' WHERE brand_color='#3A34C4'"),
    # Троттлинг входа считает по журналу: сколько раз этот логин (и этот адрес)
    # ошибся за последние пятнадцать минут. Индекса под это не было ни одного,
    # а `ix_audit_at` для такого запроса бесполезен — фильтр начинается с
    # `action` и `who`. То есть КАЖДАЯ попытка входа читала журнал целиком.
    #
    # На свежей установке это ноль строк и никакой разницы. Через полгода в
    # журнале сотни тысяч записей, и защита от перебора превращается в его
    # усилитель: один HTTP-запрос без пароля заставляет сервис прочитать всю
    # таблицу дважды (по логину и по адресу), причём ДО того, как дело дойдёт
    # до пароля, — то есть бесплатно для того, кто стучится.
    ("audit", "CREATE INDEX IF NOT EXISTS ix_audit_who_action"
              " ON audit(who, action, at DESC)"),
    ("audit", "CREATE INDEX IF NOT EXISTS ix_audit_target_action"
              " ON audit(target, action, at DESC)"),
]


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        # Set by `ingest_run` when it replaced an existing run: the caller needs
        # the old id to delete the artifacts that row owned.
        self.replaced_run_id: int | None = None
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        with self.connect() as c:
            existing = {r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            for table, statement in MIGRATIONS:
                if table not in existing:
                    continue
                try:
                    c.execute(statement)
                except sqlite3.OperationalError as e:
                    # «duplicate column name» — the migration is already
                    # applied. Anything else is a real problem and must not be
                    # swallowed: a half-migrated schema fails later and in a
                    # place that has nothing to do with the cause.
                    if "duplicate column" not in str(e).lower():
                        raise

    def _conn(self) -> sqlite3.Connection:
        """Соединение на поток — и на файл.

        Раньше кеш был только по потоку: `_local.conn`. Второй `Database` с
        другим путём в том же потоке получал соединение с чужой базой и молча
        работал не с тем файлом. В сервисе база одна, поэтому это годами не
        стреляло, но `Database` создаётся ещё и в `external.publish()` — уже с
        путём, вычисленным из конфигурации, — и совпадать они обязаны не по
        случайности.
        """
        cache = getattr(_local, "conns", None)
        if cache is None:
            cache = _local.conns = {}
        conn = cache.get(self.path)
        if conn is None:
            conn = sqlite3.connect(self.path, check_same_thread=False, timeout=15)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            cache[self.path] = conn
        return conn

    @contextmanager
    def connect(self):
        """Транзакция. Вложенный вызов присоединяется к внешней, а не открывает свою.

        Соединение здесь одно на поток, и `commit()` у sqlite не знает про
        вложенность: он завершает транзакцию целиком, кто бы его ни позвал.
        Поэтому `ingest_run`, обёрнутый в `with connect()`, коммитился на
        первом же `snapshot_id()` внутри себя — тот открывал «свой» блок на том
        же соединении и на выходе фиксировал всё, что успел записать внешний.

        Видно это становилось только тогда, когда что-то падало посередине: в
        базе оставался прогон с половиной сравнений, `rollback()` откатывал
        лишь хвост после последнего чужого `commit()`. Полупринятый прогон
        хуже непринятого — он выглядит настоящим и попадает в метрики, в
        очередь решений и в диффы прогонов.

        Считаем глубину: фиксирует только тот, кто открыл транзакцию; ошибка на
        любом уровне откатывает её всю.
        """
        conn = self._conn()
        depth = getattr(_local, "depth", {})
        if not hasattr(_local, "depth"):
            _local.depth = depth
        level = depth.get(self.path, 0)
        depth[self.path] = level + 1
        try:
            yield conn
        except Exception:
            depth[self.path] = level
            if level == 0:
                conn.rollback()
            raise
        else:
            depth[self.path] = level
            if level == 0:
                conn.commit()

    # ------------------------------------------------------------------ #
    def project_id(self, name: str) -> int:
        with self.connect() as c:
            c.execute("INSERT OR IGNORE INTO project(name) VALUES(?)", (name,))
            return c.execute("SELECT id FROM project WHERE name=?", (name,)).fetchone()[0]

    def snapshot_id(self, project_id: int, name: str, platform: str, browser: str) -> int:
        with self.connect() as c:
            c.execute(
                "INSERT OR IGNORE INTO snapshot(project_id,name,platform,browser)"
                " VALUES(?,?,?,?)", (project_id, name, platform, browser))
            return c.execute(
                "SELECT id FROM snapshot WHERE project_id=? AND name=?"
                " AND platform=? AND browser=?",
                (project_id, name, platform, browser)).fetchone()[0]

    # ------------------------------------------------------------------ #
    def ingest_run(self, payload: dict, project: str) -> int:
        """Record an entire run (whatever the runner put into run.json).

        Returns the run id. If a run with the same `run_key` already exists it
        is replaced — re-sending the same run is a normal thing for a retried
        CI job.
        """
        pid = self.project_id(project)
        git = payload.get("git") or {}
        totals = payload.get("totals") or {}

        scope = payload.get("baseline_scope") or "global"
        if scope not in ("global", "project", "vistest"):
            scope = "global"

        with self.connect() as c:
            # `INSERT OR REPLACE` deletes the previous row and inserts a new
            # one with a NEW id — the comparisons go by cascade, but the
            # artifacts on disk stayed under `artifacts/<old id>/` forever,
            # unreachable by any cleanup. We hand the old id back to the caller
            # so it can drop them.
            prior = c.execute("SELECT id FROM run WHERE run_key=?",
                              (payload.get("run_id"),)).fetchone()
            self.replaced_run_id = prior[0] if prior else None

            cur = c.execute(
                "INSERT OR REPLACE INTO run"
                "(project_id,run_key,branch,git_sha,ci_url,platform,browser,"
                " started_at,finished_at,total,passed,failed,new_baselines,"
                " project_key,baseline_scope,baseline_dir)"
                " VALUES(?,?,?,?,?,?,?,?,datetime('now'),?,?,?,?,?,?,?)",
                (pid, payload.get("run_id"), git.get("branch"), git.get("sha"),
                 payload.get("ci_url"), payload.get("platform"), payload.get("browser"),
                 payload.get("created_at"), totals.get("total", 0),
                 totals.get("passed", 0), totals.get("failed", 0),
                 totals.get("new", 0),
                 payload.get("project_key"), scope,
                 payload.get("baseline_dir")),
            )
            run_id = cur.lastrowid

            for comp in payload.get("comparisons", []):
                # Платформа берётся у САМОГО СРАВНЕНИЯ, если оно её назвало.
                #
                # Прогон по матрице — это один прогон и несколько вариантов
                # (chromium при 1440, firefox при 390 и так далее), у каждого
                # свой эталон и свой ключ платформы. Пока ключ брался только с
                # уровня прогона, все варианты складывались в одну строку
                # `snapshot` — то есть шесть кадров разных браузеров и размеров
                # считались одним снимком, и последний затирал историю
                # предыдущих. У прогона ключ остаётся как значение по
                # умолчанию: обычный прогон одного варианта ничего не заметил.
                sid = self.snapshot_id(
                    pid, comp["name"],
                    comp.get("platform") or payload.get("platform", "") or "",
                    comp.get("browser") or payload.get("browser", "") or "",
                )
                m = comp.get("metrics", {})
                cur = c.execute(
                    "INSERT INTO comparison"
                    "(run_id,snapshot_id,verdict,ssim,de_mean,de_p95,"
                    " changed_area_pct,max_severity,size_changed,duration_ms,"
                    " artifacts,meta) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (run_id, sid, comp.get("verdict"), m.get("ssim_global"),
                     m.get("de_mean"), m.get("de_p95"), m.get("changed_area_pct"),
                     m.get("max_severity"),
                     int(bool((comp.get("size") or {}).get("changed"))),
                     comp.get("duration_ms"),
                     json.dumps(comp.get("artifacts") or {}),
                     json.dumps(comp, ensure_ascii=False)),
                )
                comp_id = cur.lastrowid
                for r in comp.get("regions", []):
                    c.execute(
                        "INSERT INTO region"
                        "(comparison_id,x,y,w,h,kind,severity,de_mean,ssim_local,"
                        " moved_dx,moved_dy,selector,element_text,caption,"
                        " region_index)"
                        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (comp_id, r.get("x"), r.get("y"), r.get("w"), r.get("h"),
                         r.get("kind"), r.get("severity"), r.get("de_mean"),
                         r.get("ssim_local"), r.get("moved_dx"), r.get("moved_dy"),
                         r.get("selector"), r.get("element_text"), r.get("caption"),
                         r.get("region_index")),
                    )
        return run_id

    # ------------------------------------------------------------------ #
    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        with self.connect() as c:
            return [dict(r) for r in c.execute(sql, params).fetchall()]

    def one(self, sql: str, params: tuple = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params: tuple = ()) -> int:
        with self.connect() as c:
            return c.execute(sql, params).rowcount
