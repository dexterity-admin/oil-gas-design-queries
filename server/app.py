"""HTTP API + static web UI. Run with:  python -m server"""
import json
from contextlib import asynccontextmanager

import pymupdf
from fastapi import FastAPI, HTTPException
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
    return {"app": config.APP_NAME, "llm": llm.describe(), "llm_available": llm.available(), "index": ingest.status, **store.stats()}


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


app.mount("/", StaticFiles(directory=config.WEB_DIR, html=True), name="web")
