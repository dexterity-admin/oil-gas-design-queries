"""HTTP API + static web UI. Run with:  python -m server"""
import hmac
import json
import re
from contextlib import asynccontextmanager

import httpx
import pymupdf
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import chat, config, ingest, llm, store


@asynccontextmanager
async def lifespan(app):
    store.init()
    ingest.sync_in_background()
    yield


app = FastAPI(title=config.APP_NAME, lifespan=lifespan)


class ChatRequest(BaseModel):
    messages: list[dict]
    style: str = "english"


@app.get("/api/status")
def status():
    from .__main__ import lan_address
    return {"app": config.APP_NAME, "llm": llm.describe(), "llm_available": llm.available(), "index": ingest.status,
            "address": "http://%s:%d" % (lan_address(), config.PORT), **store.stats()}


@app.post("/api/reindex")
def reindex():
    ingest.sync_in_background()
    return {"ok": True}


@app.get("/api/documents")
def documents():
    return store.list_documents()


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
    path = (config.LIBRARY_DIR / d["relpath"]).resolve()
    if not path.is_file() or config.LIBRARY_DIR not in path.parents:
        raise HTTPException(404, "The original file is no longer in the library.")
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
    cache = config.CACHE_DIR / ("%d-%s-%d-%d.png" % (doc_id, d["stamp"], page, chunk))
    if not cache.exists():
        with pymupdf.open(path) as doc:
            if not 1 <= page <= len(doc):
                raise HTTPException(404, "No such page.")
            pg = doc[page - 1]
            c = store.get_chunk(chunk) if chunk else None
            if c and c["doc_id"] == doc_id and c["page"] == page:
                for r in c["rects"]:
                    pg.draw_rect(pymupdf.Rect(r) + (-2, -2, 2, 2), color=(0.95, 0.6, 0.0),
                                 fill=(1.0, 0.85, 0.2), fill_opacity=0.28, width=0.8)
            zoom = min(2.0, 1500 / max(pg.rect.width, 1))
            png = pg.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).tobytes("png")
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(png)
    return Response(cache.read_bytes(), media_type="image/png", headers={"Cache-Control": "max-age=86400"})


# ---------- Library desk: the librarian's side of the app ----------
def _is_admin(request: Request):
    if config.ADMIN_PIN:
        return hmac.compare_digest(request.headers.get("x-admin-pin", ""), config.ADMIN_PIN)
    # No PIN set: only the server PC itself may change the library (not a visitor arriving through a proxy or tunnel).
    forwarded = request.headers.get("x-forwarded-for") or request.headers.get("cf-connecting-ip")
    return request.client.host in ("127.0.0.1", "::1") and not forwarded


def _admin(request: Request):
    if not _is_admin(request):
        raise HTTPException(401, "Changing the library needs the admin PIN, or must be done on the server PC.")


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
    part = config.LIBRARY_DIR / (name + ".uploading")
    size = 0
    with open(part, "wb") as f:
        async for block in request.stream():
            size += len(block)
            if size > 500 * 1024 * 1024:
                f.close(); part.unlink(missing_ok=True)
                raise HTTPException(413, "The file is larger than 500 MB.")
            f.write(block)
    part.replace(config.LIBRARY_DIR / name)
    ingest.sync_in_background()
    return {"ok": True, "name": name, "bytes": size}


@app.delete("/api/admin/docs/{doc_id}")
def remove(doc_id: int, request: Request):
    _admin(request)
    d, path = _doc_path(doc_id)
    path.unlink()
    with store.db() as con:
        store.delete_document(con, doc_id)
    store.invalidate_vectors()
    return {"ok": True}


@app.get("/api/admin/docs/{doc_id}/passages")
def passages(doc_id: int):
    """Everything that was read out of one document, so the librarian can check the reading is right."""
    return store.list_chunks(doc_id)


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


@app.get("/api/admin/office")
async def office_get():
    on = config.LLM_PROVIDER == "ollama"
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
    config.save(provider="ollama", ollama_url=body.url.rstrip("/"), ollama_model=body.model or config.OLLAMA_MODEL)
    return await office_get()


app.mount("/", StaticFiles(directory=config.WEB_DIR, html=True), name="web")
