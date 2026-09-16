#!/usr/bin/env python3
"""One poll cycle: fetch -> diff seen -> triage new -> rebuild dashboard into ./public/."""
import json, os, datetime
from pathlib import Path
import poller, triage, build_site

BASE = Path(__file__).resolve().parent
PUB = BASE / "public"; PUB.mkdir(exist_ok=True)
STATE = BASE / "state.json"

def load_state():
    if STATE.exists(): return json.loads(STATE.read_text())
    return {"companies": {}, "notice_ids": []}

def save_state(s): STATE.write_text(json.dumps(s, indent=1))

def main():
    st = load_state()
    alive = poller.session_alive()
    st["session_ok"] = alive
    st["updated"] = datetime.datetime.now().strftime("%d %b %H:%M")
    if alive:
        for c in poller.companies():
            key = c["id"] or f"{c['company']}|{c['role']}"
            if key in st["companies"]:
                continue  # already triaged
            try:
                res = triage.triage(c["company"], c["role"], c["jd"] or c["role"])
            except Exception as e:
                res = None; print("triage failed:", e)
            st["companies"][key] = {**c, "seen": st["updated"], "triage": res}
        st["notices"] = poller.notices()
    # build dashboard data (newest first)
    data = []
    for key, c in st["companies"].items():
        t = c.get("triage") or {}
        data.append({**c, **({} if not t else {
            "verdict": t.get("verdict","APPLY_IF_TIME"), "chance": t.get("chance","decent"),
            "best_cv": t.get("best_cv","CV1"), "best_cv_label": build_site.CVS.get(t.get("best_cv","CV1"),{}).get("label",""),
            "cv_scores": t.get("cv_scores",{"CV1":0,"CV2":0,"CV3":0}), "eligible": t.get("eligible",True),
            "eligibility_note": t.get("eligibility_note",""), "reasoning": t.get("reasoning",""),
            "key_matches": t.get("key_matches",[]), "gaps": t.get("gaps",[])})})
    data.reverse()
    build_site.build_into(data, st.get("notices",[]), alive, st["updated"], PUB, os.environ.get("BOARD_NAME","board.html"))
    save_state(st)
    print(f"built: {len(data)} companies, session_ok={alive}")

if __name__ == "__main__":
    main()
