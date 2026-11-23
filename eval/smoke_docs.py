"""Release smoke test for the packaged Library: a scanned page and a Word table, both generated here.

Usage: smoke_docs.py <library_url> <corpus_dir>   (corpus_dir made by make_corpus.py)
The scanned PDF is a picture-only copy of the tank standard, so every line and number of the
original must come back out of the built-in reader. Exits non-zero with the reason on failure.
"""
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pymupdf

BASE, CORPUS = sys.argv[1].rstrip("/"), Path(sys.argv[2])
TANK, WORD = CORPUS / "DDS-ST-001 Tank Design Basis.pdf", CORPUS / "DDS-PA-003 Painting Spec.docx"
NUM = r"(?<![\d.])\d+(?:\.\d+)?(?![\d.])"


def call(path, data=None, method=None):
    req = urllib.request.Request(BASE + path, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def fail(msg):
    print("SMOKE FAIL:", msg)
    sys.exit(1)


def scanned_copy(src, dst):
    out = pymupdf.open()
    with pymupdf.open(src) as d:
        for pg in d:
            pix = pg.get_pixmap(dpi=200, colorspace=pymupdf.csGRAY)
            out.new_page(width=pg.rect.width, height=pg.rect.height).insert_image(pg.rect, stream=pix.tobytes("png"))
    out.save(dst)


scan = CORPUS.parent / "SCAN Tank Design Basis.pdf"
scanned_copy(TANK, scan)
before = call("/api/status")["documents"]
for f in (scan, WORD):
    call("/api/admin/files/" + urllib.parse.quote(f.name), f.read_bytes(), "PUT")
t0 = time.time()
while time.time() - t0 < 600:
    time.sleep(3)
    s = call("/api/status")
    if s["index"]["state"] == "idle" and s["documents"] >= before + 2:
        break
else:
    fail("indexing did not finish in 10 minutes: %s" % s["index"])
print("indexed in %.0f s" % (time.time() - t0))
docs = {d["name"]: d for d in call("/api/documents")}
squash = lambda s: re.sub(r"\s+", "", s).lower()

# Scanned page: every line and every number of the original is read back.
d = docs.get(scan.name) or fail("scanned document missing from the register")
if "scanned" not in d["note"]:
    fail("scanned document was not read by the built-in reader: note=%r" % d["note"])
read = " ".join(p["text"] for p in call("/api/admin/docs/%d/passages" % d["id"]))
with pymupdf.open(TANK) as src:
    original = "\n".join(p.get_text() for p in src)
lost = [ln for ln in original.splitlines() if ln.strip() and squash(ln) not in squash(read)]
nums = set(re.findall(NUM, read))
lost_nums = sorted({n for n in re.findall(NUM, original) if n not in nums})
if lost or lost_nums:
    fail("scan reader lost lines %s and numbers %s" % (lost, lost_nums))
print("scanned page: all %d lines read" % len([ln for ln in original.splitlines() if ln.strip()]))

# Word table: the table is kept as a table, and a question about one row is answered from that row.
d = docs.get(WORD.name) or fail("Word document missing from the register")
tables = [p["text"] for p in call("/api/admin/docs/%d/passages" % d["id"]) if p["kind"] == "table"]
if not any(re.search(r"Zinc rich epoxy primer\s*\|\s*75\b", t) for t in tables):
    fail("Word table row 'Zinc rich epoxy primer | 75' not found in %s" % tables)
q = {"messages": [{"role": "user", "content": "What is the dry film thickness of the zinc rich epoxy primer?"}]}
p = call("/api/prepare", json.dumps(q).encode())
prompt = json.dumps(p.get("messages", []))
if p.get("skip_model") or "75" not in prompt:
    fail("question about the Word table did not reach the primer row: %s" % json.dumps(p)[:800])
print("Word table: row found and used for the answer")
print("smoke docs passed")
