#!/usr/bin/env python3
"""The full cloud hourly cycle -- runs standalone on GitHub Actions, no Mac,
no Claude needed per run.
  1. Reuse the saved ERP session if alive; otherwise auto-login (fresh OTP).
  2. Extract the full notice board via a real headless browser (Playwright).
  3. Diff against previously-seen notice IDs (state committed back to the repo).
  4. Rebuild all 4 PDF documents with the latest data (committed back too).
  5. ALWAYS send a WhatsApp message this hour -- either what's new, or an
     explicit "no update" heartbeat, per spec.
"""
import datetime, json, os, sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
import login, build_docs, wa_cloud
from extract_notices_playwright import fetch as extract_notices

SESSION_FILE = BASE / "session_cookie.txt"
SEEN_FILE = BASE / "seen_notice_ids.json"
CACHE = BASE / "notices_cache.json"


def log(msg):
    print(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} | {msg}")


def session_alive(cookie):
    """Checks the TrainingPlacementSSO subsystem directly -- it has its own,
    shorter-lived session cookie independent of the main ERP session."""
    if not cookie:
        return False
    try:
        import requests
        s = requests.Session()
        s.headers.update({"Cookie": cookie, "User-Agent": "Mozilla/5.0"})
        r = s.get("https://erp.iitkgp.ac.in/TrainingPlacementSSO/Notice.jsp",
                  timeout=20, allow_redirects=True)
        return "CDC Notice" in r.text and "jqGrid" in r.text
    except Exception:
        return False


def get_session():
    saved = SESSION_FILE.read_text().strip() if SESSION_FILE.exists() else ""
    if session_alive(saved):
        log("session reused (still alive) -- no OTP needed")
        return saved
    log("session dead/absent -- logging in fresh (OTP via Gmail)")
    cookie = login.login(verbose=True)
    if cookie:
        SESSION_FILE.write_text(cookie)
        log("login succeeded")
    else:
        log("LOGIN FAILED")
    return cookie


def reflow(text):
    import re
    for m in ["Venue:", "Note:", "POC:", "Deadline:", "Important:", "Regards",
              "CDC, IIT Kharagpur", "CDC,IIT Kharagpur", "Link:", "Test link"]:
        text = text.replace(m, "\n\n" + m)
    text = re.sub(r"(\d{2}[A-Z][A-Z0-9]{6}) ?(?=\d)", r"\1\n", text)
    text = re.sub(r"(https?://\S+?)([A-Z][a-z]+ [a-z])", r"\1\n\n\2", text)
    text = re.sub(r"([.!?])(?=[A-Z])", r"\1\n\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def main():
    now_str = datetime.datetime.now().strftime("%d %b, %I:%M %p")
    cookie = get_session()
    if not cookie:
        wa_cloud.send(f"CDC watcher: login failed at {now_str} -- will retry next hour.")
        return

    log("extracting notice board via headless browser...")
    raw = extract_notices(cookie, headless=True)
    log(f"fetched {len(raw)} total notices")

    seen = set(json.loads(SEEN_FILE.read_text())) if SEEN_FILE.exists() else set()
    new_rows = [r for r in raw if r["id"] not in seen]

    cache_rows = [{
        "id": r["id"], "type": r["type"], "subject": r["subject"], "company": r["company"],
        "notice": reflow(r["notice"]), "noticeat": r["noticeat"],
        "download_raw": "<a href='#'>Download</a>" if r.get("hasDownload") else "",
    } for r in raw]
    CACHE.write_text(json.dumps(cache_rows, indent=1))
    build_docs.main()
    log(f"documents rebuilt ({len(cache_rows)} total rows)")

    SEEN_FILE.write_text(json.dumps(sorted({r["id"] for r in raw}, key=lambda x: int(x) if x.isdigit() else 0)))

    # ALWAYS send this hour -- either the real update, or an explicit heartbeat
    if new_rows:
        placement_new = [r for r in new_rows if r["type"] == "PLACEMENT" and r["subject"].upper() != "PPO"]
        internship_new = [r for r in new_rows if r["type"] == "INTERNSHIP"]
        ppo_new = [r for r in new_rows if r["subject"].upper() == "PPO"]
        lines = [f"CDC update -- {now_str}", f"{len(new_rows)} new notice(s):"]
        for r in placement_new[:6]:
            lines.append(f"• [PLACEMENT] {r['company']} ({r['subject']})")
        for r in internship_new[:6]:
            lines.append(f"• [INTERNSHIP] {r['company']} ({r['subject']})")
        if ppo_new:
            lines.append(f"+ {len(ppo_new)} PPO notice(s)")
        msg = "\n".join(lines)
    else:
        msg = f"No update for now at {now_str}."
    wa_cloud.send(msg)
    log(f"WhatsApp sent: {'NEW ' + str(len(new_rows)) if new_rows else 'no-update heartbeat'}")


if __name__ == "__main__":
    main()
