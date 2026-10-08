# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""What a person reads about a check, said in one place.

The failure message, the report, the reason of a check that passed,
`vistest compare` and `vistest bench` say a change, the numbers a verdict was
taken on, a second look and what was set aside with these functions and no
others (review v1, 4.3–4.9). When two of them say the same region in two ways,
the reader stops believing both.

The rules the words follow:

* a change is said as the engine measured it (core/v2/describe.py): its
  category — moved, line, fill, color, redrawn, shape — is the one shown
  everywhere, and the exact counts behind it go to the report only;
* no word of the machinery: not «engine v2», not «rule», not «region»;
* the color difference is one number per change, and its scale is said once
  per message (`SCALE`), not after every number;
* one thing per line, at most `WIDTH` characters; only a path may be longer.
"""

from __future__ import annotations

import re

__all__ = ["SCALE", "WIDTH", "below", "category", "command", "counted",
           "detail", "joined", "labelled", "limits_line", "limits_lines", "look", "reason",
           "region", "scale_for", "sentence", "suppressed", "suppressed_detail", "where",
           "wrap"]

#: No line a person reads is longer than this, unless it is a path.
WIDTH = 120
#: The labels of a message line up after this many characters.
COLUMN = 15
#: The scale of «color difference», said once where the numbers are read.
SCALE = ("color difference is ΔE00 (CIEDE2000): 1 ≈ barely visible, "
         "10 and more is plainly another color")
#: Changes spelled out in a below-threshold line; the rest are counted.
BELOW_SHOWN = 5


def _get(r, key):
    return r.get(key) if isinstance(r, dict) else getattr(r, key, None)


def _note(r) -> dict:
    for a in _get(r, "annotations") or ():
        if isinstance(a, dict) and a.get("kind") == "description":
            return a
    return {}


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


# --------------------------------------------------------------------------- #
#  One change
# --------------------------------------------------------------------------- #
def where(r) -> str:
    """'97x12 at (219, 522)'."""
    return f"{_get(r, 'w')}x{_get(r, 'h')} at ({_get(r, 'x')}, {_get(r, 'y')})"


def sentence(r) -> str:
    """What was measured on the change (core/v2/describe.py), or ''."""
    return str(_note(r).get("text") or "")


def detail(r) -> str:
    """The exact counts behind `sentence`, for the report; '' when it has them."""
    return str(_note(r).get("detail") or "")


#: What a change without the engine's sentence is called — a result of the
#: server's old engine, a report row written before the sentences existed.
_V1_KINDS = {"color": "color", "moved": "moved", "added": "added", "removed": "removed",
             "text": "text", "content": "content", "resized": "resized",
             "noise": "noise", "antialias": "antialiasing"}


def category(r) -> str:
    """moved, line, fill, color, redrawn or shape — the one word the report's kind
    column, the diff's label and the reason all use for this change."""
    what = _note(r).get("value")
    if what:
        return str(what)
    kind = _get(r, "kind")
    kind = str(getattr(kind, "value", kind) or "")
    return _V1_KINDS.get(kind, kind)


def region(r) -> str:
    """'97x12 at (219, 522): recolored: #343649 → #586074, color difference 14.2'."""
    said = sentence(r)
    return f"{where(r)}: {said}" if said else where(r)


# --------------------------------------------------------------------------- #
#  A check
# --------------------------------------------------------------------------- #
def counted(regions) -> str:
    """'shape in 28, fill in 3, color in 2' — every change counted, none dropped."""
    counts: dict[str, int] = {}
    for r in regions:
        counts[category(r)] = counts.get(category(r), 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])
    parts = [f"{what} in {n}" for what, n in ranked[:3]]
    rest = sum(n for _, n in ranked[3:])
    if rest:
        parts.append(f"other in {rest}")
    return ", ".join(parts)


#  Below this share of the changed pixels left outside every change, the area
#  and the changes tell the same story and nothing is added.
UNASSIGNED_SHARE_TO_MENTION = 0.10


def _coverage(result) -> str:
    """The bridge between the changed area and the changes, when they disagree."""
    changed = int(getattr(result, "changed_pixels", 0) or 0)
    unassigned = int(getattr(result, "unassigned_pixels", 0) or 0)
    if changed <= 0 or unassigned <= UNASSIGNED_SHARE_TO_MENTION * changed:
        return ""
    total = max(int(getattr(result, "total_pixels", 0) or 0), 1)
    held = 100.0 * int(getattr(result, "region_pixels", 0) or 0) / total
    suppressed = int(getattr(result, "suppressed_pixels", 0) or 0)
    outside = 100.0 * unassigned / total
    text = (f"; these changes hold {held:.2f}% of the {result.changed_area_pct:.2f}% "
            f"that differs, {outside:.2f}% is in none of them (strokes or specks too "
            "thin to count on their own)")
    if suppressed:
        text += f", {100.0 * suppressed / total:.2f}% was set aside as rendering noise"
    return text


def reason(result) -> str:
    """What changed, in one line: the same in the message and in the report.

    'shape in 28, fill in 3, color in 2; worst 193x33 at (823, 356)'.
    """
    if getattr(result, "size_changed", False):
        expected, actual = result.size_expected, result.size_actual
        return (f"the picture changed size, {expected[0]}x{expected[1]} "
                f"to {actual[0]}x{actual[1]}")
    regions = list(getattr(result, "regions", ()) or ())
    if not regions:
        return below(result) or (f"nothing stands out; {result.changed_area_pct:.2f}% of "
                                 "the frame differs")
    #  Picked by severity, and named for it: «largest» used to be checked
    #  against the sizes and found false.
    top = max(regions, key=lambda r: getattr(r, "severity", 0.0))
    selector = getattr(top, "selector", None)
    return (f"{counted(regions)}; worst {where(top)}" + (f", {selector}" if selector else "")
            + _coverage(result))


#: Where a threshold came from (`DiffConfig.threshold_source`), in words.
_SOURCES = {"call": "set in the call", "default": "the default",
            "snapshot passport": "from the baseline's passport",
            "global override": "a global override", "project override": "a project override"}


def _source(source: str) -> str:
    return _SOURCES.get(source) or f"from {source}"


def limits_line(result, limits: dict) -> str:
    """The numbers the verdict was taken on (review v1, 4.3).

    '8 changes not explained as rendering noise · worst 93/100 · no threshold
    set, so any change fails · 1.55% of the frame'.
    """
    n = len(getattr(result, "regions", ()) or ())
    worst = float(result.max_severity)
    area = f"{result.changed_area_pct:.2f}% of the frame"
    if limits.get("engine") == "v1":
        return (f"{_plural(n, 'change')} · worst {worst:.1f}/100, limit "
                f"{limits.get('fail_severity', 0):.1f} · {area}, limit "
                f"{limits.get('max_changed_area_pct', 0):.2f}%")
    source = limits.get("threshold_source")
    if not source:
        return (f"{_plural(n, 'change')} not explained as rendering noise · worst "
                f"{worst:.0f}/100 · no threshold set, so any change fails · {area}")
    from ..core.engines import AREA_DEFAULT_SOURCE, AREA_HINT

    threshold = f"the threshold {float(limits.get('fail_severity', 0)):g} ({_source(source)})"
    area_limit = (f"{limits.get('max_changed_area_pct', 0):.2f}%, "
                  f"{_source(limits.get('area_source') or AREA_DEFAULT_SOURCE)}")
    said = getattr(result, "threshold", None) or {}
    if "area_pct" in said:
        #  Every change was below the threshold, and together they cover the
        #  share of the frame that fails on its own — said with what to do.
        return (f"{_plural(n, 'change')}, all below {threshold} · worst {worst:.1f}/100 · "
                f"together {said['area_pct']:.2f}% of the frame, at or over the limit "
                f"({area_limit}) · {AREA_HINT}")
    return (f"{_plural(n, 'change')} at or above {threshold} · worst {worst:.1f}/100 · "
            f"{area} (limit {area_limit})")


def limits_lines(result, limits: dict) -> list[str]:
    """`limits_line` as the message prints it: broken between its parts."""
    return joined(limits_line(result, limits).split(" · "))


def below(result) -> str:
    """'below the threshold 25 (set in the call): 2 changes — recolored: … ×2', or ''.

    What a threshold a person set let through: said, never dropped — in the
    message, the report and the reason of a check that passed.
    """
    threshold = getattr(result, "threshold", None)
    shown = list(getattr(result, "below_threshold", None) or ())
    if not threshold or not shown:
        return ""
    #  The same sentence about several changes — a line of text recolored
    #  word by word — is said once, with how many it is about.
    counts: dict[str, int] = {}
    for r in shown:
        said = sentence(r) or where(r)
        counts[said] = counts.get(said, 0) + 1
    items = [s if k == 1 else f"{s} ×{k}" for s, k in counts.items()]
    text = "; ".join(items[:BELOW_SHOWN])
    if len(items) > BELOW_SHOWN:
        text += f"; and {sum(list(counts.values())[BELOW_SHOWN:])} more"
    return (f"below the threshold {float(threshold['value']):g} "
            f"({_source(str(threshold.get('source') or ''))}): "
            f"{_plural(len(shown), 'change')} — {text}")


def scale_for(texts) -> str:
    """`SCALE` when any of the texts says a color difference, else ''."""
    return SCALE if any("color difference" in t for t in texts) else ""


# --------------------------------------------------------------------------- #
#  What was set aside
# --------------------------------------------------------------------------- #
#: Why a change was set aside, by the name the engine files it under.
SET_ASIDE = {
    "min-size": "too small to count",
    "rerender-text": "the same text drawn again by another renderer",
    "rerender": "the same text drawn again by another renderer",
    "morphology": "nothing changed inside the box",
    "page-shift": "the page moved by a fraction of a pixel",
    "scrollbar": "a scroll bar",
    "jpeg": "JPEG re-encoding",
    "ai-layer": "the AI layer let it go",
    "ignored-kind": "a kind diff.ignore_kinds ignores",
    "unstable": "not there on a second picture",
    "noise": "scored as noise",
    "below-fail-on": "scored below fail_on",
    "antialias": "antialiasing",
}
_MIN_SIZE = re.compile(r"min-size: (\d+) px < (\d+) px")


def _filed(why: str) -> tuple[str, str]:
    """'rule: what the engine measured (was text)' -> (rule, what it measured)."""
    head, _, rest = why.partition(":")
    rest = re.sub(r"\s*\(was \w+\)\s*$", "", rest.strip())
    return head.strip(), rest


def suppressed(r) -> str:
    """'40x12 at (216, 473): antialiasing — the baseline as it is reproduces 99% …'.

    A change set aside is a claim about the pixels («the baseline moved 0.25 px
    reproduces 96% of it») that the reader is entitled to check against the
    diff, so what the engine measured stays; the name it files the reason
    under, and the kind of the old classifier («(was text)»), do not.
    """
    why = str(_get(r, "suppressed_by") or "")
    head, measured = _filed(why)
    said = SET_ASIDE.get(head)
    if said is None:
        return f"{where(r)}: {why or 'set aside'}"
    small = _MIN_SIZE.match(why)
    if small:
        return f"{where(r)}: {said} ({small.group(1)} px, under {small.group(2)})"
    return f"{where(r)}: {said}" + (f" — {measured}" if measured else "")


def suppressed_detail(r) -> str:
    """What the engine measured to set the change aside, without its file names."""
    _, rest = _filed(str(_get(r, "suppressed_by") or ""))
    return rest


# --------------------------------------------------------------------------- #
#  The second look (core/retry.py), in a line of its own
# --------------------------------------------------------------------------- #
_LOOKS = (
    (re.compile(r"^Confirmed on a second capture: .*$"),
     "the second picture is the same as the first, so the change is real, not motion"),
    (re.compile(r"^A second look was taken: (?P<what>.*)\. A later frame matched the "
                r"baseline, .*$"),
     "{what}; a later picture matched the baseline, but the page had not finished "
     "changing, so the check still fails"),
    (re.compile(r"^A second look was taken: (?P<what>.*)\. The verdict is from the later "
                r"frame, and the difference remains\.$"),
     "{what}; the later picture differs too"),
    (re.compile(r"^Failed on the first capture and passed on a later one: (?P<what>.*)\. "
                r"The verdict is .*$"),
     "{what}; the first picture failed and a later one passed — the check ran before the "
     "page was done, worth fixing in the test"),
    (re.compile(r"^The page was not ready(?: \((?P<what>.*)\))?: .*$"),
     "none: the page was not ready{why}, so nothing was masked and the first picture "
     "decided"),
    (re.compile(r"^Could not take a second capture \((?P<what>.*)\) — .*$"),
     "none: a second picture could not be taken ({what}); the first one decided"),
)
_LIVES = re.compile(r"^(?P<pct>[\d.]+% of the page) kept changing after it was ready — .*$")


def look(note: str) -> str:
    """The second look's note, without the words its label already says."""
    for pattern, words in _LOOKS:
        m = pattern.match(note.strip())
        if not m:
            continue
        what = m.groupdict().get("what") or ""
        lives = _LIVES.match(what)
        if lives:
            what = (f"{lives.group('pct')} kept changing after it was ready, as a counter "
                    "or an endless animation does")
        return words.format(what=what, why=f" ({what})" if what else "")
    return note


# --------------------------------------------------------------------------- #
#  Lines
# --------------------------------------------------------------------------- #
_OPEN, _CLOSE = "([{", ")]}"


def _words(text: str) -> list[str]:
    """Split on spaces, but never inside brackets — `mask=["#a", "#b"]` is one —
    nor around an arrow: «#e5e7eb → #000000» is one."""
    out: list[str] = []
    depth = 0
    glue = False
    for word in text.split(" "):
        if (depth > 0 or glue or word == "→") and out:
            out[-1] += " " + word
        else:
            out.append(word)
        glue = word == "→"
        depth += sum(word.count(c) for c in _OPEN) - sum(word.count(c) for c in _CLOSE)
        depth = max(depth, 0)
    return [w for w in out if w]


def wrap(text: str, first: str, rest: str, width: int = WIDTH) -> list[str]:
    """`text` after `first`, continued after `rest`, in lines of at most `width`.

    A word longer than the line — a path — stays whole on a line of its own.
    """
    lines: list[str] = []
    line = first
    fresh = True
    for word in _words(text):
        if fresh:
            line += word
            fresh = False
        elif len(line) + 1 + len(word) <= width:
            line += " " + word
        else:
            lines.append(line)
            line = rest + word
    lines.append(line)
    return lines


def labelled(label: str, text: str, width: int = WIDTH) -> list[str]:
    """'  reason:      …' with the continuation under the text."""
    first = f"  {label + ':' if label else '':<{COLUMN - 2}}"
    return wrap(text, first, " " * COLUMN, width)


def command(label: str, text: str) -> list[str]:
    """A command to run, on one line however long: it is copied, not read."""
    return [f"  {label + ':' if label else '':<{COLUMN - 2}}{text}"]


def joined(parts: list[str], first: str = "  ", rest: str = "    ",
           width: int = WIDTH) -> list[str]:
    """'a · b · c', broken between the parts — never inside one — where it is long."""
    lines: list[str] = []
    line = first
    for i, part in enumerate(parts):
        piece = part if i == 0 else f" · {part}"
        if i and len(line) + len(piece) > width:
            lines.append(line)
            line = f"{rest}· {part}"
        else:
            line += piece
    lines.append(line)
    out: list[str] = []
    for y in lines:
        if len(y) <= width:
            out.append(y)
            continue
        text = y.lstrip(" ")
        out += wrap(text, y[:len(y) - len(text)], rest + "  ", width)
    return out
