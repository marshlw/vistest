# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Running the connected test collection and gathering the analysis picture.

This is what happens when you press «Run» in the interface:

1. the run environment is assembled (their variables + secrets from `.vistest`);
2. the baselines folder is chosen by the project rules;
3. their pytest runs — with their interpreter, from their root, with their arguments;
4. the result is turned into an ordinary VisTest run.

The two modes differ only in steps 3-4:

* **adapter** — `-p vistest.adapter` is attached to their pytest, and the
  comparison happens right during the test. The full picture, including DOM
  attribution: the DOM can only be captured from a live page;
* **observe** — their tests run as-is, and afterwards the baseline / actual
  pairs left on disk are matched up. Works with any project, but the DOM can no
  longer be retrieved, and artifacts are built from two PNGs.

A run is not aborted abruptly: pytest receives a termination signal and finishes
the current test. Killing it midway through taking a screenshot is a sure way to
leave a half-written PNG instead of a baseline.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .config import VisTestConfig, platform_key
from .projects import Project
from .suites import SKIP_DIRS


@dataclass
class ExternalRun:
    project: str
    mode: str
    baseline_dir: str = ""
    # Which set of baselines this run actually compared against: "project"
    # (their committed PNGs) or "vistest" (the set VisTest captured itself).
    # It travels with the run all the way into the history, because an approval
    # made later has to land in the store the run used — not in whatever the
    # project setting says months afterwards.
    baseline_scope: str = "project"
    # Браузер, в котором прогон шёл. Едет вместе с прогоном до самой истории:
    # без него список прогонов не отличает «упало в firefox» от «упало везде»,
    # а апрув не знает, чей набор эталонов переписывать.
    browser: str = ""
    command: list[str] = field(default_factory=list)
    exit_code: int | None = None
    results: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    output: list[str] = field(default_factory=list)
    run_dir: str = ""
    elapsed_s: float = 0.0
    history_run_id: int | None = None    # number in the shared run history
    # Test-level accounting alongside the snapshot-level one. Without it a run
    # of eleven tests that produced two snapshots looks like a run of two tests.
    collected: int = 0                   # tests pytest actually took into the run
    tests: list[dict] = field(default_factory=list)   # id / phase / outcome
    # Branch and commit of THEIR repository. An external run used to reach the
    # history with an empty git: «—» in the list, «—» in the report, nothing to
    # correlate with their build, and no ground to build per-branch baselines
    # on. `git_info()` could do this from the start — nobody asked it.
    git: dict = field(default_factory=dict)

    @property
    def failed(self) -> list[dict]:
        return [r for r in self.results if r.get("verdict") == "fail"]

    @property
    def new(self) -> list[dict]:
        return [r for r in self.results if r.get("verdict") == "new_baseline"]

    @property
    def errored(self) -> list[dict]:
        return [r for r in self.results if r.get("verdict") == "error"]

    @property
    def setup_failures(self) -> list[dict]:
        """Tests that fell before their body started.

        A separate notion from «a failed test» on purpose. A test that fell in
        its own body is a result: something was checked and did not match. A
        test that fell at setup is not a result at all — nothing was checked,
        and calling such a run successful would be a lie.
        """
        return [t for t in self.tests
                if t.get("outcome") == "failed" and t.get("phase") == "setup"]

    def summary(self) -> dict:
        counted = len(self.failed) + len(self.new) + len(self.errored)
        return {
            "total": len(self.results),
            "failed": len(self.failed),
            "new": len(self.new),
            "errored": len(self.errored),
            "passed": len(self.results) - counted,
            "tests": self.collected,
            "tests_failed": sum(1 for t in self.tests
                                if t.get("outcome") == "failed"),
            "tests_not_started": len(self.setup_failures),
            "max_severity": max((r.get("max_severity") or 0
                                 for r in self.results), default=0.0),
        }

    def to_dict(self) -> dict:
        return {
            "project": self.project, "mode": self.mode,
            "baseline_dir": self.baseline_dir,
            "baseline_scope": self.baseline_scope, "browser": self.browser,
            "command": self.command,
            "exit_code": self.exit_code, "results": self.results,
            "errors": self.errors, "output": self.output[-400:],
            "run_dir": self.run_dir, "elapsed_s": round(self.elapsed_s, 1),
            "history_run_id": self.history_run_id,
            "collected": self.collected, "tests": self.tests,
            "git": self.git,
            "summary": self.summary(),
        }


# --------------------------------------------------------------------------- #
class ExternalRunRefused(RuntimeError):
    """Прогон невозможен по причине, которая уже названа словами.

    Отличается от любого другого исключения тем, что это НЕ поломка: у набора
    нет эталонов, не выбран каталог, не совпало ни одно правило. Причина
    сформулирована для человека и целиком помещается в одну строку.

    Различать это нужно ради стектрейса. Задача, упавшая с обычным
    исключением, дописывает в лог питоновский трейс — и правильно делает: там
    настоящая поломка, и её надо чинить. На объяснённом отказе трейс говорит
    «инструмент сломался» ровно там, где инструмент отработал как задумано, а
    человек в это время читает пятнадцать строк `File "...", line ...` вместо
    одной строки с ответом.
    """


def browsers_with_baselines(project, cfg) -> dict[str, bool]:
    """У каких браузеров набор VisTest уже снят, а у каких пусто.

    Нужно до запуска. Многобраузерный прогон иначе узнаёт это по одному
    браузеру за раз: chromium отработал полторы минуты, firefox отказал, — и
    только тогда выясняется, что снимать надо было заранее.
    """
    from .matrix import BROWSERS

    out = {}
    for name in BROWSERS:
        key = platform_key(name, cfg.capture.device_scale_factor)
        out[name] = has_own_baselines(project.vistest_baselines_path(cfg, key))
    return out


def has_own_baselines(directory: Path) -> bool:
    """Is there at least one baseline in VisTest's own set for this platform?

    `FileBaselineStore` lays a snapshot out as `<name>/baseline.png`, so a PNG
    never lies directly in the platform directory. Asking `glob("*.png")` here
    is exactly the mistake that made a freshly captured set look empty: the
    person pressed «Capture VisTest baselines», got nine baselines on disk, and
    the very next comparison run still refused to start with «baselines have not
    been captured yet». The layout is decided by the store, so the question is
    asked the way the store answers it.
    """
    if not directory.exists():
        return False
    return next(directory.rglob("baseline.png"), None) is not None


# --------------------------------------------------------------------------- #
def resolve_baselines(project: Project, cfg: VisTestConfig, *,
                      baseline_source: str | None = None, browser: str = "",
                      update_baselines: bool = False,
                      env: dict[str, str] | None = None) -> tuple[Path, bool]:
    """Против чего сравнивать: их папка эталонов или наш собственный набор.

    Вынесено из `run_project` не ради красоты. Приём готовых артефактов
    (`ingest`) обязан выбирать эталоны ровно теми же правилами, что и обычный
    прогон, — иначе один и тот же проект через две двери сравнивался бы с
    разными наборами, и расхождение было бы видно только по вердикту.

    `baseline_source` переопределяет настройку проекта на один раз:
    «project» — их закоммиченные PNG, «vistest» — набор, снятый нами.
    """
    # Без окружения прогона правило выбора папки эталонов проверять нечем:
    # у проектов, гоняющих и локально, и в контейнере, их две, и условие
    # написано на переменных. Пустой словарь молча выбрал бы безусловное
    # правило — то есть не ту папку.
    if env is None:
        env = {**os.environ, **(project.env or {})}

    # baseline_source overrides the baseline source for a specific run:
    #   "project" — compare against their committed PNGs; "vistest" — against the
    #   set captured by VisTest itself. Without it the project setting is used.
    if baseline_source in ("project", "vistest"):
        own = baseline_source == "vistest"
    else:
        own = project.uses_own_baselines()

    if own:
        # Our own set is captured by our capture and lives outside their repo.
        # The platform in the path is mandatory: text rendering in Windows and in
        # a container is physically different, there is no shared baseline between
        # them.
        platform = platform_key(browser or "chromium",
                                cfg.capture.device_scale_factor)
        baseline_dir = project.vistest_baselines_path(cfg, platform)
        # Каталог НЕ создаётся здесь.
        #
        # Здесь стоял `mkdir(parents=True, exist_ok=True)` — до всех проверок,
        # то есть каждая неудачная попытка прогона оставляла на диске пустой
        # каталог платформы. Дальше он попадал в `baseline_status()` и на
        # карточку: «11 on docker-chromium-1x (11), docker-firefox-1x (0),
        # docker-webkit-1x (0)». Читается это как «набор для firefox есть, но
        # пуст», хотя на самом деле его не снимали ни разу и попытка была
        # отвергнута секундой раньше.
        #
        # Каталог создаёт тот, кто в него пишет: адаптер, когда
        # `VISTEST_ADAPTER_UPDATE=1`. Отсутствие каталога — честный ответ
        # «этого набора нет».
        # An empty set + no request to capture is an error, not a «first run».
        # Capturing VisTest baselines is a separate explicit action (a button in «⋯»).
        if not update_baselines and not has_own_baselines(baseline_dir):
            # Совет обязан вести туда, где помогает.
            #
            # Здесь стояло «снимите набор через ⋯ → Capture VisTest baselines»,
            # и для многобраузерного прогона это тупик: то действие снимает
            # набор браузера по умолчанию, то есть chromium — который как раз и
            # работал. Человек делал ровно то, что написано, и получал ту же
            # ошибку про firefox.
            #
            # Поэтому сообщение называет БРАУЗЕР и говорит, у каких браузеров
            # набор уже есть: из этого сразу видно, что вопрос не в проекте, а
            # в одном движке.
            have = [name for name, ok in
                    browsers_with_baselines(project, cfg).items() if ok]
            hint = (f"Sets are already captured for: {', '.join(have)}. "
                    if have else "")
            for_browser = f" for {browser}" if browser else ""
            raise ExternalRunRefused(
                f"The VisTest baseline set for platform {platform} has not been "
                f"captured yet — there is nothing in {baseline_dir}. {hint}"
                f"Capture it{for_browser}: «⋯ → Snap VisTest baselines», "
                f"picking{for_browser or ' the browser'} in the «▾» menu, "
                "then run the comparison again.")
    else:
        baseline_dir = project.baseline_dir(env)
        if baseline_dir is None:
            raise ExternalRunRefused(
                "No baselines folder selected. Check the rules in the project "
                "description: no condition matched the run environment.")
        if not baseline_dir.exists():
            raise ExternalRunRefused(f"Baselines folder not found: {baseline_dir}")
    return baseline_dir, own


# --------------------------------------------------------------------------- #
def run_project(project: Project, *, cfg: VisTestConfig | None = None,
                env_overrides: dict[str, str] | None = None,
                update_baselines: bool = False,
                extra_args: list[str] | None = None,
                baseline_source: str | None = None,
                browser: str = "",
                ci: bool = False, only: str = "",
                log=print, should_stop=lambda: False) -> ExternalRun:
    """`browser` — в каком браузере гнать их набор.

    Пусто — как было: браузер выбирает их conftest, а мы про него не знаем.
    Задан — имя доезжает до их pytest (см. `_browser_env_and_args`), и, что
    важнее, попадает в КЛЮЧ ПЛАТФОРМЫ.

    Ключ платформы здесь и был главной ошибкой. Он считался как
    `platform_key()` — без аргументов, то есть всегда `…-chromium-…`. Прогон
    того же проекта в firefox складывал свои кадры в набор chromium и с ним же
    сравнивался. Отрисовка шрифтов в этих движках физически разная, так что
    падало всё подряд; а если эталоны сначала сняли под firefox, то chromium
    потом «чинил» их обратно. Ни в одном логе это не написано: обе стороны
    уверены, что работают со своим набором.
    """
    cfg = cfg or VisTestConfig.load()
    browser = (browser or "").strip().lower()
    if browser:
        from .matrix import normalize_browser

        browser = normalize_browser(browser)
    unresolved: list[str] = []
    env = _build_env(project, cfg, env_overrides, unresolved)

    # Движка может просто не быть.
    #
    # Это и была настоящая причина «firefox и webkit не работают». Меню
    # предлагало три движка, потому что `known_browsers` — список того, что
    # УМЕЕТ Playwright, а не того, что установлено. Выбор firefox уходил в
    # работу, pytest падал строкой «Executable doesn't exist at
    # …/firefox-1495/firefox/firefox» посреди чужого вывода, набор оставался
    # пустым — и следующий прогон отвечал «набор для docker-firefox-1x ещё не
    # снят, снимите его», то есть отправлял по кругу, который не размыкается.
    #
    # Спрашиваем ИХ интерпретатор: браузеры берутся из установки Playwright
    # проекта, наша к этому отношения не имеет.
    #
    # Отказ ровно на одном состоянии — `not-installed`: пакет Playwright есть,
    # он сам называет путь к бинарю, бинаря по этому пути нет. Это факт, а не
    # предположение.
    #
    # `no-playwright` отказом НЕ является, и это не осторожность, а суть дела:
    # чужой набор может гоняться selenium'ом или чем угодно ещё — адаптер
    # цепляется за их драйвер, а не за наш. Отказать здесь значило бы
    # остановить работающий набор по причине, которой у него нет. Не смогли
    # спросить — тоже запускаем.
    if browser and project.uses_pytest():
        from .matrix import NOT_INSTALLED, install_hint

        state = project_browser_state(project, browser)
        if state == NOT_INSTALLED:
            raise ExternalRunRefused(
                install_hint(browser, state, where="the project environment"))

    # In CI we never write baselines: a run only compares.
    if ci:
        update_baselines = False

    baseline_dir, own = resolve_baselines(
        project, cfg, baseline_source=baseline_source, browser=browser,
        update_baselines=update_baselines, env=env)

    # Секунды не хватает на идентификатор прогона, а с многобраузерностью и
    # подавно: три прогона одного проекта стартуют в одну секунду штатно, а
    # `run_key` уникален и приём сделан на `INSERT OR REPLACE` — то есть второй
    # молча стёр бы первый вместе со всеми его сравнениями. Браузер в имени
    # заодно делает ключ читаемым в списке прогонов.
    run_key = "-".join(x for x in (
        "ext", project.key, browser, time.strftime("%Y%m%d-%H%M%S"),
        uuid.uuid4().hex[:4]) if x)
    run_dir = cfg.runs_path() / run_key
    run_dir.mkdir(parents=True, exist_ok=True)

    mode = project.mode()
    # Ask git in THEIR repository rather than the environment: the CI variables
    # describe the build the service itself runs inside — a different commit
    # entirely.
    from .runner import git_info

    run = ExternalRun(project=project.key, mode=mode,
                      baseline_dir=str(baseline_dir), run_dir=str(run_dir),
                      baseline_scope="vistest" if own else "project",
                      # Браузер — свойство прогона с самого начала, а не с
                      # момента сборки команды. Прогон, оборвавшийся на сборе
                      # тестов, тоже обязан помнить, чьим он был: иначе в
                      # истории и в отчёте он выглядит прогоном chromium.
                      browser=browser,
                      git=git_info(project.root_path))
    if run.git.get("sha"):
        log(f"commit: {run.git.get('branch') or '—'} · {run.git['sha'][:8]}")

    env.update({
        "VISTEST_ADAPTER_BASELINES": str(baseline_dir),
        "VISTEST_ADAPTER_SIDECAR": str(project.sidecar_dir(cfg)),
        "VISTEST_ADAPTER_RUN": str(run_dir),
        # The effective source of this run (can be overridden by a button),
        # not just the project setting — otherwise the adapter compares against
        # the wrong thing.
        "VISTEST_ADAPTER_STORE": "vistest" if own else "project",
        "VISTEST_ROOT": str(cfg.root_path),
    })

    browser_args: list[str] = []
    if browser:
        env_bits, browser_args = _browser_env_and_args(project, browser, log=log)
        env.update(env_bits)

    # Verdict thresholds set in the interface. This is a separate process with
    # its own `vistest.yaml`, and it knows nothing about our database — so the
    # values travel as environment variables (`VisTestConfig._apply_env` picks
    # them up). Without this, the setting would apply to the service's own runs
    # and silently not to project runs, which is the kind of discrepancy that
    # reads as «the setting does not work at all».
    thresholds = _threshold_env(project.key)
    if thresholds:
        env.update(thresholds)
        log("thresholds from the interface: "
            + ", ".join(f"{k.removeprefix('VISTEST_').lower()}={v}"
                        for k, v in sorted(thresholds.items())))

    if update_baselines:
        env["VISTEST_ADAPTER_UPDATE"] = "1"

    if mode == "adapter":
        env.update({
            "VISTEST_ADAPTER_TARGET": project.adapter.target,
            "VISTEST_ADAPTER_NAME_ARG": str(project.adapter.name_arg),
            "VISTEST_ADAPTER_PAGE_ATTR": project.adapter.page_attr,
            "VISTEST_ADAPTER_PASSTHROUGH":
                "1" if project.adapter.passthrough_on_error else "0",
        })

    # An empty value in the project settings does not mean «an empty string», it
    # means «this variable must not exist» — and a blank field in the form looks
    # exactly like «I did not fill this in». Then the run happens without the
    # variable, and the tests fall at setup for a reason nobody would connect to
    # a project setting.
    blanked = sorted(k for k, v in {**(project.env or {}),
                                    **(env_overrides or {})}.items() if v == "")
    if blanked:
        log(f"WARNING: variables declared empty and therefore REMOVED from the "
            f"run environment: {', '.join(blanked)}. If you meant a value, fill "
            "it in; if you meant «unset», this is correct.")

    # A `${NAME}` that resolved to nothing. Silence here would cost a whole run:
    # the variable simply would not exist, and the tests would fall at setup with
    # a reason nobody would connect to a reference in the project settings.
    if unresolved:
        log("WARNING: references with nothing to resolve to: "
            + ", ".join(sorted(set(unresolved)))
            + ". These variables were NOT set. Add the source in "
              "«Settings → Secrets» or fix the reference.")

    log(f"mode: {mode}")
    log(f"baselines: {baseline_dir}"
        + ("  (own VisTest set)" if own
           else "  (project folder)"))
    if own and update_baselines and not has_own_baselines(baseline_dir):
        log("The set is empty — this run will capture baselines and mark "
            "snapshots as new_baseline. The tests stay green: there is nothing "
            "to compare against yet.")

    if project.uses_pytest():
        exe = choose_interpreter(project, log=log)
        log(f"interpreter: {exe}"
            + ("  (VisTest environment)" if exe == sys.executable else ""))
    else:
        # Nothing to choose: their command decides for itself what to start with.
        exe = None
        log(f"runner: command  ({' '.join(project.command)})")

    # Check that their tests collect at all. Otherwise the run fails with
    # «No module named pytest» on the first line, there is no report — and the
    # person sees «total 0, failed 0», which hides the real cause.
    pre = preflight(project, cfg=cfg, env_overrides=env_overrides, log=log,
                    exe=exe)
    if not pre["ok"]:
        run.errors.append(pre["hint"] or "Tests do not collect")
        run.output = pre["output"]
        run.exit_code = -1
        _write_run(run, run_dir)
        return run
    run.collected = int(pre["collected"] or 0)
    log(f"tests to run: {run.collected}")

    # A snapshot of «what was there before the run» is needed by the review mode:
    # the directory could still hold actual_*.png files from the day before
    # yesterday, and passing them off as the result would be a lie.
    before: dict[str, float] = {}
    if mode == "observe":
        for where in _observe_roots(project, project.profile(),
                                    baseline_dir, run_dir):
            before.update(_png_snapshot(where))

    run.command = _build_command(project, mode,
                                 list(extra_args or []) + browser_args,
                                 exe=exe, only=only)
    log("$ " + " ".join(run.command))

    started = time.perf_counter()
    run.exit_code, run.output = _spawn(run.command, project.root_path, env,
                                       log=log, should_stop=should_stop)
    run.elapsed_s = time.perf_counter() - started

    # Collecting the results must not cost us the run record. Without the
    # `finally` a failure while gathering left the run directory with pictures
    # and no `external.json` — a run that happened but cannot be opened.
    try:
        if mode == "adapter":
            _collect_adapter(run, run_dir, project=project, log=log)
        else:
            _collect_observed(run, project, cfg, baseline_dir, run_dir,
                              before=before, log=log, own=own,
                              update_baselines=update_baselines,
                              browser=browser)
    except Exception as e:
        run.errors.append(f"Failed to collect the results: {type(e).__name__}: {e}")
        log(run.errors[-1])
        raise
    finally:
        _write_run(run, run_dir)
    return run


def _threshold_env(project_key: str) -> dict[str, str]:
    """Overrides handed to somebody else's process as environment variables.

    `external` is also imported by the CLI, where there is no service database
    at all. That — and only that — means «no overrides»: an `ImportError` here
    is the absence of the service, not a failure of it.

    Anything else is raised, with the project named. A thresholds table that
    cannot be read has to stop the run rather than start it under the defaults:
    the whole reason these variables exist is that the project's own
    `vistest.yaml` says something different, so «could not read them» and «there
    are none» produce two different verdicts on the same screenshots.
    """
    try:
        from .api.main import db
        from .api.thresholds import env_for
    except ImportError:
        return {}
    try:
        return env_for(db, project_key)
    except Exception as e:
        raise RuntimeError(
            f"cannot read the verdict thresholds for project "
            f"{project_key!r}: {type(e).__name__}: {e}") from e


def _write_run(run: ExternalRun, run_dir: Path) -> None:
    try:
        (run_dir / "external.json").write_text(
            json.dumps(run.to_dict(), indent=2, ensure_ascii=False,
                       default=str),
            encoding="utf-8")
    except OSError:
        pass

    # Прогон подключённого проекта — единственный случай, где pytest ничего не
    # сообщает CI сам: в режиме наблюдения их тесты уже отработали и покрасили
    # себя как считали нужным, а наш вердикт пересчитан отдельно. Без этого
    # файла результат VisTest для сборки не существует вовсе.
    try:
        from .report.junit import render_junit

        (run_dir / "junit.xml").write_text(
            render_junit(run.results or [], suite=run.project,
                         run_key=Path(run.run_dir or run_dir).name,
                         duration_s=run.elapsed_s or 0.0,
                         honor_review=False),
            encoding="utf-8")
    except Exception:
        pass


# --------------------------------------------------------------------------- #
def _build_env(project: Project, cfg: VisTestConfig,
               overrides: dict[str, str] | None,
               unresolved: list[str] | None = None) -> dict[str, str]:
    """The run environment.

    Order matters: first the service environment, then VisTest secrets, then the
    project variables, and at the very end — what the person specified directly in
    the interface. The closer to the person, the stronger.

    `unresolved` — an optional list the caller passes in to learn about `${NAME}`
    references that had nothing to resolve to. It is not an exception: a run with
    a missing variable is still a run, and the caller decides how loudly to say
    it.
    """
    env = dict(os.environ)

    secrets = cfg.root_path / "secrets.env"
    if secrets.exists():
        for line in secrets.read_text("utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip("'\"")

    theirs = {str(k): str(v) for k, v in (project.env or {}).items()}
    theirs.update({str(k): str(v) for k, v in (overrides or {}).items()})
    # `${NAME}` refers to a variable that is already known — from the service
    # environment or from `secrets.env`. This exists so that a password does not
    # have to be typed into the project description: `projects.yaml` is meant to
    # be committed to git, and a secret in it is a secret in the repository.
    # A project whose tests want `ACME_PASSWORD` while the installation keeps
    # `VISTEST_PASSWORD` is one line: ACME_PASSWORD=${VISTEST_PASSWORD}.
    theirs = _expand(theirs, env, unresolved=unresolved)
    env.update(theirs)

    # Their modules must import without installing the package: `from
    # UiTests.pages...` is written relative to the repo root, and adding it to
    # PYTHONPATH is cheaper and more honest than demanding `pip install -e .` from
    # a project that never asked for it.
    entries = project.python_path_entries()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(
        [*entries, *([existing] if existing else [])])

    # An empty value means «the variable must not exist». For projects that
    # choose baselines based on the presence of CI, this is the only way to make
    # a run behave as a local one.
    #
    # Only the variables the person set are treated this way. Sweeping the whole
    # environment removed system variables that are legitimately empty — on
    # Windows there are several, and their disappearance shows up much later and
    # in a completely unrelated place.
    for key, value in theirs.items():
        if value == "":
            env.pop(key, None)

    # Ключ проекта в окружении прогона. Нужен адаптеру: снимок, снятый чужим
    # тестом, потом надо уметь перепроверить ЧУЖИМ интерпретатором, из ЧУЖОГО
    # корня и с их conftest. Без ключа мы знаем идентификатор теста, но не
    # знаем, где его искать, — и «перепроверить» снова превращается в «загрузить
    # адрес», то есть в форму входа.
    env["VISTEST_PROJECT_KEY"] = project.key

    env.setdefault("PYTHONUNBUFFERED", "1")
    # Without this their pytest writes in the Windows console encoding, we read it
    # as UTF-8, and Russian test titles turn into garbage. Debugging from such
    # output is impossible, and the output is the only thing that shows what went
    # wrong for them.
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand(values: dict[str, str], base: dict[str, str], *,
            unresolved: list[str] | None = None) -> dict[str, str]:
    """Substitute `${NAME}` from the already assembled environment.

    A reference that resolves to nothing removes the whole variable rather than
    leaving a literal `${NAME}` in it. A test that receives the string
    «${ACME_PASSWORD}» as a password fails in a way nobody can read; an absent
    variable fails understandably, and the run report says which reference broke.
    """
    out: dict[str, str] = {}
    for name, value in values.items():
        missing = [ref for ref in _REF_RE.findall(value) if ref not in base]
        if missing:
            if unresolved is not None:
                unresolved.extend(f"{name}=${{{ref}}}" for ref in missing)
            continue
        out[name] = _REF_RE.sub(lambda m: base[m.group(1)], value)
    return out


def _has_pytest(exe: str) -> bool:
    try:
        return subprocess.run(
            [exe, "-c", "import pytest"], capture_output=True, timeout=60
        ).returncode == 0
    except Exception:
        return False


def choose_interpreter(project: Project, log=print) -> str:
    """Which interpreter to run with.

    If the configured one has no pytest but ours does — we will not fail silently
    nor force manual config edits: we switch ourselves and say so. This is exactly
    what a typical first attempt looks like: the project venv is set up for a
    linter, there is no pytest in it, and «No module named pytest» explains nothing
    to the person.
    """
    exe = project.resolve_python()
    if exe == sys.executable or _has_pytest(exe):
        return exe
    if _has_pytest(sys.executable):
        log(f"{exe} has no pytest — using the VisTest environment")
        log("to make it permanent: remove the interpreter in the project settings")
        return sys.executable
    return exe


def _browser_env_and_args(project: Project, browser: str, *, log=print
                          ) -> tuple[dict[str, str], list[str]]:
    """Как имя браузера дойдёт до ИХ pytest.

    Своего браузера в этом пути у нас нет: его поднимает их conftest. Значит
    единственный способ — попросить их код, и способов попросить ровно два.

    `VISTEST_BROWSER` выставляется ВСЕГДА, даже когда используется флаг. Это
    дешёвая страховка: проекту, у которого свой разбор аргументов, достаточно
    прочитать переменную, и ему не нужно ни нашего согласия, ни настройки.

    `--browser` добавляется только там, где он существует. Флаг вводит
    pytest-playwright; без него pytest падает на «unrecognized arguments» —
    то есть многобраузерный прогон убивал бы набор, который прекрасно
    работает. Поэтому в режиме `auto` наличие плагина проверяется их же
    интерпретатором, а не предполагается.
    """
    env = {"VISTEST_BROWSER": browser}
    how = project.browser_control()
    if how == "env":
        return env, []
    if how == "flag":
        return env, ["--browser", browser]
    if how == "none":                                      # pragma: no cover
        return env, []
    if how == "profile":
        # Инструмент опознан, и аргумент берётся из его профиля, а не из
        # предположения: у Playwright это `--project`, у Cypress `--browser`,
        # у `mvn test` — ничего, и профиль честно не даёт ничего.
        profile = project.profile()
        args = [a.format(browser=browser) for a in profile.browser_arg]
        if args:
            log(f"browser: {browser} → {' '.join(args)}  "
                f"(«{profile.id}» profile)")
        return env, args

    # auto
    if any(str(a).startswith("--browser") for a in (project.pytest_args or [])):
        # Проект уже назвал браузер сам. Приписать второй флаг значит спорить с
        # его собственной настройкой — а чья возьмёт, зависит от порядка
        # аргументов, то есть непредсказуемо.
        log(f"browser: {browser} только через VISTEST_BROWSER — в pytest_args "
            "проекта уже есть свой --browser")
        return env, []
    if _has_pytest_playwright(project):
        return env, ["--browser", browser]
    log(f"browser: {browser} только через VISTEST_BROWSER — pytest-playwright "
        "в окружении проекта не найден, флага --browser у их pytest нет")
    return env, []


_BROWSER_PROBE: dict[str, dict[str, bool]] = {}


def project_browser_state(project: Project, browser: str) -> str:
    """Есть ли этот движок В ОКРУЖЕНИИ ПРОЕКТА.

    Спрашивать надо именно там. Прогон чужого проекта поднимает ИХ pytest их
    интерпретатором, и браузеры берутся из их установки Playwright — наша к
    этому отношения не имеет вовсе. Сервис вполне может стоять в образе без
    единого браузера (`Dockerfile.api` собран из `python:3.12-slim`), а проект
    при этом гоняться прекрасно.

    Отсюда же и правило на случай «не смогли спросить»: отвечаем `installed`.
    Ложный отказ здесь дороже молчания — он останавливает набор, который
    работает, и объясняет это причиной, которой нет.
    """
    from .matrix import INSTALLED, NO_PLAYWRIGHT, NOT_INSTALLED, PROBE

    exe = project.resolve_python()
    if exe not in _BROWSER_PROBE:
        try:
            done = subprocess.run([exe, "-c", PROBE],
                                  capture_output=True, timeout=30, text=True)
            found = json.loads((done.stdout or "{}").strip().splitlines()[-1])
            _BROWSER_PROBE[exe] = found if isinstance(found, dict) else {}
        except Exception:
            _BROWSER_PROBE[exe] = {"__unknown__": True}
    found = _BROWSER_PROBE[exe]
    if found.get("__unknown__"):
        return INSTALLED
    if not found:
        return NO_PLAYWRIGHT
    return INSTALLED if found.get(browser) else NOT_INSTALLED


_PW_PROBE: dict[str, bool] = {}


def _has_pytest_playwright(project: Project) -> bool:
    """Стоит ли pytest-playwright в ИХ окружении.

    Один короткий подпроцесс на интерпретатор, с запоминанием: спрашивать это
    на каждый прогон одного и того же проекта незачем, а угадывать нельзя —
    цена ошибки в обе стороны одинаковая. Не смогли спросить (нет
    интерпретатора, таймаут) — считаем, что плагина нет: не добавить флаг
    безопаснее, чем добавить неизвестный.
    """
    exe = project.resolve_python()
    if exe in _PW_PROBE:
        return _PW_PROBE[exe]
    try:
        done = subprocess.run(
            [exe, "-c", "import pytest_playwright"],
            capture_output=True, timeout=20)
        ok = done.returncode == 0
    except Exception:
        ok = False
    _PW_PROBE[exe] = ok
    return ok


def _build_command(project: Project, mode: str,
                   extra_args: list[str] | None,
                   exe: str | None = None, only: str = "") -> list[str]:
    """What is actually started from the project root.

    Two shapes, because two kinds of project. A pytest suite is assembled by us
    — interpreter, plugin, path, markers — and that is the only shape the
    adapter can attach to. Anything else is started by their own command, as
    written: `npx playwright test`, `npx cypress run`, `mvn -Dtest=Visual* test`,
    `dotnet test`. We do not try to understand it; we run it and then look at
    the PNG pairs it left behind.
    """
    if not project.uses_pytest():
        cmd = list(project.command)
        if only:
            # Прицел берётся из профиля, а не угадывается: у Cypress это
            # `--spec`, у Maven `-Dtest`, у Playwright — просто путь. Пустой
            # `only_arg` сюда не доходит: такой прогон отвергается раньше, на
            # входе, — потому что молча прогнать весь набор в ответ на
            # «перепроверь один снимок» хуже любого отказа.
            cmd += [a.format(only=only) for a in project.profile().only_arg]
        return cmd + list(extra_args or [])

    cmd = [exe or project.resolve_python(), "-m", "pytest"]
    if mode == "adapter":
        cmd += ["-p", "vistest.adapter"]
    # `only` — идентификатор одного теста; он ЗАМЕНЯЕТ путь к набору, а не
    # добавляется к нему. Приписанный сбоку, он дал бы pytest две цели: весь
    # набор и вдобавок этот тест, — то есть «перепроверить один снимок»
    # означало бы прогнать всё.
    if only:
        cmd.append(only)
    elif project.tests:
        cmd.append(project.tests)
    cmd += list(project.pytest_args or [])
    cmd += list(extra_args or [])
    return cmd


STOP_GRACE_S = 120.0        # how long a terminated pytest is given to finish


def _spawn(cmd: list[str], cwd: Path, env: dict[str, str],
           *, log, should_stop, grace_s: float = STOP_GRACE_S
           ) -> tuple[int, list[str]]:
    """Run their pytest and relay the output line by line.

    The output is shown to the person as-is. Their tests, their output: any attempt
    to rephrase it would make debugging impossible.

    Three things here are deliberate, and each of them used to be wrong:

    * `FileNotFoundError` is caught around `Popen`, not around reading. A missing
      interpreter fails at spawn time, so the friendly message never used to fire
      — the person got a bare traceback instead of «check the project interpreter».
    * after a stop we keep draining stdout instead of breaking out of the loop.
      A terminated pytest still writes the tail of the current test and the
      summary; with nobody reading the pipe it blocks on a full buffer and then
      has to be killed — which is precisely what «finish the current test» was
      meant to avoid, and it costs us the last lines of the log.
    * the exit code is never `None`. `Popen.returncode` stays `None` until the
      process is reaped, and a `None` in `exit_code` reads in the report as
      «no data» rather than «killed».
    """
    lines: list[str] = []
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(cwd), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
    except (FileNotFoundError, NotADirectoryError, PermissionError) as e:
        raise RuntimeError(
            f"Failed to start {cmd[0]!r} in {cwd}: {e}. "
            "Check the project interpreter and root in the project settings."
        ) from e

    stopping = False
    assert proc.stdout is not None
    try:
        for raw in proc.stdout:
            line = raw.rstrip()
            lines.append(line)
            log(line)
            if not stopping and should_stop():
                log("stopping: sending a termination signal, "
                    "the current test is finishing")
                stopping = True
                proc.terminate()
    finally:
        try:
            proc.stdout.close()
        except OSError:
            pass

    try:
        proc.wait(timeout=grace_s)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        log(f"the process did not finish within {grace_s:.0f} s — killed forcibly")

    return (proc.returncode if proc.returncode is not None else -1), lines


# --------------------------------------------------------------------------- #
def _note(run: ExternalRun, log, text: str) -> None:
    """Записать замечание в прогон и сразу сказать его вслух.

    Одной функцией, потому что раньше часть замечаний логировалась, часть нет, а
    вызывающая сторона на всякий случай печатала весь список ещё раз. В итоге
    один и тот же диагноз появлялся в логе четыре раза и терялся среди самого
    себя.
    """
    run.errors.append(text)
    log(text)


def _collect_adapter(run: ExternalRun, run_dir: Path, *, project=None,
                     log=print) -> None:
    path = run_dir / "adapter.json"
    if not path.exists():
        _note(run, log,
              "The adapter left no report. Usually this means their pytest did "
              "not start: look at the output above.")
        return

    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as e:
        _note(run, log, f"The adapter report is unreadable ({e}).")
        return

    run.results = data.get("results") or []
    run.errors += data.get("errors") or []
    run.tests = data.get("tests") or []
    # The adapter counts after deselection, so its number is the more precise
    # one — but it must not erase the preflight estimate if it is missing.
    run.collected = int(data.get("collected") or 0) or run.collected

    if not data.get("patched"):
        _note(run, log,
              "The comparison point was not intercepted — the comparison ran "
              "through their code. Check the path in the project settings.")

    log(f"snapshots checked: {len(run.results)}")

    # The most confusing outcome of a run is «fewer snapshots than tests» with
    # no explanation: it reads as «the tests did not start», although usually
    # the tests started, ran and broke before the screenshot. We say which of
    # the two it was, by name.
    if not run.collected:
        return
    log(f"tests run: {run.collected}")
    if len(run.results) >= run.collected:
        return

    for note in diagnose(run, project=project):
        _note(run, log, note)


def diagnose(run: ExternalRun, *, project=None) -> list[str]:
    """Why there are fewer snapshots than tests, in words the person can act on.

    Three different situations look identical in the report — «the tests did not
    run» — and the difference between them is the whole answer:

    * a test fell at fixture setup: the body never started, nothing was tested,
      and it is almost never the tests that are at fault. Nine identical
      failures on `logged_in_page` mean one broken precondition, most often a
      variable that did not reach the run;
    * a test fell in its own body: it started and broke on its way to the
      screenshot. Their code, their reason, the output above;
    * a test passed but produced no snapshot: the comparison point is not the
      one configured, or the snapshot name was not recognized.
    """
    setup = [t for t in run.tests
             if t.get("outcome") == "failed" and t.get("phase") == "setup"]
    body = [t for t in run.tests
            if t.get("outcome") == "failed" and t.get("phase") != "setup"]
    notes: list[str] = []

    if setup:
        notes.append(_setup_note(setup, run.collected, project))
    if body:
        notes.append(
            f"{len(body)} of {run.collected} tests broke on their way to the "
            f"screenshot: {_names(body)}. They started, so the reason is in "
            "their own output above.")
    silent = run.collected - len(run.results) - len(setup) - len(body)
    if silent > 0:
        notes.append(
            f"{silent} of {run.collected} tests finished without a single "
            "comparison: either the comparison point is not called there, or "
            "the snapshot name could not be worked out from the call arguments "
            "(adapter.target and name_arg in the project settings).")
    return notes


def _setup_note(setup: list[dict], collected: int, project=None) -> str:
    from collections import Counter

    note = (f"{len(setup)} of {collected} tests fell at fixture setup — the "
            f"test body never started, nothing was compared: {_names(setup)}.")

    causes = Counter(t.get("error", "") for t in setup if t.get("error"))
    if causes:
        cause, times = causes.most_common(1)[0]
        note += f" Cause ({times} of {len(setup)}): {cause}"

    empty = sorted({name for t in setup for name in (t.get("empty_args") or [])})
    if not empty:
        return note

    note += f" · Fixtures that arrived empty: {', '.join(empty)}."
    # «Add the variable somewhere» is half an answer: the person still goes to
    # open the project's conftest to find out which one. We can read that for
    # them — the project is right here, and the question is answered by parsing
    # the AST, without executing anyone's code.
    sources = _sources_of(empty, project)
    if sources:
        note += " " + " ".join(sources)
    else:
        note += (
            " Almost always this is a value that did not reach the run: add it "
            "in «Settings → variables» or in the project env — and note that an "
            "empty value there does not mean «empty string», it REMOVES the "
            "variable. If the project takes it from a pytest option, add it to "
            "pytest_args.")
    return note


def _sources_of(names: list[str], project) -> list[str]:
    """Где определены пустые фикстуры и откуда они берут значение."""
    if project is None:
        return []
    try:
        from .introspect import find_fixture_sources

        found = find_fixture_sources(project.root_path, names)
    except Exception:      # диагностика не имеет права ронять прогон
        return []
    return [f"{src.describe()} — {src.advice()}."
            for _, src in sorted(found.items())]


def _names(tests: list[dict], limit: int = 5) -> str:
    heads = [str(t.get("id", "?")).rsplit("::", 1)[-1] for t in tests[:limit]]
    return ", ".join(heads) + (" …" if len(tests) > limit else "")


def _store_for(project: Project, cfg: VisTestConfig, baseline_dir: Path,
               *, own: bool | None = None, browser: str = ""):
    """Baselines store for a directory THIS RUN chose.

    `own` is the effective source of this run — the same flag `run_project`
    used to pick `baseline_dir`. It is a parameter and not a lookup on purpose.

    Reading it from `project.uses_own_baselines()` was a real bug, and a nasty
    one: with the project set to «their PNGs» and the run started by «Run on
    VisTest baselines», the directory came from one source and the store layout
    from the other. `FileBaselineStore` keeps `<name>/baseline.png`,
    `ExternalBaselineStore` keeps `<name>.png` flat — so `store.exists()`
    answered False for every single snapshot, the log filled with «no baseline
    in …», and the run looked as if nothing had been captured at all.
    """
    if own is None:                      # only for callers outside a run
        own = project.uses_own_baselines()

    if own:
        from .storage import FileBaselineStore

        return FileBaselineStore(baseline_dir)

    from .storage import ExternalBaselineStore

    # Подсказка для набора, где платформа записана в имя файла:
    # Playwright хранит `login-chromium-linux.png`, а снимок называется
    # `login`. Без токенов прогона выбор между вариантами был бы случайным.
    variants = tuple(x for x in (browser, _os_token()) if x)
    return ExternalBaselineStore(baseline_dir, project.sidecar_dir(cfg),
                                 variants=variants)


def _os_token() -> str:
    """Как их инструмент называет эту операционную систему в имени файла."""
    return {"win32": "win32", "darwin": "darwin"}.get(sys.platform, "linux")


def _observe_roots(project: Project, profile, baseline_dir: Path,
                   run_dir: Path) -> list[Path]:
    """Where to look for the pictures a run left behind.

    Three sources, and the third is the one that used to be missing. The
    baseline folder and our run directory were always scanned; the folder the
    tool actually writes to was not, and for Playwright that is `test-results`
    at the repository root — outside both. A run therefore found nothing and
    said so as if the suite had produced nothing.

    The tests directory is scanned only when the profile says the tool writes
    the actual next to the baseline. For everyone else it is a walk over their
    whole source tree for no reason, and on a Node repository that walk is the
    slowest thing in the run.
    """
    extra: list[Path] = [baseline_dir, run_dir]
    if profile.search_tests:
        extra.append(project.tests_path())
    return profile.roots(project.root_path,
                         extra=tuple(x for x in extra if x is not None))


def _png_snapshot(directory: Path) -> dict[str, float]:
    """«What lay here before the run» — keyed by full path.

    Keying by file name alone made two `actual_login.png` in different folders a
    single entry, and a fresh snapshot got mistaken for yesterday's because a
    namesake elsewhere was newer.
    """
    out: dict[str, float] = {}
    if not directory or not directory.exists():
        return out
    for dirpath, dirnames, filenames in os.walk(directory):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS
                       and not d.startswith(".")]
        for filename in filenames:
            if not filename.lower().endswith(".png"):
                continue
            path = Path(dirpath) / filename
            try:
                out[str(path.resolve())] = path.stat().st_mtime
            except OSError:
                continue
    return out


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix() or "."
    except ValueError:
        return str(path)


def _collect_observed(run: ExternalRun, project: Project, cfg: VisTestConfig,
                      baseline_dir: Path, run_dir: Path, *,
                      before: dict[str, float], log, own: bool | None = None,
                      update_baselines: bool = False, browser: str = "") -> None:
    """Mode without injection: compare the pictures left after their run.

    Their test already decided whether it matched or not, and already colored
    itself. Our task is different — to show **what exactly** diverged: regions,
    classes, severity, artifacts. That is why the verdict is recomputed by our
    engine.

    Which file is which is not decided here. It is decided by the suite
    profile, and that is the whole reason this path works outside pytest at
    all: classification used to go by prefix (`actual_login.png`), while
    Playwright, Cypress and jest-image-snapshot all name by suffix
    (`login-actual.png`). Every picture they wrote was filed as a baseline,
    no actuals were found, and the run ended by blaming the suite.
    """
    from .capture.playwright_capture import read_png
    from .service import CheckService
    from .storage import SiblingBaselineStore
    from .suites import ACTUAL, EXPECTED

    profile = project.profile()
    roots = _observe_roots(project, profile, baseline_dir, run_dir)
    log(f"suite profile: {profile.id} — {profile.title}")
    log("looking for pictures in: "
        + (", ".join(_relative(r, project.root_path) for r in roots) or "—"))

    actuals: dict[str, Path] = {}
    groups: dict[str, str] = {}
    expected: dict[str, Path] = {}
    collisions: dict[str, list[Path]] = {}
    stale = 0

    for snapshot in profile.walk(roots, relative_to=project.root_path):
        if snapshot.kind == EXPECTED:
            expected.setdefault(snapshot.group, snapshot.path)
            continue
        if snapshot.kind != ACTUAL:
            continue
        # Freshness by time: the directory could still hold pictures from the
        # day before yesterday, and passing those off as the run result is a lie.
        try:
            mtime = snapshot.path.stat().st_mtime
        except OSError:
            continue
        if before.get(str(snapshot.path.resolve()), -1.0) >= mtime:
            stale += 1
            continue
        previous = actuals.get(snapshot.name)
        if previous is not None and previous != snapshot.path:
            collisions.setdefault(snapshot.name, [previous]).append(snapshot.path)
        actuals[snapshot.name] = snapshot.path
        groups[snapshot.name] = snapshot.group

    if not actuals:
        _note(run, log, _nothing_found(profile, roots, project, stale))
        return

    # Их собственный эталон, лежащий рядом с фактом. Для Playwright и Cypress
    # это единственный способ узнать, С ЧЕМ они сравнивали: путь к эталону
    # считает их конфигурация, а не мы. При съёмке своего набора он не нужен —
    # там эталоном становится сам факт.
    siblings: dict[str, Path] = {}
    if not update_baselines:
        for name in actuals:
            found = expected.get(groups.get(name, ""))
            if found is not None:
                siblings[name] = found

    for name, paths in sorted(collisions.items()):
        _note(run, log,
              f"«{name}»: {len(paths)} different files claim this name "
              + ", ".join(_relative(x, project.root_path) for x in paths)
              + ". Only the last one was checked. Turn on «keep the folder in "
                "the snapshot name» in the project settings so they stop "
                "colliding.")
    if stale:
        log(f"skipped as left over from an earlier run: {stale}")

    primary = _store_for(project, cfg, baseline_dir, own=own, browser=browser)
    store = SiblingBaselineStore(primary, siblings) if siblings else primary
    service = CheckService(cfg, run_dir=run_dir, store=store,
                           browser=browser or "chromium")

    if update_baselines:
        # Съёмка набора в этом режиме возможна — и до сих пор её не было: в
        # разборе готовых PNG стояло `update_baseline=False`, то есть кнопка
        # «Snap VisTest baselines» для всего, что не pytest, не делала ничего,
        # а прогон потом отправлял к ней же. Круг размыкается здесь.
        #
        # Но сказать надо ровно то, что происходит: эталоном становится ИХ
        # картинка, снятая их конвейером. Наш захват — заморозка анимаций,
        # подмена времени, серия кадров — в этом пути не участвует, и обещать
        # его стабильность было бы неправдой.
        log("capturing the VisTest set from their own pictures: our capture "
            "(frozen animations, substituted time, a series of frames) is not "
            "involved here — the pixels are theirs, the history and the review "
            "are ours")

    log(f"pairs found for review: {len(actuals)}"
        + (f" (of them {len(siblings)} against the suite's own expected picture)"
           if siblings else ""))

    for name, png in sorted(actuals.items()):
        borrowed = isinstance(store, SiblingBaselineStore) and store.borrowed(name)
        if not update_baselines and not store.exists(name):
            _note(run, log,
                  f"{name}: no baseline — neither in {baseline_dir.name} nor "
                  "beside the actual picture")
            continue
        notes = [f"Review of a ready snapshot: {png}"]
        if borrowed:
            notes.append(
                f"Compared against the suite's own baseline copy "
                f"({_relative(siblings[name], project.root_path)}). Accepting "
                "this change means updating it with their tool, not here.")
        try:
            res = service.check(name, read_png(png),
                                update_baseline=update_baselines and not borrowed,
                                notes=notes)
        except Exception as e:
            _note(run, log, f"{name}: {type(e).__name__}: {e}")
            continue

        res.artifacts.setdefault("actual", str(png))
        entry = res.to_dict()
        metrics = entry.get("metrics") or {}
        entry.update({
            "actual": str(png),
            "source": str(png),
            "baseline_source": "suite" if borrowed else (
                "vistest" if own else "project"),
            "max_severity": metrics.get("max_severity", 0.0),
            "changed_area_pct": metrics.get("changed_area_pct", 0.0),
            "ssim": metrics.get("ssim_global", 1.0),
            "region_count": len(entry.get("regions") or []),
            "suppressed_count": len(entry.get("suppressed") or []),
        })
        run.results.append(entry)
        log(f"  {res.verdict.value:12s} {name}  severity={res.max_severity:.1f}")


def _nothing_found(profile, roots, project: Project, stale: int) -> str:
    """Why the run produced nothing — with the rules it actually applied.

    «Did not find a single fresh actual_*.png» was true and useless: it named
    one naming convention out of the several in use and left the person to
    guess whether the suite wrote nothing, wrote it elsewhere, or wrote it
    under a name we do not recognise. All three are fixable, and they are
    fixed in different places.
    """
    where = ", ".join(_relative(r, project.root_path) for r in roots) or "—"
    rules = ", ".join(profile.actual) or "—"
    # Каталоги, которых нет, называются отдельно и намеренно. Отсутствие
    # `test-results` — это не «мы туда не смотрели», это «их прогон ничего не
    # написал», и различить два случая по одному списку «где искали» нельзя.
    missing = [d for d in profile.search_dirs if not (project.root_path / d).exists()]
    absent = (f" The «{profile.id}» profile also expects "
              f"{', '.join(missing)}, and there is no such folder in the "
              "project — so the run wrote nothing there." if missing else "")
    tail = (f" {stale} picture(s) were found but left over from an earlier run."
            if stale else "")
    return (
        f"No fresh actual picture was found. Looked in: {where}.{absent} Rules "
        f"of the «{profile.id}» profile for the actual: {rules}.{tail} Either "
        "the suite wrote nothing (for Playwright a green run leaves no "
        "artifacts at all), or it writes to another folder — add it in «Where "
        "the pictures land» — or it names them differently: describe the name "
        "in «Snapshot naming».")


# --------------------------------------------------------------------------- #
def ingest_project(project: Project, *, cfg: VisTestConfig | None = None,
                   dirs: list[str] | None = None, browser: str = "",
                   baseline_source: str | None = None,
                   update_baselines: bool = False, run_key: str = "",
                   ci_url: str = "", log=print) -> ExternalRun:
    """Разобрать то, что осталось после ИХ прогона. Ничего не запуская.

    Третий вход, и он закрывает то, чего не закрывали первые два.

    Прогон чужой командой (`runner: command`) требует, чтобы их инструмент был
    в НАШЕМ образе: `npx playwright test` внутри контейнера VisTest — это node,
    `npm ci` и вся их сборка у нас. Для Node это решается образом, для JVM и
    .NET — уже нет: тащить в образ ревью-сервиса maven с их зависимостями
    никто не станет, и правильно сделает.

    Приём готовых артефактов переворачивает порядок. Их CI гоняет тесты сам,
    там, где у него всё уже стоит, а VisTest получает каталог с картинками и
    делает единственное, чего у них нет: вердикт движка, регионы, классы
    изменений, историю и ревью. Ни одной строчки их кода мы при этом не
    исполняем — и не должны.

    Правила разбора те же, что у обычного прогона: тот же профиль набора, те
    же эталоны, тот же сборщик. Двух дверей с разным поведением быть не может.
    """
    cfg = cfg or VisTestConfig.load()
    browser = (browser or "").strip().lower()
    if browser:
        from .matrix import normalize_browser

        browser = normalize_browser(browser)

    baseline_dir, own = resolve_baselines(
        project, cfg, baseline_source=baseline_source, browser=browser,
        update_baselines=update_baselines)

    if dirs:
        # Каталог из команды — это разовая подсказка «вот сюда положил мой
        # пайплайн», а не изменение описания проекта: сохранять её значило бы
        # менять проект побочным эффектом чтения.
        from dataclasses import replace as _replace

        project = _replace(project, search_dirs=list(
            dict.fromkeys([*(project.search_dirs or []), *dirs])))

    run_key = run_key or "-".join(x for x in (
        "ext", project.key, browser, time.strftime("%Y%m%d-%H%M%S"),
        uuid.uuid4().hex[:4]) if x)
    run_dir = cfg.runs_path() / run_key
    run_dir.mkdir(parents=True, exist_ok=True)

    from .runner import git_info

    run = ExternalRun(project=project.key, mode="observe",
                      baseline_dir=str(baseline_dir), run_dir=str(run_dir),
                      baseline_scope="vistest" if own else "project",
                      browser=browser, git=git_info(project.root_path))
    run.command = ["(ingest)", *(dirs or [])]
    if ci_url:
        run.git.setdefault("ci_url", ci_url)

    log(f"ingest: {project.name or project.key}")
    log(f"baselines: {baseline_dir}"
        + ("  (own VisTest set)" if own else "  (project folder)"))

    started = time.perf_counter()
    try:
        # `before` пуст намеренно, и это не забывчивость. Обычный прогон
        # отсекает вчерашние картинки по времени, потому что он сам их
        # застал; здесь каталог приносит вызывающий и говорит «вот результат
        # моего прогона». Молча выбросить половину принесённого, сверившись с
        # чужими часами, было бы хуже любой лишней пары.
        _collect_observed(run, project, cfg, baseline_dir, run_dir,
                          before={}, log=log, own=own,
                          update_baselines=update_baselines, browser=browser)
    except Exception as e:
        run.errors.append(f"Failed to collect the results: {type(e).__name__}: {e}")
        log(run.errors[-1])
        raise
    finally:
        run.elapsed_s = time.perf_counter() - started
        run.exit_code = 0
        _write_run(run, run_dir)
    return run


# --------------------------------------------------------------------------- #
def publish(run: ExternalRun, project: Project, *,
            cfg: VisTestConfig | None = None, log=print) -> int | None:
    """Put the run into the shared history.

    Works from the command line too: the database is an ordinary file next to the
    baselines, no running service is needed for writing. A run from the terminal
    and a run from a button land in the same place, and that is correct — the
    history must not depend on where it was launched from.
    """
    cfg = cfg or VisTestConfig.load()
    try:
        from .api.db import Database
        from .api.publish import publish_external_run
    except ImportError as e:
        log(f"history unavailable (service dependencies required): {e}")
        return None

    db = Database(cfg.root_path / "vistest.db")
    run_id = publish_external_run(
        db, cfg.root_path / "artifacts", run.to_dict(),
        project=project.key, log=log)
    run.history_run_id = run_id
    return run_id


def baseline_status(project: Project, cfg: VisTestConfig | None = None) -> dict:
    """What is currently in each baseline set. Shown on the card.

    `scope` is what the interface needs to open this set on the «Baselines»
    tab: the VisTest set of a connected project used to be invisible not
    because it was empty, but because nothing linked to it.
    """
    cfg = cfg or VisTestConfig.load()
    out: dict = {"store": project.baseline_store, "project": [], "vistest": [],
                 "scope": f"project:{project.key}"}

    for b in project.baselines:
        d = project.root_path / b.dir
        out["project"].append({
            "dir": b.dir,
            "when": b.when,
            "label": b.label,
            "count": len(list(d.glob("*.png"))) if d.exists() else 0,
            "exists": d.exists(),
        })

    base = project.vistest_baselines_path(cfg)
    if base.exists():
        for d in sorted(base.iterdir()):
            if d.is_dir():
                names = sorted(
                    p.parent.relative_to(d).as_posix() + ".png"
                    for p in d.rglob("baseline.png"))
                out["vistest"].append({
                    "platform": d.name,
                    "count": len(names),
                    "path": str(d),
                    # A short preview so the card can say what is in the set,
                    # not just how many things are in it.
                    "names": names[:12],
                    "more": max(0, len(names) - 12),
                    # How many of them can be launched on their own: a snapshot
                    # with an address bound is runnable from the «Baselines» tab.
                    "runnable": sum(
                        1 for p in d.rglob("meta.json")
                        if _has_url(p)),
                })
    # `current_platform` — платформа МАШИНЫ СЕРВИСА, то есть всегда
    # `…-chromium-…`: `platform_key()` без аргументов не знает ни про какой
    # другой движок. Карточка выбирала по нему набор для «Open VisTest
    # snapshots» и уводила на вкладку эталонов, прибив платформу к chromium.
    # Снаружи это и было «сняли эталоны для firefox — на вкладке их нет»: они
    # там были, просто открывался чужой набор.
    #
    # Поэтому рядом появляется `best_platform` — непустой набор, самый большой
    # из имеющихся, с предпочтением текущему. Открывать надо то, где что-то
    # есть; пустая платформа по умолчанию не отвечает ни на один вопрос.
    out["current_platform"] = platform_key()
    out["vistest_total"] = sum(v["count"] for v in out["vistest"])
    filled = [v for v in out["vistest"] if v["count"]]
    here = next((v for v in filled if v["platform"] == out["current_platform"]),
                None)
    best = here or (max(filled, key=lambda v: v["count"]) if filled else None)
    out["best_platform"] = best["platform"] if best else ""
    return out


def _has_url(meta_path: Path) -> bool:
    try:
        return bool(json.loads(meta_path.read_text("utf-8")).get("url"))
    except Exception:
        return False


def reset_baselines(project: Project, *, cfg: VisTestConfig | None = None,
                    platform: str = "", log=print) -> int:
    """Delete the own VisTest baseline set.

    Only the own one: this command never touches the project folder. Needed when
    the set should be recaptured from scratch — for example, after a browser
    version change or an intentional redesign, where recreating everything is
    easier than reviewing twenty diffs one by one.
    """
    cfg = cfg or VisTestConfig.load()
    base = project.vistest_baselines_path(cfg, platform or "")
    if not base.exists():
        log("There is no own set — nothing to delete.")
        return 0

    count = sum(1 for _ in base.rglob("baseline.png"))
    shutil.rmtree(base, ignore_errors=True)
    log(f"Baselines deleted: {count} ({base})")
    log("The next run will capture them again.")
    return count


def approve(project: Project, name: str, *, cfg: VisTestConfig | None = None,
            actual_png: str | Path | None = None,
            scope: str | None = None) -> Path:
    """Accept a snapshot as a baseline, in the store the run compared against.

    `scope` is the source of baselines of the RUN this snapshot came from:
    `"project"` (their committed PNGs) or `"vistest"` (the set VisTest captured
    itself). When it is omitted we fall back to the project setting, which is
    the best guess available and nothing more.

    That distinction is the whole point of this parameter. The choice used to
    be made from `project.uses_own_baselines()` — the *saved setting* — while
    the run itself could have been started by the other button. Set the project
    to «their PNGs», press «Run on VisTest baselines», accept a failure as the
    new baseline: the file that changed was the one in the project repository,
    the file the next run reads was untouched, and the same diff came back. In
    the other direction it silently rewrote a foreign repository on a decision
    that was never about it.

    Nothing is copied between stores: the run's store is where the decision
    belongs, and moving it elsewhere would only recreate the same confusion.
    """
    from .capture.playwright_capture import read_png
    from .storage import BaselineRecord

    cfg = cfg or VisTestConfig.load()
    if actual_png is None:
        raise RuntimeError("Nothing to accept: no snapshot passed")

    if scope not in ("project", "vistest", None):
        raise RuntimeError(f"Unknown baseline scope: {scope!r}")
    if scope is None:
        scope = "vistest" if project.uses_own_baselines() else "project"

    if scope == "vistest":
        from .storage import FileBaselineStore

        baseline_dir = project.vistest_baselines_path(cfg, platform_key())
        store = FileBaselineStore(baseline_dir)
        store.save(BaselineRecord(name=name, image=read_png(actual_png),
                                  meta={"approved_from": str(actual_png),
                                        "scope": scope}))
        return store.dir_for(name) / "baseline.png"

    from .storage import ExternalBaselineStore

    # The same environment the run selects the folder by: otherwise an approval
    # after a CI-profile run would land in the local folder.
    baseline_dir = project.baseline_dir({**os.environ, **(project.env or {})})
    if baseline_dir is None:
        raise RuntimeError("No baselines folder selected")

    store = ExternalBaselineStore(baseline_dir, project.sidecar_dir(cfg))
    store.save(BaselineRecord(name=name, image=read_png(actual_png),
                              meta={"approved_from": str(actual_png),
                                    "scope": scope}))
    return store.png_for(name)


def only_refusal(project: Project, only: str) -> str:
    """Почему один тест из этого набора запустить нельзя. Пусто — можно.

    Существует по той же причине, что и отказ по браузеру: пункт меню,
    который заведомо сделает не то, о чём просили, — это не строгость, а
    неправда. «Перепроверить один снимок», тихо прогоняющее весь набор,
    выглядит как медленная кнопка, а не как неподдерживаемая операция.
    """
    if not only or project.uses_pytest():
        return ""
    profile = project.profile()
    if profile.only_arg:
        return ""
    return (
        f"The suite «{project.name or project.key}» is started by its own "
        f"command, and the «{profile.id}» profile names no argument that "
        "would narrow it down to one test. Running the whole suite instead "
        "would answer a different question than the one asked. Pick a profile "
        "that matches the tool, or run the whole suite deliberately.")


def check_ready(project: Project) -> list[str]:
    """What will get in the way of a run. Shown before the button is pressed, not after."""
    problems = project.validate()
    exe = Path(project.resolve_python())
    if not exe.exists() and exe.name not in ("python", "python3"):
        problems.append(f"interpreter not found: {exe}")
    if project.adapter.enabled and not project.adapter.target.count("."):
        problems.append(
            "the comparison point must be a path of the form "
            "package.module.Class.method")
    return problems


# --------------------------------------------------------------------------- #
#  Preflight check
# --------------------------------------------------------------------------- #
_MISSING_RE = re.compile(
    r"(?:ModuleNotFoundError|ImportError): No module named ['\"]([\w.]+)['\"]")
_FIXTURE_RE = re.compile(r"fixture ['\"]([\w-]+)['\"] not found")

# A missing fixture → the plugin that provides it.
#
# This is the second most common way to fail to run someone else's tests, and by
# symptoms it is completely unreadable: «fixture 'context' not found» says nothing
# about pytest-playwright not being installed. The package meanwhile installs
# silently — there are no imports in the code, it is in requirements, and test
# collection passes, because fixtures are resolved later, at setup.
_FIXTURE_TO_PACKAGE = {
    "page": "pytest-playwright",
    "context": "pytest-playwright",
    "browser": "pytest-playwright",
    "browser_type": "pytest-playwright",
    "playwright": "pytest-playwright",
    "browser_context_args": "pytest-playwright",
    "browser_type_launch_args": "pytest-playwright",
    "new_context": "pytest-playwright",
    "base_url": "pytest-base-url",
    "live_server": "pytest-django",
    "admin_client": "pytest-django",
    "django_db_setup": "pytest-django",
    "event_loop": "pytest-asyncio",
    "aiohttp_client": "pytest-aiohttp",
    "mocker": "pytest-mock",
    "freezer": "pytest-freezegun",
    "benchmark": "pytest-benchmark",
    "httpx_mock": "pytest-httpx",
    "requests_mock": "requests-mock",
    "snapshot": "syrupy",
    "subtests": "pytest-subtests",
    "selenium": "pytest-selenium",
    "dash_duo": "dash",
}


def preflight(project: Project, *, cfg: VisTestConfig | None = None,
              env_overrides: dict[str, str] | None = None,
              log=print, exe: str | None = None) -> dict:
    """Collect their tests and resolve fixture names without running anything.

    The point is to learn the truth before a run, in a second and with no side
    effects.

    `--setup-plan` is used, not `--collect-only`, and that matters. Test
    collection checks only imports: their `conftest` comes up, modules load, and
    everything looks healthy. But fixtures are *resolved* later — and that is
    where it turns out there is no one to hand `page` or `context` to, because
    the plugin is not installed. `--setup-plan` walks that resolution too.

    What it deliberately does NOT do — and what the comment here used to promise
    — is execute the fixtures. `--setup-plan` prints the plan; it never calls a
    fixture body. So a browser that fails to start, a login that does not go
    through, a variable that did not reach the run — none of that can be seen
    from here, only from the run itself. That is what `diagnose()` is for: it
    reads the setup failures afterwards and names the cause. Executing the
    fixtures for real would mean `--setup-only`, i.e. a browser and a login per
    test — the price of a full run for a check that is supposed to be free.

    Names are taken from their own errors, not guessed from `requirements.txt`: the
    package name, the module name and the fixture name are far from always the same.
    """
    cfg = cfg or VisTestConfig.load()
    env = _build_env(project, cfg, env_overrides)
    exe = exe or choose_interpreter(project, log=log)

    out: dict = {
        "interpreter": exe,
        "own_interpreter": exe == sys.executable,
        "pytest": False,
        "collected": 0,
        "missing": [],            # missing modules
        "missing_fixtures": [],   # missing fixtures
        "plugins": [],            # which packages will provide them
        "ok": False,
        "output": [],
        "hint": "",
    }

    if not project.uses_pytest():
        # A foreign command is not collected: how it discovers its tests is its
        # own business, and there is no `--setup-plan` for `npx cypress run`.
        # What can be checked is that there is something to start at all — and
        # that is worth checking, because a typo in the command otherwise
        # surfaces as an empty run with no explanation.
        return _preflight_command(project, out, log=log)

    probe = subprocess.run(
        [exe, "-c", "import pytest, sys; print(pytest.__version__)"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    out["pytest"] = probe.returncode == 0
    if not out["pytest"]:
        out["hint"] = (
            f"The interpreter {exe} has no pytest. Remove the interpreter in the "
            "project settings — the tests run with the VisTest environment, which "
            "already has pytest and browsers. If this is the VisTest environment, "
            "run: python run.py setup")
        log(out["hint"])
        return out

    # `--continue-on-collection-errors` is the difference between learning about
    # one missing module and learning about all of them. Without it pytest stops
    # at the first broken import, so «install the missing libraries» reports a
    # single package, installs it, and the next attempt finds the next one — the
    # person presses the button four times in a row for what is one answer.
    cmd = [exe, "-m", "pytest", "--setup-plan",
           "--continue-on-collection-errors"]
    if project.tests:
        cmd.append(project.tests)
    cmd += list(project.pytest_args or [])

    log("$ " + " ".join(cmd))
    proc = subprocess.run(cmd, cwd=str(project.root_path), env=env,
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    text = (proc.stdout or "") + (proc.stderr or "")
    out["output"] = text.splitlines()[-200:]
    out["missing"] = sorted({m.split(".")[0] for m in _MISSING_RE.findall(text)})
    out["missing_fixtures"] = sorted(set(_FIXTURE_RE.findall(text)))
    out["plugins"] = sorted({
        _FIXTURE_TO_PACKAGE[f] for f in out["missing_fixtures"]
        if f in _FIXTURE_TO_PACKAGE
    })

    # «collected 33 items / 22 deselected / 11 selected» — with a marker in
    # `pytest_args` the interesting number is the last one. Reporting the first
    # one promises thirty-three tests and then eleven arrive, and the difference
    # gets read as tests silently disappearing.
    m = re.search(r"(\d+)\s+selected", text) \
        or re.search(r"collected (\d+) item", text) \
        or re.search(r"(\d+)\s+tests? collected", text)
    if m:
        out["collected"] = int(m.group(1))

    files = [p.name for p in project.requirements_files()]
    where = (f"The project has {', '.join(files)} — install with the "
             "«Install missing libraries» button or the command "
             f"`vistest project deps {project.key}`."
             if files else
             f"Install with the command `vistest project deps {project.key}`.")

    if out["missing"]:
        out["hint"] = "Missing modules: " + ", ".join(out["missing"]) + ". " + where
    elif out["plugins"]:
        out["hint"] = (
            "Missing pytest plugins: " + ", ".join(out["plugins"])
            + f" (fixtures did not resolve: {', '.join(out['missing_fixtures'])}). "
            + where)
    elif out["missing_fixtures"]:
        out["hint"] = (
            "Fixtures did not resolve: " + ", ".join(out["missing_fixtures"])
            + ". Which plugin provides them is not known automatically — look at "
              "the project requirements.txt and install the needed package.")
    elif proc.returncode != 0:
        out["hint"] = (
            "Collection finished with an error. Most often this is their conftest: "
            "look at the output — it is shown as-is.")
    elif out["collected"] == 0:
        # Collection succeeded and found nothing. Starting the run anyway means
        # «total 0, failed 0» and a hunt for a cause that is already known here.
        args = " ".join(project.pytest_args or []) or "—"
        out["hint"] = (
            "Not a single test was collected. Check the tests path "
            f"({project.tests or project.root}) and the pytest arguments ({args}): "
            "a marker that matches nothing deselects the whole suite.")
    else:
        out["ok"] = True

    if out["hint"]:
        log(out["hint"])
    else:
        log(f"tests collected: {out['collected']}")
    return out


# The module in an import error and the package on PyPI are different things, and
# they are far from always the same. The table covers cases where the discrepancy
# is not obvious; the rest is derived by name normalization.
def _preflight_command(project: Project, out: dict, *, log) -> dict:
    """A pre-run check for a project started by its own command."""
    command = list(project.command or [])
    out["command"] = command
    out["runner"] = "command"
    if not command:
        out["hint"] = ("The run command is not specified. Set it in the project "
                       "settings: for example `npx playwright test`.")
        log(out["hint"])
        return out

    exe = command[0]
    found = shutil.which(exe) or shutil.which(exe, path=str(project.root_path))
    if not found and not (project.root_path / exe).exists():
        out["hint"] = (
            f"`{exe}` not found — neither in PATH nor in the project root. The "
            "command runs inside the VisTest container, so the tool has to be "
            "available there. Two ways out, and the second one is usually the "
            "right one: build the image with the runtime "
            "(`docker/Dockerfile.node`) and mount the project together with "
            "its `node_modules`; or do not run their suite here at all — let "
            "their CI run it and hand us the result: "
            f"`vistest project ingest {project.key} --dir <folder>`.")
        log(out["hint"])
        return out

    out["ok"] = True
    log(f"run command: {' '.join(command)}")
    log("collection is not checked: how a foreign runner finds its tests is its "
        "own business. The result is judged by the PNG pairs after the run.")
    return out


_MODULE_TO_PACKAGE = {
    "dotenv": "python-dotenv",
    "yaml": "PyYAML",
    "psycopg2": "psycopg2-binary",
    "allure": "allure-pytest",
    "allure_commons": "allure-python-commons",
    "cv2": "opencv-python",
    "PIL": "Pillow",
    "skimage": "scikit-image",
    "factory": "factory_boy",
    "faker": "Faker",
    "xdist": "pytest-xdist",
    "slugify": "python-slugify",
    "dateutil": "python-dateutil",
    "bs4": "beautifulsoup4",
    "jose": "python-jose",
    "attr": "attrs",
    "OpenSSL": "pyOpenSSL",
    "jwt": "PyJWT",
}


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def parse_requirements(files: list[Path]) -> dict[str, str]:
    """Dependency files → {normalized name: requirement string}."""
    out: dict[str, str] = {}
    for f in files:
        try:
            text = f.read_text("utf-8", errors="replace")
        except OSError:
            continue
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line or line.startswith("-"):
                continue
            # maxsplit only on the name: since 3.13 the positional argument is deprecated.
            name = re.split(r"[<>=!~\[; ]", line, maxsplit=1)[0].strip()
            if name:
                out[_normalize(name)] = line
    return out


def packages_for(modules: list[str], requirements: dict[str, str]) -> list[str]:
    """Missing modules → requirement strings that need to be installed.

    The version is taken from their `requirements.txt` if the package is pinned
    there: the team may have reasons for a specific pin, and silently installing a
    different version would be wrong.
    """
    out: list[str] = []
    for module in modules:
        package = _MODULE_TO_PACKAGE.get(module, module)
        key = _normalize(package)
        spec = requirements.get(key)
        if spec is None:
            # The package might be named differently in requirements — try the module name.
            spec = requirements.get(_normalize(module))
        out.append(spec or package)
    # Duplicates are possible: two modules from one package.
    seen, unique = set(), []
    for spec in out:
        if spec not in seen:
            seen.add(spec)
            unique.append(spec)
    return unique


def _pip_install(spec: str, *, log, allow_build: bool) -> tuple[bool, str]:
    """Install a single package. Returns (success, short reason)."""
    cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check"]
    if not allow_build:
        # Without a wheel pip goes off to compile a C extension and dumps a hundred
        # lines of compiler output. For a person who just pressed a button, this is
        # useless noise: it is more honest to say «no wheel» in a single line.
        cmd.append("--only-binary=:all:")
    cmd.append(spec)

    log("$ " + " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode == 0:
        for line in (proc.stdout or "").splitlines():
            if line.startswith(("Successfully installed", "Requirement already")):
                log("  " + line.strip())
        return True, ""

    text = (proc.stdout or "") + (proc.stderr or "")
    if "no matching distribution" in text.lower() \
            or "could not find a version" in text.lower():
        return False, "no suitable version for this Python"
    if "only-binary" in text.lower() or "no wheels" in text.lower():
        return False, "no prebuilt wheel for this Python"
    tail = [ln for ln in text.splitlines() if ln.strip()][-1:]
    return False, (tail[0][:160] if tail else "installation error")


def install_dependencies(project: Project, *, log=print,
                         should_stop=lambda: False) -> int:
    """Поставить зависимости ЧУЖОГО набора — тем, чем их ставят у них.

    Кнопка «Install dependencies» всегда ставила pip-пакеты в окружение
    VisTest. Для питоновского набора это верно и остаётся как было: он и
    гоняется нашим интерпретатором. Для Node или JVM это не частичный ответ, а
    неправильный: pip не поставит `@playwright/test`, а сообщение об успехе
    после этого — прямая ложь.

    Команда берётся из профиля по признаку в репозитории: локфайл говорит,
    каким менеджером собран проект, и это единственный надёжный признак.
    Не знаем — говорим, что не знаем; молча ничего не сделать хуже.
    """
    if project.uses_pytest():
        return install_requirements(project, log=log)

    profile = project.profile()
    command = profile.install_command(project.root_path)
    if not command:
        log(f"The «{profile.id}» profile does not know how dependencies are "
            f"installed in this project, and guessing would be worse than "
            f"saying so. Install them the way the team does — in "
            f"{project.root_path} — or connect the suite through ready "
            f"artifacts: `vistest project ingest {project.key}`.")
        return 1

    exe = shutil.which(command[0])
    if not exe and not (project.root_path / command[0]).exists():
        log(f"`{command[0]}` not found. The command runs inside the VisTest "
            "container, so the tool has to be available there: see "
            "`docker/Dockerfile.node` for a Node stack.")
        return 1

    log("$ " + " ".join(command))
    env = {**os.environ, **_expand(project.env or {}, dict(os.environ))}
    code, _ = _spawn(command, project.root_path, env, log=log,
                     should_stop=should_stop)
    log("dependencies installed" if code == 0
        else f"installation finished with code {code}")
    return code


def install_requirements(project: Project, *, log=print,
                         only: list[str] | None = None,
                         packages: list[str] | None = None,
                         all_requirements: bool = False) -> int:
    """Install the project dependencies into the VisTest environment.

    By default **only what is missing** is installed, not the whole
    `requirements.txt`. The reason is simple: the dependency file describes the
    entire project, but we need the visual tests. Pulling in a database driver for
    their sake that does not even build under the current Python means failing the
    install on a package that is never used in these tests.

    Each package is installed separately, and the failure of one does not cancel
    the rest: a list of what is missing at the end is more honest than «nothing was
    installed».
    """
    requirements = parse_requirements(project.requirements_files())

    if all_requirements:
        specs = list(requirements.values())
    elif only or packages:
        specs = packages_for(only or [], requirements)
        # Plugins come not from an import error but from an unresolved fixture, so
        # they already have a ready package name — we still take the version from
        # their file if it is pinned there.
        for name in packages or []:
            spec = requirements.get(_normalize(name), name)
            if spec not in specs:
                specs.append(spec)
    else:
        log("Not specified what to install: first «Check», then «Install missing "
            "libraries» — exactly what is missing will be installed.")
        return 0

    if not specs:
        log("Nothing to install.")
        return 0

    log(f"installing into the VisTest environment ({sys.executable}): {len(specs)} package(s)")
    failed: list[tuple[str, str]] = []
    for spec in specs:
        ok, reason = _pip_install(spec, log=log, allow_build=False)
        if not ok and re.search(r"[=<>~]", spec):
            # The pin may simply be old: the pinned version has no wheel for a fresh
            # Python yet, while the current one already does.
            loose = re.split(r"[<>=!~ ]", spec, maxsplit=1)[0]
            log(f"  {spec}: {reason}. Trying without the version pin…")
            ok, reason = _pip_install(loose, log=log, allow_build=False)
        if not ok:
            failed.append((spec, reason))
            log(f"  SKIPPED {spec}: {reason}")

    if not failed:
        log("Done. Check again with the «Check» button.")
        return 0

    log("")
    log(f"Not installed: {len(failed)} of {len(specs)}")
    for spec, reason in failed:
        log(f"  {spec} — {reason}")
    log("")
    log("What to do about it. If the package is not needed by the visual tests (a "
        "database driver, a data generator) — its absence does not hinder anything: "
        "press «Check» and see whether the tests collect. If it is needed — it "
        "usually helps to remove the version pin in requirements.txt or to build the "
        "VisTest environment on the same Python version as the project.")
    return 1
