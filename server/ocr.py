"""Built-in reader for scanned pages (RapidOCR: small ONNX models that ship inside the program).

No separate install and no internet: the models are part of the package and run on the CPU
through the same ONNX runtime the search already uses.
"""
import threading

import numpy as np
import pymupdf

from . import config

_engine, _lock = None, threading.Lock()
DPI = 200


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
        _engine = RapidOCR()
    return _engine


def read_page(page, min_score=0.5, min_number_score=0.72):
    """Lines of a page picture in reading order: [(text, (x0, y0, x1, y1) in page points, confidence)]."""
    pix = page.get_pixmap(dpi=DPI, colorspace=pymupdf.csGRAY)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
    with _lock:
        result, _ = _get()(img)
    k = 72.0 / DPI
    words = []
    for box, text, score in result or []:
        text = (text or "").strip()
        if not text or float(score) < min_score:
            continue
        # A faintly printed number is the costliest thing to misread: one the reader is unsure of is
        # left out, so the answer says "not found, see the page" instead of quoting a wrong dimension.
        if sum(ch.isdigit() for ch in text) >= 0.5 * len(text) and float(score) < min_number_score:
            continue
        xs, ys = [p[0] for p in box], [p[1] for p in box]
        words.append([min(xs) * k, min(ys) * k, max(xs) * k, max(ys) * k, text, float(score)])
    if not words:
        return []
    # Two-column pages: when hardly anything crosses the middle of the page, read the left column first.
    mid = page.rect.width / 2
    crossing = sum(1 for w in words if w[0] < mid - 12 and w[2] > mid + 12)
    columns = [words] if crossing > 0.1 * len(words) else [[w for w in words if (w[0] + w[2]) / 2 < mid],
                                                         [w for w in words if (w[0] + w[2]) / 2 >= mid]]
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
