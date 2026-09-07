# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""JVM и .NET на настоящих раскладках, а не на предположениях.

Профили `maven`, `gradle` и `dotnet` собирались из распространённых написаний,
потому что общепринятого соглашения там нет: каждая команда разводит пути
вокруг `image-comparison`, AShot или Shutterbug сама. Список догадок, который
никто не проверил, — это не поддержка, а обещание поддержки.

Здесь раскладки построены такими, какими их делают на практике, и разбор
прогоняется целиком — от определения инструмента до вердикта. Две из них
поймали настоящий пробел: ApprovalTests пишет пару `approved` / `received`,
Verify — `verified` / `received`, и «received» мы узнавали, а эталон нет. Пара
разваливалась ровно посередине: факт находился, сравнивать было не с чем.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from vistest import suites
from vistest.config import VisTestConfig
from vistest.external import ingest_project
from vistest.projects import BaselineDir, Project


def _png(path: Path, color=(30, 90, 200), blot=False) -> None:
    from vistest.capture.playwright_capture import _write_png

    image = np.zeros((80, 120, 3), np.uint8)
    image[:] = color
    if blot:
        image[10:50, 10:70] = (240, 40, 40)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_png(path, image)


@pytest.fixture(autouse=True)
def _clean_detection():
    suites.forget()
    yield
    suites.forget()


# --------------------------------------------------------------------------- #
#  Maven + image-comparison: эталоны в ресурсах, результат в target
# --------------------------------------------------------------------------- #
def test_a_maven_suite_with_a_resources_baseline(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "ui-tests"
    (root / "src" / "test" / "resources" / "baseline").mkdir(parents=True)
    (root / "pom.xml").write_text("<project/>\n")

    _png(root / "src/test/resources/baseline/login.png")
    _png(root / "target/screenshots/login-actual.png")
    _png(root / "target/screenshots/cart-actual.png", blot=True)
    _png(root / "src/test/resources/baseline/cart.png")

    project = Project(key="ui", root=str(root), runner="command",
                      command=["mvn", "-B", "test"],
                      baselines=[BaselineDir(dir="src/test/resources/baseline")])

    assert project.profile().id == "maven"
    run = ingest_project(project, cfg=VisTestConfig.load(), log=lambda *_a: None)

    verdicts = {r["name"]: r["verdict"] for r in run.results}
    assert verdicts == {"login.png": "pass", "cart.png": "fail"}


# --------------------------------------------------------------------------- #
#  ApprovalTests: пара approved / received в одном каталоге
# --------------------------------------------------------------------------- #
def test_approval_tests_pairs_are_understood(tmp_path, monkeypatch):
    """`approved` мы не узнавали, а `received` узнавали.

    То есть факт находился, эталон — нет, и прогон честно сообщал «нет
    эталона» по каждому снимку. Для ApprovalTests это основной способ, а не
    краевой случай.
    """
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "svc"
    (root / "src" / "test" / "java").mkdir(parents=True)
    (root / "pom.xml").write_text("<project/>\n")

    shots = root / "src/test/java"
    _png(shots / "LoginTest.checkout.approved.png")
    _png(shots / "LoginTest.checkout.received.png")

    project = Project(key="svc", root=str(root), runner="command",
                      command=["mvn", "-B", "test"],
                      search_dirs=["src/test/java"],
                      baselines=[BaselineDir(dir="src/test/java")])

    run = ingest_project(project, cfg=VisTestConfig.load(), log=lambda *_a: None)

    assert [r["name"] for r in run.results] == ["LoginTest.checkout.png"]
    assert run.results[0]["verdict"] == "pass"


# --------------------------------------------------------------------------- #
#  Gradle: то же, но дерево сборки другое
# --------------------------------------------------------------------------- #
def test_a_gradle_suite_is_recognised_and_read(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "web"
    (root / "build" / "reports" / "shots").mkdir(parents=True)
    (root / "build.gradle.kts").write_text("plugins { java }\n")
    (root / "baseline").mkdir()

    _png(root / "baseline/home.png")
    _png(root / "build/reports/shots/home-actual.png")

    project = Project(key="web", root=str(root), runner="command",
                      command=["./gradlew", "test"],
                      baselines=[BaselineDir(dir="baseline")])

    assert project.profile().id == "gradle"
    run = ingest_project(project, cfg=VisTestConfig.load(), log=lambda *_a: None)

    assert [(r["name"], r["verdict"]) for r in run.results] == [("home.png", "pass")]


# --------------------------------------------------------------------------- #
#  .NET + Verify: verified / received
# --------------------------------------------------------------------------- #
def test_a_dotnet_suite_with_verify_snapshots(tmp_path, monkeypatch):
    """Verify — основной способ снапшот-тестирования в .NET.

    Его пара называется `verified` / `received`, и без первого имени разбор
    находил факт и не находил эталон.
    """
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "Shop.Tests"
    (root / "Snapshots").mkdir(parents=True)
    (root / "Shop.Tests.csproj").write_text("<Project/>\n")

    _png(root / "Snapshots/CheckoutTests.Renders.verified.png")
    _png(root / "Snapshots/CheckoutTests.Renders.received.png", blot=True)

    project = Project(key="shop", root=str(root), runner="command",
                      command=["dotnet", "test"],
                      search_dirs=["Snapshots"],
                      baselines=[BaselineDir(dir="Snapshots")])

    assert project.profile().id == "dotnet"
    run = ingest_project(project, cfg=VisTestConfig.load(), log=lambda *_a: None)

    assert [r["name"] for r in run.results] == ["CheckoutTests.Renders.png"]
    assert run.results[0]["verdict"] == "fail"
    assert run.results[0]["baseline_source"] == "suite", \
        "эталон приехал из их же пары, а не из нашего хранилища"


# --------------------------------------------------------------------------- #
#  Прицел и установка — то, что у этих профилей спрашивают в интерфейсе
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("marker,command,expected_only,expected_install", [
    ("pom.xml", ["mvn", "-B", "test"], ["-Dtest=LoginTest"],
     ["mvn", "-B", "-q", "dependency:go-offline"]),
    ("build.gradle", ["gradle", "test"], ["--tests", "LoginTest"],
     ["gradle", "--quiet", "dependencies"]),
])
def test_the_jvm_profiles_know_their_own_flags(tmp_path, marker, command,
                                               expected_only, expected_install):
    from vistest.external import _build_command

    root = tmp_path / "repo"
    root.mkdir()
    (root / marker).write_text("x\n")
    project = Project(key="p", root=str(root), runner="command", command=command)

    built = _build_command(project, "observe", None, only="LoginTest")

    assert built == command + expected_only
    assert project.profile().install_command(root) == expected_install
