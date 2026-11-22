"""Turn the library folder (PDF, Word, Excel, PowerPoint) into searchable, citable chunks.

Every chunk remembers exactly where it came from (page and rectangle, sheet and rows,
slide, or heading) so the app can show the original and highlight it.
"""
import re
import shutil
import threading
import time
import traceback

import numpy as np
import pymupdf

from . import config, store

HEADING_RE = re.compile(r"^(\d+(\.\d+){0,4}|[A-Z]\.\d+(\.\d+)*|APPENDIX|ANNEX|SECTION|PART|TABLE|FIGURE)\b", re.I)

status = {"state": "idle", "current": "", "done": 0, "total": 0, "errors": [], "embeddings": "off", "log": []}
_again = False  # a file arrived while indexing was running: go round once more
_lock = threading.Lock()
_embedder = None


# ---------- embeddings (optional) ----------
def embedder():
    global _embedder
    if config.EMBEDDINGS == "off" or _embedder is False:
        return None
    if _embedder is None:
        try:
            from fastembed import TextEmbedding
            _embedder = TextEmbedding(config.EMBED_MODEL, cache_dir=str(config.DATA_DIR / "models"))
            status["embeddings"] = "on"
        except Exception as e:  # no internet / not installed: keyword search still works
            _embedder = False
            status["embeddings"] = "unavailable (%s)" % type(e).__name__
            return None
    return _embedder


def embed_query(text):
    m = embedder()
    if not m:
        return None
    v = np.asarray(next(iter(m.query_embed([text]))), dtype=np.float32)
    return v / (np.linalg.norm(v) or 1.0)


def _embed_missing():
    m = embedder()
    if not m:
        return
    while True:
        with store.db() as con:
            rows = con.execute("SELECT id, heading, text FROM chunks WHERE emb IS NULL LIMIT 64").fetchall()
        if not rows:
            break
        vecs = list(m.embed([(r["heading"] + "\n" + r["text"])[:2000] for r in rows]))
        with store.db() as con:
            for r, v in zip(rows, vecs):
                v = np.asarray(v, dtype=np.float32)
                v = v / (np.linalg.norm(v) or 1.0)
                con.execute("UPDATE chunks SET emb=? WHERE id=?", (v.tobytes(), r["id"]))
    store.invalidate_vectors()


# ---------- helpers ----------
def _looks_like_heading(text):
    t = text.strip()
    if not t or len(t) > 110 or "\n" in t.strip():
        return False
    if t.isupper() and len(t) > 4:
        return True
    # "5.3 Hydrostatic testing" is a heading; "5.3.3 Chloride content shall not exceed 50 ppm." is a clause.
    return bool(HEADING_RE.match(t)) and len(t) <= 70 and not t.endswith((".", ";", ":")) and " shall " not in t


def _md_table(rows):
    rows = [["" if c is None else str(c).replace("\n", " ").replace("|", "/").strip() for c in r] for r in rows]
    rows = [r for r in rows if any(r)]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    keep = [i for i in range(width) if any(r[i] for r in rows)]  # drop empty spacer columns
    rows, width = [[r[i] for i in keep] for r in rows], len(keep)
    out = ["| " + " | ".join(rows[0]) + " |", "|" + " --- |" * width]
    out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(out)


class _Packer:
    """Groups small pieces of text into chunks of roughly CHUNK_CHARS."""

    def __init__(self, page, loc):
        self.page, self.loc, self.chunks = page, loc, []
        self.buf, self.rects, self.heading, self.buf_heading = [], [], "", ""

    def add(self, text, rect=None, heading=None):
        if heading is not None:
            self.heading = heading
        if sum(len(b) for b in self.buf) + len(text) > config.CHUNK_CHARS and self.buf:
            self.flush()
        if not self.buf:
            self.buf_heading = self.heading
        self.buf.append(text)
        if rect:
            self.rects.append([round(x, 1) for x in rect])

    def flush(self):
        text = "\n".join(self.buf).strip()
        if len(text) > 20:
            self.chunks.append({"page": self.page, "loc": self.loc, "heading": self.buf_heading,
                                "kind": "text", "text": text, "rects": self.rects})
        self.buf, self.rects = [], []


# ---------- parsers ----------
def parse_pdf(path):
    chunks, notes, heading = [], [], ""
    ocr = bool(shutil.which("tesseract"))
    with pymupdf.open(path) as doc:
        pages = len(doc)
        for pno, page in enumerate(doc, start=1):
            loc = "Page %d" % pno
            table_boxes, tables, marks = [], [], [(-1.0, heading)]
            try:
                for tb in page.find_tables().tables:
                    rows = [[page.get_textbox(pymupdf.Rect(c)) if c else "" for c in row.cells] for row in tb.rows]
                    md = _md_table(rows)
                    if md.count("\n") >= 2:
                        table_boxes.append(pymupdf.Rect(tb.bbox))
                        tables.append({"page": pno, "loc": loc + " (table)", "kind": "table", "text": md[:6000],
                                       "rects": [[round(x, 1) for x in tb.bbox]]})
            except Exception:
                pass
            blocks = [b for b in page.get_text("blocks", sort=True) if b[6] == 0 and b[4].strip()]
            if sum(len(b[4]) for b in blocks) < 40 and not table_boxes:
                if ocr:
                    try:
                        tp = page.get_textpage_ocr(dpi=200, full=True)
                        blocks = [b for b in page.get_text("blocks", textpage=tp, sort=True) if b[4].strip()]
                    except Exception:
                        blocks = []
                if not blocks:
                    notes.append(pno)
                    chunks += tables
                    continue
            packer = _Packer(pno, loc)
            packer.heading = heading
            for b in blocks:
                rect = pymupdf.Rect(b[:4])
                if any((rect & tb).get_area() > 0.6 * rect.get_area() for tb in table_boxes):
                    continue  # already captured as a table
                text = re.sub(r"[ \t]+", " ", b[4]).strip()
                if _looks_like_heading(text):
                    packer.flush()
                    heading = text
                    marks.append((b[1], heading))
                    packer.add(text, b[:4], heading=heading)
                else:
                    packer.add(text, b[:4])
            packer.flush()
            for t in tables:  # a table belongs to the last heading printed above it
                t["heading"] = [h for y, h in marks if y <= t["rects"][0][1]][-1]
            chunks += packer.chunks + tables
    note = ""
    if notes:
        note = "%d scanned page(s) had no readable text%s" % (
            len(notes), "" if ocr else " (install Tesseract on the server to OCR them)")
    return pages, chunks, note


def parse_docx(path):
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    d = docx.Document(str(path))
    packer, section = _Packer(1, "Document start"), 0
    chunks = []
    for el in d.element.body.iterchildren():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = Paragraph(el, d)
            text = p.text.strip()
            if not text:
                continue
            style = (p.style.name or "") if p.style is not None else ""
            if style.lower().startswith(("heading", "title")) or _looks_like_heading(text):
                packer.flush()
                section += 1
                packer.page, packer.loc = section, "Section: " + text[:80]
                packer.add(text, heading=text)
            else:
                packer.add(text)
        elif tag == "tbl":
            t = Table(el, d)
            md = _md_table([[c.text for c in row.cells] for row in t.rows])
            if md:
                chunks.append({"page": max(section, 1), "loc": packer.loc + " (table)",
                               "heading": packer.heading, "kind": "table", "text": md[:6000], "rects": []})
    packer.flush()
    return max(section, 1), packer.chunks + chunks, ""


def parse_xlsx(path):
    import openpyxl

    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    chunks = []
    for sno, ws in enumerate(wb.worksheets, start=1):
        rows = []
        for i, row in enumerate(ws.iter_rows(values_only=True), start=1):
            if any(v not in (None, "") for v in row):
                rows.append((i, list(row)))
        if not rows:
            continue
        width = max(max((j for j, v in enumerate(r) if v not in (None, "")), default=0) for _, r in rows) + 1
        header = rows[0][1][:width]
        body = rows[1:] or rows
        for start in range(0, len(body), 30):
            part = body[start:start + 30]
            md = _md_table([header] + [r[:width] for _, r in part])
            chunks.append({"page": sno, "loc": "Sheet '%s', rows %d-%d" % (ws.title, part[0][0], part[-1][0]),
                           "heading": ws.title, "kind": "table", "text": md[:6000], "rects": []})
    return len(wb.worksheets), chunks, ""


def parse_pptx(path):
    import pptx

    pres = pptx.Presentation(str(path))
    chunks = []
    for sno, slide in enumerate(pres.slides, start=1):
        parts, title = [], ""
        for shape in slide.shapes:
            if shape.has_text_frame and shape.text_frame.text.strip():
                parts.append(shape.text_frame.text.strip())
            if getattr(shape, "has_table", False) and shape.has_table:
                parts.append(_md_table([[c.text for c in r.cells] for r in shape.table.rows]))
        if slide.shapes.title is not None:
            title = slide.shapes.title.text.strip()
        text = "\n".join(p for p in parts if p)
        if len(text) > 20:
            chunks.append({"page": sno, "loc": "Slide %d" % sno, "heading": title, "kind": "text",
                           "text": text[:6000], "rects": []})
    return len(pres.slides), chunks, ""


PARSERS = {".pdf": parse_pdf, ".docx": parse_docx, ".xlsx": parse_xlsx, ".xlsm": parse_xlsx, ".pptx": parse_pptx}


# ---------- sync the library folder with the index ----------
def sync():
    """Index new and changed files, drop deleted ones. Safe to call any time."""
    global _again
    if not _lock.acquire(blocking=False):
        _again = True
        return
    try:
        _again = False
        config.LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
        files = {str(p.relative_to(config.LIBRARY_DIR)): p for p in sorted(config.LIBRARY_DIR.rglob("*"))
                 if p.is_file() and p.suffix.lower() in config.SUPPORTED and not p.name.startswith("~$")}
        with store.db() as con:
            known = {r["relpath"]: (r["id"], r["stamp"]) for r in con.execute("SELECT id,relpath,stamp FROM documents")}
            for rel in set(known) - set(files):
                store.delete_document(con, known[rel][0])
        todo = []
        for rel, p in files.items():
            st = p.stat()
            stamp = "%d-%d" % (st.st_size, int(st.st_mtime))
            if rel not in known or known[rel][1] != stamp:
                todo.append((rel, p, stamp))
        status.update(state="indexing", done=0, total=len(todo), errors=[])
        for rel, p, stamp in todo:
            status["current"] = p.name
            t0, entry = time.time(), {"name": p.name}
            try:
                pages, chunks, note = PARSERS[p.suffix.lower()](p)
                entry.update(pages=pages, passages=len(chunks), tables=sum(c["kind"] == "table" for c in chunks), note=note)
                with store.db() as con:
                    if rel in known:
                        store.delete_document(con, known[rel][0])
                    store.add_document(con, rel, p.name, p.suffix.lower().lstrip("."), stamp, pages, note, chunks)
            except Exception as e:
                status["errors"].append("%s: %s" % (p.name, e))
                entry["error"] = str(e)[:200]
                traceback.print_exc()
            entry["seconds"] = round(time.time() - t0, 1)
            status["log"] = ([entry] + status["log"])[:40]
            status["done"] += 1
        store.invalidate_vectors()
        status.update(state="embedding", current="")
        _embed_missing()
    finally:
        status.update(state="idle", current="", finished=time.time())
        _lock.release()
        if _again:
            sync()


def sync_in_background():
    threading.Thread(target=sync, daemon=True).start()
