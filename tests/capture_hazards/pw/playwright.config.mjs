/* VisTest - visual regression testing for Python and Playwright.
 * Copyright 2026 Kirill Kulagin
 * SPDX-License-Identifier: Apache-2.0
 *
 * This file is part of VisTest, licensed under the Apache License, Version 2.0.
 * You may not use it except in compliance with the License. See LICENSE for
 * the terms and NOTICE for attribution and the trademark.
 */

/* The stand under Playwright: the Chromium the Python side launches
 * (HAZARD_CHROMIUM — the same build, so both photograph with one renderer),
 * 800x600 at 1x, and two launches: Playwright's headless default, which
 * passes --hide-scrollbars, and the same without that flag. Everything else
 * is Playwright's default, toHaveScreenshot() included. */

const viewport = { width: 800, height: 600 };
const executablePath = process.env.HAZARD_CHROMIUM || undefined;

export default {
  testDir: '.',
  testMatch: /hazards\.spec\.mjs/,
  snapshotPathTemplate: '{testDir}/snaps/{arg}{ext}',
  outputDir: './test-results',
  workers: Number(process.env.HAZARD_WORKERS || 1),
  fullyParallel: true,
  retries: 0,
  timeout: 60000,
  reporter: 'dot',
  projects: [
    { name: 'default', use: { viewport, launchOptions: { executablePath } } },
    { name: 'scrollbars',
      use: { viewport, launchOptions: { executablePath, ignoreDefaultArgs: ['--hide-scrollbars'] } } },
  ],
};
