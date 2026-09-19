#!/usr/bin/env python3
"""WhatsApp Web automation for a headless CI runner (GitHub Actions).

Two modes:
  setup  -- one-time: requests a "log in with phone number" linking code,
            prints it to the run log for the human to enter on their phone,
            waits for login to complete, then leaves the resulting session
            in WA_PROFILE_DIR for the caller to persist (encrypt + commit).
  send   -- sends a message to "Message yourself" using an already-linked
            session in WA_PROFILE_DIR.

The session is created and lives entirely on the runner -- never touches any
other machine. A modern User-Agent is required or WhatsApp Web refuses to
load at all ("update your Chrome"), even though the underlying engine is
perfectly current.
"""
import os, sys, time, urllib.parse
from pathlib import Path
from playwright.sync_api import sync_playwright

PROFILE_DIR = Path(os.environ.get("WA_PROFILE_DIR", "wa_profile"))
MY_NUMBER = os.environ.get("WA_NUMBER", "")  # E.164 without '+' (country code + number)
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")


def _launch(p, headless=True):
    return p.chromium.launch_persistent_context(str(PROFILE_DIR), headless=headless, user_agent=UA)


def is_logged_in(headless=True, timeout_ms=20000):
    with sync_playwright() as p:
        ctx = _launch(p, headless)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://web.whatsapp.com/", timeout=timeout_ms)
        try:
            page.wait_for_selector(
                '[aria-label="Search or start a new chat" i], [title="Search or start a new chat" i]',
                timeout=timeout_ms)
            ok = True
        except Exception:
            ok = page.locator("canvas").count() == 0 and "web.whatsapp.com" in page.url
        ctx.close()
        return ok


def _extract_code(full_text):
    """The 8-char code renders as one character per line (e.g.
    "Z\\n3\\nB\\n9\\n-\\nY\\nA\\nX\\nL") -- find the run of single-char lines right
    after the "(edit)" marker and join them into e.g. "Z3B9-YAXL"."""
    lines = [l.strip() for l in full_text.split("\n")]
    for i, l in enumerate(lines):
        if "(edit)" in l or "Linking WhatsApp account" in l:
            chunk = []
            for l2 in lines[i+1:i+15]:
                if len(l2) == 1 and (l2.isalnum() or l2 == "-"):
                    chunk.append(l2)
                    if len(chunk) == 9:  # 4 chars + dash + 4 chars, exactly
                        break
                elif chunk:
                    break
            candidate = "".join(chunk)
            import re
            if re.match(r"^[A-Z0-9]{4}-[A-Z0-9]{4}$", candidate):
                return candidate
    return None


def setup_with_phone_code(timeout_s=480):
    """Prints an 8-character linking code to stdout, then polls (up to
    timeout_s) for the human to enter it on their phone. WhatsApp's linking
    code REFRESHES every ~60s if unused -- this re-checks periodically and
    re-prints whenever a new code appears, so a slow human-relay loop (e.g.
    reading it off a workflow log) always has a live, usable code available.
    Prints LOGIN_OK or LOGIN_TIMEOUT as the last line so the calling workflow
    step can branch."""
    if not MY_NUMBER:
        print("WA_NUMBER not set"); return False
    with sync_playwright() as p:
        ctx = _launch(p, headless=True)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://web.whatsapp.com/", timeout=30000)
        page.wait_for_timeout(4000)
        try:
            page.get_by_text("Log in with phone number", exact=False).click(timeout=15000)
        except Exception as e:
            print("could not find 'log in with phone number' link:", e)
            print("--- page text for diagnosis ---")
            print(page.evaluate("document.body.innerText")[:600])
            ctx.close(); return False
        # a cold CI runner (fresh browser download, no local cache) can take
        # noticeably longer to render this form than a warm local run --
        # explicitly wait for the field to exist rather than a fixed sleep.
        try:
            page.wait_for_selector('input[data-testid="phone-number-input"]', timeout=20000)
        except Exception as e:
            print("phone number field never appeared:", e)
            print("--- page text for diagnosis ---")
            print(page.evaluate("document.body.innerText")[:600])
            ctx.close(); return False
        page.wait_for_timeout(1500)
        # WhatsApp auto-selects the country by the SERVER's IP geolocation, not
        # the target number's actual country -- on a US-based CI runner this
        # silently defaults to the US, which then misreads the whole number.
        # Fix: type the FULL international number with a leading '+' directly
        # into the phone field -- the widget auto-detects and self-corrects the
        # country from the '+91' prefix regardless of whatever it defaulted to,
        # which is far more robust than trying to drive the country dropdown UI.
        try:
            box = page.locator('input[data-testid="phone-number-input"]')
            box.click(timeout=15000)
            box.fill("")
            box.type("+" + MY_NUMBER, delay=30)
            page.wait_for_timeout(1200)
            cur = page.evaluate("document.body.innerText")
            if "+91" not in cur:
                print("WARNING: country did not auto-correct to +91 as expected")
                print(cur[:300])
        except Exception as e:
            print("could not fill phone number field:", e)
            ctx.close(); return False
        page.wait_for_timeout(1000)
        # the "Next" button may take longer to enable/render on a CI runner than
        # it did in local testing -- try a real click first (longer timeout),
        # then fall back to just pressing Enter in the field, which most forms
        # (including this one) also accept as a submit trigger.
        submitted = False
        try:
            page.get_by_role("button", name="Next", exact=False).click(timeout=20000)
            submitted = True
        except Exception as e:
            print("'Next' button click failed, falling back to Enter key:", e)
            try:
                box.press("Enter")
                submitted = True
            except Exception as e2:
                print("Enter-key fallback also failed:", e2)
        if not submitted:
            print("--- page text for diagnosis ---")
            print(page.evaluate("document.body.innerText")[:600])
            ctx.close(); return False
        page.wait_for_timeout(3000)
        full_text = page.evaluate("document.body.innerText")
        code = _extract_code(full_text)
        print("LINKING_CODE:", code or "NOT FOUND -- see full text below")
        if not code:
            print(full_text[:800])
            ctx.close(); return False
        print(f">>> On your phone: WhatsApp > Settings > Linked Devices > Link with phone number instead > enter {code} <<<")
        print(">>> This code refreshes automatically every ~60s if unused -- watch this log, a NEW code will be printed each time it changes. Use whichever code is printed MOST RECENTLY. <<<")
        last_code = code
        end = time.time() + timeout_s
        ok = False
        while time.time() < end:
            try:
                cur_text = page.evaluate("document.body.innerText")
                cur_code = _extract_code(cur_text)
                if cur_code and cur_code != last_code:
                    last_code = cur_code
                    print(f"LINKING_CODE (refreshed): {cur_code}")
                    print(f">>> On your phone: enter the NEW code {cur_code} <<<")
            except Exception:
                pass
            try:
                page.wait_for_selector(
                    '[aria-label="Search or start a new chat" i], [title="Search or start a new chat" i]',
                    timeout=5000)
                ok = True
                break
            except Exception:
                time.sleep(3)
        print("LOGIN_OK" if ok else "LOGIN_TIMEOUT")
        ctx.close()
        return ok


def send(message, headless=True, timeout_ms=30000):
    if not MY_NUMBER:
        raise RuntimeError("WA_NUMBER not set")
    text = urllib.parse.quote(message)
    url = f"https://web.whatsapp.com/send?phone={MY_NUMBER}&text={text}"
    with sync_playwright() as p:
        ctx = _launch(p, headless)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(url, timeout=timeout_ms)
        box = page.wait_for_selector('div[contenteditable="true"][data-tab="10"]', timeout=timeout_ms)
        page.wait_for_timeout(1500)
        box.click()
        page.keyboard.press("Enter")
        page.wait_for_timeout(1500)
        ctx.close()


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "send"
    if mode == "setup":
        ok = setup_with_phone_code()
        sys.exit(0 if ok else 1)
    elif mode == "check":
        print("logged in:", is_logged_in())
    else:
        msg = sys.argv[2] if len(sys.argv) > 2 else "Test message from cloud watcher."
        send(msg)
        print("sent:", msg)
