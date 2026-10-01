/* The stand's pages: the seed, the signal, and the oracle.
 *
 * window.__ready turns true when the page's hazard is over: done() marks the
 * moment the last of it is in the DOM, and two animation frames later — once
 * it has been painted — the flag is set. window.__events keeps both times for
 * the oracle test. Neither the library nor Playwright reads them.
 */
const Q = new URLSearchParams(location.search);
const SEED = Q.get('seed') || '0';
const SIGNAL = Q.get('signal') === '1';
window.__ready = false;
window.__events = { start: performance.now() };

function api(path, extra) {
  const q = new URLSearchParams({ seed: SEED, signal: SIGNAL ? '1' : '0', ...(extra || {}) });
  return `${path}?${q}`;
}

function fetchJSON(path, extra) {
  return fetch(api(path, extra), { cache: 'no-store' }).then((r) => r.json());
}

function done() {
  if (window.__events.done !== undefined) return;
  window.__events.done = performance.now();
  requestAnimationFrame(() => requestAnimationFrame(() => {
    window.__events.ready = performance.now();
    window.__ready = true;
  }));
}

function table(rows) {
  return '<table><thead><tr><th>Customer</th><th class="num">Total</th></tr></thead><tbody>'
    + rows.map((r) => `<tr><td>${r[0]}</td><td class="num">$${r[1]}</td></tr>`).join('')
    + '</tbody></table>';
}

/* The pages with nothing to wait for but their own load and fonts. */
function readyOnLoad() {
  const go = () => document.fonts.ready.then(done);
  if (document.readyState === 'complete') go();
  else window.addEventListener('load', go);
}
