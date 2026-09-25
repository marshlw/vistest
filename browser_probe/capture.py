"""Capture a baseline, 9 rendering variants (noise) and 22 CSS mutations (signal)
of one admin page in Chromium. Probe for stage R1, not a benchmark.

    python capture.py      # writes shots/*.png and shots/meta.json
"""
import sys, json, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from page import html
from playwright.sync_api import sync_playwright
OUT=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'shots'); os.makedirs(OUT, exist_ok=True)
VP={"width":1280,"height":720}

NOISE = {
 "N0 re-render, fresh browser": dict(),
 "N1 font hinting none": dict(args=["--font-render-hinting=none"]),
 "N2 font hinting full": dict(args=["--font-render-hinting=full"]),
 "N3 no LCD text / no subpixel pos": dict(args=["--disable-lcd-text","--disable-font-subpixel-positioning"]),
 "N4 whole page +0.4px,+0.3px": dict(css={"--shift":"translate(0.4px,0.3px)"}),
 "N5 whole page +1px": dict(css={"--shift":"translate(1px,0px)"}),
 "N6 text-rendering geometricPrecision": dict(extra_css="body{text-rendering:geometricPrecision}"),
 "N7 headless=new (full chromium)": dict(channel="chromium"),
 "N8 gpu rasterization forced": dict(args=["--enable-gpu-rasterization","--force-gpu-rasterization"]),
}
SIGNAL = {
 "S1 button #2563eb->#1d4ed8": dict(css={"--primary":"#1d4ed8"}),
 "S2 button #2563eb->#3b82f6": dict(css={"--primary":"#3b82f6"}),
 "S3 link colour blue->violet": dict(css={"--link":"#7c3aed"}),
 "S4 body text #111827->#374151": dict(css={"--text":"#374151"}),
 "S5 price 49.00->48.00": dict(price="$48.00"),
 "S6 heading weight 600->700": dict(css={"--h-weight":"700"}),
 "S7 button padding 8->10px": dict(css={"--btn-pad":"10px 16px"}),
 "S8 card border removed": dict(css={"--card-border":"1px solid transparent"}),
 "S9 radius 6->10px": dict(css={"--radius":"10px"}),
 "S10 badge green->yellow": dict(css={"--badge-bg":"#fef9c3","--badge-fg":"#854d0e"}),
 "S11 card opacity 0.85": dict(css={"--card-opacity":"0.85"}),
 "S12 nav letter-spacing +0.3px": dict(css={"--nav-ls":"0.3px"}),
 "S13 chevron icon swapped": dict(chevron="M6 9l6 6 6-6"),
 "S14 placeholder text": dict(placeholder="Search order"),
 "S15 link underlined": dict(css={"--link-deco":"underline"}),
 "S16 card shadow removed": dict(css={"--card-shadow":"none"}),
 "S17 row stripe #f9fafb->#f3f4f6": dict(css={"--stripe":"#f3f4f6"}),
 "S18 error red-600->red-500": dict(css={"--error":"#ef4444"}),
 "S19 paragraph line-height 1.5->1.6": dict(css={"--p-lh":"1.6"}),
 "S20 paragraph 14->15px": dict(css={"--p-size":"15px"}),
 "S21 muted text #6b7280->#9ca3af": dict(css={"--muted":"#9ca3af"}),
 "S22 text colour black->dark red": dict(css={"--text":"#7f1d1d"}),
}

def shoot(p, spec, path):
    args=["--no-sandbox"]+spec.get("args",[])
    kw={}
    if spec.get("channel"): kw["channel"]=spec["channel"]
    b=p.chromium.launch(args=args, **kw)
    pg=b.new_page(viewport=VP, device_scale_factor=1)
    pg.set_content(html(css=spec.get("css"), price=spec.get("price","$49.00"),
                        placeholder=spec.get("placeholder","Search orders"),
                        chevron=spec.get("chevron","M9 6l6 6-6 6"), extra_css=spec.get("extra_css","")))
    pg.wait_for_timeout(100)
    pg.screenshot(path=path, animations="disabled", caret="hide")
    b.close()

with sync_playwright() as p:
    shoot(p, {}, f"{OUT}/base.png")
    meta={}
    for group, d in (("NOISE",NOISE),("SIGNAL",SIGNAL)):
        for name, spec in d.items():
            fn=f"{OUT}/{name.split()[0]}.png"
            try:
                shoot(p, spec, fn); meta[name]={"group":group,"file":fn}
            except Exception as e:
                print("skip", name, e)
    json.dump(meta, open(f"{OUT}/meta.json","w"), indent=1)
print("done")
