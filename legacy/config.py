# config.py
import os
from pathlib import Path # Added pathlib

# --- >> NEW: Supported File Extensions << ---
SUPPORTED_EXTENSIONS = ['.pdf', '.pptx', '.docx']
# --- >> END NEW << ---

# --- Tesseract Configuration ---
TESSERACT_CMD = None
TESSDATA_PREFIX = None

# --- Embedding Model ---
EMBEDDING_MODEL_NAME = 'all-MiniLM-L6-v2'

# --- ChromaDB Configuration ---
DEFAULT_CHROMA_PATH = "chroma_db"
COLLECTION_NAME = "pdf_knowledge_base"
IMAGE_OUTPUT_SUBFOLDER = "images"

# --- Processing Configuration ---
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 150
STOP_CHECK_INTERVAL = 5 # Used only for PDF page iteration check

# --- Worker Resource Control ---
DEFAULT_WORKER_COUNT = max(1, os.cpu_count() // 2)

# --- Logging ---
LOG_FILE = "processing_log.log"
PROCESSED_LOG_FILE = "processed_pdfs.log" # Consider renaming this if handling more types

# --- Helper function for safe filenames ---
def sanitize_filename(name):
    """Removes or replaces characters unsafe for filenames."""
    name = name.replace('/', '_').replace('\\', '_')
    invalid_chars = r'<>:"|?*'
    for char in invalid_chars: name = name.replace(char, '')
    name = name.replace(' ', '_')
    max_len = 100
    if len(name) > max_len: name = name[:max_len]
    return name