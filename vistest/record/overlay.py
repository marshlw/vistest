# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""Recording panel embedded into the page.

Lives in the Shadow DOM so the site's styles can't break it and so it doesn't
affect the page layout itself. Marked with `data-vistest="ignore"` and in any
case hidden before a snapshot — it can't end up in the baseline.

Communication with Python happens via `window.__vistestRecord(payload)`, a
binding exposed from Playwright (`context.expose_binding`).
"""

from __future__ import annotations

OVERLAY_JS = r"""
(() => {
  if (window.__vistestOverlay) return 'already';

  const HOST_ID = '__vistest_overlay';
  const host = document.createElement('div');
  host.id = HOST_ID;
  host.setAttribute('data-vistest', 'ignore');
  host.style.cssText = 'position:fixed;z-index:2147483647;inset:auto 16px 16px auto;';
  const root = host.attachShadow({ mode: 'open' });

  root.innerHTML = `
  <style>
    :host, * { box-sizing: border-box; }
    .panel{width:300px;background:#141821;color:#e7eaf0;border:1px solid #2b3140;
      border-radius:12px;box-shadow:0 12px 40px rgba(0,0,0,.45);
      font:13px/1.45 ui-sans-serif,-apple-system,Segoe UI,Roboto,sans-serif;overflow:hidden}
    .hd{display:flex;align-items:center;gap:8px;padding:10px 12px;background:#1b2029;
      border-bottom:1px solid #2b3140;cursor:move}
    .hd b{font-size:12px;letter-spacing:1px}
    .dot{width:8px;height:8px;border-radius:50%;background:#2ecc71}
    .bd{padding:12px;display:flex;flex-direction:column;gap:8px}
    input{width:100%;background:#0f131a;border:1px solid #2b3140;color:#e7eaf0;
      padding:7px 10px;border-radius:7px;font-size:13px}
    input:focus{outline:none;border-color:#4c8dff}
    .row{display:flex;gap:6px}
    button{flex:1;background:#222836;border:1px solid #2b3140;color:#e7eaf0;
      padding:8px 6px;border-radius:7px;cursor:pointer;font-size:12px}
    button:hover{border-color:#4c8dff}
    button.primary{background:#3b7dff;border-color:#3b7dff;color:#fff;font-weight:600}
    button.active{background:#4c8dff;border-color:#4c8dff;color:#fff}
    button.done{background:#1f6f43;border-color:#2ecc71}
    button:disabled{opacity:.5;cursor:progress}
    .hint{color:#8b93a5;font-size:11px;line-height:1.4}
    .list{max-height:190px;overflow:auto;display:flex;flex-direction:column;gap:4px}
    .it{display:flex;gap:6px;align-items:center;background:#0f131a;
      border:1px solid #232936;border-radius:6px;padding:5px 8px;font-size:12px}
    .it .n{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    .it .t{color:#8b93a5;font-size:10px}
    .it .x{cursor:pointer;color:#8b93a5}
    .it .x:hover{color:#e74c3c}
    .tag{display:inline-block;background:#222836;border-radius:4px;padding:0 5px;
      font-size:10px;color:#98a0b0}
    .empty{color:#6b7280;font-size:12px;text-align:center;padding:8px}
    .min{display:none}
    .panel.collapsed .bd{display:none}
  </style>
  <div class="panel">
    <div class="hd"><span class="dot"></span><b>VISTEST REC</b>
      <span style="margin-left:auto;color:#8b93a5;cursor:pointer" id="collapse">—</span></div>
    <div class="bd">
      <input id="name" placeholder="snapshot name, e.g. checkout" />
      <div class="row">
        <button class="primary" id="snap">Capture page</button>
        <button id="pick">Select element</button>
      </div>
      <div class="row">
        <button id="zone">Ignore zone</button>
        <button id="clear">Reset zones</button>
      </div>
      <div class="row">
        <button id="rec">● Record actions</button>
        <button id="steps">Steps: 0</button>
      </div>
      <div class="hint" id="hint">
        Ctrl+Shift+S — capture page. Name is optional; it will be taken from the URL.
      </div>
      <div class="list" id="list"><div class="empty">Nothing captured yet</div></div>
      <button class="done" id="finish">Done — save and exit</button>
    </div>
  </div>`;

  document.documentElement.appendChild(host);

  const $ = (id) => root.getElementById(id);
  const state = { mode: null, zones: [], items: [], busy: false, pending: null,
                  recording: false, steps: [], lastUrl: location.href };

  /* ---------------- helpers ---------------- */
  const nameFromUrl = () => {
    const path = location.pathname.replace(/\/+$/, '').replace(/^\//, '') || 'index';
    return path.replace(/\//g, '-');
  };

  /* Element selector.
     A chain of utility classes like `div.min-h-screen.flex > div.w-full.max-w-md >
     form.flex.flex-col > input.input` technically works, but it breaks with any
     markup change and is unreadable in review. So we first look for something
     stable and meaningful, and only as a last resort build a path. */
  const uniq = (sel) => {
    try { return document.querySelectorAll(sel).length === 1; } catch (e) { return false; }
  };
  const esc = (v) => String(v).replace(/"/g, '\\"');

  const stableSelector = (el) => {
    const tag = el.tagName.toLowerCase();

    for (const attr of ['data-testid', 'data-test', 'data-qa', 'data-cy']) {
      const v = el.getAttribute(attr);
      if (v) return `[${attr}="${esc(v)}"]`;
    }
    if (el.id && !/^\d/.test(el.id) && uniq(`#${CSS.escape(el.id)}`)) {
      return `#${CSS.escape(el.id)}`;
    }
    for (const attr of ['name', 'aria-label', 'placeholder', 'title']) {
      const v = el.getAttribute(attr);
      if (v && uniq(`${tag}[${attr}="${esc(v)}"]`)) {
        return `${tag}[${attr}="${esc(v)}"]`;
      }
    }
    // Form fields: type + role are almost always unique on a login screen
    if (tag === 'input' && el.type) {
      const s = `input[type="${el.type}"]`;
      if (uniq(s)) return s;
    }
    if (tag === 'button' && el.type === 'submit' && uniq('button[type="submit"]')) {
      return 'button[type="submit"]';
    }
    // A button or link with short text — readable and survives refactoring
    if (['button', 'a'].includes(tag)) {
      const text = (el.textContent || '').trim().replace(/\s+/g, ' ');
      if (text && text.length <= 30) {
        const candidates = Array.from(document.querySelectorAll(tag))
          .filter(e => (e.textContent || '').trim().replace(/\s+/g, ' ') === text);
        if (candidates.length === 1) return `${tag}:has-text("${esc(text)}")`;
      }
    }
    const role = el.getAttribute('role');
    if (role && uniq(`[role="${esc(role)}"]`)) return `[role="${esc(role)}"]`;
    return null;
  };

  const cssPath = (el) => {
    const stable = stableSelector(el);
    if (stable) return stable;

    // Found nothing meaningful — build a short path, anchoring on the
    // nearest ancestor with a stable selector.
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && parts.length < 4) {
      const anchor = node !== el ? stableSelector(node) : null;
      if (anchor) { parts.unshift(anchor); break; }

      // Утилитарные классы (tailwind и подобное) в селекторе бесполезны:
      // они описывают оформление, а не то, ЧТО это за элемент.
      const UTILITY_CLASS = new RegExp('^(flex|grid|w-|h-|p-|m-|px-|py-'
        + '|mx-|my-|gap-|text-|bg-|border|rounded|min-|max-|items-'
        + '|justify-|space-)');
      let part = node.tagName.toLowerCase();
      const cls = (node.getAttribute('class') || '').split(/\s+/)
        .filter(c => c && !/^\d/.test(c) && c.length < 24
                     && !UTILITY_CLASS.test(c))
        .slice(0, 2);
      if (cls.length) part += '.' + cls.join('.');
      const parent = node.parentElement;
      if (parent) {
        const sibs = Array.from(parent.children).filter(c => c.tagName === node.tagName);
        if (sibs.length > 1) part += ':nth-of-type(' + (sibs.indexOf(node) + 1) + ')';
      }
      parts.unshift(part);
      node = node.parentElement;
    }
    return parts.join(' > ');
  };

  /* ---------------- element passport ----------------
     The same click is described three ways of differing robustness:
     data-testid survives both re-layouts and UI translation; a role with
     an accessible name survives re-layouts; a CSS path survives nothing.

     We write all three and leave the decision to the code generator: the
     browser knows what's on the page, but which locator is appropriate is a
     matter of generation. */
  const ROLE_BY_TAG = {
    a: 'link', button: 'button', select: 'combobox', textarea: 'textbox',
    h1: 'heading', h2: 'heading', h3: 'heading', h4: 'heading', img: 'img',
  };
  const ROLE_BY_INPUT = {
    checkbox: 'checkbox', radio: 'radio', submit: 'button', button: 'button',
    reset: 'button', search: 'searchbox', number: 'spinbutton',
    range: 'slider', text: 'textbox', email: 'textbox', tel: 'textbox',
    url: 'textbox', password: 'textbox',
  };

  const roleOf = (el) => {
    const explicit = (el.getAttribute && el.getAttribute('role')) || '';
    if (explicit) return explicit;
    const tag = el.tagName.toLowerCase();
    if (tag === 'input') return ROLE_BY_INPUT[(el.type || 'text').toLowerCase()] || 'textbox';
    return ROLE_BY_TAG[tag] || '';
  };

  const clean = (s) => String(s || '').trim().replace(/\s+/g, ' ');

  /* Approximation of the accessible name. The full ARIA algorithm isn't needed
     here: if the name is computed inaccurately, the locator simply won't be
     unique, and the generator falls back to CSS. The mistake is cheap and not
     silent. */
  const accessibleName = (el) => {
    const aria = clean(el.getAttribute && el.getAttribute('aria-label'));
    if (aria) return aria;
    const by = el.getAttribute && el.getAttribute('aria-labelledby');
    if (by) {
      const ref = document.getElementById(by);
      if (ref) return clean(ref.textContent);
    }
    if (el.labels && el.labels.length) return clean(el.labels[0].textContent);
    const own = clean(el.getAttribute && (el.getAttribute('placeholder')
                                          || el.getAttribute('title')));
    if (own) return own;
    if (el.tagName === 'INPUT' && /^(submit|button|reset)$/i.test(el.type || '')) {
      return clean(el.value);
    }
    const text = clean(el.textContent);
    return text.length <= 60 ? text : '';
  };

  const testIdOf = (el) => {
    for (const attr of ['data-testid', 'data-test', 'data-qa', 'data-cy']) {
      const v = el.getAttribute && el.getAttribute(attr);
      if (v) return { attr: attr, value: v };
    }
    return null;
  };

  const roleIsUnique = (role, name) => {
    if (!role || !name) return false;
    try {
      let hits = 0;
      for (const n of document.querySelectorAll('*')) {
        if (roleOf(n) === role && accessibleName(n) === name) {
          if (++hits > 1) return false;
        }
      }
      return hits === 1;
    } catch (e) { return false; }
  };

  const describeEl = (el) => {
    if (!el || !el.tagName) return null;
    const tid = testIdOf(el);
    const role = roleOf(el);
    const name = accessibleName(el);
    return {
      tag: el.tagName.toLowerCase(),
      type: el.type || null,
      testid_attr: tid ? tid.attr : null,
      testid: tid ? tid.value : null,
      role: role || null,
      name: name || null,
      role_unique: tid ? false : roleIsUnique(role, name),
      text: clean(el.textContent).slice(0, 60) || null,
    };
  };

  const setHint = (text, color) => {
    const h = $('hint');
    h.textContent = text;
    h.style.color = color || '#8b93a5';
  };

  const renderList = () => {
    const list = $('list');
    if (!state.items.length) {
      list.innerHTML = '<div class="empty">Nothing captured yet</div>';
      return;
    }
    list.innerHTML = '';
    state.items.forEach((it, i) => {
      const d = document.createElement('div');
      d.className = 'it';
      d.innerHTML = `<span class="n">${it.name}</span>
        <span class="t">${it.selector ? 'element' : 'page'}${
          it.ignore_boxes && it.ignore_boxes.length ? ' +zones' : ''}</span>
        <span class="x" data-i="${i}">×</span>`;
      d.querySelector('.x').onclick = () => {
        state.items.splice(i, 1);
        send({ action: 'drop', name: it.name });
        renderList();
      };
      list.append(d);
    });
    list.scrollTop = list.scrollHeight;
  };

  /* ---------------- highlight while selecting an element ---------------- */
  const hl = document.createElement('div');
  hl.setAttribute('data-vistest', 'ignore');
  hl.style.cssText = 'position:absolute;pointer-events:none;z-index:2147483646;' +
    'border:2px solid #4c8dff;background:rgba(76,141,255,.14);display:none;' +
    'border-radius:3px;transition:all .04s linear';
  document.documentElement.appendChild(hl);

  const showHl = (el) => {
    const r = el.getBoundingClientRect();
    hl.style.display = 'block';
    hl.style.left = (r.left + scrollX) + 'px';
    hl.style.top = (r.top + scrollY) + 'px';
    hl.style.width = r.width + 'px';
    hl.style.height = r.height + 'px';
  };
  const hideHl = () => { hl.style.display = 'none'; };

  const elementAt = (e) => {
    const el = document.elementFromPoint(e.clientX, e.clientY);
    if (!el || el === host || el === hl || el.closest?.(`#${HOST_ID}`)) return null;
    return el;
  };

  /* ---------------- "select element" mode ---------------- */
  const onPickMove = (e) => { const el = elementAt(e); if (el) showHl(el); };
  const onPickClick = (e) => {
    const el = elementAt(e);
    if (!el) return;
    e.preventDefault(); e.stopPropagation();
    stopModes();
    capture({ selector: cssPath(el), selector_element: describeEl(el) });
  };

  /* ---------------- "ignore zone" mode ---------------- */
  let zoneStart = null;
  const box = document.createElement('div');
  box.setAttribute('data-vistest', 'ignore');
  box.style.cssText = 'position:absolute;z-index:2147483646;pointer-events:none;' +
    'border:2px dashed #f1c40f;background:rgba(241,196,15,.15);display:none';
  document.documentElement.appendChild(box);

  const onZoneDown = (e) => {
    if (e.target === host || e.target.closest?.(`#${HOST_ID}`)) return;
    e.preventDefault();
    zoneStart = { x: e.clientX + scrollX, y: e.clientY + scrollY };
    box.style.display = 'block';
  };
  const onZoneMove = (e) => {
    if (!zoneStart) return;
    const x = e.clientX + scrollX, y = e.clientY + scrollY;
    box.style.left = Math.min(x, zoneStart.x) + 'px';
    box.style.top = Math.min(y, zoneStart.y) + 'px';
    box.style.width = Math.abs(x - zoneStart.x) + 'px';
    box.style.height = Math.abs(y - zoneStart.y) + 'px';
  };
  const onZoneUp = (e) => {
    if (!zoneStart) return;
    const x = e.clientX + scrollX, y = e.clientY + scrollY;
    const z = {
      x: Math.round(Math.min(x, zoneStart.x)), y: Math.round(Math.min(y, zoneStart.y)),
      w: Math.round(Math.abs(x - zoneStart.x)), h: Math.round(Math.abs(y - zoneStart.y)),
    };
    zoneStart = null;
    box.style.display = 'none';
    if (z.w < 6 || z.h < 6) return;
    state.zones.push(z);
    drawZones();
    setHint(`Ignore zones: ${state.zones.length}.`
            + ' They will be included in the next snapshot.', '#f1c40f');
    stopModes();
  };

  const zoneMarks = [];
  const drawZones = () => {
    zoneMarks.forEach(m => m.remove());
    zoneMarks.length = 0;
    state.zones.forEach(z => {
      const m = document.createElement('div');
      m.setAttribute('data-vistest', 'ignore');
      m.style.cssText = `position:absolute;z-index:2147483645;pointer-events:none;
        left:${z.x}px;top:${z.y}px;width:${z.w}px;height:${z.h}px;
        border:2px dashed #f1c40f;background:rgba(241,196,15,.12)`;
      document.documentElement.appendChild(m);
      zoneMarks.push(m);
    });
  };

  /* ---------------- recording actions ----------------
     We record what's actually needed to bring the page to a state:
     navigations, clicks, input. Passwords are not saved — instead of the
     value, an environment variable name is substituted, so the secret doesn't
     leak into git. */
  const SECRET_RE = /pass|secret|token|otp|pin|cvv/i;

  const isSecretField = (el) =>
    el.type === 'password' ||
    SECRET_RE.test(el.name || '') || SECRET_RE.test(el.id || '') ||
    SECRET_RE.test(el.getAttribute('autocomplete') || '');

  const looksLikeLogin = (el) =>
    /user|login|email|phone|tel/i.test((el.name || '') + (el.id || '') +
      (el.getAttribute('autocomplete') || ''));

  /* Recording survives navigation between pages: otherwise a login that ends
     with a redirect would be impossible to record at all. */
  const STORE_KEY = '__vistest_rec';

  function saveRec() {
    try {
      sessionStorage.setItem(STORE_KEY, JSON.stringify({
        recording: state.recording, steps: state.steps, url: location.href,
      }));
    } catch (e) { /* private mode — we'll survive */ }
  }

  function restoreRec() {
    let saved;
    try { saved = JSON.parse(sessionStorage.getItem(STORE_KEY) || 'null'); }
    catch (e) { return; }
    if (!saved) return;
    state.steps = saved.steps || [];
    $('steps').textContent = `Steps: ${state.steps.length}`;
    if (saved.recording) {
      toggleRecording();
      // The navigation is also part of the scenario — otherwise the steps
      // end up detached from the URL.
      if (saved.url && saved.url !== location.href) {
        state.steps.push({ action: 'goto', url: location.href });
        $('steps').textContent = `Steps: ${state.steps.length}`;
        saveRec();
      }
    }
  }

  function pushStep(step) {
    if (!state.recording) return;
    // Re-entering the same field replaces the previous entry rather than accumulating.
    const last = state.steps[state.steps.length - 1];
    if (last && last.action === 'fill' && step.action === 'fill'
        && last.selector === step.selector) {
      state.steps[state.steps.length - 1] = step;
    } else {
      state.steps.push(step);
    }
    $('steps').textContent = `Steps: ${state.steps.length}`;
    saveRec();
    setHint(`Recorded: ${step.action} ${step.selector || step.url || ''}`.slice(0, 60),
            '#2ecc71');
  }

  const onRecClick = (e) => {
    if (!state.recording || state.mode) return;
    const el = elementAt(e);
    if (!el) return;
    const tag = el.tagName.toLowerCase();
    if (['input', 'textarea', 'select'].includes(tag)) return; // input is recorded separately
    const target = el.closest('a,button,[role=button],label,li,td') || el;
    pushStep({ action: 'click', selector: cssPath(target),
               element: describeEl(target) });
  };

  const onRecChange = (e) => {
    if (!state.recording) return;
    const el = e.target;
    if (!el || !el.tagName) return;
    const tag = el.tagName.toLowerCase();
    const meta = describeEl(el);
    if (tag === 'select') {
      pushStep({ action: 'select', selector: cssPath(el), value: el.value,
                 element: meta });
      return;
    }
    if (tag !== 'input' && tag !== 'textarea') return;
    if (el.type === 'checkbox') {
      pushStep({ action: el.checked ? 'check' : 'uncheck',
                 selector: cssPath(el), element: meta });
      return;
    }
    if (isSecretField(el)) {
      pushStep({ action: 'fill', selector: cssPath(el),
                 value: '${VISTEST_PASSWORD}', secret: true, element: meta });
      return;
    }
    if (looksLikeLogin(el)) {
      pushStep({ action: 'fill', selector: cssPath(el),
                 value: '${VISTEST_USER}', element: meta });
      return;
    }
    pushStep({ action: 'fill', selector: cssPath(el), value: el.value,
               element: meta });
  };

  const onRecKey = (e) => {
    if (!state.recording) return;
    if (e.key === 'Enter' && e.target && e.target.tagName === 'INPUT') {
      pushStep({ action: 'press', selector: cssPath(e.target),
                 value: 'Enter', element: describeEl(e.target) });
    }
  };

  function toggleRecording() {
    state.recording = !state.recording;
    $('rec').classList.toggle('active', state.recording);
    $('rec').textContent = state.recording ? '■ Stop' : '● Record actions';
    if (state.recording) {
      document.addEventListener('click', onRecClick, true);
      document.addEventListener('change', onRecChange, true);
      document.addEventListener('keydown', onRecKey, true);
      setHint('Recording actions: click and fill in forms as usual. ' +
              'The password will be saved as ${VISTEST_PASSWORD}.', '#2ecc71');
    } else {
      document.removeEventListener('click', onRecClick, true);
      document.removeEventListener('change', onRecChange, true);
      document.removeEventListener('keydown', onRecKey, true);
      setHint(`Recorded steps: ${state.steps.length}. ` +
              'They will be included in the next snapshot.');
    }
    saveRec();
  }

  function showSteps() {
    if (!state.steps.length) {
      setHint('No steps. Enable "Record actions" and go through the path you need.');
      return;
    }
    const text = state.steps
      .map((s, i) => `${i + 1}. ${s.action} ${s.selector || s.url || ''} ` +
                     `${s.value !== undefined ? '= ' + s.value : ''}`.trim())
      .join('\n');
    if (confirm(`Recorded steps:\n\n${text}\n\nOK — keep, Cancel — clear.`)) return;
    state.steps = [];
    $('steps').textContent = 'Steps: 0';
    saveRec();
    setHint('Steps cleared.');
  }

  /* ---------------- mode switching ---------------- */
  function stopModes() {
    state.mode = null;
    hideHl();
    document.removeEventListener('mousemove', onPickMove, true);
    document.removeEventListener('click', onPickClick, true);
    document.removeEventListener('mousedown', onZoneDown, true);
    document.removeEventListener('mousemove', onZoneMove, true);
    document.removeEventListener('mouseup', onZoneUp, true);
    $('pick').classList.remove('active');
    $('zone').classList.remove('active');
  }

  /* ---------------- snapshot ----------------
     The request is queued and returns immediately; the snapshot itself is
     taken by the main loop on the Python side and reports progress via
     progress() and the result via onResult(). This way the panel can't
     "hang on Capturing…", even if the page is heavy. */
  let watchdog = null;

  async function capture(opts = {}) {
    if (state.busy) {
      setHint('The previous snapshot is still running.', '#f1c40f');
      return;
    }
    const name = ($('name').value || '').trim() || nameFromUrl();
    state.busy = true;
    state.pending = {
      selector: opts.selector || null,
      selector_element: opts.selector_element || null,
      zones: state.zones.slice(),
      steps: state.steps.slice(),
      started: Date.now(),
    };
    setBusyUI(true);
    setHint('Queuing…', '#4c8dff');
    armWatchdog();

    try {
      const res = await send({
        action: 'capture',
        name: name.endsWith('.png') ? name : name + '.png',
        selector: opts.selector || null,
        selector_element: opts.selector_element || null,
        url: location.href,
        viewport: { w: innerWidth, h: innerHeight },
        dpr: devicePixelRatio || 1,
        ignore_boxes: state.zones.slice(),
        steps: state.steps.slice(),
      });
      if (!res || !res.ok) {
        finishCapture({ ok: false, error: (res && res.error) || 'no connection to vistest' });
      }
    } catch (e) {
      finishCapture({ ok: false, error: e.message });
    }
  }

  function armWatchdog() {
    clearTimeout(watchdog);
    // If within a minute neither progress nor a result arrives, something broke
    // on the Python side. Better to say so honestly than keep spinning "Capturing…".
    watchdog = setTimeout(() => {
      if (!state.busy) return;
      finishCapture({
        ok: false,
        error: 'no response for 60 s — check the console output where record is running',
      });
    }, 60000);
  }

  function setBusyUI(busy) {
    ['snap', 'pick', 'zone', 'finish', 'rec'].forEach(id => { $(id).disabled = busy; });
    $('snap').textContent = busy ? 'Capturing…' : 'Capture page';
  }

  /* Called from Python during the snapshot. */
  function progress(text) {
    if (!state.busy) return;
    const sec = ((Date.now() - (state.pending?.started || Date.now())) / 1000).toFixed(0);
    setHint(`${text}… ${sec} s`, '#4c8dff');
    armWatchdog();
  }

  /* Called from Python on completion. */
  function finishCapture(res) {
    clearTimeout(watchdog);
    state.busy = false;
    setBusyUI(false);
    const pending = state.pending || {};
    state.pending = null;

    if (res && res.ok) {
      state.items.push({ name: res.name, selector: pending.selector || null,
                         ignore_boxes: pending.zones || [],
                         steps: pending.steps || [] });
      state.zones = [];
      drawZones();
      renderList();
      $('name').value = '';
      // We keep the steps: the next snapshot is usually taken from the same
      // state, no need to go through login with the mouse again.
      const extra = res.seconds ? ` in ${res.seconds} s` : '';
      setHint(`Done: ${res.name} (${res.width}×${res.height})${extra}`, '#2ecc71');
      if (res.notes && res.notes.length) {
        setHint(`${res.name}: ${res.notes[0]}`, '#f1c40f');
      }
    } else {
      setHint('Error: ' + ((res && res.error) || 'unknown'), '#e74c3c');
    }
  }

  function send(payload) {
    if (!window.__vistestRecord) {
      return Promise.resolve({ ok: false, error: 'no connection to vistest' });
    }
    return window.__vistestRecord(payload);
  }

  /* ---------------- panel handlers ---------------- */
  $('snap').onclick = () => capture();

  $('pick').onclick = () => {
    if (state.mode === 'pick') return stopModes();
    stopModes();
    state.mode = 'pick';
    $('pick').classList.add('active');
    setHint('Hover over an element and click. Esc — cancel.', '#4c8dff');
    document.addEventListener('mousemove', onPickMove, true);
    document.addEventListener('click', onPickClick, true);
  };

  $('zone').onclick = () => {
    if (state.mode === 'zone') return stopModes();
    stopModes();
    state.mode = 'zone';
    $('zone').classList.add('active');
    setHint('Drag with the mouse to select the area to ignore.', '#f1c40f');
    document.addEventListener('mousedown', onZoneDown, true);
    document.addEventListener('mousemove', onZoneMove, true);
    document.addEventListener('mouseup', onZoneUp, true);
  };

  $('clear').onclick = () => {
    state.zones = [];
    drawZones();
    setHint('Ignore zones reset.');
  };

  $('rec').onclick = toggleRecording;
  $('steps').onclick = showSteps;

  $('finish').onclick = async () => {
    stopModes();
    setHint('Saving…', '#4c8dff');
    await send({ action: 'finish' });
    setHint('You can close the window.', '#2ecc71');
  };

  $('collapse').onclick = () => {
    root.querySelector('.panel').classList.toggle('collapsed');
  };

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') stopModes();
    if (e.ctrlKey && e.shiftKey && (e.key === 'S' || e.key === 's')) {
      e.preventDefault();
      capture();
    }
  }, true);

  /* dragging the panel */
  (() => {
    const hd = root.querySelector('.hd');
    let drag = null;
    hd.addEventListener('mousedown', (e) => {
      const r = host.getBoundingClientRect();
      drag = { dx: e.clientX - r.left, dy: e.clientY - r.top };
      e.preventDefault();
    });
    document.addEventListener('mousemove', (e) => {
      if (!drag) return;
      host.style.inset = 'auto';
      host.style.left = (e.clientX - drag.dx) + 'px';
      host.style.top = (e.clientY - drag.dy) + 'px';
    });
    document.addEventListener('mouseup', () => { drag = null; });
  })();

  restoreRec();

  window.__vistestOverlay = {
    hide() { host.style.display = 'none'; hideHl();
             zoneMarks.forEach(m => m.style.display = 'none'); },
    show() { host.style.display = ''; zoneMarks.forEach(m => m.style.display = ''); },
    progress,
    onResult: finishCapture,
    items: () => state.items,
  };

  return 'ok';
})();
"""
