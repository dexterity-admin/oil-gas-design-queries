"""Run with:  python -m pytest tests
Each case is text the scan reader once lost or must keep losing."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import ingest


def test_scanned_table_rows_with_short_words_are_kept():
    # Seen on a generated 200 dpi scan: "Up to 15 | 24 | 8" was dropped and "Above 15 | 48 | 16" kept,
    # so a 10 m tank question could only find the 48 h row.
    read = ingest._readable("Tank diameter (m) Hold time (h) Settlement points\nUp to 15 24 8\nAbove15 48 16")
    assert "Up to 15 24 8" in read.splitlines()
    assert "Above15 48 16" in read.splitlines()
    for row in ["2 to 4 3.2 1.6", "Sch 40 6.02", "Over 600 12.5 %", "Up to 2 40 60", "3to8 50 80"]:
        assert ingest._readable(row) == row


def test_pdf_lock_is_let_go_between_pages(tmp_path):
    # Seen on a real PC: reading a 445-page scanned book held the PyMuPDF lock for over an hour, and
    # every citation page image in the app hung until it finished.
    import threading, pymupdf
    doc = pymupdf.open()
    for i in range(5):
        doc.new_page().insert_text((72, 72), "Page %d of a test document about pipe supports." % (i + 1))
    doc.save(tmp_path / "five.pdf")

    class Watched:
        def __init__(self):
            self.lock, self.gaps = threading.Lock(), 0
        def acquire(self):
            self.lock.acquire()
        def release(self):
            self.gaps += 1
            self.lock.release()

    w = Watched()
    w.acquire()
    ingest.parse_pdf(tmp_path / "five.pdf", lock=w)
    assert w.gaps == 4 and w.lock.locked()   # let go before pages 2 to 5, and held again when it returns


def test_drawing_debris_is_still_dropped():
    for junk in ["C.'~I~DE:", "1ZaA0J~ rbJ.pz~L", "lI 1Z 4a", "~~ 12 ~~"]:
        assert ingest._readable(junk) == "", junk
