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


def test_drawing_debris_is_still_dropped():
    for junk in ["C.'~I~DE:", "1ZaA0J~ rbJ.pz~L", "lI 1Z 4a", "~~ 12 ~~"]:
        assert ingest._readable(junk) == "", junk
