# llm_server.py
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from llama_cpp import Llama
import os
import uvicorn
import traceback
import json
import asyncio # Added for potential async sleep

# --- Configuration ---
MODEL_PATH = r"D:\Py\Oil & Gas Design Queries\models\qwen2.5-0.5b-instruct-q5_k_m.gguf"
N_CTX = 2048
N_GPU_LAYERS = 0
N_THREADS = os.cpu_count() or 4
HOST = "127.0.0.1"
PORT = 8000
# --- End Configuration ---

app = FastAPI(title="LLM Inference Server (Streaming)", version="0.2.1")
llama_instance: Llama | None = None

@app.on_event("startup")
async def startup_event():
    global llama_instance
    print(f"[LLM Server] Loading LLM: {MODEL_PATH}")
    try:
        from llama_cpp import Llama # Import within function
        llama_instance = Llama(model_path=MODEL_PATH, n_ctx=N_CTX, n_gpu_layers=N_GPU_LAYERS, n_threads=N_THREADS, verbose=False)
        print(f"[LLM Server] LLM model loaded successfully.")
    except Exception as e:
        print(f"[LLM Server] CRITICAL ERROR loading LLM: {e}\n{traceback.format_exc()}")
        llama_instance = None

class InferenceRequest(BaseModel):
    prompt: str
    max_tokens: int = 512
    temperature: float = 0.6
    stop: list[str] = ["<|im_end|>", "<|endoftext|>"]

async def generate_stream_sse(request: InferenceRequest):
    global llama_instance
    if llama_instance is None:
        error_detail = "LLM model is not available on the server."; print(f"[LLM Server] Stream Error: {error_detail}")
        yield f"data: {json.dumps({'error': error_detail})}\n\n"; return

    print(f"[LLM Server] Received stream request (prompt len: {len(request.prompt)} chars).")
    try:
        stream = llama_instance.create_completion(
            prompt=request.prompt, max_tokens=request.max_tokens, temperature=request.temperature,
            stop=request.stop, stream=True
        )
        token_count = 0
        for output_chunk in stream:
            if "choices" in output_chunk and output_chunk["choices"]:
                text_piece = output_chunk["choices"][0].get("text", "")
                if text_piece:
                    token_count += 1; yield f"data: {json.dumps({'text': text_piece})}\n\n"
                    # await asyncio.sleep(0.005) # Tiny delay to simulate streaming visually if needed locally

        print(f"[LLM Server] Stream finished. Yielded {token_count} token chunks.")
        yield f"data: {json.dumps({'end_of_stream': True, 'token_count': token_count})}\n\n"
    except Exception as e:
        error_detail = f"LLM inference error during stream: {e}"
        print(f"[LLM Server] Error during stream: {e}\n{traceback.format_exc()}")
        yield f"data: {json.dumps({'error': error_detail})}\n\n"

# --- >> CORRECTED Endpoint Name << ---
@app.post("/generate") # Changed back to /generate
async def stream_text_endpoint(request: InferenceRequest):
    """Endpoint for streaming text generation using Server-Sent Events."""
    # Ensure the generator is passed correctly to StreamingResponse
    return StreamingResponse(generate_stream_sse(request), media_type="text/event-stream")
# --- >> END CORRECTION << ---

@app.get("/")
async def read_root():
    model_status = "Loaded" if llama_instance else "Not Loaded/Error"
    return {"message": "LLM Inference Server is running.", "model_status": model_status}

@app.get("/docs")
async def get_docs():
    # Need to import HTMLResponse for this specific route if customizing
    from fastapi.responses import HTMLResponse
    # You can return custom HTML or rely on FastAPI's default /docs
    return HTMLResponse("<html><body><h1>LLM Server Docs</h1><p>Use POST /generate</p></body></html>")


if __name__ == "__main__":
    print(f"[LLM Server] Starting Uvicorn server on http://{HOST}:{PORT}"); print(f"[LLM Server] Using model: {MODEL_PATH}")
    uvicorn.run(app, host=HOST, port=PORT)