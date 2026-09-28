# browser_probe

Seed for stage R1 of the plan. One admin page rendered by Chromium, 22 CSS
mutations with known labels and 9 rendering variants, scored with VisTest
presets and the native Playwright comparator. A probe, not a benchmark:
one page, one browser, mutations picked by hand.

    pip install -e ".[dev]" playwright && playwright install chromium
    npm ci --prefix scripts/bench
    python browser_probe/capture.py
    python browser_probe/compare.py --bench scripts/bench
    python browser_probe/jpeg_channel_order.py tests/benchmark_corpus/identical/expected.png

Result on 2026-09-25 (Linux x86-64, Chromium from Playwright 1.56, OpenCV 5.0):
five variants (re-run, GPU raster, headless shell vs full Chromium, two
hinting modes) produce 0 differing pixels; on the four that differ VisTest
balanced fails 3, Playwright fails 4 at every setting; VisTest misses 7 of 21
mutations (link colour, underline, radius, card border, shadow, row stripe,
error-text shade), Playwright misses 1 to 16 depending on threshold.

**Superseded by the browser corpus** (`tests/browser_corpus/`,
`scripts/browser_corpus.py`, `python tests/benchmark.py --corpus browser`),
which grew out of this probe: the 22 CSS mutations became 22 graded families,
the 9 rendering variants became the 8 `NOISE_CONFIGS` plus the two-browser
control on every frame, `pw_compare.mjs` became
`scripts/bench_playwright_grid.mjs`, and `compare.py` became
`tests/benchmark_browser.py`. Two things the probe did are known to be
unreliable and were not carried over: CSS injected after the page was drawn
(a fractional shift then rasterises one way or another depending on timing),
and one page drawn with the system's fonts. The probe stays as the record of
what was measured on 2026-09-25.
