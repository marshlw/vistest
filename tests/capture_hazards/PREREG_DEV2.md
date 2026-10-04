# The capture-hazard stand, addendum for 0.2.0.dev2 (before any change to the library)

Written and committed alone, before the library, the capture or the engine is
touched and before any run on seeds 0–19 of the two new pages. It adds to the
stand's earlier pre-registrations (S1, S2, S2a, S2b); what they fix is not
changed here. Base: `main` at `0e25ad6` (the library as of S2b, version
`0.2.0.dev1`), branch `capture/dev2`.

## Why

A trial of 0.2.0.dev1 on a real application (v10, 36 checks, 10 quiet runs)
found a screenshot of a spinner that the readiness wait had let through, and
checks that failed because the background page under a modal was scrolled
differently from the baseline's. The stand has no page that reproduces either.
Two are added.

## The two new hazards

**`chained`** (`pages/chained.html`). Request A (`/api/data?h=chain_a`); when it
is answered, the same loading markup — a spinning CSS spinner and «Loading…» —
is drawn again and request B (`h=chain_b`) goes out at once; when B is answered,
a picture C (`/img?i=1`) is inserted with the data of B (a figure, a table), and
C itself is answered late. Every delay is the server holding its answer for
`stand.delay_ms` (50–1500 ms from the seed, the stand's usual range): A, B and C
in sequence. The page never stops showing a spinner until C is decoded; between
two requests nothing is in flight for a few milliseconds. Oracle: no spinner in
the DOM, the picture loaded with `naturalWidth > 0`, the table has its six rows,
and `__ready` follows within 150 ms; the page cannot be over before the three
holds have passed (their sum). Signal variant: B's figure and accent colour
change and C is a different picture.

**`modal_scroll`** (`pages/modal.html`). A long page, 800×600: a sticky header
with a balance, a 700 px block, a button «Details» below the fold. Two blocks
arrive by request after load (`h=ms_a` on top of the page, `h=ms_b` above the
button), each held by the server for 30–130 ms from the seed
(`stand.MODAL_MS`) — near the moment of the test's click, about 50–70 ms after
the page's script starts. The test's step is `click('#open')` right after
`goto`: Playwright scrolls the button into view, the modal opens at once (a
fixed dialog and a backdrop), and how far the page was scrolled to reach the
button, and what stands where under the backdrop when the blocks have arrived,
depends on which answer came before the click. The baseline is taken after the
same step, after `__ready` and 500 ms. Oracle: both blocks present and the modal
open. Signal variant: a real change in the background under the modal — the
balance in the sticky header and the figure above the button.

Both are in `stand.HAZARDS` (`chained`, `modal_scroll`), have a signal variant,
and run on both paths of our tool: `ours` (the library sees only the page) and
`ours_plugin` (the context's requests are counted, as the pytest plugin does).
`test_oracle.py` and `test_signals.py` cover them like the other pages.

## A pilot, and what it changed

To see that a page bites before it is registered, both were run once against
the S2b library on seeds 100–107, outside the calibration and the held-out set
(`measure --seeds 100-103`, `100-107`), tools `ours` and `ours_plugin`, 20 checks
and 5 of the signal variant per seed:

* `chained`, seeds 100–103: false failures `ours` 0/80, `ours+plugin` 4/80;
  misses 0/20 and 0/20. One of the four is a picture not yet loaded in the
  frame; three are text moved by under a pixel. Kept as it was.
* `modal_scroll`, first version with delays of 50–400 ms (seeds 100–103): false
  1/80 and 0/80 — the click always came before any answer (about 52–71 ms), so
  there was no race. The range was changed to 30–130 ms. Second version
  (seeds 100–107): false `ours` 22/160, `ours+plugin` 23/160; misses 0/40 and
  0/40.

Nothing else was chosen on the pilot. The pages and ranges above are the ones
committed next.

## Seeds

0–9 are the calibration, 10–19 are held out, as in S1. Everything chosen for
dev2 is chosen on 0–9. The held-out seeds are run once, at the end, with the
finished library, and printed only then; the S2b library's held-out run is made
after that and printed after it.

## Criteria (fixed here, before the numbers)

1. **Misses 0** on `chained` and on `modal_scroll`, on both paths (`ours`,
   `ours_plugin`), on the calibration seeds and on the held-out seeds (50
   checks of the signal variant each).
2. **False failures at most 2 of 200** (10 seeds × 20 checks), with the plugin
   (`ours_plugin`), on each of the two new pages, calibration and held-out
   separately.
3. **The other hazards are no worse than S2b:** for every hazard and each of
   `ours`, `ours_plugin`: the false failures and the misses on the same seeds
   are not more than the S2b library's. Equal is not worse. A hazard that is
   worse is printed as such and the criterion is not met.
4. **Time of static pages:** for `hover_focus`, `hover_focus_reset`, `scrolled`,
   `pulse` and `spinning_logo` (nothing to wait for but load and fonts), the
   median time of a check of the plain page, `ours_plugin`, is not more than the
   S2b library's median plus 100 ms, on the calibration seeds, both runs on one
   machine, one after the other.

A criterion that is not met is published as it is and is not revised.

## What is run, and in which order

1. the S2b library (`0e25ad6`, in its own work tree) on all 20 hazards,
   calibration seeds, `ours` and `ours_plugin`, with times;
2. the changes (readiness, pointer, scroll of the window, messages, DPR, folder,
   version), each test first where it can be written;
3. dev2 on all 20 hazards, calibration seeds, both tools, with times;
4. dev2 on the held-out seeds, once;
5. the S2b library on the held-out seeds, after 4 is recorded.

Playwright's column is not repeated: it is a property of the stand's pages, not
of the library change. The environment — Chromium, Playwright 1.56.0, 2 cores —
is written next to the numbers.
