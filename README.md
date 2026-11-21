# DexGuide (working name)

Ask questions about your own standards and design documents and get a plain-language,
cited answer, with the exact page shown and highlighted next to it.

## How it is built

- **One server** (`server/`) holds the documents, the search index and the connection to the
  language model. It runs on one always-on machine.
- **One thin web UI** (`web/`) is what engineers use. It runs no models and stores no documents,
  so it stays light on old office PCs. The same UI will be wrapped as the desktop app and the Android app.
- `legacy/` is the original two-app PySide6 version, kept for reference.

## Run the server

```bash
pip install -r requirements.txt
```

```bash
python -m server
```

Copy PDF, Word, Excel and PowerPoint files into `library/` (sub-folders are fine), then open
http://127.0.0.1:8700. New and changed files are indexed on start, or via *Library → Re-scan*.

## Settings (environment variables)

| Variable | Default | Meaning |
| --- | --- | --- |
| `LIBRARY_DIR` | `./library` | Folder with your documents |
| `DATA_DIR` | `./kb` | Index and page-image cache |
| `HOST` / `PORT` | `127.0.0.1` / `8700` | Use `HOST=0.0.0.0` to serve the office network |
| `LLM_PROVIDER` | `anthropic` if a key is set, else `none` | `anthropic`, `ollama` or `none` |
| `ANTHROPIC_API_KEY` | | Key for the cloud model |
| `ANTHROPIC_MODEL` | `claude-haiku-5-5` | Cloud model |
| `OLLAMA_URL` / `OLLAMA_MODEL` | `http://127.0.0.1:11434` / `qwen2.5:3b` | Self-hosted model |
| `EMBEDDINGS` | `auto` | `off` for keyword search only |
| `APP_NAME` | `DexGuide` | Name shown in the UI |

With `LLM_PROVIDER=none` the app still finds and shows the matching passages and pages.
Scanned PDFs are OCR'd if Tesseract is installed on the server.

The server has no login of its own yet: keep it on `127.0.0.1` or the office network, or put it
behind Cloudflare Access.
