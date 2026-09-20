#!/usr/bin/env python3
"""Downloads the ERP "Download" attachment of PLACEMENT notices into docs/files/.

Why a browser: the grid's Download link calls TPNotice(year, id), which loads
TrainingPlacementSSO/AdmFilePDF.htm?type=NOTICE&year=..&id=.. But that endpoint
only serves real bytes when the session entered the placement module through
the ERP's own menu (IIT_ERP3 menulist -> showMenu(... TPStudent.jsp) ->
Notice). Opening Notice.jsp directly, or GETting AdmFilePDF.htm with a plain
HTTP client, gets 200 / application/pdf / Content-Length: 0 for every id.
So this replays the menu path in headless Chromium and uses the real download.
"""
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = Path(__file__).resolve().parent
FILES = BASE / "docs" / "files"
YEAR = os.environ.get("ERP_YEAR", "2026-2027")
MENU = "https://erp.iitkgp.ac.in/IIT_ERP3/menulist.htm?module_id=26"
ENTER = ("showMenu('26','11','https://erp.iitkgp.ac.in/TrainingPlacementSSO/TPStudent.jsp',"
         "'','Y','CDC','Student','Application of Placement/Internship')")
MAX_PER_RUN = 25


def _cookie_dicts(cookie_str):
    out = []
    for part in cookie_str.split(";"):
        if "=" in part:
            n, v = part.split("=", 1)
            out.append({"name": n.strip(), "value": v.strip(),
                        "domain": "erp.iitkgp.ac.in", "path": "/"})
    return out


def _download_all(cookie, ids, log):
    got = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(accept_downloads=True, viewport={"width": 1400, "height": 900})
        ctx.add_cookies(_cookie_dicts(cookie))
        page = ctx.new_page()
        page.goto(MENU, timeout=60000)
        page.wait_for_timeout(2000)
        page.evaluate(ENTER)
        page.wait_for_timeout(6000)
        tp = [f for f in page.frames if "TPStudent" in f.url]
        if not tp:
            log("attachments: could not enter the placement module")
            browser.close()
            return got
        tp[0].click('a[href="Notice.jsp"]')
        page.wait_for_timeout(6000)
        nf = [f for f in page.frames if "Notice.jsp" in f.url]
        if not nf:
            log("attachments: Notice frame never opened")
            browser.close()
            return got
        nf = nf[0]
        nf.wait_for_function(
            "()=>{const t=window.jQuery&&window.jQuery('table.ui-jqgrid-btable').first();"
            "return t&&t.length&&t.jqGrid('getGridParam','records')>0;}", timeout=60000)
        for nid in ids:
            try:
                with page.expect_download(timeout=20000) as d:
                    nf.evaluate(f"TPNotice('{YEAR}','{nid}')")
                tmp = FILES / f".{nid}.part"
                d.value.save_as(str(tmp))
                data = tmp.read_bytes()
                tmp.unlink(missing_ok=True)
                if data[:4] == b"%PDF":
                    got[nid] = data
                else:
                    log(f"attachments: notice {nid} returned {len(data)} bytes, not a PDF -- skipped")
            except Exception as e:
                log(f"attachments: notice {nid} download failed: {str(e)[:90]}")
            page.wait_for_timeout(800)
        browser.close()
    return got


def sync(cookie, rows, log=print):
    """rows: placement rows (dicts with id/download_raw). Downloads every
    attachment not already on disk. Returns {id: Path} for ids that have a file."""
    FILES.mkdir(parents=True, exist_ok=True)
    have, todo = {}, []
    for r in rows:
        if not (r.get("download_raw") or "").strip():
            continue
        dest = FILES / f"{r['id']}.pdf"
        if dest.exists() and dest.stat().st_size > 0:
            have[r["id"]] = dest
        else:
            todo.append(r["id"])
    if todo:
        try:
            for nid, data in _download_all(cookie, todo[:MAX_PER_RUN], log).items():
                dest = FILES / f"{nid}.pdf"
                dest.write_bytes(data)
                have[nid] = dest
        except Exception as e:
            log(f"attachments: download pass failed (non-fatal): {str(e)[:120]}")
    return have
