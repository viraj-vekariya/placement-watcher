#!/usr/bin/env python3
"""
Placement triage engine — matches a company JD against Viraj's 3 CVs using Gemini.
Runtime deps: only the Gemini API (no Claude). Reads key from secrets.env.
"""
import json, os, sys, time, urllib.request, urllib.error
from pathlib import Path

BASE = Path(__file__).resolve().parent

def load_env():
    env = {}
    p = BASE / "secrets.env"
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env

ENV = load_env()
API_KEY = os.environ.get("GEMINI_API_KEY") or ENV.get("GEMINI_API_KEY", "")
MODEL = os.environ.get("GEMINI_MODEL") or ENV.get("GEMINI_MODEL", "gemini-3.6-flash")

# Candidate profile (hard filters the AI must respect)
CANDIDATE = {
    "name": "Vekariya Viraj Parbatbhai (23AE10041)",
    "cgpa": 8.24,
    "branch": "Aerospace Engineering (B.Tech Hons) + Micro-spl. in AI & Applications",
    "grad_year": 2027,
}

def load_cvs():
    cvs = {}
    for n, label in [("1", "SDE / GenAI / ML"), ("2", "Data Science / Data Analyst / BA"),
                     ("3", "Product / Operations / Consulting / General Management")]:
        p = BASE / "cvs" / f"CV{n}.txt"
        cvs[f"CV{n}"] = {"label": label, "text": p.read_text() if p.exists() else ""}
    return cvs

CVS = load_cvs()

SCHEMA = {
    "type": "object",
    "properties": {
        "eligible": {"type": "boolean"},
        "eligibility_note": {"type": "string"},
        "cv_scores": {"type": "object", "properties": {
            "CV1": {"type": "integer"}, "CV2": {"type": "integer"}, "CV3": {"type": "integer"}}},
        "best_cv": {"type": "string", "enum": ["CV1", "CV2", "CV3"]},
        "chance": {"type": "string", "enum": ["strong", "decent", "weak"]},
        "verdict": {"type": "string", "enum": ["APPLY", "APPLY_IF_TIME", "SKIP"]},
        "reasoning": {"type": "string"},
        "key_matches": {"type": "array", "items": {"type": "string"}},
        "gaps": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["eligible","eligibility_note","cv_scores","best_cv","chance","verdict","reasoning","key_matches","gaps"],
}

PROMPT = """You are a placement advisor for an IIT Kharagpur final-year student. Judge one company/JD against his THREE CVs and give an honest, decisive verdict. Be blunt — do not inflate fit.

CANDIDATE (hard facts — enforce eligibility strictly):
- {name}
- CGPA: {cgpa}/10 | Branch: {branch} | Graduating: {grad_year}

His THREE CVs (he can apply with exactly one):
--- CV1 = {l1} ---
{cv1}
--- CV2 = {l2} ---
{cv2}
--- CV3 = {l3} ---
{cv3}

COMPANY / JD TO JUDGE:
Company: {company}
Role: {role}
JD / details:
{jd}

Rules for the verdict:
- eligible=false if the JD states a CGPA cutoff above {cgpa}, or excludes his branch/degree/grad-year. Note it.
- Score each CV 0-10 for fit to THIS role. Pick best_cv = highest.
- chance: strong (best score >=7 and eligible), decent (5-6), weak (<5 or shaky eligibility).
- verdict: APPLY if eligible and best score >=6; APPLY_IF_TIME if eligible but 4-5 or a domain he lacks (e.g. pure finance with no finance experience) yet worth a shot; SKIP if ineligible or best score <4.
- reasoning: 1-2 blunt sentences a busy student can act on. key_matches/gaps: 2-4 short bullets each.
"""

# model fallback chain — 3.8 is best but often 503s on free tier; try alternates
MODELS = [MODEL, "gemini-3.7-flash", "gemini-3.8-flash", "gemini-3.5-flash"]

def _call_gemini(prompt, max_tries=6):
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "responseSchema": SCHEMA, "temperature": 0.2},
    }
    data = json.dumps(body).encode()
    last = None
    for attempt in range(max_tries):
        model = MODELS[attempt % len(MODELS)]
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        req = urllib.request.Request(url, data=data,
                                     headers={"x-goog-api-key": API_KEY, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                resp = json.load(r)
            return json.loads(resp["candidates"][0]["content"]["parts"][0]["text"]), model
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code not in (429, 500, 503):  # 404 etc: skip to next model fast
                continue
        except Exception as e:
            last = str(e)
        time.sleep(min(2 ** attempt, 20))  # backoff on overload/timeout
    raise RuntimeError(f"Gemini unavailable after {max_tries} tries (last: {last})")

def triage(company, role, jd):
    prompt = PROMPT.format(
        name=CANDIDATE["name"], cgpa=CANDIDATE["cgpa"], branch=CANDIDATE["branch"], grad_year=CANDIDATE["grad_year"],
        l1=CVS["CV1"]["label"], l2=CVS["CV2"]["label"], l3=CVS["CV3"]["label"],
        cv1=CVS["CV1"]["text"], cv2=CVS["CV2"]["text"], cv3=CVS["CV3"]["text"],
        company=company, role=role, jd=jd)
    res, used = _call_gemini(prompt)
    res["_model"] = used
    return res

def format_verdict(company, role, res):
    icon = {"APPLY": "✅ APPLY", "APPLY_IF_TIME": "🟡 APPLY IF TIME", "SKIP": "⛔ SKIP"}[res["verdict"]]
    s = res["cv_scores"]
    best = res["best_cv"]
    lines = [
        f"🏢 {company} — {role}",
        f"{icon}  |  chance: {res['chance']}",
        f"Best CV: {best} ({CVS[best]['label']})  [CV1 {s['CV1']} · CV2 {s['CV2']} · CV3 {s['CV3']}]",
        f"Eligible: {'yes' if res['eligible'] else 'NO'} — {res['eligibility_note']}",
        f"Why: {res['reasoning']}",
        f"Matches: {', '.join(res['key_matches'])}",
        f"Gaps: {', '.join(res['gaps'])}",
    ]
    return "\n".join(lines)

if __name__ == "__main__":
    # quick manual test: triage.py "Company" "Role" "jd text or @path"
    company, role, jd = sys.argv[1], sys.argv[2], sys.argv[3]
    if jd.startswith("@"):
        jd = Path(jd[1:]).read_text()
    print(format_verdict(company, role, triage(company, role, jd)))
