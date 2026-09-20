#!/usr/bin/env python3
"""The full cloud hourly cycle -- runs standalone on GitHub Actions, no Mac,
no Claude needed per run.
  1. Reuse the saved ERP session if alive; otherwise auto-login (fresh OTP).
  2. Extract the full notice board via a real headless browser (Playwright).
  3. Diff against previously-seen notice IDs (state committed back to the repo).
  4. Rebuild all 4 PDF documents + docs/notices.json with the latest data,
     and commit them back so the GitHub Pages dashboard picks it up.
  5. ALWAYS send a WhatsApp message this hour -- either what's new, or an
     explicit "no update" heartbeat. Best-effort: a WhatsApp hiccup never
     stops the docs/website from being rebuilt and committed.
"""
import datetime, json, os, sys
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
import login, build_docs, wa_cloud, attachments, gate
from extract_notices_playwright import fetch as extract_notices

SESSION_FILE = BASE / "session_cookie.txt"
SEEN_FILE = BASE / "seen_notice_ids.json"
CACHE = BASE / "notices_cache.json"
WA_GROUP = os.environ.get("WA_GROUP_NAME", "CDC Updates")  # dedicated group,
                                                            # keeps placement
                                                            # noise out of the
                                                            # personal self-DM
SITE_URL = "https://viraj-vekariya.github.io/placement-watcher/"
IST = ZoneInfo("Asia/Kolkata")  # the GitHub Actions runner's clock is UTC --
                                # every timestamp must convert explicitly or
                                # messages silently show UTC as if it were IST


def log(msg):
    print(f"{datetime.datetime.now(IST):%Y-%m-%d %H:%M:%S} IST | {msg}")


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


def email_fallback(subject, message):
    """WhatsApp Web on a headless runner can fail in ways that look like
    success (see wa_cloud.send's docstring, 21 Sep 2026) -- when it now
    raises instead, this is the one channel that never depends on the
    flaky part: same Gmail app password already used for OTP retrieval,
    sent to the same inbox the user already checks every cycle for OTPs."""
    gmail, app_pw = os.environ.get("GMAIL", ""), os.environ.get("APP_PW", "")
    if not (gmail and app_pw):
        log("email fallback skipped -- GMAIL/APP_PW not set")
        return
    try:
        import smtplib
        from email.mime.text import MIMEText
        msg = MIMEText(message)
        msg["Subject"] = subject
        msg["From"] = gmail
        msg["To"] = gmail
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as s:
            s.starttls()
            s.login(gmail, app_pw)
            s.send_message(msg)
        log("fallback email sent (WhatsApp send failed)")
    except Exception as e:
        log(f"fallback email ALSO failed: {e}")


def notify_all(message):
    """Personal self-DM only -- the reliable, proven channel. The "CDC
    Updates" group send was tried as a second channel but is still flaky
    (WhatsApp-side sync glitches + UI timing issues), so it's been pulled
    back out of the automated path per explicit decision (20 Sep 2026) to
    not risk the one reliable channel while the group is still being
    debugged separately. wa_cloud.send_to_chat() stays available in
    wa_cloud.py for that ongoing debugging -- just not called from here
    until it's proven reliable.

    If WhatsApp itself fails (now verified, not just attempted -- see
    wa_cloud.send), that failure would otherwise be totally silent to the
    user (logged only in a GitHub Actions run log nobody is watching).
    Falls back to email so a WhatsApp outage is never a silent outage."""
    try:
        wa_cloud.send(message)
        log("WhatsApp sent to personal DM (delivery confirmed)")
    except Exception as e:
        log(f"WhatsApp personal-DM send failed: {e}")
        email_fallback("CDC watcher -- WhatsApp failed, here's the update", f"{message}\n\n(WhatsApp error: {e})")


def main():
    if not gate.run_is_due():
        log("skipping -- not due (a recent attempt/success exists; another trigger already handled this hour)")
        return
    gate.record_attempt()

    now_str = datetime.datetime.now(IST).strftime("%d %b, %I:%M %p IST")
    cookie = get_session()
    if not cookie:
        log(f"login failed at {now_str} -- will retry next hour.")
        notify_all(f"CDC watcher: login failed at {now_str} -- will retry next hour.")
        return

    log("extracting notice board via headless browser...")
    raw = extract_notices(cookie, headless=True)
    log(f"fetched {len(raw)} total notices")

    seen = set(json.loads(SEEN_FILE.read_text())) if SEEN_FILE.exists() else set()
    # placement notices only (internships deliberately dropped per request);
    # seen-tracking still covers every id so old internships never resurface.
    new_rows = [r for r in raw if r["id"] not in seen and r["type"] == "PLACEMENT"]

    cache_rows = [{
        "id": r["id"], "type": r["type"], "subject": r["subject"], "company": r["company"],
        "notice": reflow(r["notice"]), "noticeat": r["noticeat"],
        "download_raw": "<a href='#'>Download</a>" if r.get("hasDownload") else "",
    } for r in raw]
    CACHE.write_text(json.dumps(cache_rows, indent=1))
    placement_rows = [r for r in cache_rows if r["type"] == "PLACEMENT"]
    files = attachments.sync(cookie, placement_rows, log)
    log(f"attachments: {len(files)} file(s) captured, "
        f"{sum(1 for r in placement_rows if r['download_raw'])} placement notice(s) list a Download")
    build_docs.main()
    log(f"documents rebuilt ({len(cache_rows)} total rows)")

    SEEN_FILE.write_text(json.dumps(sorted({r["id"] for r in raw}, key=lambda x: int(x) if x.isdigit() else 0)))
    login.logout(cookie)
    log("ERP session closed")

    log(f"cycle complete -- {len(new_rows)} new placement notice(s) this hour" if new_rows else "cycle complete -- no new placement notices this hour")
    prev_run = gate.last_success()
    gate.record_success()

    # FULL notice text per new placement notice, then the ERP attachment (if
    # the ERP actually served one), always with the website link. When
    # nothing new: an explicit window line so the silence is visibly checked.
    if new_rows:
        for r in new_rows:
            header = f"[PLACEMENT] {r['company'] or '(no company / general notice)'} ({r['subject']})"
            body = reflow(r["notice"])
            listed = bool(r.get("hasDownload"))
            fpath = files.get(r["id"])
            if fpath:
                note = "\n\n\U0001F4CE Attachment sent below."
            elif listed:
                note = ("\n\n\U0001F4CE ERP lists an attachment for this notice but it could not "
                        "be downloaded automatically - open Download on the ERP notice board.")
            else:
                note = ""
            notify_all(f"{header}\n\n{body}{note}\n\n\U0001F310 {SITE_URL}")
            if fpath:
                try:
                    wa_cloud.send_file(fpath, caption=f"{r['company']} - attachment")
                    log(f"WhatsApp attachment sent for notice {r['id']}")
                except Exception as e:
                    log(f"WhatsApp attachment send failed for {r['id']} (non-fatal): {e}")
    else:
        end = datetime.datetime.now(IST).strftime("%I:%M %p")
        if prev_run:
            start = prev_run.astimezone(IST).strftime("%I:%M %p")
            notify_all(f"No CDC update from {start} to {end} IST.\n\n\U0001F310 {SITE_URL}")
        else:
            notify_all(f"No CDC update for now ({end} IST).\n\n\U0001F310 {SITE_URL}")


if __name__ == "__main__":
    main()
