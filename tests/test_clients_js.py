# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Клиент и репортёр на Node — проверяются тем же прогоном, что и всё.

Пакет живёт в этом же репозитории и ломается так же тихо, как всё остальное:
опечатка в имени вложения или потерянный заголовок с токеном не видны ни
глазами, ни линтером, а проявляются на чужом CI молчанием — «прогон прошёл,
в VisTest пусто».

Node может не стоять на машине, где гоняют только Python: тогда проверка
пропускается, а не краснеет. В CI он есть.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLIENTS = ROOT / "clients"


def _node() -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("нет node — проверка JS-клиента пропущена")
    return node


def _run(script: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([_node(), str(script), *args],
                          capture_output=True, text=True, timeout=120)


def test_the_client_and_the_reporter_parse(tmp_path):
    """Синтаксис — минимум, ниже которого говорить не о чем."""
    for name in ("vistest.mjs", "playwright-reporter.mjs", "cypress.mjs"):
        done = subprocess.run([_node(), "--check", str(CLIENTS / name)],
                              capture_output=True, text=True, timeout=60)
        assert done.returncode == 0, done.stderr


def test_the_reporter_sends_what_it_should():
    """Что именно репортёр считает результатом и в каком порядке отправляет."""
    done = _run(ROOT / "tests" / "clients" / "playwright_reporter.mjs",
                str(CLIENTS))

    assert done.returncode == 0, done.stdout + done.stderr
    assert "OK" in done.stdout


def test_the_cypress_plugin_reacts_to_the_right_screenshots():
    """`after:screenshot` срабатывает на каждый снимок — в том и смысл.

    У репортёра Playwright вложения появляются только при падении, то есть
    зелёный прогон не оставляет ничего. Здесь оставляет, и потому проверяется
    обратное: что снимок ПАДЕНИЯ, который Cypress делает сам, результатом не
    считается.
    """
    done = _run(ROOT / "tests" / "clients" / "cypress_plugin.mjs", str(CLIENTS))

    assert done.returncode == 0, done.stdout + done.stderr
    assert "OK" in done.stdout


def test_the_package_describes_both_entry_points():
    """`vistest-client/playwright-reporter` обязан существовать как экспорт.

    Путь из README, который не разрешается, — это README, проверенный
    глазами: `npm i` пройдёт, а импорт упадёт у первого же пользователя.
    """
    meta = json.loads((CLIENTS / "package.json").read_text("utf-8"))

    exports = meta["exports"]
    assert set(exports) == {".", "./playwright-reporter", "./cypress"}
    for target in exports.values():
        assert (CLIENTS / target).exists(), target
    for name in meta["files"]:
        assert (CLIENTS / name).exists(), name


def test_the_licence_travels_with_the_package():
    """Пакет уезжает отдельно от репозитория, а условия — те же."""
    meta = json.loads((CLIENTS / "package.json").read_text("utf-8"))

    assert meta["license"] == "AGPL-3.0-or-later"
    for name in ("vistest.mjs", "playwright-reporter.mjs", "cypress.mjs"):
        head = (CLIENTS / name).read_text("utf-8")[:600]
        assert "SPDX-License-Identifier: AGPL-3.0-or-later" in head
