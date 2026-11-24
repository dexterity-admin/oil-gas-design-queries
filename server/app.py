"""HTTP API + static web UI. Run with:  python -m server"""
import hashlib
import hmac
import ipaddress
import json
import os
import re
import socket
import threading
import time
from contextlib import asynccontextmanager

import httpx
import pymupdf
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import chat, config, discovery, ingest, llm, pack, store


@asynccontextmanager
async def lifespan(app):
    import threading
    from .__main__ import lan_address
    store.init()
    ingest.sync_in_background()
    threading.Thread(target=ingest.rescan_forever, daemon=True).start()
    discovery.serve(config.APP_NAME, lambda: "http://%s:%d" % (lan_address(), config.PORT))
    yield


app = FastAPI(title=config.APP_NAME, lifespan=lifespan)
PDF_LOCK = threading.Lock()  # PyMuPDF is not safe to use from two threads at once (indexing and page rendering)


def _host_ok(host):
    """Only answer when addressed by this PC's own name or a private network address. A web page
    on the internet that points its own domain name at this PC ("DNS rebinding") is refused."""
    name = host.rsplit(":", 1)[0].strip("[]").lower() if host else ""
    if name in ("localhost", socket.gethostname().lower()) or name in config.ALLOWED_HOSTS:
        return True
    try:
        ip = ipaddress.ip_address(name)
        return ip.is_loopback or ip.is_private
    except ValueError:
        return False


@app.middleware("http")
async def guard(request: Request, call_next):
    if not _host_ok(request.headers.get("host", "")):
        return Response("This address is not allowed to reach DexMentor Library.", status_code=400)
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin and origin.split("://", 1)[-1] != request.headers.get("host"):  # another web site calling us
            return Response("Cross-site requests are not allowed.", status_code=403)
        size = int(request.headers.get("content-length") or 0)
        limit = 64_000_000 if request.url.path == "/local/chats" else 2_000_000
        if size > limit and not request.url.path.startswith("/api/admin/files/"):
            return Response("Request too large.", status_code=413)
    return await call_next(request)


class ChatRequest(BaseModel):
    messages: list[dict]
    style: str = "english"


@app.get("/api/status")
def status():
    from .__main__ import lan_address
    return {"app": config.APP_NAME, "version": config.VERSION, "llm": llm.describe(), "llm_available": llm.available(), "index": ingest.status,
            "address": "http://%s:%d" % (lan_address(), config.PORT), **store.stats()}


@app.post("/api/reindex")
def reindex(request: Request):
    _admin(request)
    ingest.sync_in_background(retry_failed=True)  # the librarian pressed Re-read: try the failed files again too
    return {"ok": True}


@app.get("/api/documents")
def documents():
    docs = store.list_documents()
    for d in docs:  # say where each document lives, without exposing full server paths to every user
        rel = d.pop("relpath")
        d["linked"] = os.path.isabs(rel)
        d["folder"] = os.path.basename(os.path.dirname(rel)) if d["linked"] else os.path.dirname(rel)
    return docs


@app.post("/api/chat")
async def chat_endpoint(req: ChatRequest):
    async def lines():
        async for event in chat.answer(req.messages, req.style):
            yield json.dumps(event) + "\n"
    return StreamingResponse(lines(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class PrepareRequest(ChatRequest):
    query: str | None = None
    stage: str | None = None
    answer: str | None = None


class VerifyRequest(BaseModel):
    answer: str
    grounding: str


@app.post("/api/prepare")
def prepare(req: PrepareRequest):
    return chat.prepare(req.messages, req.style, req.query, req.stage, answer=req.answer)


class RewordRequest(BaseModel):
    answer: str


@app.post("/api/tanglish")
async def tanglish(req: RewordRequest):
    return await chat.reword_tanglish(req.answer)


@app.post("/api/verify")
def verify(req: VerifyRequest):
    return chat.verify(req.answer, req.grounding)


@app.get("/api/chunks/{chunk_id}")
def chunk(chunk_id: int):
    c = store.get_chunk(chunk_id)
    if not c:
        raise HTTPException(404, "Source not found (the library may have been re-indexed).")
    c.pop("relpath", None)
    return c


def _doc_path(doc_id):
    d = store.get_document(doc_id)
    if not d:
        raise HTTPException(404, "Document not found.")
    path = config.resolve(d["relpath"])
    if path is None or not path.is_file():
        raise HTTPException(404, "The original file cannot be reached right now (is the network drive connected?).")
    return d, path


@app.get("/api/docs/{doc_id}/file")
def original(doc_id: int):
    """The exact original file, so the user can cross-check the citation."""
    d, path = _doc_path(doc_id)
    inline = "inline" if d["kind"] == "pdf" else "attachment"
    return FileResponse(path, filename=d["name"], content_disposition_type=inline)


@app.get("/api/docs/{doc_id}/page/{page}.png")
def page_image(doc_id: int, page: int, chunk: int = 0):
    """A PDF page rendered as an image, with the cited passage highlighted."""
    d, path = _doc_path(doc_id)
    if d["kind"] != "pdf":
        raise HTTPException(400, "Page images are only available for PDF files.")
    c = store.get_chunk(chunk) if chunk else None
    if not (c and c["doc_id"] == doc_id and c["page"] == page):
        c, chunk = None, 0  # an unknown highlight must not create a new cache file
    cache = config.CACHE_DIR / ("%d-%s-%d-%d.png" % (doc_id, d["stamp"], page, chunk))
    if not cache.exists():
        with PDF_LOCK, pymupdf.open(path) as doc:
            if not 1 <= page <= len(doc):
                raise HTTPException(404, "No such page.")
            pg = doc[page - 1]
            if c:
                for r in c["rects"]:
                    pg.draw_rect(pymupdf.Rect(r) + (-2, -2, 2, 2), color=(0.95, 0.6, 0.0),
                                 fill=(1.0, 0.85, 0.2), fill_opacity=0.28, width=0.8)
            zoom = min(2.0, 1500 / max(pg.rect.width, 1))
            png = pg.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).tobytes("png")
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix(".%d.tmp" % threading.get_ident())
        tmp.write_bytes(png)
        tmp.replace(cache)
    return Response(cache.read_bytes(), media_type="image/png", headers={"Cache-Control": "max-age=86400"})


# ---------- Library desk: the librarian's side of the app ----------
_fails = {}  # caller address -> (failed attempts, time of last one)


def _pin_hash(pin):
    return hashlib.sha256(("dexmentor:" + pin).encode()).hexdigest()


def _is_admin(request: Request):
    if config.ADMIN_PIN:
        who = request.client.host
        n, last = _fails.get(who, (0, 0))
        if n >= 5 and time.time() - last < 300:
            raise HTTPException(429, "Too many wrong PINs. Wait five minutes and try again.")
        given = request.headers.get("x-admin-pin", "")
        if not given:
            return False
        stored = config.ADMIN_PIN
        ok = hmac.compare_digest(_pin_hash(given).encode(), stored.encode()) if len(stored) == 64 else \
            hmac.compare_digest(given.encode(), stored.encode())
        _fails[who] = (0, 0) if ok else (n + 1 if time.time() - last < 300 else 1, time.time())
        return ok
    # No PIN set: only the server PC itself may change the library (not a visitor arriving through a proxy or tunnel).
    forwarded = request.headers.get("x-forwarded-for") or request.headers.get("cf-connecting-ip")
    return request.client.host in ("127.0.0.1", "::1") and not forwarded


def _admin(request: Request):
    if not _is_admin(request):
        raise HTTPException(401, "Changing the library needs the admin PIN, or must be done on the server PC.")


class Pin(BaseModel):
    pin: str


@app.post("/api/admin/pin")
def set_pin(body: Pin, request: Request):
    """Sets (or clears, with an empty value) the PIN that lets other PCs manage the library."""
    _admin(request)
    if body.pin and len(body.pin) < 8:
        raise HTTPException(400, "Use at least 8 characters.")
    config.save(admin_pin=_pin_hash(body.pin) if body.pin else "")
    return {"ok": True, "pin_set": bool(body.pin)}


@app.post("/api/admin/quit")
def quit_library(request: Request):
    """Stops the Library program (it has no window of its own to close)."""
    _admin(request)
    threading.Timer(0.5, lambda: os._exit(0)).start()
    return {"ok": True}


# ---------- saved chats of the person at the Library PC itself ----------
# Kept on disk in this Windows user's own data folder, written safely (a temporary file flushed to the disk,
# then swapped in, the previous copy kept as .bak), so a forced restart or a power cut never loses the history.
# Only the Library PC's own window may read or write them: never a visitor from the network.
CHATS = config.DATA_DIR / "chats.json"
_chats_lock = threading.Lock()


def _local_only(request: Request):
    forwarded = request.headers.get("x-forwarded-for") or request.headers.get("cf-connecting-ip")
    host = request.headers.get("host", "").rsplit(":", 1)[0].strip("[]").lower()
    if request.client.host not in ("127.0.0.1", "::1") or forwarded or host not in ("127.0.0.1", "localhost", "::1"):
        raise HTTPException(403, "Saved chats are only available on this PC.")


def load_chats(path=None):
    path = path or CHATS
    for p in (path, path.with_name(path.name + ".bak")):
        try:
            v = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(v, dict) and isinstance(v.get("chats"), list):
                return v
        except (OSError, ValueError):
            pass
    return {"chats": [], "current": None}


def save_chats(v, path=None):
    path = path or CHATS
    if not (isinstance(v, dict) and isinstance(v.get("chats"), list)):
        raise ValueError("not a chat history")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(v, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())  # on the disk, not only in the Windows cache
    if path.exists():
        try:
            import shutil
            shutil.copyfile(path, path.with_name(path.name + ".bak"))
        except OSError:
            pass
    os.replace(tmp, path)


@app.get("/local/chats")
def get_chats(request: Request):
    _local_only(request)
    with _chats_lock:
        return load_chats()


@app.put("/local/chats")
async def put_chats(request: Request):
    _local_only(request)
    try:
        v = json.loads(await request.body())
        with _chats_lock:
            save_chats(v)
    except ValueError:
        raise HTTPException(400, "That is not a chat history.")
    return {"ok": True}


@app.get("/api/admin/check")
def admin_check(request: Request):
    return {"admin": _is_admin(request), "pin_set": bool(config.ADMIN_PIN)}


@app.put("/api/admin/files/{name}")
async def upload(name: str, request: Request):
    """Receives one document into the library folder and starts indexing it."""
    _admin(request)
    name = re.sub(r'[\\/:*?"<>|]', "_", name).strip(". ")
    if not name or "." + name.rsplit(".", 1)[-1].lower() not in config.SUPPORTED:
        raise HTTPException(400, "Only PDF, Word (.docx), Excel (.xlsx) and PowerPoint (.pptx) files can be added.")
    config.LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    if re.fullmatch(r"(con|prn|aux|nul|com\d|lpt\d)(\..*)?", name, re.I):
        raise HTTPException(400, "That file name is not allowed on Windows.")
    part = config.LIBRARY_DIR / (name + ".uploading")
    size = 0
    try:
        with open(part, "wb") as f:
            async for block in request.stream():
                size += len(block)
                if size > 500 * 1024 * 1024:
                    raise HTTPException(413, "The file is larger than 500 MB.")
                f.write(block)
        part.replace(config.LIBRARY_DIR / name)
    finally:
        part.unlink(missing_ok=True)  # a dropped connection must not leave half a file behind
    ingest.sync_in_background()
    return {"ok": True, "name": name, "bytes": size}


@app.delete("/api/admin/docs/{doc_id}")
def remove(doc_id: int, request: Request):
    _admin(request)
    d, path = _doc_path(doc_id)
    if config.LIBRARY_DIR not in path.parents:
        raise HTTPException(400, "This document lives in a linked folder. DexMentor never deletes files there: "
                                 "remove it in that folder, or unlink the folder.")
    path.unlink()
    with store.db() as con:
        store.delete_document(con, doc_id)
    store.invalidate_vectors()
    return {"ok": True}


@app.get("/api/admin/docs/{doc_id}/passages")
def passages(doc_id: int):
    """Everything that was read out of one document, so the librarian can check the reading is right."""
    return store.list_chunks(doc_id)


class Folder(BaseModel):
    path: str


@app.get("/api/admin/folders")
def folders(request: Request = None):
    seen = ingest.status.get("folders", {})
    full = request is None or _is_admin(request)  # other PCs see the folder's name, not the server's full path
    return [{"path": f if full else os.path.basename(f.rstrip("\\/")) or f,
             **seen.get(f, {"reachable": os.path.isdir(f), "documents": 0, "skipped": 0})} for f in config.FOLDERS]


def _too_broad(path):
    """A whole drive, the Windows folder or a user's profile is never a standards library."""
    p = os.path.normcase(os.path.abspath(path)).rstrip("\\/")
    drive, rest = os.path.splitdrive(p)
    if not rest.strip("\\/") and not drive.startswith(("\\\\", "//")):
        return True
    banned = [os.getenv("SystemRoot"), os.getenv("ProgramFiles"), os.getenv("ProgramFiles(x86)"), os.getenv("ProgramData"),
              os.path.expanduser("~"), os.path.dirname(os.path.expanduser("~"))]
    return any(b and p == os.path.normcase(os.path.abspath(b)).rstrip("\\/") for b in banned) or \
        any(b and p.startswith(os.path.normcase(os.path.abspath(b)) + os.sep) for b in banned[:4])


@app.post("/api/admin/folders")
def folder_add(body: Folder, request: Request):
    """Links a folder (with its sub-folders) that this PC can read, such as a mapped network drive."""
    _admin(request)
    path = body.path.strip().strip('"')
    if not os.path.isabs(path) or not os.path.isdir(path):
        raise HTTPException(400, "This PC cannot open that folder. Use the full path as this PC sees it, for example "
                                 "Z:\\Standards or \\\\fileserver\\Standards.")
    path = os.path.abspath(path)
    if _too_broad(path):
        raise HTTPException(400, "That folder is too broad (a whole drive, a system folder or a user profile). "
                                 "Link the folder that holds the standards.")
    if os.path.normcase(path) not in {os.path.normcase(f) for f in config.FOLDERS}:  # one folder, however it is typed
        config.save(folders=config.FOLDERS + [path])
    ingest.sync_in_background()
    return folders(request)


@app.post("/api/admin/folders/unlink")
def folder_remove(body: Folder, request: Request):
    _admin(request)
    config.save(folders=[f for f in config.FOLDERS if f != body.path])
    ingest.sync_in_background()
    return folders(request)


class OfficeModel(BaseModel):
    url: str = ""
    model: str = ""


async def _ollama_models(url):
    try:
        async with httpx.AsyncClient(timeout=4) as c:
            r = await c.get(url.rstrip("/") + "/api/tags")
            return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        return None


def _private_url(url):
    try:
        host = url.split("://", 1)[1].split("/", 1)[0].rsplit(":", 1)[0].strip("[]")
        infos = socket.getaddrinfo(host, None)
        return all(ipaddress.ip_address(i[4][0]).is_private or ipaddress.ip_address(i[4][0]).is_loopback for i in infos)
    except (OSError, ValueError, IndexError):
        return False


@app.get("/api/admin/office")
async def office_get(request: Request = None):
    on = config.LLM_PROVIDER == "ollama"
    if request is not None and not _is_admin(request):
        return {"enabled": on}
    models = await _ollama_models(config.OLLAMA_URL) if on else None
    return {"enabled": on, "url": config.OLLAMA_URL, "model": config.OLLAMA_MODEL, "reachable": models is not None,
            "models": models or [], "model_present": bool(models) and any(m.split(":")[0] == config.OLLAMA_MODEL.split(":")[0] for m in models)}


@app.post("/api/admin/office")
async def office_set(body: OfficeModel, request: Request):
    """Connects (or disconnects) the office PC that runs the larger model through Ollama."""
    _admin(request)
    if not body.url:
        config.save(provider="none")
        return await office_get()
    if not body.url.startswith(("http://", "https://")):
        raise HTTPException(400, "The address must start with http:// or https://")
    if not _private_url(body.url):
        raise HTTPException(400, "The office model must be a PC on your own network: questions and document "
                                 "passages are sent to it.")
    config.save(provider="ollama", ollama_url=body.url.rstrip("/"), ollama_model=body.model or config.OLLAMA_MODEL)
    return await office_get()


class StarterPack(BaseModel):
    repo: str = ""
    token: str = ""


@app.get("/api/admin/pack")
def pack_get(request: Request):
    return pack.status(full=_is_admin(request))


@app.post("/api/admin/pack")
def pack_set(body: StarterPack, request: Request):
    """Connects the Library to the private repository that holds the company's starter documents."""
    _admin(request)
    if not body.repo:
        pack.disconnect()
    else:
        repo = pack.clean_repo(body.repo)
        if not repo:
            raise HTTPException(400, "Type the repository as owner/name, for example dexterity-admin/dexmentor-starter-pack.")
        token = body.token.strip() or (config.PACK_TOKEN if repo == config.PACK_REPO else "")
        if not token:
            raise HTTPException(400, "Paste the read-only access key for that repository.")
        config.save(pack_repo=repo, pack_token=token, pack_sha="")
        pack.fetch(force=True)
    ingest.sync_in_background()
    return pack.status(full=True)


app.mount("/", StaticFiles(directory=config.WEB_DIR, html=True), name="web")
