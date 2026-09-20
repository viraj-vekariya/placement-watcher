#!/usr/bin/env python3
"""Fetches the ERP "Download" attachment of PLACEMENT notices into docs/files/.

The CDC Notice grid's Download link calls TPNotice(year, id), which just opens
TrainingPlacementSSO/AdmFilePDF.htm?type=NOTICE&year=<year>&id=<id> in an
iframe. As of 20 Sep 2026 that endpoint answers 200 / application/pdf /
Content-Length: 0 for every notice (auth IS accepted -- with no session it
302s to login), so nothing can be saved yet. A file is only stored when real
bytes come back, so this starts working by itself if the ERP ever serves them.
"""
import os
from pathlib import Path
import requests

BASE = Path(__file__).resolve().parent
FILES = BASE / "docs" / "files"
YEAR = os.environ.get("ERP_YEAR", "2026-2027")
URL = "https://erp.iitkgp.ac.in/TrainingPlacementSSO/AdmFilePDF.htm?type=NOTICE&year={year}&id={id}"


def fetch_one(cookie, notice_id):
    """Returns the file bytes, or None if the ERP returned nothing usable."""
    try:
        r = requests.get(URL.format(year=YEAR, id=notice_id), timeout=40,
                         headers={"Cookie": cookie, "User-Agent": "Mozilla/5.0"})
    except Exception:
        return None
    if r.status_code != 200 or not r.content or r.content.lstrip()[:1] == b"<":
        return None
    return r.content


def sync(cookie, rows):
    """rows: placement rows (dicts with id/download_raw). Saves any attachment
    not already on disk. Returns {id: Path} for every id that now has a file."""
    FILES.mkdir(parents=True, exist_ok=True)
    have = {}
    for r in rows:
        if not (r.get("download_raw") or "").strip():
            continue
        dest = FILES / f"{r['id']}.pdf"
        if dest.exists() and dest.stat().st_size > 0:
            have[r["id"]] = dest
            continue
        data = fetch_one(cookie, r["id"])
        if data:
            dest.write_bytes(data)
            have[r["id"]] = dest
    return have
