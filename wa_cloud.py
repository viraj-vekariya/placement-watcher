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


def setup_with_phone_code(timeout_s=300):
    """Prints an 8-character linking code to stdout, then polls (up to
    timeout_s) for the human to enter it on their phone. Prints LOGIN_OK or
    LOGIN_TIMEOUT as the last line so the calling workflow step can branch."""
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
            ctx.close(); return False
        page.wait_for_timeout(1500)
        try:
            box = page.locator('input[type="text"]').first
            box.click()
            box.fill(MY_NUMBER)
            page.get_by_role("button", name="Next", exact=False).click(timeout=10000)
        except Exception as e:
            print("could not submit phone number:", e)
            ctx.close(); return False
        page.wait_for_timeout(3000)
        full_text = page.evaluate("document.body.innerText")
        # the 8-char code renders as one character per line (e.g. "Z\n3\nB\n9\n-\nY\nA\nX\nL")
        # -- find the run of single-char lines right after the "(edit)" marker and join them.
        lines = [l.strip() for l in full_text.split("\n")]
        code = None
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
                m = re.match(r"^[A-Z0-9]{4}-[A-Z0-9]{4}$", candidate)
                if m:
                    code = candidate
                    break
        print("LINKING_CODE:", code or "NOT FOUND -- see full text below")
        if not code:
            print(full_text[:800])
            ctx.close(); return False
        print(f">>> On your phone: WhatsApp > Settings > Linked Devices > Link with phone number instead > enter {code} <<<")
        end = time.time() + timeout_s
        ok = False
        while time.time() < end:
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
