/* VisTest - self-hosted visual regression testing.
 * Copyright (C) 2026 Kirill Kulagin
 * SPDX-License-Identifier: AGPL-3.0-or-later
 *
 * This file is part of VisTest. See LICENSE for the full terms and NOTICE for
 * the trademark and commercial-licensing terms. Removing this header does not
 * remove those obligations.
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
