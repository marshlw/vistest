# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The tools we know by sight.

Each entry is a statement about somebody else's naming convention, so each one
is only as good as the convention is real. Where a tool has a documented layout
— Playwright, Cypress, jest-image-snapshot, BackstopJS — the patterns are
precise. Where an ecosystem has no dominant convention — and the JVM and .NET
genuinely do not, every team wires its own paths around `image-comparison`,
AShot or Shutterbug — the profile says so, ships the widest reasonable set of
patterns, and expects the project to correct it. That is honest, and it is why
`naming` overrides exist at all.

Order in `PROFILES` is detection precedence: the most specific marker first. A
repository can hold `playwright.config.ts` **and** `package.json` **and** a
`pom.xml`, and the answer must not depend on dictionary order.
"""

from __future__ import annotations

from .base import SuiteProfile

#  Suffix and prefix spellings of the same three words. Used by the profiles
#  that have no convention of their own to enforce — better to recognise a
#  layout we did not predict than to insist on one nobody uses.
_ANY_ACTUAL = (
    r"(?P<name>.+)[._-]actual\.png",
    r"(?P<name>.+)[._-]current\.png",
    r"(?P<name>.+)[._-]received\.png",
    r"(?P<name>.+)[._-]result\.png",
    r"actual[._-](?P<name>.+)\.png",
    r"current[._-](?P<name>.+)\.png",
    r"received[._-](?P<name>.+)\.png",
)
#  `approved` и `verified` здесь не для полноты списка.
#
#  ApprovalTests (Java, .NET, JS) пишет пару `<имя>.approved.png` /
#  `<имя>.received.png`, Verify (.NET) — `<имя>.verified.png` /
#  `<имя>.received.png`. Оба «received» мы узнавали и раньше, а вот эталон —
#  нет: пара разваливалась ровно посередине, факт находился, сравнивать было
#  не с чем. Для .NET это не краевой случай, а основной способ.
_ANY_EXPECTED = (
    r"(?P<name>.+)[._-]expected\.png",
    r"(?P<name>.+)[._-]baseline\.png",
    r"(?P<name>.+)[._-]reference\.png",
    r"(?P<name>.+)[._-]approved\.png",
    r"(?P<name>.+)[._-]verified\.png",
    r"(?P<name>.+)[._-]golden\.png",
    r"expected[._-](?P<name>.+)\.png",
    r"baseline[._-](?P<name>.+)\.png",
)
_ANY_DIFF = (
    r"(?P<name>.+)[._-]diff\.png",
    r"(?P<name>.+)[._-]difference\.png",
    r"diff[._-](?P<name>.+)\.png",
    r"combined[._-](?P<name>.+)\.png",
    r"failed_diff_(?P<name>.+)\.png",
)

#  Playwright bakes the browser and the operating system into the baseline file
#  name. Both are ours to decide — the browser is a run property and the
#  platform is part of the storage key — so keeping them in the snapshot name
#  would turn one snapshot into three that never meet.
#  Установка зависимостей Node. Порядок — от точного к общему: локфайл
#  говорит, каким менеджером собран проект, и это единственный надёжный
#  признак. `npm ci` без локфайла падает, поэтому он и стоит под условием.
_NODE_INSTALL = (
    ("package-lock.json", ("npm", "ci")),
    ("yarn.lock", ("yarn", "install", "--frozen-lockfile")),
    ("pnpm-lock.yaml", ("pnpm", "install", "--frozen-lockfile")),
    ("package.json", ("npm", "install")),
)

_PW_STRIP = (
    r"-(?:chromium|firefox|webkit|chrome|msedge)"
    r"(?:-(?:darwin|linux|win32))?$",
    r"-(?:darwin|linux|win32)$",
)


PYTEST = SuiteProfile(
    id="pytest",
    title="pytest (Python) — comparison inside the test",
    runner="pytest",
    intercept=True,
    search_tests=True,
    baseline_dirs=("snapshots", "snapshots_ci", "baseline", "baselines",
                   "screenshots", "__screenshots__", "expected", "reference"),
    actual=(r"actual[_-](?P<name>.+)\.png",
            r"current[_-](?P<name>.+)\.png",
            r"received[_-](?P<name>.+)\.png",
            r"(?P<name>.+)[._-]actual\.png"),
    diff=(r"diff[_-](?P<name>.+)\.png",
          r"combined[_-](?P<name>.+)\.png",
          r"(?P<name>.+)[._-]diff\.png"),
    detect=("conftest.py", "pytest.ini", "tox.ini", "pyproject.toml"),
    note="The only mode in which the adapter can be attached: it is a pytest "
         "plugin and lives inside their process, so the DOM attribution and "
         "our own capture are available.",
)

PLAYWRIGHT = SuiteProfile(
    id="playwright",
    title="Playwright Test (TypeScript / JavaScript)",
    command=("npx", "playwright", "test"),
    #  Playwright has no `--browser`; the equivalent is a project from the
    #  config. Sending `--browser` here is what used to break the run.
    browser_arg=("--project={browser}",),
    search_dirs=("test-results",),
    #  `<spec>-snapshots` is the default; `__screenshots__` is what teams move
    #  it to with `snapshotPathTemplate`. Both are folder-name patterns.
    baseline_dirs=("*-snapshots", "__screenshots__", "snapshots"),
    actual=(r"(?P<name>.+)-actual\.png",),
    expected=(r"(?P<name>.+)-expected\.png",),
    diff=(r"(?P<name>.+)-diff\.png",),
    strip=_PW_STRIP,
    #  Позиционный аргумент, а не флаг: `npx playwright test tests/a.spec.ts`
    #  и `… -g "имя"` — оба сужают прогон, и путь спеки надёжнее.
    only_arg=("{only}",),
    install=_NODE_INSTALL,
    test_files=("*.spec.ts", "*.spec.js", "*.spec.mjs", "*.spec.tsx",
                "*.test.ts", "*.test.js"),
    test_decl=r"^\s*(?:test|it)(?:\.\w+)*\s*\(",
    detect=("playwright.config.ts", "playwright.config.js",
            "playwright.config.mjs", "playwright.config.cjs",
            "playwright.config.mts"),
    note="`toHaveScreenshot()` writes the triple actual/expected/diff into "
         "test-results, and only for a failed comparison. A snapshot that "
         "passed leaves nothing behind — so a green Playwright run is a run "
         "with nothing for us to look at, and that is a property of the tool, "
         "not a fault of the connection.",
)

CYPRESS = SuiteProfile(
    id="cypress",
    title="Cypress (cypress-image-snapshot / plugin-snapshots)",
    command=("npx", "cypress", "run"),
    browser_arg=("--browser", "{browser}"),
    search_dirs=("cypress/snapshots", "cypress/screenshots"),
    baseline_dirs=("__image_snapshots__", "base", "snapshots"),
    actual=(r"(?P<name>.+)[.-]actual\.png",
            r"(?P<name>.+)-received\.png"),
    expected=(r"(?P<name>.+)[.-]expected\.png",
              r"(?P<name>.+)-snap\.png"),
    diff=(r"(?P<name>.+)[.-]diff\.png",),
    only_arg=("--spec", "{only}"),
    install=_NODE_INSTALL,
    test_files=("*.cy.ts", "*.cy.js", "*.cy.jsx", "*.cy.tsx",
                "*.spec.ts", "*.spec.js"),
    test_decl=r"^\s*(?:it|specify)(?:\.\w+)*\s*\(",
    detect=("cypress.config.ts", "cypress.config.js", "cypress.config.mjs",
            "cypress.json"),
)

JEST_IMAGE_SNAPSHOT = SuiteProfile(
    id="jest-image-snapshot",
    title="jest-image-snapshot (Jest / Vitest + Puppeteer)",
    command=("npx", "jest"),
    search_tests=True,
    baseline_dirs=("__image_snapshots__",),
    actual=(r"(?P<name>.+)-received\.png",),
    expected=(r"(?P<name>.+)-snap\.png",),
    diff=(r"(?P<name>.+)-diff\.png",),
    only_arg=("{only}",),
    install=_NODE_INSTALL,
    test_files=("*.test.ts", "*.test.js", "*.test.tsx", "*.test.jsx",
                "*.spec.ts", "*.spec.js"),
    test_decl=r"^\s*(?:test|it)(?:\.\w+)*\s*\(",
    detect=("**/__image_snapshots__", "jest.config.js", "jest.config.ts"),
    note="The received picture is only written with "
         "`storeReceivedOnFailure: true`. Without that option a failure "
         "leaves a diff composite and nothing to compare — turn it on in "
         "their jest config, there is no way around it from our side.",
)

BACKSTOP = SuiteProfile(
    id="backstop",
    title="BackstopJS",
    command=("npx", "backstop", "test"),
    search_dirs=("backstop_data/bitmaps_test",),
    baseline_dirs=("backstop_data/bitmaps_reference",),
    #  Everything in bitmaps_test is a fresh capture; the only other thing that
    #  lands there is the diff, and it is prefixed.
    actual=(r"(?!failed_diff_)(?P<name>.+)\.png",),
    diff=(r"failed_diff_(?P<name>.+)\.png",),
    only_arg=("--filter={only}",),
    install=_NODE_INSTALL,
    detect=("backstop.json",),
)

WEBDRIVERIO = SuiteProfile(
    id="webdriverio",
    title="WebdriverIO (wdio-image-comparison-service)",
    command=("npx", "wdio", "run", "wdio.conf.js"),
    search_dirs=("tests/actual", ".tmp/actual", "screenshots/actual"),
    baseline_dirs=("tests/baseline", ".tmp/baseline", "screenshots/baseline"),
    actual=_ANY_ACTUAL + (r"(?P<name>.+)\.png",),
    expected=_ANY_EXPECTED,
    diff=_ANY_DIFF,
    only_arg=("--spec", "{only}"),
    install=_NODE_INSTALL,
    test_files=("*.spec.ts", "*.spec.js", "*.e2e.ts", "*.e2e.js"),
    test_decl=r"^\s*(?:it|test)(?:\.\w+)*\s*\(",
    detect=("wdio.conf.js", "wdio.conf.ts"),
    note="The service keeps actual, baseline and diff in three parallel "
         "folders, so the folders decide the kind and the file name is the "
         "snapshot name.",
)

MAVEN = SuiteProfile(
    id="maven",
    title="JVM · Maven (Selenium / Selenide)",
    command=("mvn", "-B", "test"),
    search_dirs=("target", "build"),
    baseline_dirs=("src/test/resources/baseline",
                   "src/test/resources/screenshots", "baseline"),
    actual=_ANY_ACTUAL,
    expected=_ANY_EXPECTED,
    diff=_ANY_DIFF,
    only_arg=("-Dtest={only}",),
    install=(("pom.xml", ("mvn", "-B", "-q", "dependency:go-offline")),),
    test_files=("*Test.java", "*Tests.java", "*IT.java", "*Test.kt"),
    test_decl=r"^\s*@Test\b",
    detect=("pom.xml",),
    note="The JVM has no dominant visual-testing convention: image-comparison, "
         "AShot and Shutterbug all leave the paths to the team. Two shapes are "
         "recognised for certain — the ApprovalTests pair "
         "«<name>.approved.png / <name>.received.png», and the plain "
         "«<name>-actual.png» beside a baseline folder. Anything else is a "
         "common spelling rather than a standard: check it against the project "
         "and correct it in «Snapshot naming».",
)

GRADLE = SuiteProfile(
    id="gradle",
    title="JVM · Gradle (Selenium / Selenide)",
    command=("./gradlew", "test"),
    search_dirs=("build", "build/reports"),
    baseline_dirs=("src/test/resources/baseline",
                   "src/test/resources/screenshots", "baseline"),
    actual=_ANY_ACTUAL,
    expected=_ANY_EXPECTED,
    diff=_ANY_DIFF,
    only_arg=("--tests", "{only}"),
    install=(("gradlew", ("./gradlew", "--quiet", "dependencies")),
             ("build.gradle", ("gradle", "--quiet", "dependencies")),
             ("build.gradle.kts", ("gradle", "--quiet", "dependencies"))),
    test_files=("*Test.java", "*Tests.java", "*IT.java", "*Test.kt"),
    test_decl=r"^\s*@Test\b",
    detect=("build.gradle", "build.gradle.kts"),
    note=MAVEN.note,
)

DOTNET = SuiteProfile(
    id="dotnet",
    title=".NET (dotnet test)",
    command=("dotnet", "test"),
    search_dirs=("TestResults", "bin"),
    baseline_dirs=("Baseline", "Snapshots", "__snapshots__"),
    actual=_ANY_ACTUAL,
    expected=_ANY_EXPECTED,
    diff=_ANY_DIFF,
    only_arg=("--filter", "{only}"),
    install=(("", ("dotnet", "restore")),),
    test_files=("*Test.cs", "*Tests.cs"),
    test_decl=r"^\s*\[(?:Fact|Test|TestMethod|Theory)\b",
    detect=("*.sln", "**/*.csproj"),
    note="Verify and ApprovalTests are the two snapshot libraries in wide use "
         "here, and both are recognised: «<name>.verified.png» or "
         "«<name>.approved.png» beside «<name>.received.png». Everything else "
         "is a guess — correct it in «Snapshot naming».",
)

GENERIC = SuiteProfile(
    id="generic",
    title="Any other tool — the widest set of naming rules",
    search_tests=True,
    baseline_dirs=("snapshots", "baseline", "baselines", "screenshots",
                   "expected", "reference", "__screenshots__",
                   "__image_snapshots__"),
    actual=_ANY_ACTUAL,
    expected=_ANY_EXPECTED,
    diff=_ANY_DIFF,
    note="Nothing was recognised, so the rules are guesses. If the run finds "
         "no pairs, the layout is described in «Snapshot naming» — one regular "
         "expression with a (?P<name>…) group per kind.",
)


#  Detection precedence, most specific marker first.
PROFILES: tuple[SuiteProfile, ...] = (
    BACKSTOP, PLAYWRIGHT, CYPRESS, WEBDRIVERIO, JEST_IMAGE_SNAPSHOT,
    PYTEST, MAVEN, GRADLE, DOTNET, GENERIC,
)

DEFAULT = PYTEST
FALLBACK = GENERIC
