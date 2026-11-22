"""Turn the library folder (PDF, Word, Excel, PowerPoint) into searchable, citable chunks.

Every chunk remembers exactly where it came from (page and rectangle, sheet and rows,
slide, or heading) so the app can show the original and highlight it.
"""
import os
import re
from pathlib import Path
import shutil
import threading
import time
import traceback

import numpy as np
import pymupdf

from . import config, grounding, pack, store

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
        vecs = list(m.embed([grounding.expand(r["heading"] + "\n" + r["text"])[:2000] for r in rows]))
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
    if not re.search(r"[A-Za-z]{3}", t) or re.fullmatch(r"[A-Z]{2,5}[-/ ]?[A-Z0-9-/]*\d[A-Z0-9-/]*", t) or re.fullmatch(r"\d+\s*(of|/)\s*\d+", t, re.I):
        return False  # "12", "150 mm", "3 of 20", "DDS-ST-001"
    if t.isupper() and len(t) > 4:
        return len(t.split()) >= 2  # "CONFIDENTIAL" on every page is a stamp, not a heading
    # "5.3 Hydrostatic testing" is a heading; "5.3.3 Chloride content shall not exceed 50 ppm." is a clause.
    return bool(HEADING_RE.match(t)) and len(t) <= 70 and not t.endswith((".", ";", ":")) and " shall " not in t


def _unglue(text):
    """Some PDFs store their words in pieces that come out stuck together ("steelUsed", "industries.Let").

    Only the safe joins are opened: a capitalised word glued to the end of a lower-case one, and a
    sentence glued to the full stop or comma before it. Codes such as TP304, mmHg or A106 are untouched.
    """
    text = re.sub(r"([a-z]{3})([A-Z][a-z]{2})", r"\1 \2", text)
    text = re.sub(r"([a-z]{2}[.;:)])([A-Z][a-z])", r"\1 \2", text)
    return re.sub(r"([a-z]),([A-Za-z]{2})", r"\1, \2", text)


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


def _table_parts(rows, limit=3500):
    """A long table as several markdown tables, each repeating the header row, never cut mid-row."""
    rows = [["" if c is None else str(c).replace("\n", " ").replace("|", "/").strip() for c in r] for r in rows]
    rows = [r for r in rows if any(r)]
    if len(rows) < 2:
        return [_md_table(rows)] if rows else []
    parts, cur, size = [], [], 0
    for r in rows[1:]:
        n = sum(len(c) + 3 for c in r)
        if cur and size + n > limit:
            parts.append(_md_table([rows[0]] + cur)); cur, size = [], 0
        cur.append(r); size += n
    if cur:
        parts.append(_md_table([rows[0]] + cur))
    return [p for p in parts if p]


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


def _cluster(vals, tol=2.5):
    out = []
    for v in sorted(vals):
        if out and v - out[-1][-1] <= tol:
            out[-1].append(v)
        else:
            out.append([v])
    return [sum(g) / len(g) for g in out]


def _without_cells(text, cells):
    """Block text with a table's cell texts cut out, leaving the writing around the table."""
    idx = [i for i, ch in enumerate(text) if not ch.isspace()]
    flat = "".join(text[i] for i in idx)
    cells = [re.sub(r"\s", "", c) for c in cells]
    cells = [c for c in cells if c]
    if not cells:
        return text, ""
    kill, whole = [], flat.find("".join(cells))
    if whole >= 0:
        kill.append((whole, whole + len("".join(cells))))
    else:
        pos = max(flat.find(cells[0]), 0)
        for c in cells:
            k = flat.find(c, pos)
            if 0 <= k <= pos + 40:  # cells follow one another; a far match is some other text
                kill.append((k, k + len(c))); pos = k + len(c)
    out, last = [], 0
    for a, b in kill:
        out.append(text[last:idx[a]]); last = idx[b - 1] + 1
    lead = [ln.strip() for ln in re.sub(r"[ \t]+", " ", out[0]).splitlines() if ln.strip()] if out else []
    return "\n".join(out + [text[last:]]), (lead[-1] if lead else "")


def _grid_tables(page):
    """Tables whose borders are drawn as thin filled bars (Word and many PDF converters do this).

    PyMuPDF's own finder needs clean word boxes and misses these. The grid is rebuilt from the bars,
    merged cells are joined where no bar divides them, and each cell is read by its rectangle.
    Returns [(bbox, rows, cell texts in reading order, title)].
    """
    hs, vs = [], []
    for d in page.get_cdrawings():
        if "f" not in d.get("type", "") and d.get("type") != "s":
            continue
        r = pymupdf.Rect(d["rect"])
        if r.height <= 2.5 and r.width >= 8:
            hs.append(((r.y0 + r.y1) / 2, r.x0, r.x1))
        elif r.width <= 2.5 and r.height >= 5:
            vs.append(((r.x0 + r.x1) / 2, r.y0, r.y1))
    if len(hs) < 2 or len(vs) < 2:
        return []
    # Split the page into separate tables: a vertical gap with no upright bar ends a table.
    spans = []
    for x, y0, y1 in sorted(vs, key=lambda v: v[1]):
        if spans and y0 <= spans[-1][1] + 3:
            spans[-1][1] = max(spans[-1][1], y1)
        else:
            spans.append([y0, y1])
    out = []
    for top, bottom in spans:
        h = [s for s in hs if top - 3 <= s[0] <= bottom + 3]
        v = [s for s in vs if s[1] >= top - 3 and s[2] <= bottom + 3]
        ys, xs = _cluster([s[0] for s in h]), _cluster([s[0] for s in v])
        if len(ys) < 3 or len(xs) < 3 or (len(ys) - 1) * (len(xs) - 1) > 4000:
            continue  # a table has at least two rows and two columns
        rows_n, cols_n = len(ys) - 1, len(xs) - 1

        def hbar(y, x0, x1):
            return any(abs(s[0] - y) <= 2.5 and s[1] <= x0 + 3 and s[2] >= x1 - 3 for s in h) or \
                sum(min(s[2], x1) - max(s[1], x0) for s in h if abs(s[0] - y) <= 2.5 and s[2] > x0 and s[1] < x1) >= 0.8 * (x1 - x0)

        def vbar(x, y0, y1):
            return sum(min(s[2], y1) - max(s[1], y0) for s in v if abs(s[0] - x) <= 2.5 and s[2] > y0 and s[1] < y1) >= 0.8 * (y1 - y0)

        parent = {(i, j): (i, j) for i in range(rows_n) for j in range(cols_n)}

        def find(a):
            while parent[a] != a:
                a = parent[a]
            return a

        for i in range(rows_n):
            for j in range(cols_n):
                if j + 1 < cols_n and not vbar(xs[j + 1], ys[i], ys[i + 1]):
                    parent[find((i, j + 1))] = find((i, j))
                if i + 1 < rows_n and not hbar(ys[i + 1], xs[j], xs[j + 1]):
                    parent[find((i + 1, j))] = find((i, j))
        groups = {}
        for cell in parent:
            groups.setdefault(find(cell), []).append(cell)
        grid = [[""] * cols_n for _ in range(rows_n)]
        spanned, full, raw = set(), set(), {}
        for cells in groups.values():
            i0, i1 = min(c[0] for c in cells), max(c[0] for c in cells)
            j0, j1 = min(c[1] for c in cells), max(c[1] for c in cells)
            # Letter boxes are taller than the letters and poke into the rows above and below,
            # so the cell is read through a slightly smaller window.
            dy = min(4.0, 0.3 * min(ys[k + 1] - ys[k] for k in range(i0, i1 + 1)))
            text = page.get_textbox(pymupdf.Rect(xs[j0] + 1.5, ys[i0] + dy, xs[j1 + 1] - 1.5, ys[i1 + 1] - dy))
            raw[(i0, j0)] = text or ""
            text = _unglue(re.sub(r"\s+", " ", text or "").strip())
            # A cell merged across columns belongs to each of them, so its text is repeated;
            # one merged down several rows likewise, so every row still reads complete.
            for i in range(i0, i1 + 1):
                for j in range(j0, j1 + 1):
                    grid[i][j] = text
            if j1 > j0:
                spanned.add(i0)
            if j0 == 0 and j1 == cols_n - 1 and cols_n > 1:
                full.update(range(i0, i1 + 1))
        if sum(1 for r in grid for c in r if c) < 4:
            continue
        top_y, title = ys[0], ""
        while len(grid) > 2 and 0 in full:
            title = (title + " " + grid[0][0]).strip()
            raw = {k: t for k, t in raw.items() if k[0] != rows_n - len(grid)}
            grid, top_y = grid[1:], ys[len(ys) - len(grid)]  # a title bar above the table stays ordinary text
            spanned, full = {k - 1 for k in spanned if k > 0}, {k - 1 for k in full if k > 0}
        # "Carbon Steel" over "ASTM A53M | ASTM A106M": fold the two header rows into one.
        if 0 in spanned and len(grid) > 2 and 1 not in full:
            grid = [[a if a == b else (a + " " + b).strip() for a, b in zip(grid[0], grid[1])]] + grid[2:]
            full = {k - 1 for k in full if k > 1}
        # A note that runs the full width of the table is not a data row.
        grid = [r for k, r in enumerate(grid) if k == 0 or k not in full]
        out.append(((xs[0], top_y, xs[-1], ys[-1]), grid, [raw[k] for k in sorted(raw)], title))
    return out


# ---------- parsers ----------
def parse_pdf(path):
    chunks, notes, heading = [], [], ""
    ocr = bool(shutil.which("tesseract"))
    with pymupdf.open(path) as doc:
        pages = len(doc)
        for pno, page in enumerate(doc, start=1):
            loc = "Page %d" % pno
            table_boxes, table_cells, tables, marks = [], [], [], [(-1.0, heading)]
            try:
                # Table detection is the slowest step and needs ruled lines: skip it on pages that have none.
                ruled = len(page.get_cdrawings()) >= 4
                found = []
                for tb in (page.find_tables().tables if ruled else []):
                    found.append((tb.bbox, [[page.get_textbox(pymupdf.Rect(c)) if c else "" for c in row.cells]
                                            for row in tb.rows], [], ""))
                if ruled:
                    for g in _grid_tables(page):
                        if not any((pymupdf.Rect(g[0]) & pymupdf.Rect(f[0])).get_area() > 0 for f in found):
                            found.append(g)
                for bbox, rows, cells, title in found:
                    parts = [md for md in _table_parts(rows) if md.count("\n") >= 2]
                    if parts:
                        table_boxes.append(pymupdf.Rect(bbox))
                        table_cells.append(cells)
                    for md in parts:
                        tables.append({"page": pno, "loc": loc + " (table)", "kind": "table", "text": md,
                                       "rects": [[round(x, 1) for x in bbox]], "title": title, "box": len(table_boxes) - 1})
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
                text = b[4]
                for k, (tb, cells) in enumerate(zip(table_boxes, table_cells)):
                    if (rect & tb).get_area() > 0 and cells:
                        text, lead = _without_cells(text, cells)  # one block runs through a table
                        for t in tables:  # the line that introduces a table names it when it has no title bar
                            if t["box"] == k and not t["title"] and 8 < len(lead) <= 120:
                                t["title"] = _unglue(lead)
                text = _unglue(re.sub(r"[ \t]+", " ", text)).strip()
                if not text:
                    continue
                if _looks_like_heading(text):
                    packer.flush()
                    heading = text
                    marks.append((b[1], heading))
                    packer.add(text, b[:4], heading=heading)
                else:
                    packer.add(text, b[:4])
            packer.flush()
            for t in tables:  # a table belongs to its own title, or to the last heading printed above it
                t["heading"] = t.pop("title") or [h for y, h in marks if y <= t["rects"][0][1]][-1]
                t.pop("box")
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
            for md in _table_parts([[c.text for c in row.cells] for row in t.rows]):
                chunks.append({"page": max(section, 1), "loc": packer.loc + " (table)",
                               "heading": packer.heading, "kind": "table", "text": md, "rects": []})
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
            for md in _table_parts([header] + [r[:width] for _, r in part]):
                chunks.append({"page": sno, "loc": "Sheet '%s', rows %d-%d" % (ws.title, part[0][0], part[-1][0]),
                               "heading": ws.title, "kind": "table", "text": md, "rects": []})
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
def seed_starter():
    """Copy the documents that ship with the program into the Library, once each.
    A starter document the librarian later deletes stays deleted; a new one in an update is added."""
    try:
        names = sorted(p.name for p in config.STARTER_DIR.iterdir()
                       if p.is_file() and p.suffix.lower() in config.SUPPORTED)
    except OSError:
        return
    new = [n for n in names if n not in config.STARTER_SEEDED]
    if not new:
        return
    dest = config.LIBRARY_DIR / "Starter library"
    dest.mkdir(parents=True, exist_ok=True)
    done = []
    for n in new:
        try:
            if not (dest / n).exists():
                shutil.copyfile(config.STARTER_DIR / n, dest / n)
            done.append(n)
        except OSError:
            pass
    if done:
        config.save(starter_seeded=config.STARTER_SEEDED + done)


def sync():
    """Index new and changed files, drop deleted ones. Safe to call any time."""
    global _again
    if not _lock.acquire(blocking=False):
        _again = True
        return
    global _embedder
    try:
        _again = False
        if _embedder is False:
            _embedder = None  # it may have failed for lack of internet: try again this round
        config.LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
        seed_starter()
        pack.fetch()
        wanted = lambda p: p.suffix.lower() in config.SUPPORTED and not p.name.startswith(("~$", "."))
        files = {str(p.relative_to(config.LIBRARY_DIR)): p for p in sorted(config.LIBRARY_DIR.rglob("*"))
                 if p.is_file() and wanted(p)}
        offline, report = [], {}
        for folder in config.FOLDERS:  # linked folders (for example a mapped network drive) are read where they are
            root = os.path.abspath(folder)
            info = report[folder] = {"reachable": os.path.isdir(root), "documents": 0, "skipped": 0}
            if not info["reachable"]:
                offline.append(root)
                continue
            broken = []
            for base, dirs, names in os.walk(root, onerror=broken.append):
                dirs[:] = [d for d in dirs if not d.startswith((".", "$", "~"))]
                for n in names:
                    p = Path(base) / n
                    if wanted(p):
                        files[str(p)] = p
                        info["documents"] += 1
                    elif p.suffix.lower() in (".doc", ".xls", ".ppt", ".dwg", ".txt", ".rtf"):
                        info["skipped"] += 1
            if broken:  # part of the share could not be listed: treat it as offline so nothing is dropped
                info["reachable"] = False
                offline.append(root)
        status["folders"] = report
        changed = False
        with store.db() as con:
            known = {r["relpath"]: (r["id"], r["stamp"]) for r in con.execute("SELECT id,relpath,stamp FROM documents")}
            for rel in set(known) - set(files):
                # A network drive that is switched off must not wipe its documents from the index.
                if any(rel == o or rel.startswith(o.rstrip("\\/") + os.sep) for o in offline):
                    continue
                store.delete_document(con, known[rel][0])
                changed = True
        todo = []
        for rel, p in files.items():
            try:
                st = p.stat()
            except OSError:
                continue
            stamp = "%d-%d" % (st.st_size, int(st.st_mtime))
            if rel not in known or known[rel][1] != stamp:
                todo.append((rel, p, stamp))
        status.update(state="indexing", done=0, total=len(todo), errors=[])
        for rel, p, stamp in todo:
            status["current"] = p.name
            t0, entry = time.time(), {"name": p.name}
            try:
                if p.suffix.lower() == ".pdf":
                    from .app import PDF_LOCK
                    with PDF_LOCK:
                        pages, chunks, note = parse_pdf(p)
                else:
                    pages, chunks, note = PARSERS[p.suffix.lower()](p)
                entry.update(pages=pages, passages=len(chunks), tables=sum(c["kind"] == "table" for c in chunks), note=note)
                with store.db() as con:
                    if rel in known:
                        store.delete_document(con, known[rel][0])
                    store.add_document(con, rel, p.name, p.suffix.lower().lstrip("."), stamp, pages, note, chunks)
            except Exception as e:
                msg = str(e).replace(str(p.parent), "").replace(str(p), p.name)[:200]  # no server paths in messages
                status["errors"].append("%s: %s" % (p.name, msg))
                entry["error"] = msg
                traceback.print_exc()
            entry["seconds"] = round(time.time() - t0, 1)
            status["log"] = ([entry] + status["log"])[:40]
            status["done"] += 1
        if changed or todo:  # an unchanged library keeps its search index in memory
            store.invalidate_vectors()
            store.stats.cache = None
        status.update(state="embedding", current="")
        _embed_missing()
        _prune_pages()
    finally:
        status.update(state="idle", current="", finished=time.time())
        _lock.release()
        if _again:
            sync()


def _prune_pages(keep=4000):
    """The cache of rendered pages would grow for ever; keep the most recently made ones."""
    try:
        files = sorted(config.CACHE_DIR.glob("*.png"), key=lambda f: f.stat().st_mtime, reverse=True)
        for f in files[keep:]:
            f.unlink(missing_ok=True)
    except OSError:
        pass


def rescan_forever(minutes=30):
    """New files saved to a linked folder are picked up without anyone pressing a button."""
    while True:
        time.sleep(minutes * 60)
        if config.FOLDERS or config.PACK_REPO:
            sync()


def sync_in_background():
    threading.Thread(target=sync, daemon=True).start()
