BASE_CSS = {
 "--primary": "#2563eb", "--link": "#2563eb", "--text": "#111827", "--muted": "#6b7280",
 "--h-weight": "600", "--btn-pad": "8px 16px", "--card-border": "1px solid #e5e7eb",
 "--radius": "6px", "--badge-bg": "#dcfce7", "--badge-fg": "#166534", "--card-opacity": "1",
 "--nav-ls": "0px", "--link-deco": "none", "--card-shadow": "0 1px 3px rgba(0,0,0,.12)",
 "--stripe": "#f9fafb", "--error": "#dc2626", "--p-lh": "1.5", "--p-size": "14px",
 "--shift": "translate(0px,0px)", "--font": "'DejaVu Sans', Arial, sans-serif",
}
PRICE = "$49.00"; PLACEHOLDER = "Search orders"; CHEVRON = "M9 6l6 6-6 6"

def html(css=None, price=PRICE, placeholder=PLACEHOLDER, chevron=CHEVRON, extra_css=""):
    v = dict(BASE_CSS); v.update(css or {})
    root = ";".join(f"{k}:{val}" for k, val in v.items())
    rows = "".join(
        f"<tr><td>#{1000+i}</td><td>Customer {i}</td><td>{['Paid','Pending','Refunded'][i%3]}</td>"
        f"<td class=num>${(i*13)%97+10}.00</td></tr>" for i in range(8))
    return f"""<!doctype html><html><head><meta charset=utf-8><style>
:root{{{root}}}
*{{box-sizing:border-box}} body{{margin:0;font-family:var(--font);color:var(--text);background:#fff;font-size:14px}}
.wrap{{transform:var(--shift)}}
header{{display:flex;align-items:center;gap:24px;padding:14px 24px;border-bottom:1px solid #e5e7eb}}
header b{{font-size:18px}} nav a{{margin-right:16px;color:var(--muted);text-decoration:none;letter-spacing:var(--nav-ls)}}
main{{display:grid;grid-template-columns:2fr 1fr;gap:24px;padding:24px}}
.card{{border:var(--card-border);border-radius:var(--radius);padding:16px;box-shadow:var(--card-shadow);opacity:var(--card-opacity)}}
h1{{font-size:22px;font-weight:var(--h-weight);margin:0 0 8px}} h2{{font-size:16px;font-weight:var(--h-weight);margin:0 0 12px}}
p{{line-height:var(--p-lh);font-size:var(--p-size);color:var(--text);margin:0 0 12px}}
a.link{{color:var(--link);text-decoration:var(--link-deco)}}
.btn{{background:var(--primary);color:#fff;border:0;border-radius:var(--radius);padding:var(--btn-pad);font-size:14px;font-family:inherit}}
.btn.ghost{{background:#fff;color:var(--text);border:1px solid #d1d5db}}
.badge{{background:var(--badge-bg);color:var(--badge-fg);border-radius:999px;padding:2px 8px;font-size:12px}}
table{{width:100%;border-collapse:collapse}} td{{padding:8px;border-bottom:1px solid #f3f4f6}} tr:nth-child(even) td{{background:var(--stripe)}}
td.num{{text-align:right}} .price{{font-size:28px;font-weight:700}} .err{{color:var(--error);font-size:13px}}
input{{width:100%;padding:8px;border:1px solid #d1d5db;border-radius:var(--radius);font-family:inherit;font-size:14px}}
{extra_css}
</style></head><body><div class=wrap>
<header><b>Acme Admin</b><nav><a>Dashboard</a><a>Orders</a><a>Customers</a><a>Reports</a><a>Settings</a></nav>
<span style="margin-left:auto" class=badge>Online</span></header>
<main><section class=card><h1>Orders</h1>
<p>Review and manage recent orders. Orders marked as <a class=link>pending</a> need confirmation within 24 hours, otherwise they are cancelled automatically.</p>
<div style="display:flex;gap:8px;margin-bottom:12px"><input placeholder="{placeholder}"><button class=btn>Export</button><button class="btn ghost">Filter</button></div>
<table>{rows}</table></section>
<aside class=card><h2>Plan</h2><div class=price>{price}</div><p style="color:var(--muted)">per month, billed annually</p>
<button class=btn style="width:100%">Upgrade plan</button>
<p class=err style="margin-top:12px">Payment method expires soon.</p>
<div style="display:flex;align-items:center;gap:6px"><span class=badge>Active</span>
<svg width=16 height=16 viewBox="0 0 24 24" fill=none stroke="#6b7280" stroke-width=2><path d="{chevron}"/></svg></div>
</aside></main></div></body></html>"""
