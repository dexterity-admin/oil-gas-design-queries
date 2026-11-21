"""Scores one model on DexMentor's real prompt path. Usage: run_eval.py <base_url> <label> [system_suffix]"""
import json, os, re, sys, time, urllib.request
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import chat, grounding, ingest, store

BASE, LABEL = sys.argv[1].rstrip("/"), sys.argv[2]
SUFFIX = sys.argv[3] if len(sys.argv) > 3 else ""
LOCAL = os.getenv("EVAL_MODE", "local") == "local"
store.init(); ingest.sync()
print("index:", store.stats(), "embeddings:", ingest.status["embeddings"], flush=True)

A1 = "For a tank above 15 m diameter the hydrotest hold time is 48 hours [1]."
A2 = "The torque for M20 bolts on a 300# flange is 215 Nm [1]."
REFUSE = r"\b(not|no|cannot|can't|unable|don't|doesn't|does not|do not|illa|illai)\b"
TAMIL = r"\b(ku|la|irukk\w*|venum|pannanum|kudukk?anum|illa|mela|keezha|indha|andha|dhaan|aagum)\b"
U = "user"
TESTS = [  # (name, messages, style, must match, must NOT match, kind)
    ("hold time >15 m", [(U, "What is the hydrotest hold time for a tank above 15 m diameter?")], "english", [r"\b48\b"], [], "fact"),
    ("hold time 10 m tank", [(U, "How long is the hydrotest hold for a 10 m diameter tank, and how many settlement points?")], "english", [r"\b24\b", r"\b8\b"], [r"\b48\b", r"\b16\b"], "table"),
    ("water temperature", [(U, "What is the minimum test water temperature for a tank hydrotest?")], "english", [r"\b5\b"], [], "fact"),
    ("chloride limit", [(U, "What is the chloride limit of hydrotest water for stainless steel tanks?")], "english", [r"\b50\b"], [], "fact"),
    ("bottom corrosion allowance", [(U, "What corrosion allowance applies to carbon steel tank bottoms?")], "english", [r"1\.5"], [r"\b3(\.0)?\s*mm"], "fact"),
    ("roof plate thickness", [(U, "What is the minimum roof plate thickness?")], "english", [r"\b5\s*mm"], [], "fact"),
    ("6 inch span", [(U, "What is the maximum support span for 6 inch carbon steel pipe?")], "english", [r"5\.2"], [r"5\.8", r"4\.3", r"3\.9"], "table"),
    ("M20 300# torque", [(U, "What torque should I use for M20 bolts on a 300# flange?")], "english", [r"\b215\b"], [r"\b190\b", r"\b370\b", r"\b110\b"], "table"),
    ("M24 300# torque", [(U, "Torque for M24 bolts, 300# flange?")], "english", [r"\b370\b"], [r"\b215\b", r"\b540\b"], "table"),
    ("M27 lubricant", [(U, "Which lubricant is used for M27 bolts?")], "english", [r"nickel"], [r"molybdenum"], "table"),
    ("total DFT", [(U, "What total dry film thickness is needed for piping below 120 C?")], "english", [r"\b250\b"], [], "fact"),
    ("primer DFT", [(U, "What is the dry film thickness of the zinc rich epoxy primer?")], "english", [r"\b75\b"], [r"\b125\b"], "table"),
    ("insulated CS coating", [(U, "What coating thickness is needed on insulated carbon steel to prevent corrosion under insulation?")], "english", [r"\b200\b"], [r"\b250\b"], "fact"),
    ("surface prep", [(U, "What surface preparation is required before painting?")], "simple", [r"Sa\s*2\.5"], [], "fact"),
    ("vertical guide spacing", [(U, "How often must vertical pipe runs be guided?")], "english", [r"\b6\s*m"], [], "fact"),
    ("20 m tank settlement", [(U, "For a 20 m diameter tank, how many settlement points are needed and when are they measured?")], "english", [r"\b16\b", r"50"], [r"\b8\s+(settlement\s+)?points"], "table"),
    ("insulation 6in 300C", [(U, "What mineral wool thickness for a 6 inch pipe at 300 C?")], "english", [r"\b80\b"], [r"\b(100|60|50|40)\s*mm"], "table"),
    ("insulation 2in 150C", [(U, "Mineral wool thickness for a 2 inch pipe at 150 C?")], "english", [r"\b40\b"], [r"\b(100|60|50|80)\s*mm"], "table"),
    ("4 inch span with valve", [(U, "What span should I use for a 4 inch pipe that has a valve in the span?")], "english", [r"3\.2"], [], "calc"),
    ("8 inch thin stainless span", [(U, "Maximum span for 8 inch stainless steel pipe thinner than schedule 10S?")], "english", [r"4\.64"], [], "calc"),
    ("not in library: wind", [(U, "What is the design wind speed for flare stacks?")], "english", [REFUSE], [r"\d\s*(m/s|km/h|mph|met(er|re)s per second)"], "refuse"),
    ("not in library: wall thickness", [(U, "What is the minimum wall thickness for 6 inch schedule 40 pipe?")], "english", [REFUSE], [r"\d\s*mm\b"], "refuse"),
    ("follow-up smaller tanks", [(U, "What is the hydrotest hold time for a tank above 15 m diameter?"), ("assistant", A1), (U, "and for smaller ones?")], "english", [r"\b24\b"], [], "followup"),
    ("follow-up M24", [(U, "What torque should I use for M20 bolts on a 300# flange?"), ("assistant", A2), (U, "what about M24?")], "english", [r"\b370\b"], [r"\b540\b"], "followup"),
    ("tanglish span", [(U, "6 inch pipe ku maximum support span evlo?")], "tanglish", [r"5\.2", TAMIL], [r"5\.8", r"4\.3"], "tanglish"),
    ("tanglish torque", [(U, "M20 bolt 300# flange ku torque evlo venum?")], "tanglish", [r"\b215\b", TAMIL], [r"\b190\b"], "tanglish"),
    ("tanglish water temp", [(U, "tank hydrotest ku water temperature minimum evlo irukkanum?")], "tanglish", [r"\b5\b", TAMIL], [], "tanglish"),
]


def llm(req, max_tokens):
    body = json.dumps({"messages": [{"role": "system", "content": req["system"] + SUFFIX}] + req["messages"],
                       "temperature": 0, "max_tokens": max_tokens, "stream": False}).encode()
    t = time.time()
    r = urllib.request.urlopen(urllib.request.Request(BASE + "/v1/chat/completions", body, {"Content-Type": "application/json"}), timeout=1500)
    d = json.load(r)
    text = re.sub(r"<think>.*?</think>|</?think>", "", d["choices"][0]["message"].get("content") or "", flags=re.S).strip()
    return text, time.time() - t


rows, secs = [], []
for name, msgs, style, musts, nots, kind in TESTS:
    messages = [{"role": r, "content": c} for r, c in msgs]
    query, total, note = None, 0.0, ""
    rw = chat.prepare(messages, style, stage="rewrite")
    if rw["needed"]:
        query, dt = llm(rw, rw["max_tokens"]); total += dt
    prep = chat.prepare(messages, style, query=query, local=LOCAL)
    if not prep["sources"] or prep.get("skip_model"):
        ans = prep["message"]; prep["sources"] = []; note = " | (answered by code, not the model)"
    else:
        ans, dt = llm(prep, prep["max_tokens"] if LOCAL else 450); total += dt
    body = grounding.tidy(chat._clean(ans))
    if prep.get("tanglish_step"):  # on the PC, Tanglish wording is the office model's job; here the facts are checked
        musts = [m for m in musts if m != TAMIL]; note = " | (PC answers in simple English; office model rewords)"
    invented = chat.verify(body, prep.get("grounding", ""))["unverified"] if prep["sources"] else []
    wrong = [n for n in nots if re.search(n, body, re.I)]
    ok = all(re.search(m, body, re.I) for m in musts) and not wrong and not invented
    rows.append((name, kind, ok, bool(invented), bool(wrong))); secs.append(total)
    print("\n=== [%s] %s (%s) | ok=%s invented=%s wrong_value=%s | %.1fs%s%s\nQ: %s\nA: %s" % (
        LABEL, name, kind, ok, invented or "-", wrong or "-", total, (" | search query: " + query) if query else "", note, msgs[-1][1], body), flush=True)

print("\nRESULT|%s|correct %d/%d|answers with a number not in the sources %d|answers with a wrong value %d|avg %.1fs|max %.1fs|failed: %s" % (
    LABEL, sum(r[2] for r in rows), len(rows), sum(r[3] for r in rows), sum(r[4] for r in rows), sum(secs) / len(secs), max(secs),
    ", ".join(r[0] for r in rows if not r[2]) or "none"))
