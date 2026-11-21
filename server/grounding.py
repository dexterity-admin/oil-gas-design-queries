"""Deterministic help around the language model, so correctness does not rest on the model alone.

- Tables: the matching row (and column) is found by code and handed to the model first.
- Percent rules ("reduce by 25 percent"): the arithmetic is done by code and offered to the model.
- Checking: every number in an answer must exist in the sources or in that arithmetic;
  anything else is reported so the app can flag it instead of showing it as fact.
- Tanglish: the answer is produced in English first, then reworded; the numbers must survive.
"""
import re

NUM = r"\d+(?:\.\d+)?"
_UP = r"(?:up to|upto|below|under|less than|not more than|max(?:imum)?|<=?|≤)"
_OVER = r"(?:above|over|more than|greater than|exceeding|>|≥)"


def _rows(md):
    rows = [[c.strip() for c in l.strip().strip("|").split("|")] for l in md.splitlines() if l.strip().startswith("|")]
    if len(rows) < 3:
        return rows
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    # PDF table detection often leaves empty spacer columns; they carry nothing.
    keep = [i for i in range(width) if any(r[i] for j, r in enumerate(rows) if j != 1)]
    return [[r[i] for i in keep] for r in rows]


def _query_numbers(q):
    """[(value, unit word after it)], nudged when the question says 'above 15' or 'below 15'."""
    out = []
    for m in re.finditer(r"(?:(%s|%s)\s+)?(%s)\s*([a-zA-Z#°%%]*)" % (_UP, _OVER, NUM), q, re.I):
        v = float(m.group(2))
        word = (m.group(1) or "").lower()
        if word and re.fullmatch(_OVER, word):
            v += 1e-6
        elif word in ("below", "under", "less than"):
            v -= 1e-6
        out.append((v, m.group(3).lower().lstrip("°")))
    return out


def _in_range(cell, v):
    c = cell.lower().strip()
    m = re.fullmatch(r"%s\s*(%s)" % (_UP, NUM), c)
    if m:
        return v <= float(m.group(1))
    m = re.fullmatch(r"%s\s*(%s)" % (_OVER, NUM), c)
    if m:
        return v > float(m.group(1))
    m = re.fullmatch(r"(%s)\s*(?:and above|and over|or more|\+)" % NUM, c)
    if m:
        return v >= float(m.group(1))
    m = re.fullmatch(r"(%s)\s*(?:to|-|–)\s*(%s)" % (NUM, NUM), c)
    if m:
        return float(m.group(1)) <= v <= float(m.group(2))
    return None


def _units(label):
    return set(re.findall(r"[a-z#%]+", label.lower()))


def _score(cell, label, q, qtokens, qnums):
    c = cell.lower().strip()
    if not c:
        return 0
    if len(c) > 3 and c in q:  # the question literally says "above 15"
        return 3
    if c in qtokens:
        return 2
    bound = [v for v, u in qnums if u and u in _units(label)]
    for v in (bound or [v for v, u in qnums if not u or not bound]):
        if _in_range(c, v):
            return 2
    return 0


def table_text(md, question):
    """A table as labelled rows, with the row (and value) that fits the question put first."""
    rows = _rows(md)
    if len(rows) < 3 or not all(rows[0]) or len(set(rows[0])) != len(rows[0]):
        return md, []
    head, body = rows[0], rows[2:]
    q = " " + question.lower() + " "
    qtokens = set(re.findall(r"[a-z0-9#.]+", q))
    qnums = _query_numbers(question)
    line = lambda r: "; ".join("%s: %s" % (h, v) for h, v in zip(head, r) if v)
    scores = [sum(_score(v, h, q, qtokens, qnums) for h, v in zip(head, r)) for r in body]
    best = max(scores, default=0)
    hits = [r for r, s in zip(body, scores) if s == best] if best >= 2 else []
    values = []
    out = []
    if hits and len(hits) < len(body):
        out.append("Row that matches the question:")
        for r in hits:
            out.append("- " + line(r))
            values += [float(v) for v in r[1:] if re.fullmatch(NUM, v)]
        if len(hits) == 1:  # column headers can be ranges too ("201 to 400 C (mm)")
            cols = [(h, v) for h, v in zip(head[1:], hits[0][1:])
                    if any(_in_range(re.sub(r"\s*[a-zA-Z°]*\s*\(.*\)\s*$", "", h).strip(), n) for n, u in qnums
                           if u and u in _units(re.sub(r"\(.*\)", "", h)))]
            if len(cols) == 1:  # show only the cell that answers the question, so no other value can be picked up
                out[-1] = "- %s: %s; %s: %s" % (head[0], hits[0][0], cols[0][0], cols[0][1])
                out.append("Value that answers the question: %s" % cols[0][1])
                values = [float(cols[0][1])] if re.fullmatch(NUM, cols[0][1]) else values
        out.append("Other rows of the same table (do not use them for this question):")
        out += ["- " + line(r) for r in body if r not in hits]
    else:
        out.append("Table, one row per line:")
        out += ["- " + line(r) for r in body]
    return "\n".join(out), values


def arithmetic_help(texts, values):
    """Does the sums for percent rules found in the sources, for the model to use if the rule applies."""
    lines = []
    fmt = lambda x: ("%.3f" % x).rstrip("0").rstrip(".")
    sentences = re.split(r"(?<=[.;])\s+", re.sub(r"\s+", " ", " ".join(texts)))
    for v in sorted(set(values))[:4]:
        for sent in sentences:
            rule = 'Rule "%s"' % sent.strip()[:170]
            for word, pct in re.findall(r"(reduc|increas)\w*\s+by\s+(%s)\s*(?:percent|%%)" % NUM, sent, re.I):
                sign = -1 if word.lower() == "reduc" else 1
                lines.append("%s -> %s %s by %s percent = %s" % (rule, fmt(v), "reduced" if sign < 0 else "increased", pct,
                                                                 fmt(v * (1 + sign * float(pct) / 100))))
            for pct in re.findall(r"(%s)\s*(?:percent|%%)\s+of\s+the\s+(?:tabulated|table|listed)" % NUM, sent, re.I):
                lines.append("%s -> %s percent of %s = %s" % (rule, pct, fmt(v), fmt(v * float(pct) / 100)))
    lines = list(dict.fromkeys(lines))[:8]
    if not lines:
        return ""
    return ("ARITHMETIC HELP (already calculated). Use a line ONLY if the question describes the situation in "
            "that rule; if the question does not mention it, ignore the line:\n" + "\n".join("- " + l for l in lines))


_COMMON = set("""a an and are as at be been by can could do does did for from had has have how i if in into is it its
me my of on or our shall should that the their them then there these this those to us was we were what when where which
who whom whose why will with would you your about above across after against all also any applies apply applied around
before below between both but during each either every give given gives kind long many maximum minimum max min more most
much must need needed needs no not often only other over per please required requires same show some such tell than through
type under until up use used using value values very while within without smaller larger bigger ones one inch they them specification tamil english evlo venum irukkanum irukku ku la
enna yenna epdi eppadi pannanum""".split())


_QUANTITY = set("""thickness pressure temperature span torque speed diameter length width height weight load stress
velocity flow rating size grade clearance spacing distance slope allowance tolerance time depth capacity power voltage
current frequency density viscosity hardness strength factor ratio""".split())


def missing_terms(question, chunk_texts):
    """What the question asks about that the sources never mention: single words, and "<thing> <quantity>"
    pairs that never share a passage. "Wall thickness" is not covered just because one passage says
    "ring wall" and another says "insulation thickness"."""
    texts = [t.lower() for t in chunk_texts]
    raw = re.findall(r"[a-z][a-z-]{2,}", question.lower())
    stem = lambda w: w[:5] if len(w) > 5 else w.rstrip("s")
    seen = lambda w: {i for i, t in enumerate(texts) if stem(w) in t}
    out = [w for w in dict.fromkeys(raw) if w not in _COMMON and not seen(w)]
    for a, b in zip(raw, raw[1:]):  # words that are directly next to each other in the question
        if b in _QUANTITY and a not in _COMMON and a not in _QUANTITY and a not in out and not seen(a) & seen(b):
            out.append(a + " " + b)
    return out


def tidy(answer):
    """Small models sometimes tack the refusal sentence onto a real answer; a real answer wins."""
    a = re.sub(r"\n+\s*(Source|Sources)\s*:\s*(?=\[)", " ", answer).strip()
    stripped = re.sub(r"\s*The documents do not cover this( question)?( directly)?\.?\s*$", "", a, flags=re.I).strip()
    return stripped if len(stripped) > 25 else a


def unverified_numbers(answer, source_text):
    """Numbers in the answer that appear nowhere in the sources, the question or the arithmetic help."""
    body = re.sub(r"\[\d{1,2}(?:\s*,\s*\d{1,2})*\]", " ", answer)          # citations
    body = re.sub(r"(?m)^\s*\d+[.)]\s", " ", body)                           # list numbering
    known = set(re.findall(NUM, source_text))
    known_f = [float(k) for k in known]
    bad = []
    for n in re.findall(NUM, body):
        f = float(n)
        decimals = len(n.split(".")[1]) if "." in n else 0
        if n in known or any(abs(round(k, decimals) - f) < 1e-9 for k in known_f):
            continue
        if n not in bad:
            bad.append(n)
    return bad


TANGLISH_SYSTEM = (
    "Rewrite the English answer in Tanglish: spoken Tamil written in English letters, mixed with English "
    "technical words, the way Tamil engineers talk at work. Keep every number, unit, grade, document name "
    "and [1]-style citation exactly as given. Do not add, remove or change any fact. Reply with the Tanglish text only.")
TANGLISH_SHOTS = [
    ("The hold time for a tank above 15 m diameter is 48 hours [1].",
     "15 m diameter ku mela irukkura tank ku hold time 48 hours [1]."),
    ("The torque for M16 bolts on a 150# flange is 110 Nm [1]. Use molybdenum disulphide lubricant [1].",
     "150# flange la M16 bolts ku torque 110 Nm kudukkanum [1]. Lubricant ah molybdenum disulphide use pannanum [1]."),
    ("Carbon steel piping below 120 degrees C needs a total dry film thickness of 250 microns [1]. "
     "Surface preparation must be Sa 2.5 [1].",
     "120 degrees C ku keezha irukkura carbon steel piping ku total dry film thickness 250 microns venum [1]. "
     "Surface preparation Sa 2.5 la irukkanum [1]."),
    ("The documents do not cover this.", "Indha vishayam namma documents la illa."),
]


def tanglish_request(english):
    msgs = []
    for en, ta in TANGLISH_SHOTS:
        msgs += [{"role": "user", "content": en}, {"role": "assistant", "content": ta}]
    return {"system": TANGLISH_SYSTEM, "messages": msgs + [{"role": "user", "content": english}], "max_tokens": 500}


def tanglish_ok(english, tanglish):
    """The reworded answer must carry exactly the same numbers and citations, and actually be Tanglish."""
    nums = lambda t: sorted(re.findall(NUM, t))
    tamil = re.search(r"\b(ku|la|irukk\w*|venum|pannanum|kudukk?anum|illa|mela|keezha|indha|andha|dhaan|aagum)\b", tanglish, re.I)
    # "illa" negates a sentence: reject it unless the English answer is itself a negative.
    flipped = re.search(r"\billai?\b", tanglish, re.I) and not re.search(r"\b(not|no|never|without)\b", english, re.I)
    return nums(english) == nums(tanglish) and bool(tamil) and len(tanglish) > 10 and not flipped
