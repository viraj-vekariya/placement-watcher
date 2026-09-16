#!/usr/bin/env python3
"""Dashboard generator: public Updates page + private CV Board page."""
import html, json
from pathlib import Path
BASE = Path(__file__).resolve().parent

CVS = {"CV1":{"label":"SDE / GenAI / ML"},"CV2":{"label":"Data Science / BA"},
       "CV3":{"label":"Product / Ops / Consulting"}}

CSS = """
:root{--bg:#0f1420;--card:#181f2e;--card2:#1e2740;--tx:#e7ecf5;--mut:#8a97b0;--line:#2a3550;
--green:#2ecc71;--amber:#f5b544;--red:#ff6b6b;--accent:#7aa2ff}
*{box-sizing:border-box}body{margin:0;background:linear-gradient(180deg,#0c1019,#0f1420);color:var(--tx);
font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:760px;margin:0 auto;padding:22px 16px 60px}
h1{font-size:20px;margin:0}.sub{color:var(--mut);font-size:13px;margin:2px 0 14px}
.banner{border-radius:12px;padding:10px 13px;font-size:13px;margin:0 0 16px;border:1px solid}
.ok{display:none}.down{background:rgba(255,107,107,.12);border-color:rgba(255,107,107,.45);color:#ffb3b3}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:15px 16px;margin-bottom:12px}
.row1{display:flex;align-items:baseline;justify-content:space-between;gap:10px}
.co{font-size:16px;font-weight:650}.role{color:var(--mut);font-size:13.5px;margin-top:1px}
.meta{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
.chip{font-size:12px;color:var(--mut);background:var(--card2);border:1px solid var(--line);border-radius:8px;padding:3px 8px}
.seen{color:var(--mut);font-size:12px;white-space:nowrap}
.badge{font-size:12px;font-weight:700;padding:4px 10px;border-radius:999px;white-space:nowrap}
.b-APPLY{background:rgba(46,204,113,.16);color:var(--green);border:1px solid rgba(46,204,113,.4)}
.b-APPLY_IF_TIME{background:rgba(245,181,68,.16);color:var(--amber);border:1px solid rgba(245,181,68,.4)}
.b-SKIP{background:rgba(255,107,107,.14);color:var(--red);border:1px solid rgba(255,107,107,.4)}
.why{margin:11px 0 0;font-size:13.5px;color:#cdd6ea}
.cvline{display:flex;align-items:center;gap:10px;margin-top:12px;flex-wrap:wrap}
.bestcv{font-weight:700;color:var(--accent)}
.scores{display:flex;gap:7px;flex-wrap:wrap;margin-top:9px}.sc{font-size:11.5px;color:var(--mut)}
.bar{display:inline-block;width:52px;height:6px;border-radius:4px;background:var(--card2);vertical-align:middle;margin:0 5px;overflow:hidden}
.bar>i{display:block;height:100%;background:var(--accent)}
.mg{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px}
.mg h4{margin:0 0 5px;font-size:11px;letter-spacing:.6px;text-transform:uppercase;color:var(--mut)}
.mg ul{margin:0;padding-left:16px}.mg li{font-size:12.5px;color:#c3cde3;margin-bottom:3px}
.gaps li{color:#c9b39a}.inelig{color:var(--red);font-size:12.5px;margin-top:8px}
.notice{font-size:13px;color:#cdd6ea;padding:9px 12px;background:var(--card);border:1px solid var(--line);border-radius:10px;margin-bottom:8px}
.sec{font-size:12px;text-transform:uppercase;letter-spacing:.7px;color:var(--mut);margin:22px 0 10px}
.empty{text-align:center;color:var(--mut);padding:44px 0}
footer{color:var(--mut);font-size:12px;text-align:center;margin-top:26px}
@media(max-width:520px){.mg{grid-template-columns:1fr}}
"""
def esc(s): return html.escape(str(s))
def shell(title, heading, sub, banner, body):
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)}</title>'
        f'<style>{CSS}</style></head><body><div class="wrap"><h1>{heading}</h1><div class="sub">{esc(sub)}</div>'
        f'{banner}{body}<footer>Auto-updated hourly · Gemini · free on the cloud</footer></div></body></html>')

def banner_html(alive, updated):
    if alive: return f'<div class="banner ok"></div>'
    return ('<div class="banner down">⚠️ ERP session expired — the feed is paused. '
            'Re-paste a fresh cookie to resume (see setup notes).</div>')

def notices_html(notices):
    if not notices: return ""
    items = "".join(f'<div class="notice">📌 {esc(n["text"])}</div>' for n in notices)
    return f'<div class="sec">Notices</div>{items}'

def public_page(companies, notices, alive, updated):
    cards = ""
    for c in companies:
        cards += (f'<div class="card"><div class="row1"><div><div class="co">{esc(c["company"])}</div>'
            f'<div class="role">{esc(c.get("role",""))}</div></div><div class="seen">{esc(c.get("seen",""))}</div></div>'
            f'<div class="meta"><span class="chip">💰 {esc(c.get("ctc","—") or "—")}</span>'
            f'<span class="chip">⏳ {esc(c.get("deadline","—") or "—")}</span></div></div>')
    if not cards and not notices:
        cards = '<div class="empty">No companies yet — the season hasn\'t opened.<br>This page updates automatically.</div>'
    body = notices_html(notices) + (f'<div class="sec">Companies</div>{cards}' if cards else "")
    return shell("Placement Updates","🎯 Placement Updates",
        f"IIT KGP CDC · live feed · updated {updated}", banner_html(alive,updated), body)

def board_page(companies, alive, updated):
    cards = ""
    for c in companies:
        if not c.get("verdict"):  # not triaged
            continue
        s = c.get("cv_scores",{"CV1":0,"CV2":0,"CV3":0}); best = c.get("best_cv","CV1")
        bars = "".join(f'<span class="sc">{k}<span class="bar"><i style="width:{int(s.get(k,0))*10}%"></i></span>{s.get(k,0)}</span>' for k in ("CV1","CV2","CV3"))
        mg = (f'<div class="mg"><div><h4>Matches</h4><ul>'+"".join(f"<li>{esc(x)}</li>" for x in c.get("key_matches",[]))+'</ul></div>'
              f'<div class="gaps"><h4>Gaps</h4><ul>'+"".join(f"<li>{esc(x)}</li>" for x in c.get("gaps",[]))+'</ul></div></div>')
        inelig = "" if c.get("eligible",True) else f'<div class="inelig">⛔ {esc(c.get("eligibility_note",""))}</div>'
        cards += (f'<div class="card"><div class="row1"><div><div class="co">{esc(c["company"])}</div>'
            f'<div class="role">{esc(c.get("role",""))}</div></div><span class="badge b-{c["verdict"]}">{c["verdict"].replace("_"," ")}</span></div>'
            f'<div class="cvline"><span class="bestcv">→ Apply with {esc(best)} · {esc(c.get("best_cv_label",""))}</span>'
            f'<span class="chip">chance: {esc(c.get("chance",""))}</span></div>'
            f'<div class="scores">{bars}</div><div class="why">{esc(c.get("reasoning",""))}</div>{inelig}{mg}</div>')
    if not cards:
        cards = '<div class="empty">No triaged companies yet — they appear here as the season opens.</div>'
    return shell("My CV Board","🧭 My CV Board",
        f"Private · which CV to apply with · updated {updated}", banner_html(alive,updated), cards)

def build_into(companies, notices, alive, updated, outdir, board_name="board.html"):
    outdir = Path(outdir)
    (outdir/"index.html").write_text(public_page(companies, notices, alive, updated))
    (outdir/board_name).write_text(board_page(companies, alive, updated))

if __name__ == "__main__":
    data = json.loads((BASE/"sample_data.json").read_text()) if (BASE/"sample_data.json").exists() else []
    Path(BASE/"public").mkdir(exist_ok=True)
    build_into(data, [], True, "now", BASE/"public")
    print("built into ./public/")
