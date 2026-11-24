"""Saved chats on the Library PC: kept on disk safely, and readable only from the PC itself."""
import json

import pytest
from fastapi.testclient import TestClient

from server import app as appmod

HISTORY = {"chats": [{"id": "c1", "title": "Flange bolts", "updated": 1, "messages": [{"role": "user", "content": "M20 torque?"}]}],
           "current": "c1"}


def test_save_load_and_backup(tmp_path):
    f = tmp_path / "chats.json"
    assert appmod.load_chats(f) == {"chats": [], "current": None}
    appmod.save_chats(HISTORY, f)
    assert appmod.load_chats(f) == HISTORY
    newer = {**HISTORY, "current": None}
    appmod.save_chats(newer, f)
    f.write_text("{half a fi", encoding="utf-8")  # a damaged file: the previous copy is used
    assert appmod.load_chats(f) == HISTORY
    assert not (tmp_path / "chats.json.tmp").exists()
    with pytest.raises(ValueError):
        appmod.save_chats({"nope": 1}, f)


def test_only_this_pc(tmp_path, monkeypatch):
    monkeypatch.setattr(appmod, "CHATS", tmp_path / "chats.json")
    here = TestClient(appmod.app, base_url="http://127.0.0.1:8700", client=("127.0.0.1", 50000))
    assert here.put("/local/chats", content=json.dumps(HISTORY)).json() == {"ok": True}
    assert here.get("/local/chats").json() == HISTORY
    assert here.put("/local/chats", content=b"[1, 2]").status_code == 400
    lan = TestClient(appmod.app, base_url="http://192.168.1.20:8700", client=("192.168.1.30", 50000))
    assert lan.get("/local/chats").status_code == 403
    tunnel = TestClient(appmod.app, base_url="http://127.0.0.1:8700", client=("127.0.0.1", 50000))
    assert tunnel.get("/local/chats", headers={"x-forwarded-for": "8.8.8.8"}).status_code == 403
