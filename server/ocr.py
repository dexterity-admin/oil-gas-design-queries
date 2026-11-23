"""Built-in reader for scanned pages (RapidOCR: small ONNX models that ship inside the program).

No separate install and no internet: the models are part of the package and run on the CPU
through the same ONNX runtime the search already uses.
"""
import re
import threading

import numpy as np
import pymupdf

from . import config

_engine, _lock = None, threading.Lock()
DPI = 200
DET_SIDE = 1600  # shorter side of the picture the line finder sees (RapidOCR 1.4: det_limit_side_len)


def available():
    if config.OCR == "off":
        return False
    try:
        import rapidocr_onnxruntime  # noqa: F401
        return True
    except Exception:
        return False


def _get():
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR
        # By default the line finder works on a page shrunk to about 90 dpi and silently misses whole
        # lines of ordinary 10 pt print. At 1600 px it found every line of the test pages for +0.3 s a page.
        # One thread: the Library runs under a hard CPU cap, where more threads only queue (and each
        # thread's work buffers cost RAM).
        _engine = RapidOCR(det_limit_side_len=DET_SIDE, intra_op_num_threads=config.WORK_THREADS,
                           inter_op_num_threads=1)
    return _engine


class Reader:
    """Reads the scanned pages of one document in a separate worker process.

    The reader needs about 1 GB while it works and Python never hands that back to Windows, so the Library
    stayed at 1.25 GB after reading a book (237 MB without). A worker returns every byte when it closes,
    and a worker that runs out of memory costs one page, not the Library. It runs under the same CPU cap.
    """

    def __init__(self, path):
        self.path, self.pool = str(path), None

    def read(self, pno):
        if self.pool is None:
            import multiprocessing
            self.pool = multiprocessing.get_context("spawn").Pool(1)
        return self.pool.apply(_read_in_worker, (self.path, pno))

    def close(self):
        if self.pool is not None:
            self.pool.close()
            self.pool.join()
            self.pool = None


def _read_in_worker(path, pno):
    with pymupdf.open(path) as doc:
        return read_page(doc[pno - 1])


def recognise(page):
    """Raw pieces of a page picture: [[x0, y0, x1, y1 in page points, text, confidence]]."""
    pix = page.get_pixmap(dpi=DPI, colorspace=pymupdf.csGRAY)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
    with _lock:
        result, _ = _get()(img)
    k, out = 72.0 / DPI, []
    for box, text, score in result or []:
        xs, ys = [p[0] for p in box], [p[1] for p in box]
        out.append([min(xs) * k, min(ys) * k, max(xs) * k, max(ys) * k, (text or "").strip(), float(score)])
    return out


def _gutter(words, width):
    """The x of the empty strip between two columns of text, or None on a one-column page."""
    best, best_x = None, None
    x = 0.35 * width
    while x <= 0.65 * width:
        crossing = sum(1 for w in words if w[0] < x - 2 and w[2] > x + 2)
        if best is None or crossing < best:
            best, best_x = crossing, x
        x += 4
    left = sum(1 for w in words if w[2] <= best_x + 2)
    right = sum(1 for w in words if w[0] >= best_x - 2)
    ok = best <= 0.06 * len(words) and left >= 0.2 * len(words) and right >= 0.2 * len(words)
    return best_x if ok else None


def read_page(page, min_score=0.5, min_number_score=0.72):
    """Lines of a scanned page in reading order: [(text, (x0, y0, x1, y1) in page points, confidence)]."""
    words = []
    for w in recognise(page):
        text, score = w[4], w[5]
        if not text or score < min_score or re.search(r"[^\x00-\u024f\u2000-\u22ff]", text):
            continue
        # A faintly printed number is the costliest thing to misread: one the reader is unsure of is
        # left out, so the answer says "not found, see the page" instead of quoting a wrong dimension.
        if sum(ch.isdigit() for ch in text) >= 0.5 * len(text) and score < min_number_score:
            continue
        # Lettering inside drawings comes out as short capitals the reader is unsure of ("DAMZER").
        if text.isupper() and len(text) < 30 and score < 0.85:
            continue
        words.append(w)
    if not words:
        return []
    gx = _gutter(words, page.rect.width)
    columns = [words] if gx is None else [[w for w in words if (w[0] + w[2]) / 2 < gx],
                                          [w for w in words if (w[0] + w[2]) / 2 >= gx]]
    lines = []
    for col in columns:
        rows = []
        for w in sorted(col, key=lambda w: (w[1] + w[3]) / 2):
            cy, h = (w[1] + w[3]) / 2, w[3] - w[1]
            if rows and abs(cy - rows[-1]["cy"]) <= 0.5 * max(h, rows[-1]["h"]):
                rows[-1]["w"].append(w)
            else:
                rows.append({"cy": cy, "h": h, "w": [w]})
        for r in rows:  # pieces on one row ("Headroom" ... "2,100") become one line
            ws = sorted(r["w"], key=lambda w: w[0])
            lines.append((" ".join(w[4] for w in ws),
                          (min(w[0] for w in ws), min(w[1] for w in ws), max(w[2] for w in ws), max(w[3] for w in ws)),
                          min(w[5] for w in ws)))
    return lines
