"""All settings come from environment variables so nothing is hardcoded to one PC."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

APP_NAME = os.getenv("APP_NAME", "DexGuide")  # working name, change in one place
LIBRARY_DIR = Path(os.getenv("LIBRARY_DIR", ROOT / "library")).resolve()  # drop standards here
DATA_DIR = Path(os.getenv("DATA_DIR", ROOT / "kb")).resolve()  # index + page-image cache
DB_PATH = DATA_DIR / "index.sqlite"
CACHE_DIR = DATA_DIR / "pages"
WEB_DIR = ROOT / "web"

HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8700"))

# LLM: "anthropic" (cloud), "ollama" (own machine), or "none" (search-only answers).
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic" if os.getenv("ANTHROPIC_API_KEY") else "none").lower()
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-5-5")
LLM_EFFORT = os.getenv("LLM_EFFORT", "low")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")

EMBEDDINGS = os.getenv("EMBEDDINGS", "auto").lower()  # auto | off
EMBED_MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-small-en-v1.5")

CHUNK_CHARS = int(os.getenv("CHUNK_CHARS", "1200"))
TOP_K = int(os.getenv("TOP_K", "6"))
SUPPORTED = {".pdf", ".docx", ".xlsx", ".xlsm", ".pptx"}
