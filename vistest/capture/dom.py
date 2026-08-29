# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""DOM-снепшот: геометрия + идентификация каждого видимого элемента.

Снимается одновременно со скриншотом. Даёт две вещи, которых нет у чистого
пиксельного сравнения:

  1. Attribution — регион диффа можно назвать по имени:
     «изменился button[data-testid=checkout]», а не «прямоугольник (412,880)».
  2. Семантику для приоритизации: изменение внутри <nav> и внутри
     .cookie-banner — события разной важности.

Координаты — в device-пикселях, в системе документа, то есть ровно те же,
что и у full_page-скриншота.
"""

from __future__ import annotations

import json
from pathlib import Path

SNAPSHOT_JS = """
(dpr) => {
  const MAX_NODES = 4000;
  const out = [];

  const cssPath = (el) => {
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && parts.length < 6) {
      let part = node.tagName.toLowerCase();
      if (node.id) { parts.unshift(part + '#' + node.id); break; }
      const testid = node.getAttribute('data-testid') || node.getAttribute('data-test');
      if (testid) { parts.unshift(part + '[data-testid="' + testid + '"]'); break; }
      const cls = (node.getAttribute('class') || '')
        .split(/\\s+/).filter(c => c && !/^\\d/.test(c) && c.length < 30).slice(0, 2);
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

  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
  let n = 0;
  while (walker.nextNode() && n < MAX_NODES) {
    const el = walker.currentNode;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none' || cs.opacity === '0') continue;
    n++;

    let text = '';
    for (const c of el.childNodes) {
      if (c.nodeType === 3) text += c.textContent;
    }
    text = text.trim().replace(/\\s+/g, ' ').slice(0, 80);

    out.push({
      x: Math.round((r.x + window.scrollX) * dpr),
      y: Math.round((r.y + window.scrollY) * dpr),
      w: Math.round(r.width * dpr),
      h: Math.round(r.height * dpr),
      tag: el.tagName.toLowerCase(),
      id: el.id || null,
      testid: el.getAttribute('data-testid') || el.getAttribute('data-test') || null,
      role: el.getAttribute('role') || null,
      aria: el.getAttribute('aria-label') || null,
      cls: (el.getAttribute('class') || '').slice(0, 120) || null,
      text: text || null,
      selector: cssPath(el),
      depth: (() => { let d = 0, p = el; while ((p = p.parentElement)) d++; return d; })(),
      interactive: ['a', 'button', 'input', 'select', 'textarea'].includes(
        el.tagName.toLowerCase()) || !!el.onclick,
    });
  }

  return {
    dpr: dpr,
    scrollWidth: document.documentElement.scrollWidth,
    scrollHeight: document.documentElement.scrollHeight,
    url: location.href,
    nodes: out,
  };
}
"""


def snapshot(page, device_scale: float = 1.0) -> dict:
    try:
        return page.evaluate(SNAPSHOT_JS, device_scale)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "nodes": []}


def save(path: str | Path, data: dict) -> str:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(p)


def load(path: str | Path) -> dict | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
