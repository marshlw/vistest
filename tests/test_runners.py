# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Проект, который запускается не через pytest.

Режим разбора готовых PNG никогда не требовал Python: он сопоставляет пары
«эталон / факт», оставшиеся на диске, и ему всё равно, кто их нарисовал —
Playwright на TypeScript, Cypress, Selenium на Java. Единственным питоновским
местом на этом пути была захардкоженная команда `python -m pytest`.
"""

from __future__ import annotations

import sys

import pytest

from vistest.config import VisTestConfig
from vistest.external import _build_command, preflight
from vistest.projects import Adapter, Project


def _project(tmp_path, **kw) -> Project:
    root = tmp_path / "their-repo"
    root.mkdir(exist_ok=True)
    return Project(key="t", root=str(root), **kw)


# --------------------------------------------------------------------------- #
#  Сборка команды
# --------------------------------------------------------------------------- #
def test_pytest_stays_the_default(tmp_path):
    p = _project(tmp_path, tests="screens", pytest_args=["-m", "screenshot"])

    cmd = _build_command(p, "observe", None, exe="/usr/bin/python")

    assert cmd == ["/usr/bin/python", "-m", "pytest", "screens",
                   "-m", "screenshot"]


def test_adapter_plugin_is_attached_only_for_pytest(tmp_path):
    p = _project(tmp_path, adapter=Adapter(target="pages.Base.check"))

    assert "-p" in _build_command(p, "adapter", None, exe="python")


def test_own_command_is_run_as_written(tmp_path):
    p = _project(tmp_path, runner="command",
                 command=["npx", "playwright", "test"],
                 pytest_args=["-m", "screenshot"])

    cmd = _build_command(p, "observe", None, exe="/usr/bin/python")

    assert cmd == ["npx", "playwright", "test"]
    assert "pytest" not in cmd, "их аргументы pytest сюда не примешиваются"


def test_extra_arguments_reach_a_foreign_command(tmp_path):
    p = _project(tmp_path, runner="command", command=["npx", "cypress", "run"])

    assert _build_command(p, "observe", ["--spec", "a.cy.ts"], exe=None) == \
        ["npx", "cypress", "run", "--spec", "a.cy.ts"]


# --------------------------------------------------------------------------- #
#  Режим
# --------------------------------------------------------------------------- #
def test_a_foreign_command_cannot_be_intercepted(tmp_path):
    """Адаптер — плагин pytest и живёт в их процессе. Вне Python его нет.

    Честнее сказать это режимом, чем пообещать DOM-атрибуцию, которой не будет.
    """
    p = _project(tmp_path, runner="command", command=["npx", "playwright", "test"],
                 adapter=Adapter(target="pages.Base.check"))

    assert p.mode() == "observe"
    assert not p.uses_pytest()


# --------------------------------------------------------------------------- #
#  Проверка перед прогоном
# --------------------------------------------------------------------------- #
def test_missing_command_is_reported(tmp_path):
    p = _project(tmp_path, runner="command", command=[])

    assert "runner=command, but the command itself is not specified" in p.validate()


def test_unknown_runner_is_reported(tmp_path):
    p = _project(tmp_path, runner="gradle", command=["gradle", "test"])

    assert any("unknown runner" in x for x in p.validate())


def test_preflight_does_not_look_for_pytest(tmp_path, monkeypatch):
    """`--setup-plan` для `npx cypress run` не существует и не нужен."""
    monkeypatch.chdir(tmp_path)
    p = _project(tmp_path, runner="command", command=[sys.executable, "-c", "pass"])

    out = preflight(p, cfg=VisTestConfig.load(), log=lambda _t: None)

    assert out["ok"] is True
    assert out["runner"] == "command"
    assert out["pytest"] is False, "мы про их pytest ничего не спрашивали"


def test_preflight_says_when_the_tool_is_absent(tmp_path, monkeypatch):
    """Опечатка в команде иначе выглядит как пустой прогон без объяснений."""
    monkeypatch.chdir(tmp_path)
    p = _project(tmp_path, runner="command", command=["npx-that-is-not-here", "test"])

    out = preflight(p, cfg=VisTestConfig.load(), log=lambda _t: None)

    assert out["ok"] is False
    assert "npx-that-is-not-here" in out["hint"]


def test_runner_survives_the_registry(tmp_path, monkeypatch):
    pytest.importorskip("yaml")
    monkeypatch.chdir(tmp_path)

    from vistest.projects import ProjectRegistry

    reg = ProjectRegistry(VisTestConfig.load())
    reg.save(_project(tmp_path, runner="command",
                      command=["npx", "playwright", "test"]))

    back = reg.get("t")
    assert back.runner == "command"
    assert back.command == ["npx", "playwright", "test"]
    assert back.mode() == "observe"


# --------------------------------------------------------------------------- #
#  Зависимости чужого набора
# --------------------------------------------------------------------------- #
def test_dependencies_are_installed_by_their_own_manager(tmp_path, monkeypatch):
    """Кнопка всегда ставила pip-пакеты в окружение VisTest.

    Для питоновского набора это верно: он этим окружением и гоняется. Для Node
    это не частичный ответ, а неправильный — pip не поставит
    `@playwright/test`, а сообщение об успехе после этого прямая ложь.
    """
    from vistest.external import install_dependencies
    from vistest.suites import forget

    forget()
    p = _project(tmp_path, runner="command", command=["npx", "playwright", "test"])
    (p.root_path / "playwright.config.ts").write_text("export default {};\n")
    (p.root_path / "yarn.lock").write_text("")

    seen = {}
    monkeypatch.setattr("vistest.external._spawn",
                        lambda cmd, cwd, env, **kw: seen.update(
                            cmd=cmd, cwd=cwd) or (0, []))
    monkeypatch.setattr("vistest.external.shutil.which", lambda _x: "/usr/bin/yarn")
    try:
        assert install_dependencies(p, log=lambda *_a: None) == 0
    finally:
        forget()

    assert seen["cmd"] == ["yarn", "install", "--frozen-lockfile"], \
        "локфайл — единственный надёжный признак менеджера"
    assert seen["cwd"] == p.root_path, "ставим в ИХ корне, а не в нашем"


def test_an_unknown_stack_says_so_instead_of_pretending(tmp_path):
    """Молча ничего не сделать — хуже, чем сказать «не знаю как»."""
    from vistest.external import install_dependencies
    from vistest.suites import forget

    forget()
    p = _project(tmp_path, runner="command", command=["./run.sh"])
    said = []
    try:
        code = install_dependencies(p, log=said.append)
    finally:
        forget()

    assert code == 1
    assert any("does not know how" in line for line in said), said
    assert any("ingest" in line for line in said), "назван путь, который работает"


def test_a_pytest_project_keeps_the_old_behaviour(tmp_path, monkeypatch):
    """Питоновский набор гоняется нашим окружением — pip там и нужен."""
    from vistest import external

    p = _project(tmp_path)
    called = []
    monkeypatch.setattr(external, "install_requirements",
                        lambda project, **kw: called.append(project) or 0)

    assert external.install_dependencies(p, log=lambda *_a: None) == 0
    assert called == [p]
