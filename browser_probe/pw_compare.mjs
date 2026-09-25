// Native Playwright comparator (the one toHaveScreenshot calls) over the probe pairs.
// usage: node pw_compare.mjs pairs.json <dir with node_modules/@playwright/test>
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { join } from 'node:path';
const req = createRequire(join(process.argv[3], 'package.json'));
let getComparator;
for (const mod of ['playwright-core/lib/coreBundle', 'playwright-core/lib/utils']) {
  try { const m = req(mod); getComparator = m.utils?.getComparator ?? m.getComparator; if (getComparator) break; } catch {}
}
const cmp = getComparator('image/png');
const pairs = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const out = {};
for (const thr of [0.2, 0.1, 0.05]) {
  out[thr] = {};
  for (const [name, [exp, act]] of Object.entries(pairs)) {
    const r = cmp(readFileSync(act), readFileSync(exp), { threshold: thr });
    let n = 0; if (r) { const m = r.errorMessage.match(/(\d+) pixels/); n = m ? +m[1] : 1e9; }
    out[thr][name] = n;
  }
}
console.log(JSON.stringify(out));
