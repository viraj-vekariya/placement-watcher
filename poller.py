#!/usr/bin/env python3
"""Polls the IIT KGP CDC placement grid + notices via the logged-in cookie (plain HTTP)."""
import os, urllib.request, xml.etree.ElementTree as ET

BASE = "https://erp.iitkgp.ac.in/TrainingPlacementSSO"
COOKIE = os.environ.get("ERP_COOKIE", "")
COMPANY_COLS = ["companyname","view2","view3","designation","description","ctc","Currency",
                "view1","apply","resumedeadline_st","resumedeadline","interview_date_confirmed","contract","view"]

def _get(url):
    req = urllib.request.Request(url, headers={
        "Cookie": COOKIE, "User-Agent": "Mozilla/5.0", "Referer": f"{BASE}/TPStudent.jsp"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "ignore")

def session_alive():
    """True if the cookie still authenticates (home shows the welcome, not a login page)."""
    try:
        html = _get("https://erp.iitkgp.ac.in/IIT_ERP3/home.htm")
        return "loginForm" not in html and "Welcome" in html
    except Exception:
        return False

def keepalive():
    try: _get(f"{BASE}/ERPMonitoring.htm?action=fetchData&jqqueryid=37"); return True
    except Exception: return False

def _rows(jqqueryid):
    xml = _get(f"{BASE}/ERPMonitoring.htm?action=fetchData&jqqueryid={jqqueryid}")
    try: root = ET.fromstring(xml)
    except ET.ParseError: return []
    out = []
    for row in root.findall("row"):
        cells = [(c.text or "").strip() for c in row.findall("cell")]
        out.append({"id": row.get("id",""), "cells": cells})
    return out

def companies():
    res = []
    for r in _rows(37):
        c = r["cells"]
        d = {COMPANY_COLS[i]: (c[i] if i < len(c) else "") for i in range(len(COMPANY_COLS))}
        res.append({
            "id": r["id"], "company": _clean(d["companyname"]), "role": _clean(d["designation"]),
            "jd": _clean(d["description"]), "ctc": _clean(d["ctc"]), "currency": _clean(d["Currency"]),
            "deadline": _clean(d["resumedeadline"]) or _clean(d["resumedeadline_st"]),
        })
    return res

def notices():
    res = []
    for r in _rows(54):
        txt = " ".join(_clean(x) for x in r["cells"] if _clean(x))
        if txt: res.append({"id": r["id"], "text": txt})
    return res

import re
def _clean(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", s).strip()

if __name__ == "__main__":
    print("session_alive:", session_alive())
    print("companies:", len(companies()), "| notices:", len(notices()))
