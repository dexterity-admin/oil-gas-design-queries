"""The question-answering pipeline: understand the question, find sources, teach from them."""
import asyncio
import re

from . import config, ingest, llm, store

FOLLOWUP_MARK = "<<<FOLLOWUPS>>>"

STYLES = {
    "english": "Write in clear, professional English.",
    "simple": "Write in very simple English: short sentences, everyday words, and explain every "
              "technical term the first time you use it.",
    "tanglish": "Write in Tanglish: Tamil written in English letters, mixed naturally with English, the way "
                "Tamil engineers chat with each other. Keep every technical term, number, unit, clause number "
                "and document name in English exactly as in the source.",
}

SYSTEM = """You are {app}, a patient senior engineer who teaches designers and engineers at an engineering \
design company. You teach ONLY from the company's own standards, design bases and reference documents.

Each user message ends with numbered SOURCES retrieved from that library, followed by the question.

Rules:
- Use only the SOURCES. Never fill in values, limits, formulas or clause numbers from general knowledge. \
If the sources do not answer the question, say so plainly, mention the closest thing they do cover, and stop.
- Put a citation like [1] or [2][3] right after every fact you take from a source.
- Copy numbers, units, grades and clause numbers exactly as written.
- Start with the direct answer in one or two sentences. Then explain the why or the how briefly, the way \
you would teach a junior colleague. Use a markdown table when comparing values, and a numbered list for steps.
- If sources disagree, show both and say which document each comes from.
- {style}
- After the answer, write a line containing only {mark} and then up to 3 short follow-up questions \
(one per line, no numbering) that the user would likely ask next and that these sources can answer."""

REWRITE = """Rewrite the user's latest message as one standalone English search query for a library of \
engineering standards. Resolve words like "it" or "that" using the conversation. Translate Tamil or \
Tanglish to English. Keep standard names, clause numbers, grades and units exactly. Reply with the \
query only."""


def _clean(text):
    return text.split(FOLLOWUP_MARK)[0].strip()


def _source_block(chunks):
    parts = []
    for i, c in enumerate(chunks, start=1):
        head = "[%d] %s - %s" % (i, c["name"], c["loc"])
        if c["heading"]:
            head += " - " + c["heading"][:90]
        parts.append(head + "\n" + c["text"])
    return "\n\n".join(parts)


def _public(c, n):
    return {"n": n, "chunk_id": c["id"], "doc_id": c["doc_id"], "doc": c["name"], "doc_kind": c["doc_kind"],
            "page": c["page"], "loc": c["loc"], "heading": c["heading"], "kind": c["kind"],
            "snippet": c["text"][:280]}


def _normalize(messages):
    history = [{"role": m["role"], "content": _clean(str(m["content"]))[:4000]}
               for m in messages if m.get("role") in ("user", "assistant") and m.get("content")][-13:]
    if not history or history[-1]["role"] != "user":
        return None, []
    prior = history[:-1]
    while prior and prior[0]["role"] != "user":
        prior = prior[1:]
    return history[-1]["content"], prior


def rewrite_request(question, prior):
    convo = "\n".join("%s: %s" % (m["role"], m["content"][:600]) for m in prior[-4:])
    return {"system": REWRITE, "max_tokens": 120, "messages": [
        {"role": "user", "content": "Conversation:\n%s\n\nLatest message: %s" % (convo or "(none)", question)}]}


def needs_rewrite(question, prior, style):
    return bool(prior) or style == "tanglish" or bool(re.search(r"[^\x00-\x7F]", question))


def answer_request(question, prior, chunks, style):
    system = SYSTEM.format(app=config.APP_NAME, style=STYLES.get(style, STYLES["english"]), mark=FOLLOWUP_MARK)
    return system, prior + [{"role": "user", "content":
                             "SOURCES:\n\n%s\n\nQUESTION: %s" % (_source_block(chunks), question)}]


NOT_FOUND = ("I could not find anything about this in the indexed documents. Try different words, "
             "or check that the document has been added to the library.")


def prepare(messages, style="english", query=None, stage=None):
    """For answers written by the model on the user's own PC: the server does the searching
    and hands back a ready-made prompt; the PC only has to generate the words."""
    question, prior = _normalize(messages)
    if question is None:
        return {"error": "Please type a question."}
    if stage == "rewrite":
        return {"needed": needs_rewrite(question, prior, style), **rewrite_request(question, prior)}
    query = query.strip().strip('"') if query and 3 < len(query.strip()) < 400 else question
    chunks = store.hybrid_search(query, ingest.embed_query(query), k=config.LOCAL_TOP_K)
    sources = [_public(c, i) for i, c in enumerate(chunks, 1)]
    for c in chunks:
        c["text"] = c["text"][:config.LOCAL_SOURCE_CHARS]
    short_prior = [{"role": m["role"], "content": m["content"][:700]} for m in prior[-4:]]
    while short_prior and short_prior[0]["role"] != "user":
        short_prior = short_prior[1:]
    system, msgs = answer_request(question, short_prior, chunks, style)
    return {"query": query, "sources": sources, "system": system, "messages": msgs, "max_tokens": 700,
            "message": "" if chunks else NOT_FOUND}


async def answer(messages, style="english"):
    """Async generator of events: status, sources, delta, error, done."""
    question, prior = _normalize(messages)
    if question is None:
        yield {"type": "error", "message": "Please type a question."}
        return

    # 1. Turn follow-ups and Tanglish into a clean search query.
    query = question
    if needs_rewrite(question, prior, style) and llm.available():
        yield {"type": "status", "message": "Understanding your follow-up" if prior else "Understanding your question"}
        try:
            req = rewrite_request(question, prior)
            q = await llm.complete(req["system"], req["messages"])
            if 3 < len(q) < 400:
                query = q
        except llm.LLMError:
            pass

    # 2. Retrieve.
    yield {"type": "status", "message": "Searching the standards"}
    qvec = await asyncio.to_thread(ingest.embed_query, query)
    chunks = await asyncio.to_thread(store.hybrid_search, query, qvec)
    yield {"type": "sources", "query": query, "sources": [_public(c, i) for i, c in enumerate(chunks, 1)]}
    if not chunks:
        yield {"type": "delta", "text": NOT_FOUND}
        yield {"type": "done"}
        return

    # 3. Answer.
    if not llm.available():
        yield {"type": "delta", "text": "No language model is connected yet, so here are the most relevant "
                                        "passages. Tap a source to see it in the original document.\n\n" +
                                        "\n\n".join("**[%d] %s, %s**\n\n%s" % (i, c["name"], c["loc"], c["text"][:500])
                                                    for i, c in enumerate(chunks[:3], 1))}
        yield {"type": "done"}
        return
    system, final = answer_request(question, prior, chunks, style)
    try:
        async for piece in llm.stream(system, final):
            yield {"type": "delta", "text": piece}
    except llm.LLMError as e:
        yield {"type": "error", "message": str(e)}
    yield {"type": "done"}
