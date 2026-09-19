#!/usr/bin/env python3
"""Builds 4 documents from the cached notice fetch (notices_cache.json):
  1. ALL_NOTICES        -- every row fetched, as-is
  2. PLACEMENT_ONLY     -- type == PLACEMENT
  3. PLACEMENT_NO_PPO   -- type == PLACEMENT and subject != PPO (the agreed filter)
  4. TOP10_INTERNSHIPS  -- 10 most recent INTERNSHIP notices, with any resolvable
                           download link/file noted
Each is written as a styled HTML file, then exported to PDF via Playwright's
bundled Chromium (page.pdf()) -- this works identically on macOS (local) and
the Ubuntu GitHub Actions runner, unlike shelling out to a hardcoded
/Applications/Google Chrome.app path which only exists locally.
"""
import html, json, re, sys
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = Path(__file__).resolve().parent
CACHE = BASE / "notices_cache.json"
DOCS = BASE / "docs"
DOCS.mkdir(exist_ok=True)

CSS = """
body{font:14px/1.5 -apple-system,Helvetica,Arial,sans-serif;color:#1a1a1a;margin:0;padding:28px 34px;background:#fff}
h1{font-size:19px;margin:0 0 4px}
.meta{color:#666;font-size:12px;margin-bottom:18px}
.row{border:1px solid #ddd;border-radius:8px;padding:12px 14px;margin-bottom:10px;page-break-inside:avoid}
.hd{display:flex;justify-content:space-between;gap:10px;font-size:12.5px;color:#444;margin-bottom:6px}
.tag{font-weight:700;padding:1px 7px;border-radius:5px;font-size:11px;text-transform:uppercase}
.tag.PLACEMENT{background:#e6f7ee;color:#0b7a3e}
.tag.INTERNSHIP{background:#eaf0ff;color:#1a4fd6}
.co{font-weight:700;font-size:13.5px}
.subj{color:#7a4b00;font-weight:600}
.body{white-space:pre-wrap;font-size:13px;color:#222;margin-top:6px}
.dl{margin-top:8px;font-size:12px;color:#0b5fff}
.idx{color:#999;font-size:11px}
"""

def esc(s): return html.escape(str(s or ""))

def load():
    if not CACHE.exists():
        sys.exit(f"No cache found at {CACHE}. Run fetch_and_cache.py first.")
    return json.loads(CACHE.read_text())

def row_html(r, idx, show_download=False):
    tag = r["type"] if r["type"] in ("PLACEMENT", "INTERNSHIP") else "OTHER"
    dl = ""
    if show_download:
        has_file = bool((r.get("download_raw") or "").strip())
        dl = (f'<div class="dl">📎 A file is attached to this notice on the ERP -- open it via the '
              f'"Download" button on the CDC Notice board (id {esc(r["id"])}).</div>') if has_file else \
             '<div class="dl">📎 No file attached to this notice.</div>'
    return f'''<div class="row">
      <div class="hd"><span class="idx">#{esc(idx)} · id {esc(r["id"])}</span>
        <span><span class="tag {tag}">{esc(r["type"])}</span> <span class="subj">{esc(r["subject"])}</span></span>
        <span>{esc(r["noticeat"])}</span></div>
      <div class="co">{esc(r["company"]) or "(no company / general notice)"}</div>
      <div class="body">{esc(r["notice"])}</div>
      {dl}
    </div>'''

def extract_download_link(raw_html):
    """Pull a href or onclick(id) pattern out of the raw download cell, if any."""
    if not raw_html:
        return None
    m = re.search(r'href=[\'"]([^\'"]+)[\'"]', raw_html)
    if m and m.group(1) not in ("#", "javascript:void(0)"):
        return m.group(1)
    m = re.search(r'onclick=[\'"]?\s*(\w+)\(([^)]*)\)', raw_html)
    if m:
        return f"{m.group(1)}({m.group(2)})  [JS action -- see notes]"
    return None

def page(title, subtitle, rows_html):
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{esc(title)}</title>
<style>{CSS}</style></head><body>
<h1>{esc(title)}</h1><div class="meta">{esc(subtitle)}</div>
{rows_html}
</body></html>"""

def write_and_pdf(browser_page, name, title, subtitle, rows, show_download=False):
    body = "\n".join(row_html(r, i+1, show_download) for i, r in enumerate(rows)) or "<p>No matching notices.</p>"
    html_path = DOCS / f"{name}.html"
    pdf_path = DOCS / f"{name}.pdf"
    html_path.write_text(page(title, subtitle, body))
    browser_page.goto(f"file://{html_path}")
    browser_page.pdf(path=str(pdf_path))
    print(f"  wrote {pdf_path.name}  ({len(rows)} rows)")

def write_json(rows):
    """Writes docs/notices.json -- the data source for the website dashboard.
    Same categorization rules as the 4 PDFs, kept in this one place so the
    site and the documents can never drift apart."""
    import datetime
    def slim(r, show_download=False):
        out = {"id": r["id"], "type": r["type"], "subject": r["subject"],
               "company": r["company"], "notice": r["notice"], "noticeat": r["noticeat"]}
        if show_download:
            out["has_download"] = bool((r.get("download_raw") or "").strip())
        return out

    placement = [r for r in rows if r["type"] == "PLACEMENT"]
    placement_no_ppo = [r for r in placement if r["subject"].upper() != "PPO"]
    internships = [r for r in rows if r["type"] == "INTERNSHIP"]
    internships_sorted = sorted(internships, key=lambda r: int(r["id"]) if str(r["id"]).isdigit() else 0, reverse=True)
    top10 = internships_sorted[:10]

    data = {
        "last_updated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stats": {
            "total": len(rows), "internship": len(internships), "placement": len(placement),
            "placement_no_ppo": len(placement_no_ppo),
        },
        "categories": {
            "all": [slim(r) for r in sorted(rows, key=lambda r: int(r["id"]) if str(r["id"]).isdigit() else 0, reverse=True)],
            "placement": [slim(r) for r in sorted(placement, key=lambda r: int(r["id"]) if str(r["id"]).isdigit() else 0, reverse=True)],
            "placement_no_ppo": [slim(r) for r in sorted(placement_no_ppo, key=lambda r: int(r["id"]) if str(r["id"]).isdigit() else 0, reverse=True)],
            "top10_internships": [slim(r, show_download=True) for r in top10],
        },
    }
    (DOCS / "notices.json").write_text(json.dumps(data, indent=1))
    print(f"  wrote notices.json ({len(rows)} total rows)")

def main():
    rows = load()
    print(f"loaded {len(rows)} cached rows\n")

    placement = [r for r in rows if r["type"] == "PLACEMENT"]
    placement_no_ppo = [r for r in placement if r["subject"].upper() != "PPO"]
    internships = [r for r in rows if r["type"] == "INTERNSHIP"]
    internships_sorted = sorted(internships, key=lambda r: int(r["id"]) if str(r["id"]).isdigit() else 0, reverse=True)[:10]

    with sync_playwright() as p:
        browser = p.chromium.launch()
        browser_page = browser.new_page()
        write_and_pdf(browser_page, "1_ALL_NOTICES", "All CDC Notices", f"Every notice fetched from the ERP notice board -- {len(rows)} rows.", rows)
        write_and_pdf(browser_page, "2_PLACEMENT_ONLY", "Placement Notices (all subjects)", f"Type = PLACEMENT only -- {len(placement)} rows.", placement)
        write_and_pdf(browser_page, "3_PLACEMENT_NO_PPO", "Placement Notices (excluding PPO)", f"Type = PLACEMENT, Subject != PPO -- {len(placement_no_ppo)} rows.", placement_no_ppo)
        write_and_pdf(browser_page, "4_TOP10_INTERNSHIPS", "Top 10 Most Recent Internship Notices", "Most recent 10 INTERNSHIP notices, with download info where available.", internships_sorted, show_download=True)
        browser.close()

    write_json(rows)

    print("\nAll 4 documents + notices.json built in ./docs/")

if __name__ == "__main__":
    main()
