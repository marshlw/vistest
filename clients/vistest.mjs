/* VisTest - self-hosted visual regression testing.
 * Copyright (C) 2026 Kirill Kulagin
 * SPDX-License-Identifier: AGPL-3.0-or-later
 *
 * This file is part of VisTest. See LICENSE for the full terms and NOTICE for
 * the trademark and commercial-licensing terms. Removing this header does not
 * remove those obligations.
 */

/**
 * VisTest — клиент для Node 18+. Без зависимостей (используется встроенный fetch).
 *
 * Подключает к движку VisTest любые уже написанные JS-тесты:
 * Playwright, Puppeteer, WebdriverIO, Cypress (через task), TestCafe.
 *
 *   import { VisTest } from './clients/vistest.mjs';
 *   const vt = new VisTest({ apiUrl: 'http://localhost:8420' });
 *
 *   // Playwright / Puppeteer
 *   await vt.checkPage(page, 'checkout.png');
 *
 *   // Любой другой источник — уже готовый буфер PNG
 *   await vt.check('checkout.png', pngBuffer);
 *
 *   // В конце прогона — одна запись в истории вместо россыпи проверок
 *   await vt.finish();
 *
 * Стабилизация страницы (заморозка анимаций, детерминированные Math.random и
 * Date, ожидание шрифтов и lazy-load, DOM-снепшот) выполняется тем же самым
 * скриптом, что и в Python-раннере: он забирается с `/api/stabilize.js`.
 * Благодаря этому снимки из JS-тестов сопоставимы с питоновскими.
 */

export class VisualMismatchError extends Error {
  constructor(result) {
    super(VisTest.formatResult(result));
    this.name = 'VisualMismatchError';
    this.result = result;
  }
}

export class VisTest {
  /**
   * @param {object} opts
   * @param {string} [opts.apiUrl]   адрес сервиса, по умолчанию VISTEST_API_URL
   * @param {string} [opts.project]
   * @param {string} [opts.platform] ключ платформы; по умолчанию решает сервер
   * @param {string} [opts.browser]
   * @param {string} [opts.runKey]   объединяет проверки в один прогон
   * @param {string} [opts.token]    токен приёма (X-VisTest-Token), по умолчанию VISTEST_TOKEN
   * @param {boolean}[opts.throwOnFail] бросать исключение при регрессе (по умолчанию да)
   * @param {number} [opts.stabilityShots] сколько кадров слать для детекта динамики
   * @param {object} [opts.options]  переопределение порогов
   */
  constructor(opts = {}) {
    this.apiUrl = (opts.apiUrl || process.env.VISTEST_API_URL || 'http://127.0.0.1:8420')
      .replace(/\/$/, '');
    this.project = opts.project || process.env.VISTEST_PROJECT || 'default';
    this.platform = opts.platform || process.env.VISTEST_PLATFORM || undefined;
    this.browser = opts.browser || 'chromium';
    this.runKey = opts.runKey || process.env.VISTEST_RUN_ID || undefined;
    // Токен приёма. Без него инсталляция с пользователями отвечает 401 на
    // каждый снимок: сессионной куки у раннера в CI нет и быть не может.
    this.token = opts.token || process.env.VISTEST_TOKEN
      || process.env.VISTEST_INGEST_TOKEN || '';
    this.throwOnFail = opts.throwOnFail !== false;
    this.stabilityShots = opts.stabilityShots ?? 3;
    this.stabilityDelayMs = opts.stabilityDelayMs ?? 120;
    this.preset = opts.preset;
    this.options = opts.options;
    this.results = [];
    this._stabilizeJs = null;
  }

  /* ------------------------------------------------------------------ */
  /** Заголовки запроса: токен приёма, если он задан. */
  headers() {
    return this.token ? { 'X-VisTest-Token': this.token } : {};
  }

  /**
   * Завершить прогон: собрать проверки в одну запись истории.
   *
   * До этого каждая проверка жила сама по себе. Вердикт возвращался, картинки
   * складывались на диск — но в списке прогонов не появлялось ничего, то есть
   * ни истории по снимку, ни очереди на ревью, ни ответа «этот билд сломал
   * три экрана». Прогон — отдельная сущность, и создать её может только тот,
   * кто знает, что проверки кончились: сам набор.
   *
   * Вызывать один раз в конце (globalTeardown у Playwright, after у Cypress).
   * Без `runKey` звать нечего: прогон нечем назвать.
   */
  async finish(extra = {}) {
    if (!this.runKey) {
      throw new Error('finish() требует runKey: им прогон и назван');
    }
    if (!this.results.length) return null;

    const first = this.results[0] || {};
    const s = this.summary();
    const body = {
      run_id: this.runKey,
      project: this.project,
      platform: this.platform || first.platform,
      browser: this.browser,
      created_at: extra.startedAt || new Date().toISOString(),
      ci_url: extra.ciUrl || process.env.CI_JOB_URL
        || process.env.BUILD_URL || undefined,
      git: extra.git || gitFromEnv(),
      totals: {
        total: s.total, failed: s.failed, new: s.created,
        passed: s.total - s.failed - s.created,
      },
      comparisons: this.results.map(r => ({
        name: r.name, verdict: r.verdict, platform: r.platform,
        browser: r.browser || this.browser, metrics: r.metrics || {},
        regions: r.regions || [], artifacts: r.artifacts || {},
        size: r.size, notes: r.notes || [],
      })),
    };

    const res = await fetch(`${this.apiUrl}/api/runs`, {
      method: 'POST',
      headers: { ...this.headers(), 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`VisTest API ${res.status}: ${await res.text()}`);
    return res.json();
  }

  /** Отправить готовый PNG (Buffer/Uint8Array/Blob). */
  async check(name, image, extra = {}) {
    const form = new FormData();
    form.append('name', name);
    form.append('image', toBlob(image), 'actual.png');
    for (const f of extra.frames || []) form.append('frames', toBlob(f), 'frame.png');
    if (extra.dom) {
      form.append('dom', new Blob([JSON.stringify(extra.dom)],
        { type: 'application/json' }), 'dom.json');
    }
    form.append('project', this.project);
    form.append('browser', extra.browser || this.browser);
    if (this.platform) form.append('platform', this.platform);
    if (this.runKey) form.append('run_key', this.runKey);
    if (extra.updateBaseline) form.append('update_baseline', 'true');
    if (this.preset) form.append('preset', this.preset);
    const opts = { ...(this.options || {}), ...(extra.options || {}) };
    if (Object.keys(opts).length) form.append('options', JSON.stringify(opts));

    const res = await fetch(`${this.apiUrl}/api/check`,
      { method: 'POST', body: form, headers: this.headers() });
    if (!res.ok) {
      throw new Error(`VisTest API ${res.status}: ${await res.text()}`);
    }
    const result = await res.json();
    this.results.push(result);

    if (result.verdict === 'fail' && this.throwOnFail) {
      throw new VisualMismatchError(result);
    }
    return result;
  }

  /**
   * Снять страницу Playwright/Puppeteer и проверить.
   * Делает всё то же, что питоновский раннер: стабилизирует, снимает N кадров,
   * забирает DOM-снепшот и отправляет всё это на сравнение.
   */
  async checkPage(page, name, opts = {}) {
    await this.stabilize(page, opts);

    const shot = () => (opts.selector
      ? page.locator
        ? page.locator(opts.selector).screenshot()
        : page.$(opts.selector).then(el => el.screenshot())
      : page.screenshot({ fullPage: opts.fullPage !== false }));

    const frames = [];
    const total = opts.stabilityShots ?? this.stabilityShots;
    for (let i = 0; i < Math.max(1, total); i++) {
      if (i) await sleep(this.stabilityDelayMs);
      frames.push(await shot());
    }

    let dom = null;
    if (opts.dom !== false && !opts.selector) {
      dom = await evaluate(page, '() => window.__vistest && window.__vistest.dom()');
    }

    return this.check(name, frames[0], {
      frames: frames.slice(1),
      dom: dom || undefined,
      ...opts,
    });
  }

  /** Инжектировать в страницу тот же скрипт стабилизации, что у Python-раннера. */
  async stabilize(page, opts = {}) {
    if (!this._stabilizeJs) {
      const r = await fetch(`${this.apiUrl}/api/stabilize.js`);
      if (!r.ok) throw new Error(`не удалось получить stabilize.js: ${r.status}`);
      this._stabilizeJs = await r.text();
    }
    await evaluate(page, this._stabilizeJs);
    await evaluate(page,
      `() => window.__vistest.settle({ scroll: ${opts.fullPage !== false} })`);
    await sleep(150);
  }

  /**
   * Инжектировать стабилизацию ДО загрузки страницы — так надёжнее:
   * анимации не успевают отыграть первые кадры, Math.random и Date
   * подменяются до того, как их вызовет приложение.
   * Вызывать один раз при создании контекста.
   */
  async installOnContext(context) {
    if (!this._stabilizeJs) {
      const r = await fetch(`${this.apiUrl}/api/stabilize.js`);
      this._stabilizeJs = await r.text();
    }
    if (context.addInitScript) await context.addInitScript(this._stabilizeJs);
    else if (context.evaluateOnNewDocument) await context.evaluateOnNewDocument(this._stabilizeJs);
  }

  /* ------------------------------------------------------------------ */
  static formatResult(r) {
    const m = r.metrics || {};
    const lines = [
      `Visual mismatch: ${r.name}`,
      `  SSIM=${num(m.ssim_global, 5)}  dE00 mean=${num(m.de_mean)} p95=${num(m.de_p95)}`,
      `  changed area=${num(m.changed_area_pct, 3)}%  max severity=${num(m.max_severity, 1)}`,
    ];
    for (const reg of (r.regions || []).slice(0, 10)) {
      const bits = [];
      if (reg.kind === 'moved') bits.push(`shift=(${reg.moved_dx},${reg.moved_dy})`);
      if (reg.selector) bits.push(reg.selector);
      if (reg.element_text) bits.push(`"${reg.element_text}"`);
      if (reg.caption) bits.push(reg.caption);
      lines.push(`    [${reg.kind}] sev=${num(reg.severity, 1)} ` +
        `@(${reg.x},${reg.y}) ${reg.w}x${reg.h}` + (bits.length ? '  ' + bits.join(' | ') : ''));
    }
    for (const n of r.notes || []) lines.push(`  note: ${n}`);
    if (r.artifacts && r.artifacts.boxes) {
      lines.push(`  разметка: ${r.artifacts.boxes}`);
    }
    return lines.join('\n');
  }

  summary() {
    const failed = this.results.filter(r => r.verdict === 'fail');
    return {
      total: this.results.length,
      failed: failed.length,
      created: this.results.filter(r => r.verdict === 'new_baseline').length,
      names: failed.map(r => r.name),
    };
  }
}

/* -------------------------------------------------------------------- */
/** Ветка и коммит из переменных CI. Пусто — тоже честный ответ. */
function gitFromEnv() {
  const e = process.env;
  const branch = e.VISTEST_BRANCH || e.GITHUB_HEAD_REF || e.GITHUB_REF_NAME
    || e.CI_COMMIT_REF_NAME || e.BRANCH_NAME || '';
  const sha = e.VISTEST_COMMIT || e.GITHUB_SHA || e.CI_COMMIT_SHA
    || e.GIT_COMMIT || '';
  return branch || sha ? { branch, sha } : {};
}

function toBlob(data) {
  if (typeof Blob !== 'undefined' && data instanceof Blob) return data;
  return new Blob([data], { type: 'image/png' });
}

function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

function num(v, d = 2) {
  return v == null ? '—' : Number(v).toFixed(d);
}

/** Playwright: evaluate(fnString). Puppeteer: то же. WebdriverIO: execute. */
async function evaluate(page, script) {
  if (page.evaluate) return page.evaluate(script);
  if (page.execute) return page.execute(script);
  throw new Error('Не понимаю объект страницы: нет evaluate/execute');
}

export default VisTest;
