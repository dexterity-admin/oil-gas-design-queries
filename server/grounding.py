"""Deterministic help around the language model, so correctness does not rest on the model alone.

- Tables: the matching row (and column) is found by code and handed to the model first.
- Percent rules ("reduce by 25 percent"): the arithmetic is done by code and offered to the model.
- Checking: every number in an answer must exist in the sources or in that arithmetic;
  anything else is reported so the app can flag it instead of showing it as fact.
- Tanglish: the answer is produced in English first, then reworded; the values must survive.

The guiding rule for the table matcher: when it is not sure, it says nothing. A missing hint costs
a little accuracy; a wrong "this is the row" hint puts a wrong number in front of an engineer.
"""
import re

NUM = r"\d+(?:\.\d+)?"
_NUMBER = re.compile(r"(?<![\w.,])(?:-|minus\s+)?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", re.I)
_UNITS = {"in": "inch", "inches": "inch", '"': "inch", "nps": "inch", "°c": "c", "degc": "c", "deg": "c", "degrees": "c",
          "degree": "c", "celsius": "c", "metre": "m", "metres": "m", "meter": "m", "meters": "m", "millimetre": "mm",
          "millimetres": "mm", "millimeter": "mm", "millimeters": "mm", "hours": "h", "hour": "h", "hrs": "h", "hr": "h",
          "lb": "#", "lbs": "#", "nb": "dn", "sch": "schedule", "microns": "micron", "µm": "micron"}
_LE, _LT = r"(?:up\s*to|upto|≤|<=|=<|max(?:imum)?|not more than|not exceeding|to)", r"(?:<|below|under|less than|smaller than)"
_GE, _GT = r"(?:≥|>=|=>|min(?:imum)?|not less than|at least|from)", r"(?:>|above|over|more than|greater than|exceeding|larger than)"


_FRACS = {"½": 0.5, "¼": 0.25, "¾": 0.75, "⅛": 0.125, "⅜": 0.375, "⅝": 0.625, "⅞": 0.875, "⅓": 1 / 3, "⅔": 2 / 3}


# Engineers write the same thing two ways. Each group is searched for, and counted as covered, together.
ALIASES = [("od", "outside diameter", "outer diameter"), ("id", "inside diameter", "inner diameter"),
           ("nps", "nominal pipe size"), ("dn", "diameter nominal", "nominal diameter"), ("nb", "nominal bore"),
           ("wt", "wall thickness"), ("sch", "schedule"), ("cs", "carbon steel"), ("ss", "stainless steel"),
           ("las", "low alloy steel"), ("hydro test", "hydrotest", "hydrostatic test", "hydrostatic testing"),
           ("temp", "temperature"), ("erw", "electric resistance welding"), ("saw", "submerged arc welding"),
           ("efw", "electric fusion welding"), ("ndt", "nde", "non-destructive testing", "non destructive testing"),
           ("pwht", "post weld heat treatment"), ("pmi", "positive material identification"),
           ("igc", "intergranular corrosion"), ("hic", "hydrogen-induced cracking", "hydrogen induced cracking"),
           ("rt", "radiography", "radiographic"), ("ut", "ultrasonic"), ("mt", "magnetic particle"),
           ("pt", "dye penetrant", "liquid penetrant"), ("p&id", "piping and instrumentation diagram")]


def expand(text):
    """The text plus the other ways of writing whatever abbreviations or full names it contains."""
    low = " " + re.sub(r"[^a-z0-9&-]+", " ", text.lower()) + " "
    extra = [a for group in ALIASES if any(" %s " % a in low for a in group) for a in group if " %s " % a not in low]
    return text + (" " + " ".join(extra) if extra else "")


_NOT_UNITS = {"pipe", "pipes", "size", "sizes", "nominal", "line", "lines", "of", "for", "the", "is", "as", "per", "and", "or"}


def _frac(text):
    """Pipe sizes are written as fractions: "1 ½", "1½", "1-1/2", "3/4". They become 1.5, 0.75 so they can be compared."""
    def dec(whole, part):
        return ("%.4f" % (float(whole or 0) + part)).rstrip("0").rstrip(".")
    text = re.sub(r"(?<![\d./])(?:(\d+)[ -])?(\d{1,2})/(\d{1,2})(?![\d/.])",
                  lambda m: dec(m.group(1), int(m.group(2)) / int(m.group(3))) if 0 < int(m.group(2)) < int(m.group(3)) else m.group(0), text)
    text = re.sub(r"(?:(?<![\d.])(\d+)\s?)?([%s])" % "".join(_FRACS), lambda m: dec(m.group(1), _FRACS[m.group(2)]), text)
    return re.sub(r"(\d)(to|and)\b", r"\1 \2", text)


def _upright(rows):
    """A table laid on its side (sizes run across the top, one quantity per row) turned the usual way,
    so that each size becomes a row that can be matched."""
    if len(rows) < 3:
        return rows
    head, body = rows[0], rows[2:]
    across = [_frac(h) for h in head[1:]]
    numeric = sum(1 for h in across if re.fullmatch(r"\d+(?:\.\d+)?", h))
    if len(head) < 5 or len(body) >= len(head) - 1 or len(body) > 5 or numeric < 0.8 * len(across):
        return rows
    labels = [head[0]] + [r[0] for r in body]
    if len(set(labels)) != len(labels) or any(not l or re.fullmatch(r"[\d.,\s]+", l) for l in labels):
        return rows
    return [labels, ["---"] * len(labels)] + [[head[j]] + [r[j] for r in body] for j in range(1, len(head))]


def _num(text):
    return float(re.sub(r"minus\s+", "-", text.lower()).replace(",", ""))


def _unit(word):
    w = word.lower().strip(".,;:()[]°") if word else ""
    return "" if w in _NOT_UNITS else _UNITS.get(w, w)


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
    """[(value, {unit words next to it})]. "above 15" is nudged just over 15, "below 15" just under."""
    out = []
    for m in _NUMBER.finditer(q):
        before = q[:m.start()].lower()
        if re.search(r"[\d)]\s*$", before) and m.group(0).startswith("-"):
            continue  # the dash of a range such as 50-100, not a minus sign
        v = _num(m.group(0))
        if re.search(_GT + r"\s*$", before):
            v += 1e-6
        elif re.search(_LT + r"\s*$", before):
            v -= 1e-6
        nxt = re.match(r"\s*([a-zA-Z#°%\"µ]+)", q[m.end():])
        prev = re.search(r"([a-zA-Z#]+)\s*$", before)
        units = {_unit(nxt.group(1))} if nxt else set()
        if prev:
            units.add(_unit(prev.group(1)))
        out.append((v, units - {""}))
    return out


def _range(cell):
    """A cell such as "Up to 15", "< 15", "≥ 15 mm", "3 to 8" or "10 and above" -> a test function, plus its unit."""
    c = re.sub(r"\s+", " ", _frac(cell).lower().strip())
    n = r"((?:-|minus )?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"
    tail = r"\s*([a-z°#%\"µ]*)$"
    m = re.fullmatch(r"(?:over|above|>)\s*" + n + r"\s*(?:to|-|–|—)\s*" + n + tail, c)
    if m:  # "Over 4 to 8": more than 4, up to and including 8
        a, b = _num(m.group(1)), _num(m.group(2))
        return (lambda v, a=a, b=b: a < v <= b), _unit(m.group(3))
    for pat, test in ((_LE, lambda v, a: v <= a), (_LT, lambda v, a: v < a), (_GE, lambda v, a: v >= a), (_GT, lambda v, a: v > a)):
        m = re.fullmatch(pat + r"\s*" + n + tail, c)
        if m:
            a = _num(m.group(1))
            return (lambda v, a=a, test=test: test(v, a)), _unit(m.group(2))
    m = re.fullmatch(n + r"\s*(?:and above|and over|or more|and more|\+)" + tail, c)
    if m:
        a = _num(m.group(1))
        return (lambda v, a=a: v >= a), _unit(m.group(2))
    m = re.fullmatch(n + r"\s*(?:to|-|–|—)\s*" + n + tail, c)
    if m:
        a, b = _num(m.group(1)), _num(m.group(2))
        return (lambda v, a=a, b=b: a <= v <= b), _unit(m.group(3))
    return None, ""


def _label_units(label):
    return {_unit(w) for w in re.findall(r"[a-zA-Z#°%\"µ]+", label)} - {""}


def _tokens(text):
    return re.findall(r"[a-z0-9#.]+(?:,\d{3})*", text.lower())


def _is_key(col, cells):
    """Columns a question selects a row BY (pipe size, bolt size, flange rating). Value columns
    (the torque, a "Required"/"Not required" verdict, a description) never select a row."""
    if col == 0:
        return True
    filled = [c for c in cells if c]
    if not filled:
        return False
    coded = sum(1 for c in filled if _range(c)[0] or (re.search(r"\d", c) and re.search(r"[A-Za-z#]", c) and len(c) <= 14))
    return coded >= 0.6 * len(filled)


def _cell_match(cell, label, qtokens, qnums):
    """2 if this key cell clearly matches the question, 0 if not, None if the question gives a value
    of this column's kind that this cell does not cover."""
    c = _frac(cell.strip())
    if not c:
        return 0
    test, cell_unit = _range(c)
    units = _label_units(label) | ({cell_unit} if cell_unit else set())
    bound = [v for v, us in qnums if us & units]  # a number may only be compared with a column of its own unit
    if test:
        if not bound:
            return 0
        return 2 if any(test(v) for v in bound) else None
    if re.fullmatch(r"(?:-|minus )?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", c.lower()):
        if not bound:
            return 0
        return 2 if any(abs(v - _num(c)) < 1e-9 for v in bound) else None
    want = _tokens(c)  # words and codes must match as whole tokens: "Class 150" is not "class 1500", "Water" is not "seawater"
    for i in range(len(qtokens) - len(want) + 1):
        if qtokens[i:i + len(want)] == want:
            return 2
    shape = re.sub(r"\d", "9", c.lower())
    if re.search(r"9", shape) and any(re.sub(r"\d", "9", t) == shape and t != c.lower() for t in qtokens):
        return None  # the question names another item of this kind (M30 when the rows are M16, M20, M24)
    return 0


def table_text(md, question, max_other=12):
    """A table as labelled rows, with the row (and value) that fits the question put first.
    Returns (text, numeric values of the matched row)."""
    rows = _upright(_rows(md))
    if len(rows) < 3 or not all(rows[0]) or len(set(rows[0])) != len(rows[0]):
        return md, []
    head, body = rows[0], rows[2:]
    question = _frac(question)
    qtokens, qnums = _tokens(question), _query_numbers(question)
    # A column headed NPS, NB or DN is a size designation: a row is selected by it even when it holds bare numbers.
    sized = lambda h: bool(set(re.findall(r"[a-z]+", h.lower())) & {"nps", "nb", "dn"})
    keys = [i for i in range(len(head)) if _is_key(i, [r[i] for r in body]) or sized(head[i])]
    line = lambda r: "; ".join("%s: %s" % (h, v) for h, v in zip(head, r) if v)
    scored, uncovered = [], set()
    for r in body:
        results = [_cell_match(r[i], head[i], qtokens, qnums) for i in keys]
        scored.append(None if None in results else sum(results))
        uncovered |= {head[i] for i, res in zip(keys, results) if res is None}
    best = max([s for s in scored if s is not None], default=0)
    hits = [r for r, s in zip(body, scored) if s == best] if best >= 2 else []
    # A column the question gives a value for, which no row covers, means the table does not answer it.
    asked = {head[i] for i in keys for r in body if _cell_match(r[i], head[i], qtokens, qnums) in (2, None)}
    out_of_range = [h for h in asked if not any(_cell_match(r[keys_i], h, qtokens, qnums) == 2
                                                 for r in body for keys_i in [head.index(h)])]
    values, out = [], []
    if len(hits) == 1 and not out_of_range:
        hit = hits[0]
        out.append("Row that matches the question:")
        out.append("- " + line(hit))
        values = [_num(v) for i, v in enumerate(hit) if i not in keys and re.fullmatch(r"-?[\d,]+(?:\.\d+)?", v)]
        cols = []  # value columns whose header is itself a range ("201 to 400 C (mm)")
        for i, h in enumerate(head):
            if i in keys:
                continue
            m = re.match(r"(.*?\d)\s*([a-zA-Z°]{1,3})?\s*(?:\(.*\))?\s*$", h)
            test = _range(m.group(1))[0] if m else None
            if test and m.group(2) and any(test(v) for v, us in qnums if _unit(m.group(2)) in us):
                cols.append(i)
        if len(cols) == 1:  # show only the cell that answers the question, so no neighbouring value can be picked up
            i = cols[0]
            out[-1] = "- " + "; ".join("%s: %s" % (head[k], hit[k]) for k in keys if hit[k]) + "; %s: %s" % (head[i], hit[i])
            out.append("Value that answers the question: %s" % hit[i])
            values = [_num(hit[i])] if re.fullmatch(r"-?[\d,]+(?:\.\d+)?", hit[i]) else values
        others = [r for r in body if r is not hit]
        out.append("Other rows of the same table (do not use them for this question):")
        out += ["- " + line(r) for r in others[:max_other]]
        if len(others) > max_other:
            out.append("- (%d more rows not shown)" % (len(others) - max_other))
    else:
        if out_of_range:
            out.append("NOTE: no row of this table covers the %s asked about. This table does not answer the question."
                       % " / ".join(out_of_range))
        out.append("Table, one row per line:")
        shown = body[:40]
        out += ["- " + line(r) for r in shown]
        if len(body) > len(shown):
            out.append("- (%d more rows not shown)" % (len(body) - len(shown)))
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
much must need needed needs no not often only other over per please required requires require same show some such tell
than through type under until up use used using value values very while within without smaller larger bigger ones one
inch they them specification explain explains say says said standard standards document documents spec recommend
recommended correct right allowed permitted big small large high low best good proper properly exactly actually
company according per case cases thing things way get make made take put kindly sir tamil english evlo venum
irukkanum irukku irundha vekkanum ku la enna yenna epdi eppadi pannanum ethana""".split())
_QUANTITY = set("""thickness pressure temperature span torque speed diameter length width height weight load stress
velocity flow rating size grade clearance spacing distance slope allowance tolerance time depth capacity power voltage
current frequency density viscosity hardness strength factor ratio""".split())


def _stem(w):
    for suf in ("ations", "ation", "ings", "ing", "ment", "ies", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            w = w[:-len(suf)]
            break
    return w[:6]


def missing_terms(question, chunk_texts):
    """What the question asks about that the sources never mention: single words, and "<thing> <quantity>"
    pairs that never share a passage. "Wall thickness" is not covered just because one passage says
    "ring wall" and another says "insulation thickness"."""
    texts = [expand(t).lower() for t in chunk_texts]
    raw = re.findall(r"[a-z][a-z-]{2,}", question.lower())
    seen = lambda w: {i for i, t in enumerate(texts) if _stem(w) in t}
    out = [w for w in dict.fromkeys(raw) if w not in _COMMON and not seen(w)]
    for a, b in zip(raw, raw[1:]):  # words that are directly next to each other in the question
        if b in _QUANTITY and a not in _COMMON and a not in _QUANTITY and not seen(a) & seen(b) and a + " " + b not in out:
            out = [w for w in out if w != a] + [a + " " + b]
    return out


def not_covered(question, missing):
    """True only when code can be confident the library does not cover the question: a refusal written
    by code is a hard stop, so a doubtful case is left to the model instead."""
    if any(" " in m for m in missing):
        return True
    words = re.findall(r"[a-z][a-z-]{2,}", question.lower())
    # "design pressure of a pig launcher": a quantity is asked for a two-word thing the library never names.
    if any(w in _QUANTITY for w in words) and any(a in missing and b in missing for a, b in zip(words, words[1:])):
        return True
    content = {w for w in re.findall(r"[a-z][a-z-]{2,}", question.lower()) if w not in _COMMON}
    return len(missing) >= 3 and len(missing) >= 0.6 * max(1, len(content))


def tidy(answer):
    """Small models sometimes tack the refusal sentence onto a real answer; a real answer wins."""
    a = re.sub(r"\n+\s*(Source|Sources)\s*:\s*(?=\[)", " ", answer).strip()
    stripped = re.sub(r"(?:(?<=[.!?\]])|^)\s*The documents do not cover this( question)?( directly)?\.?\s*$", "", a, flags=re.I).strip()
    return stripped if len(stripped) > 25 and stripped[-1] in ".!?])" else a


def _numbers(text):
    """Numbers as written, sign and thousands separators understood: {-29.0, 1500.0, ...} with their decimals."""
    out = []
    for m in _NUMBER.finditer(text):
        if m.group(0).startswith("-") and re.search(r"[\d)]\s*$", text[:m.start()]):
            raw = m.group(0)[1:]
        else:
            raw = m.group(0)
        clean = re.sub(r"minus\s+", "-", raw.lower()).replace(",", "")
        out.append((float(clean), len(clean.split(".")[1]) if "." in clean else 0, raw))
    return out


def unverified_numbers(answer, source_text):
    """Numbers in the answer that appear nowhere in the sources, the question or the arithmetic help.
    This proves a number was not invented; it cannot prove it was taken from the right place."""
    body = re.sub(r"\[\d{1,2}(?:\s*,\s*\d{1,2})*\]", " ", answer)          # citations
    body = re.sub(r"(?m)^\s*\d+[.)]\s", " ", body)                           # list numbering
    known = [v for v, _, _ in _numbers(source_text)]
    # "reduce by 25 percent" legitimately brings 0.75 and 75 into a shown calculation.
    for pct in re.findall(r"(%s)\s*(?:percent|%%)" % NUM, source_text):
        known += [float(pct) / 100, 1 - float(pct) / 100, 1 + float(pct) / 100, 100 - float(pct), 100 + float(pct)]
    bad = []
    for v, decimals, raw in _numbers(body):
        exact = any(abs(k - v) < 1e-9 for k in known)
        # A value may be rounded for display (3.225 -> 3.23), but never rounded away to a whole number.
        rounded = decimals >= 1 and any(abs(round(k, decimals) - v) < 1e-9 for k in known)
        if not exact and not rounded and raw not in bad:
            bad.append(raw)
    return bad


def other_row_numbers(answer, source_text):
    """Numbers the answer took from a table row the matcher ruled out for this question.

    A wrong row passes unverified_numbers, because its value is in the sources. Seen on a real PC: the
    matcher picked "Up to 15 | 24 h", a second, half-read copy of the table showed only "Above 15 | 48 h",
    and the model answered 48 h with a green stamp. A value that sits in a ruled-out row and not in the
    matched row is flagged; a false flag costs a look at the page, a missed one is a wrong number."""
    matched, ruled_out, mode = set(), set(), None
    lines = source_text.rstrip().splitlines()
    if lines and not lines[-1].startswith("- "):  # the grounding text ends with the question: its numbers are the user's
        matched |= {v for v, _, _ in _numbers(lines[-1])}
    for ln in lines:
        s = ln.strip()
        if s == "Row that matches the question:":
            mode = "match"
        elif s.startswith("Other rows of the same table"):
            mode = "other"
        elif s.startswith("Value that answers the question:"):
            matched |= {v for v, _, _ in _numbers(s)}
        elif mode and s.startswith("- "):
            (matched if mode == "match" else ruled_out).update(v for v, _, _ in _numbers(s))
        else:
            mode = None
    body = re.sub(r"\[\d{1,2}(?:\s*,\s*\d{1,2})*\]", " ", answer)
    body = re.sub(r"(?m)^\s*\d+[.)]\s", " ", body)
    bad = []
    for v, _, raw in _numbers(body):
        if v in ruled_out and v not in matched and raw not in bad:
            bad.append(raw)
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


def _value_units(text):
    """{(value, unit word after it)}: "15 m ... 48 hours" must not come back as "48 m ... 15 hours"."""
    text = re.sub(r"\[\d{1,2}(?:\s*,\s*\d{1,2})*\]", " ", text)
    out = set()
    for m in _NUMBER.finditer(text):
        nxt = re.match(r"\s*([a-zA-Z#°%]+)", text[m.end():])
        out.add((_num(m.group(0)) if not re.search(r"[\d)]\s*$", text[:m.start()]) else abs(_num(m.group(0))),
                 _unit(nxt.group(1)) if nxt else ""))
    return out


def tanglish_ok(english, tanglish):
    """The reworded answer must carry exactly the same values with the same units, keep its meaning, and be Tanglish."""
    values = lambda t: {v for v, _ in _value_units(t)}
    if values(english) != values(tanglish):
        return False
    # Units are kept in English in Tanglish, so each value must still sit next to its own unit.
    en_units = {(v, u) for v, u in _value_units(english) if u in _UNITS.values() or len(u) <= 3}
    ta = _value_units(tanglish)
    if any(u and (v, u) not in ta and any(tv == v and tu in {eu for _, eu in en_units} - {u} for tv, tu in ta) for v, u in en_units):
        return False
    tamil = re.search(r"\b(ku|la|irukk\w*|venum|pannanum|kudukk?anum|illa|mela|keezha|indha|andha|dhaan|aagum|thaan)\b", tanglish, re.I)
    # "illa" negates a sentence: reject it unless the English answer is itself a negative; and a "not" must not vanish.
    neg_en = bool(re.search(r"\b(not|no|never|without|cannot)\b", english, re.I))
    neg_ta = bool(re.search(r"\b(illai?|kudathu|koodathu|matt\w*|vendam)\b", tanglish, re.I))
    return bool(tamil) and len(tanglish) > 10 and neg_en == neg_ta
