"""SQLite index: documents, chunks, full-text search (FTS5) and optional embeddings."""
import json
import re
import sqlite3
import threading
import time

import numpy as np

from . import config, grounding

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents(
  id INTEGER PRIMARY KEY AUTOINCREMENT, relpath TEXT UNIQUE, name TEXT, kind TEXT,
  stamp TEXT, pages INTEGER DEFAULT 0, note TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS chunks(
  id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id INTEGER, page INTEGER, loc TEXT, heading TEXT,
  kind TEXT, text TEXT, rects TEXT, emb BLOB);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(doc_id);
CREATE INDEX IF NOT EXISTS chunks_todo ON chunks(id) WHERE emb IS NULL;
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, heading, tokenize='porter unicode61');
"""

STOP = set("""a an and are as at be by can do does for from how i in is it its me my of on or our
shall should that the this to we what when where which who why will with you your about tell explain
please give need want know any there their than then used use using""".split())

_vec_lock = threading.Lock()
_vec_cache = None  # (ids ndarray, matrix ndarray)


def db():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


def init():
    with db() as con:
        con.executescript(SCHEMA)


def invalidate_vectors():
    global _vec_cache
    with _vec_lock:
        _vec_cache = None


def delete_document(con, doc_id):
    ids = [r[0] for r in con.execute("SELECT id FROM chunks WHERE doc_id=?", (doc_id,))]
    con.executemany("DELETE FROM chunks_fts WHERE rowid=?", [(i,) for i in ids])
    con.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
    con.execute("DELETE FROM documents WHERE id=?", (doc_id,))


def add_document(con, relpath, name, kind, stamp, pages, note, chunks):
    cur = con.execute(
        "INSERT INTO documents(relpath,name,kind,stamp,pages,note) VALUES(?,?,?,?,?,?)",
        (relpath, name, kind, stamp, pages, note))
    doc_id = cur.lastrowid
    for c in chunks:
        cur = con.execute(
            "INSERT INTO chunks(doc_id,page,loc,heading,kind,text,rects) VALUES(?,?,?,?,?,?,?)",
            (doc_id, c["page"], c["loc"], c.get("heading", ""), c["kind"], c["text"],
             json.dumps(c.get("rects", []))))
        con.execute("INSERT INTO chunks_fts(rowid,text,heading) VALUES(?,?,?)",
                    (cur.lastrowid, grounding.expand(c["text"]), c.get("heading", "")))
    return doc_id


def stats():
    """Counts are asked for by every open app every second or two: remember them briefly."""
    cached = getattr(stats, "cache", None)
    if cached and time.time() - cached[0] < 4:
        return cached[1]
    with db() as con:
        d = con.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        c = con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        e = con.execute("SELECT COUNT(*) FROM chunks WHERE emb IS NOT NULL").fetchone()[0]
    stats.cache = (time.time(), {"documents": d, "chunks": c, "embedded": e})
    return stats.cache[1]


def _fts_query(q):
    q = grounding.expand(q)  # "outside diameter" also finds the table that says "OD"
    words = re.findall(r"[A-Za-z0-9]+(?:[.\-/][A-Za-z0-9]+)*", q)
    keep = [w for w in words if w.lower() not in STOP and len(w) > 1] or words
    return " OR ".join('"%s"' % w.replace('"', "") for w in keep[:32])


def keyword_search(q, limit=30):
    fq = _fts_query(q)
    if not fq:
        return []
    with db() as con:
        try:
            rows = con.execute(
                "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? "
                "ORDER BY bm25(chunks_fts, 1.0, 2.5) LIMIT ?", (fq, limit)).fetchall()
        except sqlite3.OperationalError:
            return []
    return [r[0] for r in rows]


def _vectors():
    global _vec_cache
    with _vec_lock:
        if _vec_cache is None:
            with db() as con:
                rows = con.execute("SELECT id, emb FROM chunks WHERE emb IS NOT NULL").fetchall()
            if rows:
                ids = np.array([r[0] for r in rows])
                mat = np.vstack([np.frombuffer(r[1], dtype=np.float32) for r in rows])
            else:
                ids, mat = np.array([]), np.zeros((0, 1), dtype=np.float32)
            _vec_cache = (ids, mat)
        return _vec_cache


def vector_search(qvec, limit=30):
    ids, mat = _vectors()
    if qvec is None or not len(ids):
        return []
    scores = mat @ np.asarray(qvec, dtype=np.float32)
    top = np.argsort(-scores)[:limit]
    return [int(ids[i]) for i in top]


def hybrid_search(q, qvec=None, k=None):
    """Reciprocal-rank fusion of keyword and semantic results."""
    k = k or config.TOP_K
    fused = {}
    for ranked in (keyword_search(q), vector_search(qvec)):
        for rank, cid in enumerate(ranked):
            fused[cid] = fused.get(cid, 0.0) + 1.0 / (60 + rank)
    best = sorted(fused, key=fused.get, reverse=True)[:k]
    return [c for c in (get_chunk(i) for i in best) if c]


def get_chunk(chunk_id):
    with db() as con:
        r = con.execute(
            "SELECT c.id,c.doc_id,c.page,c.loc,c.heading,c.kind,c.text,c.rects,"
            "d.name,d.kind AS doc_kind,d.pages,d.relpath FROM chunks c "
            "JOIN documents d ON d.id=c.doc_id WHERE c.id=?", (chunk_id,)).fetchone()
    if not r:
        return None
    out = dict(r)
    out["rects"] = json.loads(out["rects"] or "[]")
    return out


def get_document(doc_id):
    with db() as con:
        r = con.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    return dict(r) if r else None


def list_documents():
    with db() as con:
        rows = con.execute(
            "SELECT d.id,d.name,d.kind,d.pages,d.note,d.relpath,COUNT(c.id) AS chunks,"
            "COALESCE(SUM(c.kind='table'),0) AS tables FROM documents d "
            "LEFT JOIN chunks c ON c.doc_id=d.id GROUP BY d.id ORDER BY d.name").fetchall()
    return [dict(r) for r in rows]


def list_chunks(doc_id):
    with db() as con:
        rows = con.execute("SELECT id,page,loc,heading,kind,text FROM chunks WHERE doc_id=? ORDER BY page,id", (doc_id,)).fetchall()
    return [dict(r) for r in rows]
