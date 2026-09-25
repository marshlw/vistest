"""Score the probe: VisTest presets vs native Playwright comparator sweep.

    python compare.py --bench ../scripts/bench     # dir where `npm ci` installed @playwright/test

S6 (heading weight 600->700) is skipped: the bundled font has no separate 600,
so the mutation changes no pixel.
"""
import argparse, json, os, subprocess, sys
import numpy as np
from PIL import Image
from vistest.core.comparator import compare
from vistest.config import VisTestConfig

HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser()
ap.add_argument("--bench", default=os.path.join(HERE, "..", "scripts", "bench"))
args = ap.parse_args()

meta = json.load(open(os.path.join(HERE, "shots", "meta.json")))
base = os.path.join(HERE, "shots", "base.png")
load = lambda p: np.asarray(Image.open(p).convert("RGB"))
e = load(base)
names = [n for n, m in meta.items() if not n.startswith("S6") and np.any(e != load(m["file"]))]
noise = [n for n in names if meta[n]["group"] == "NOISE"]
signal = [n for n in names if meta[n]["group"] == "SIGNAL"]
pairs = {n: [base, meta[n]["file"]] for n in names}
json.dump(pairs, open(os.path.join(HERE, "pairs.json"), "w"))

print(f"{len(noise)} noise pairs with changed pixels, {len(signal)} signal pairs\n")
for preset in ("strict", "balanced", "loose"):
    cfg = VisTestConfig.preset_of(preset).diff
    fails = {n: compare(e, load(meta[n]["file"]), cfg=cfg).verdict.value == "fail" for n in names}
    fp = [n.split()[0] for n in noise if fails[n]]
    fn = [n.split()[0] for n in signal if not fails[n]]
    print(f"VisTest {preset:9s} false {len(fp)}/{len(noise)} {fp}  misses {len(fn)}/{len(signal)} {fn}")

pw = json.loads(subprocess.check_output(
    ["node", os.path.join(HERE, "pw_compare.mjs"), os.path.join(HERE, "pairs.json"), os.path.abspath(args.bench)]))
print()
for thr, res in pw.items():
    for mdp in (0, 25, 100, 500):
        fp = sum(res[n] > mdp for n in noise)
        fn = sum(res[n] <= mdp for n in signal)
        print(f"Playwright threshold {thr:<4} maxDiffPixels {mdp:4d}: false {fp}/{len(noise)} misses {fn}/{len(signal)}")
