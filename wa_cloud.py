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
        page.wait_for_timeout(6000)
        # occasionally the first click on a cold CI runner doesn't register
        # (page not fully interactive yet) and the phone-number panel never
        # appears -- retry the click itself a couple of times before giving up,
        # rather than failing the whole run on a single transient miss.
        got_input = False
        last_err = None
        for attempt in range(3):
            try:
                page.get_by_text("Log in with phone number", exact=False).first.click(timeout=15000)
            except Exception as e:
                last_err = f"could not find 'log in with phone number' link: {e}"
                page.wait_for_timeout(2000)
                continue
            try:
                page.wait_for_selector('input[data-testid="phone-number-input"]', timeout=12000)
                got_input = True
                break
            except Exception as e:
                last_err = f"phone number field never appeared: {e}"
                print(f"attempt {attempt+1}/3 failed -- {last_err}")
                page.wait_for_timeout(2000)
        if not got_input:
            print(last_err)
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
    """Sends to the personal self-chat and VERIFIES it actually went out --
    a cold headless profile can render the compose box from cached local
    state before the real multi-device socket to the phone is up, which
    made earlier versions of this function report success (no exception)
    for messages that were typed and Entered but never actually delivered.
    Now: waits a bit longer for the app to settle, bails loudly if the page
    shows an offline/connecting banner, and after sending, confirms the
    last outgoing bubble in the chat actually carries a sent/delivered tick
    before returning -- if it can't confirm that, it raises instead of
    silently claiming success."""
    if not MY_NUMBER:
        raise RuntimeError("WA_NUMBER not set")
    text = urllib.parse.quote(message)
    url = f"https://web.whatsapp.com/send?phone={MY_NUMBER}&text={text}"
    with sync_playwright() as p:
        ctx = _launch(p, headless)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(url, timeout=timeout_ms)
        box = page.wait_for_selector('div[contenteditable="true"][aria-label^="Type a message" i]', timeout=timeout_ms)
        page.wait_for_timeout(3000)  # let the socket to the phone settle, not just the DOM render

        def diag():
            """One-shot diagnostic bundle -- attached to any failure so a
            21 Sep 2026-style 'looked fine, nothing arrived' failure is
            actually debuggable from the run log instead of guessed at."""
            d = {"url": page.url}
            try:
                d["headers"] = [h.inner_text(timeout=1500) for h in page.locator("header").all()]
            except Exception as e:
                d["headers"] = f"<err {e}>"
            try:
                d["conv_panel_present"] = page.locator("#main").count() > 0
            except Exception:
                d["conv_panel_present"] = "<err>"
            try:
                d["msg_out_count"] = page.locator("div.message-out").count()
            except Exception:
                d["msg_out_count"] = "<err>"
            try:
                d["body_snippet"] = page.evaluate("document.body.innerText")[:400]
            except Exception:
                d["body_snippet"] = "<err>"
            return d

        page_text = page.evaluate("document.body.innerText")
        if any(w in page_text for w in ("Connecting", "computer is not connected", "Trying to reach phone", "phone number shared via url is invalid")):
            info = diag()
            ctx.close()
            raise RuntimeError(f"WhatsApp not actually connected before send -- {info}")

        box.click()
        page.keyboard.press("Enter")
        page.wait_for_timeout(2500)

        sent_ok = False
        try:
            last_out = page.locator("div.message-out").last
            last_out.wait_for(timeout=6000)
            sent_ok = last_out.locator(
                'span[data-icon="msg-check"], span[data-icon="msg-dblcheck"], span[data-icon="msg-dblcheck-ack"]'
            ).count() > 0
        except Exception:
            pass
        if not sent_ok:
            info = diag()
            ctx.close()
            raise RuntimeError(f"could not confirm a sent/delivered tick after Enter -- {info}")
        ctx.close()


def send_file(path, caption="", headless=True, timeout_ms=40000):
    """Sends a file (PDF etc.) as a document to the personal self-DM, with an
    optional caption. Attach menu -> Document -> file chooser -> preview ->
    Send. The file is sent as a document (not a compressed photo)."""
    if not MY_NUMBER:
        raise RuntimeError("WA_NUMBER not set")
    path = str(Path(path).resolve())
    with sync_playwright() as p:
        ctx = _launch(p, headless)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(f"https://web.whatsapp.com/send?phone={MY_NUMBER}", timeout=timeout_ms)
        page.wait_for_selector('div[contenteditable="true"][aria-label^="Type a message" i]', timeout=timeout_ms)
        page.wait_for_timeout(3000)  # let the socket to the phone settle, not just the DOM render
        page_text = page.evaluate("document.body.innerText")
        if any(w in page_text for w in ("Connecting", "computer is not connected", "Trying to reach phone", "phone number shared via url is invalid")):
            ctx.close()
            raise RuntimeError(f"WhatsApp not actually connected before send_file -- page={page_text[:300]!r}")
        page.get_by_role("button", name="Attach").first.click(timeout=timeout_ms)
        page.wait_for_timeout(800)
        with page.expect_file_chooser(timeout=timeout_ms) as fc:
            page.get_by_text("Document", exact=True).first.click(timeout=timeout_ms)
        fc.value.set_files(path)
        page.wait_for_timeout(2500)
        if caption:
            cap = page.locator('div[contenteditable="true"][aria-label*="caption" i], div[contenteditable="true"][aria-label^="Add a caption" i]').first
            try:
                cap.click(timeout=5000)
                page.keyboard.insert_text(caption)
            except Exception:
                pass
        try:
            page.get_by_role("button", name="Send", exact=True).last.click(timeout=8000, force=True)
        except Exception:
            page.keyboard.press("Enter")
        page.wait_for_timeout(5000)

        sent_ok = False
        try:
            last_out = page.locator("div.message-out").last
            last_out.wait_for(timeout=8000)
            sent_ok = last_out.locator(
                'span[data-icon="msg-check"], span[data-icon="msg-dblcheck"], span[data-icon="msg-dblcheck-ack"]'
            ).count() > 0
        except Exception:
            pass
        ctx.close()
        if not sent_ok:
            raise RuntimeError("could not confirm a sent/delivered tick after sending the file")


def _find_chat_listitem(page, chat_name, timeout_s=15):
    """Polls the currently-rendered list of chat rows for one whose text
    starts with chat_name (list items also carry a trailing timestamp/last-
    message line, so startswith is used rather than an exact match)."""
    end = time.time() + timeout_s
    while time.time() < end:
        for it in page.get_by_role("listitem").all():
            try:
                t = it.inner_text(timeout=1500)
            except Exception:
                continue
            if t.strip().startswith(chat_name):
                return it
        time.sleep(1)
    return None


def send_to_chat(message, chat_name, headless=True, timeout_ms=30000):
    """Sends to a WhatsApp GROUP (or any named chat) by name -- something the
    official/Composio API can never do, only real browser automation.

    The `?text=` deep link opens a "Send message to" forward-style picker;
    text placed via this picker preserves newlines correctly (typing it via
    the keyboard directly does not -- a literal "\\n" triggers WhatsApp's own
    Enter-to-send behaviour mid-message). Clicking the target chat then
    "Send" here does NOT actually transmit it despite the button's name --
    it leaves the text sitting as a draft -- but it also leaves that chat's
    own compose box (with the draft already in it) immediately available on
    the SAME page, so no second navigation/search is needed: just click that
    box and press Enter to actually send it.
    """
    text = urllib.parse.quote(message)
    with sync_playwright() as p:
        ctx = _launch(p, headless)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        page.goto(f"https://web.whatsapp.com/send?text={text}", timeout=timeout_ms)
        page.wait_for_timeout(4000)
        target = _find_chat_listitem(page, chat_name)
        if not target:
            ctx.close()
            raise RuntimeError(f"chat '{chat_name}' not found in forward picker")
        target.click(timeout=timeout_ms)
        page.wait_for_timeout(1200)
        page.get_by_role("button", name="Send", exact=True).click(timeout=timeout_ms)
        page.wait_for_timeout(2500)

        box = page.wait_for_selector('div[contenteditable="true"][aria-label^="Type a message" i]', timeout=timeout_ms)
        box.click(timeout=timeout_ms)
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
