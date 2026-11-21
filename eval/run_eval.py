"""Scores one small model on DexMentor's real prompt path. Usage: run_eval.py <base_url> <label> [system_suffix]"""
import json, os, re, sys, time, urllib.request
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import chat, ingest, store

BASE, LABEL = sys.argv[1].rstrip("/"), sys.argv[2]
SUFFIX = sys.argv[3] if len(sys.argv) > 3 else ""
store.init(); ingest.sync()
print("index:", store.stats(), "embeddings:", ingest.status["embeddings"], flush=True)

A1 = "For a tank above 15 m diameter the hydrotest hold time is 48 hours [1]."
REFUSE = r"\b(not|no|cannot|can't|unable|don't|doesn't|does not|do not|illa|illai)\b"
TESTS = [  # (name, messages, style, must-match regexes, kind)
    ("hold time >15 m", [("user", "What is the hydrotest hold time for a tank above 15 m diameter?")], "english", [r"\b48\b"], "fact"),
    ("water temperature", [("user", "What is the minimum test water temperature for a tank hydrotest?")], "english", [r"\b5\b"], "fact"),
    ("6 inch span", [("user", "What is the maximum support span for 6 inch carbon steel pipe?")], "english", [r"5\.2"], "fact"),
    ("M20 300# torque", [("user", "What torque should I use for M20 bolts on a 300# flange?")], "english", [r"\b215\b"], "fact"),
    ("total DFT", [("user", "What total dry film thickness is needed for piping below 120 C?")], "english", [r"\b250\b"], "fact"),
    ("surface prep", [("user", "What surface preparation is required before painting?")], "simple", [r"Sa\s*2\.5"], "fact"),
    ("20 m tank settlement", [("user", "For a 20 m diameter tank, how many settlement points are needed and when are they measured?")], "english", [r"\b16\b", r"50"], "reason"),
    ("4 inch span with valve", [("user", "What span should I use for a 4 inch pipe that has a valve in the span?")], "english", [r"3\.2\d?|25\s*(%|percent)"], "reason"),
    ("insulation 6in 300C", [("user", "What mineral wool thickness for a 6 inch pipe at 300 C?")], "english", [r"\b80\b"], "reason"),
    ("not in library", [("user", "What is the design wind speed for flare stacks?")], "english", [REFUSE], "refuse"),
    ("follow-up", [("user", "What is the hydrotest hold time for a tank above 15 m diameter?"), ("assistant", A1), ("user", "and for smaller ones?")], "english", [r"\b24\b"], "followup"),
    ("tanglish", [("user", "6 inch pipe ku maximum support span evlo?")], "tanglish", [r"5\.2"], "tanglish"),
]


def llm(req, max_tokens):
    body = json.dumps({"messages": [{"role": "system", "content": req["system"] + SUFFIX}] + req["messages"],
                       "temperature": 0.2, "max_tokens": max_tokens, "stream": False}).encode()
    t = time.time()
    r = urllib.request.urlopen(urllib.request.Request(BASE + "/v1/chat/completions", body, {"Content-Type": "application/json"}), timeout=900)
    d = json.load(r)
    text = re.sub(r"<think>.*?</think>", "", d["choices"][0]["message"].get("content") or "", flags=re.S).strip()
    return text, time.time() - t, d.get("usage", {}), d.get("timings", {})


rows, secs, ptoks, pps, gps = [], [], [], [], []
for name, msgs, style, musts, kind in TESTS:
    messages = [{"role": r, "content": c} for r, c in msgs]
    query, extra = None, 0.0
    rw = chat.prepare(messages, style, stage="rewrite")
    if rw["needed"]:
        query, extra, _, _ = llm(rw, rw["max_tokens"])
    prep = chat.prepare(messages, style, query=query)
    if not prep["sources"]:
        ans, dt, usage, tim = prep["message"], 0.0, {}, {}
    else:
        ans, dt, usage, tim = llm(prep, prep["max_tokens"])
    body = ans.split(chat.FOLLOWUP_MARK)[0]
    ok = all(re.search(m, body, re.I) for m in musts)
    if kind == "refuse" and re.search(r"\b\d{2,3}\s*(m/s|km/h|mph)", body): ok = False
    cited = bool(re.search(r"\[\d\]", body)); fu = chat.FOLLOWUP_MARK in ans
    rows.append((name, kind, ok, cited, fu, dt + extra)); secs.append(dt + extra)
    if usage.get("prompt_tokens"): ptoks.append(usage["prompt_tokens"])
    if tim.get("prompt_per_second"): pps.append(tim["prompt_per_second"]); gps.append(tim.get("predicted_per_second", 0))
    print("\n=== [%s] %s | ok=%s cited=%s followups=%s | %.1fs | prompt_tokens=%s%s\nQ: %s\nA: %s" % (
        LABEL, name, ok, cited, fu, dt + extra, usage.get("prompt_tokens"), (" | search query: " + query) if query else "", msgs[-1][1], ans), flush=True)

avg = lambda x: sum(x) / len(x) if x else 0
print("\nRESULT|%s|correct %d/%d|cited %d|followups %d|avg %.1fs|max %.1fs|avg prompt tokens %d|prompt %.0f tok/s|gen %.1f tok/s|failed: %s" % (
    LABEL, sum(r[2] for r in rows), len(rows), sum(r[3] for r in rows), sum(r[4] for r in rows), avg(secs), max(secs), avg(ptoks), avg(pps), avg(gps),
    ", ".join(r[0] for r in rows if not r[2]) or "none"))
