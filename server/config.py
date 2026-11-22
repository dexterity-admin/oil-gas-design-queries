"""All settings come from environment variables so nothing is hardcoded to one PC."""
import json
import os
import sys
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)  # running as the packaged "DexMentor Library" program
ROOT = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent.parent

APP_NAME = os.getenv("APP_NAME", "DexMentor")
LIBRARY_DIR = Path(os.getenv("LIBRARY_DIR", ROOT / "library")).resolve()  # drop standards here
DATA_DIR = Path(os.getenv("DATA_DIR", ROOT / "kb")).resolve()  # index + page-image cache
DB_PATH = DATA_DIR / "index.sqlite"
CACHE_DIR = DATA_DIR / "pages"
WEB_DIR = (Path(getattr(sys, "_MEIPASS", ROOT)) if FROZEN else ROOT) / "web"

HOST = os.getenv("HOST", "0.0.0.0" if FROZEN else "127.0.0.1")  # the packaged program serves the office network
# Adding and removing documents is allowed from the server PC itself, or from anywhere with this PIN.
ADMIN_PIN = os.getenv("ADMIN_PIN", "")
PORT = int(os.getenv("PORT", "8700"))

# LLM: "anthropic" (cloud), "ollama" (own machine), or "none" (search-only answers).
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic" if os.getenv("ANTHROPIC_API_KEY") else "none").lower()
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-5-5")
LLM_EFFORT = os.getenv("LLM_EFFORT", "low")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "granite3.3:8b")

EMBEDDINGS = os.getenv("EMBEDDINGS", "auto").lower()  # auto | off
EMBED_MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-small-en-v1.5")

CHUNK_CHARS = int(os.getenv("CHUNK_CHARS", "1200"))
TOP_K = int(os.getenv("TOP_K", "6"))
# When the answer is written by the small model on the user's own PC, send it fewer, shorter
# sources: on an old CPU every extra 1000 characters adds seconds before the first word.
LOCAL_TOP_K = int(os.getenv("LOCAL_TOP_K", "4"))
LOCAL_SOURCE_CHARS = int(os.getenv("LOCAL_SOURCE_CHARS", "1400"))
SUPPORTED = {".pdf", ".docx", ".xlsx", ".xlsm", ".pptx"}


# Choices made in the Library desk (office model address, admin PIN) are remembered here.
SETTINGS_FILE = DATA_DIR / "settings.json"


def load_saved():
    global LLM_PROVIDER, OLLAMA_URL, OLLAMA_MODEL, ADMIN_PIN
    try:
        saved = json.loads(SETTINGS_FILE.read_text())
    except (OSError, ValueError):
        return
    LLM_PROVIDER = saved.get("provider", LLM_PROVIDER)
    OLLAMA_URL = saved.get("ollama_url", OLLAMA_URL)
    OLLAMA_MODEL = saved.get("ollama_model", OLLAMA_MODEL)
    ADMIN_PIN = saved.get("admin_pin", ADMIN_PIN)


def save(**changes):
    try:
        saved = json.loads(SETTINGS_FILE.read_text())
    except (OSError, ValueError):
        saved = {}
    saved.update(changes)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(saved, indent=1))
    load_saved()


load_saved()
