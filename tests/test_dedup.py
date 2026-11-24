"""Run with:  python -m pytest tests
The indexer must never read the same content twice: copies, moves, renames, touched files,
overlapping linked folders and files that failed before are all recognised."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from server import config, ingest, store


@pytest.fixture
def lib(tmp_path, monkeypatch):
    library, data = tmp_path / "library", tmp_path / "kb"
    library.mkdir()
    for k, v in {"LIBRARY_DIR": library, "DATA_DIR": data, "DB_PATH": data / "index.sqlite",
                 "CACHE_DIR": data / "pages", "STARTER_DIR": tmp_path / "none", "FOLDERS": [],
                 "EMBEDDINGS": "off"}.items():
        monkeypatch.setattr(config, k, v)
    monkeypatch.setattr(ingest.pack, "fetch", lambda *a, **k: False)
    reads = []

    def parse(path):
        reads.append(path.name)
        text = path.read_text(encoding="utf-8")
        if text.startswith("BROKEN"):
            raise ValueError("not a Word file")
        return 1, [{"page": 1, "loc": "p1", "kind": "text", "text": text}], ""

    monkeypatch.setitem(ingest.PARSERS, ".docx", parse)
    store.init()

    def run(**kw):
        reads.clear()
        ingest.sync(**kw)
        with store.db() as con:
            docs = sorted(r[0] for r in con.execute("SELECT relpath FROM documents"))
        return list(reads), docs

    run.dir = library
    return run


def put(path, text, mtime=1_700_000_000):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.utime(path, (mtime, mtime))


def test_unchanged_library_is_not_read_again(lib):
    put(lib.dir / "a.docx", "ASME B31.3 clause")
    assert lib() == (["a.docx"], ["a.docx"])
    assert lib() == ([], ["a.docx"])


def test_copy_is_read_once_and_listed(lib):
    put(lib.dir / "a.docx", "API 650 tank")
    put(lib.dir / "sub" / "a copy.docx", "API 650 tank")
    reads, docs = lib()
    assert len(reads) == 1 and len(docs) == 1
    assert ingest.status["duplicate_count"] == 1
    assert lib() == ([], docs)  # the copy is remembered: not fingerprinted or read again
    assert ingest.status["duplicate_count"] == 1


def test_move_and_rename_keep_what_was_read(lib):
    put(lib.dir / "a.docx", "ISO 15156")
    lib()
    with store.db() as con:
        before = con.execute("SELECT id FROM documents").fetchone()[0]
    (lib.dir / "new").mkdir()
    (lib.dir / "a.docx").rename(lib.dir / "new" / "b.docx")
    assert lib() == ([], [os.path.join("new", "b.docx")])
    with store.db() as con:
        assert con.execute("SELECT id, name FROM documents").fetchone()[:] == (before, "b.docx")


def test_swapped_names_are_both_kept(lib):
    put(lib.dir / "x.docx", "first standard")
    put(lib.dir / "y.docx", "second standard, longer")
    lib()
    (lib.dir / "x.docx").rename(lib.dir / "t.docx")
    (lib.dir / "y.docx").rename(lib.dir / "x.docx")
    (lib.dir / "t.docx").rename(lib.dir / "y.docx")
    assert lib() == ([], ["x.docx", "y.docx"])


def test_deleting_the_original_promotes_the_copy(lib):
    put(lib.dir / "a.docx", "MSS SP-58")
    put(lib.dir / "b.docx", "MSS SP-58")
    lib()
    (lib.dir / "a.docx").unlink()
    assert lib() == ([], ["b.docx"])


def test_saved_without_change_is_not_read(lib):
    put(lib.dir / "a.docx", "same words")
    lib()
    put(lib.dir / "a.docx", "same words", mtime=1_800_000_000)
    assert lib() == ([], ["a.docx"])


def test_changed_file_is_read(lib):
    put(lib.dir / "a.docx", "rev 0")
    lib()
    put(lib.dir / "a.docx", "rev A with changes", mtime=1_800_000_000)
    assert lib() == (["a.docx"], ["a.docx"])


def test_broken_file_is_not_retried_until_asked_or_changed(lib):
    put(lib.dir / "bad.docx", "BROKEN")
    assert lib()[0] == ["bad.docx"]
    assert lib()[0] == []
    assert any("not read again" in e for e in ingest.status["errors"])
    put(lib.dir / "bad copy.docx", "BROKEN")
    assert lib()[0] == []  # the same broken content under another name
    assert len(lib(retry_failed=True)[0]) == 1  # asked again: that content is read once, not once per name
    put(lib.dir / "bad.docx", "fixed now", mtime=1_800_000_000)
    assert lib()[0] == ["bad.docx"]


def test_overlapping_linked_folders_are_read_once(lib, tmp_path, monkeypatch):
    share = tmp_path / "share"
    put(share / "piping" / "a.docx", "client spec")
    monkeypatch.setattr(config, "FOLDERS", [str(share), str(share / "piping").upper()])
    reads, docs = lib()
    assert len(reads) == 1 and len(docs) == 1


def test_switched_off_drive_keeps_its_documents(lib, tmp_path, monkeypatch):
    share = tmp_path / "share"
    put(share / "a.docx", "client spec")
    monkeypatch.setattr(config, "FOLDERS", [str(share)])
    lib()
    os.rename(share, tmp_path / "away")
    assert lib()[1] == [str(share / "a.docx")]


def test_old_index_with_doubles_is_cleaned(lib):
    # Indexes made before fingerprints could hold the same document twice.
    for n in ("a.docx", "b.docx"):
        put(lib.dir / n, "duplicated")
    with store.db() as con:
        for n in ("a.docx", "b.docx"):
            store.add_document(con, n, n, "docx", ingest._stamp(lib.dir / n), 1, "", [])
    assert lib() == ([], ["a.docx"])


def test_same_bytes_under_another_type_do_not_hide_the_real_file(lib, monkeypatch):
    monkeypatch.setitem(ingest.PARSERS, ".xlsx", ingest.PARSERS[".docx"])
    put(lib.dir / "a.docx", "BROKEN as Word")
    put(lib.dir / "a.xlsx", "BROKEN as Word")
    assert sorted(lib()[0]) == ["a.docx", "a.xlsx"]


def test_missing_reader_is_not_remembered_as_a_broken_file(lib, monkeypatch):
    def no_reader(path):
        raise ImportError("No module named 'docx'")
    monkeypatch.setitem(ingest.PARSERS, ".docx", no_reader)
    put(lib.dir / "a.docx", "fine")
    lib()
    with store.db() as con:
        assert con.execute("SELECT COUNT(*) FROM seen").fetchone()[0] == 0
