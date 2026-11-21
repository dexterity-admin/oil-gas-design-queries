# config_sqa.py
import configparser
import os
from pathlib import Path
# No sys import needed here as get_resource_path for model is removed

# --- Default Settings ---
# Default ChromaDB path (can be relative or absolute)
DEFAULT_CHROMA_DB_PATH = r"D:\Py\Oil & Gas Design Queries\data" # User should configure this
# --- >> NEW: Default URL for the LLM FastAPI Server << ---
DEFAULT_LLM_SERVER_URL = "http://127.0.0.1:8000" # Default for local testing
# --- >> END NEW << ---
# Embedding model used during indexing (MUST match)
DEFAULT_EMBEDDING_MODEL = 'all-MiniLM-L6-v2'
# Default number of relevant chunks to retrieve
DEFAULT_TOP_K_RESULTS = 3

# --- Constants ---
# Name of the ChromaDB collection created by the indexer
COLLECTION_NAME = "pdf_knowledge_base" # This MUST match the name used by your Indexer app

# --- Configuration File ---
CONFIG_FILENAME = "sqa_settings.ini"
CONFIG_SECTION = "Settings"

def get_config_path():
    """Gets the path for the user settings file in the user's home directory."""
    home = Path.home()
    config_dir = home / ".sqa_og_assistant_settings" # Consistent hidden directory name
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir / CONFIG_FILENAME

def load_settings():
    """Loads user-configurable settings from the INI file."""
    config_path = get_config_path()
    parser = configparser.ConfigParser()
    settings = {}

    if config_path.exists():
        try:
            parser.read(config_path)
            if CONFIG_SECTION in parser:
                settings = dict(parser[CONFIG_SECTION])
        except Exception as e:
            print(f"Error reading configuration file {config_path}: {e}")

    # Apply defaults for user-configurable settings
    settings.setdefault('chroma_db_path', DEFAULT_CHROMA_DB_PATH)
    # --- >> CHANGED: Use LLM Server URL instead of local model path << ---
    settings.setdefault('llm_server_url', DEFAULT_LLM_SERVER_URL)
    # --- >> END CHANGE << ---
    settings.setdefault('embedding_model', DEFAULT_EMBEDDING_MODEL) # For display/consistency
    settings.setdefault('top_k', str(DEFAULT_TOP_K_RESULTS))

    try:
        settings['top_k'] = int(settings['top_k'])
    except (ValueError, TypeError):
        print(f"Warning: Invalid 'top_k' value, using default: {DEFAULT_TOP_K_RESULTS}")
        settings['top_k'] = DEFAULT_TOP_K_RESULTS

    return settings

def save_settings(settings):
    """Saves user-configurable settings to the INI file."""
    config_path = get_config_path()
    parser = configparser.ConfigParser()

    settings_to_save = {
        'chroma_db_path': str(settings.get('chroma_db_path', DEFAULT_CHROMA_DB_PATH)),
        # --- >> CHANGED: Save LLM Server URL << ---
        'llm_server_url': str(settings.get('llm_server_url', DEFAULT_LLM_SERVER_URL)),
        # --- >> END CHANGE << ---
        'embedding_model': str(settings.get('embedding_model', DEFAULT_EMBEDDING_MODEL)),
        'top_k': str(settings.get('top_k', DEFAULT_TOP_K_RESULTS))
    }
    parser[CONFIG_SECTION] = settings_to_save

    try:
        with open(config_path, 'w') as configfile:
            parser.write(configfile)
        print(f"Settings saved successfully to {config_path}")
        return True
    except IOError as e:
        print(f"Error saving settings to {config_path}: {e}")
        return False