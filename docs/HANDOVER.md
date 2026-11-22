# DexMentor: project state and handover

This page is the single place to pick the project up from. It says what exists, what has been
proven and how, what has **not** been proven, and what to do next. Keep it current: when you
verify or fix something, update the tables here in the same pull request.

## What DexMentor is

Engineers at Dexterity International ask questions about the company's own standards, design
documents and reference books, and get plain-language answers with the exact source page shown.
Everything runs on the office's own PCs. There is no cloud model.

| Piece | Runs on | What it is |
| --- | --- | --- |
| **DexMentor Library** | One always-on Windows PC | FastAPI server (`server/`), port 8700. Reads PDF, Word, Excel, PowerPoint; indexes them (SQLite FTS5 + `BAAI/bge-small-en-v1.5` embeddings through fastembed); serves the web interface (`web/`) and the admin "Library desk". Packaged with PyInstaller (one folder) and Inno Setup. |
| **DexMentor** | Every engineer's Windows PC | Small launcher (`desktop/dexmentor.py`, standard library only), port 8720. Finds the Library on the LAN (UDP broadcast, port 8701, message `DEXMENTOR_DISCOVER_V1`), downloads and runs a small model on demand (llama.cpp `llama-server`, port 8719), proxies the interface. |
| **Office model** (optional) | One stronger PC | Ollama with `gemma2:9b`. When linked from the Library desk it adds deeper answers, Simple English and Tanglish. Without it the app is English only. |
| **Starter pack** (optional) | Private repo `dexterity-admin/dexmentor-starter-pack` | Reference PDFs that must not be public. The Library is connected to it once (repository name and a read-only access key) and downloads, indexes and refreshes them. |

Owner's rules that shape every decision:

- Answers must come only from the documents. A wrong number is the worst outcome; "not found, see the page" is acceptable.
- Old office PCs: CPU only, low memory. Desktop is the focus; Android is a possible by-product.
- No cloud models. The Anthropic provider code in `server/llm.py` is unused and may be removed.
- Third-party documents never go into this public repository or its installers.
- The owner works mostly from a phone and wants things done for them, with clear step-by-step instructions only for what they must do themselves.

## How an answer is made

```
question -> rewrite follow-ups -> hybrid search (keywords + meaning)
         -> code picks the table row/column, does percent arithmetic   (server/grounding.py)
         -> code refuses if the library never mentions the subject
         -> model writes the answer from the sources only               (PC model, or office model)
         -> code checks every number against the sources -> stamp or red flag
```

Design rule for `server/grounding.py`: when the code is not sure, it says nothing. A missing hint
costs a little accuracy; a wrong hint puts a wrong number in front of an engineer.

## Where things are

| Path | What |
| --- | --- |
| `server/app.py` | HTTP API, admin endpoints, host/origin guard, admin PIN |
| `server/ingest.py` | Parsers, table reconstruction, scanned-page handling, sync loop, starter seeding |
| `server/ocr.py` | Built-in reader for scanned pages (RapidOCR, ONNX, bundled) |
| `server/pack.py` | Private starter pack: download from a private GitHub repository |
| `server/grounding.py` | Table matcher, arithmetic, coverage gate, number check, Tanglish guard, abbreviations |
| `server/chat.py` | Prompts and the answer pipeline (`prepare` for the PC model, `answer` for the office model) |
| `server/store.py` | SQLite schema and hybrid search |
| `desktop/dexmentor.py`, `desktop/models.json` | Engineer launcher; pinned model and llama.cpp runner URLs |
| `web/` | Interface (vanilla JS/CSS, bundled fonts) |
| `installer/*.iss` | Inno Setup scripts (per-user installs; Library gets an autostart task) |
| `eval/` | 30-question harness on four synthetic documents (`make_corpus.py`, `run_eval.py`) |
| `tests/test_grounding.py` | Unit tests for the deterministic code |
| `.github/workflows/release.yml` | Tests, builds both programs, Windows smoke test, installers, publishes `v1.0.<run>` |
| `.github/workflows/model-eval.yml` | Real-model evaluation; the full matrix runs only when a commit message contains `[eval]` |
| `.github/workflows/pages.yml` | Landing page (`site/`) to GitHub Pages from `master` |
| `starter/` | Documents placed here ship inside the Library installer (public: company-owned files only) |

Run from source:

```bash
pip install -r requirements.txt
```

```bash
python -m server
```

```bash
python -m pytest -q tests
```

Useful environment variables: `LIBRARY_DIR`, `DATA_DIR`, `PORT`, `ADMIN_PIN`, `EMBEDDINGS=off`,
`OCR=auto|missing|off`, `STARTER_DIR`, `PACK_API`, `OLLAMA_URL`, `OLLAMA_MODEL`, `LLM_PROVIDER`.

## What has been proven, and how

| Area | Result | How it was checked |
| --- | --- | --- |
| Deterministic code | 15 unit tests pass | `tests/test_grounding.py`, run on every release build |
| PC model (Qwen2.5 1.5B Q4_K_M) | 29 of 30, nothing invented, about 8 s per answer, 1.6 to 2.3 GB RAM | `eval/run_eval.py` through the real `DexMentor.exe` on a GitHub Windows runner, **before** the table-matcher changes of this handover |
| Office model (Gemma 2 9B) | 29 of 30, best Tanglish | Same harness on a GitHub runner, also before the latest matcher changes |
| Model download and load in the engineer app | Works | "Windows in-app download and load" job in `model-eval.yml` |
| LAN auto-connect | Works on one machine | Release smoke test: launcher pointed at a dead address finds the Library by broadcast |
| Packaged programs start | Works | Release smoke test: Library starts, takes an Excel file, engineer app sees one document |
| Real pipe guide (15 pages, Word-made PDF) | 23 of 23 retrieval and row-matching checks | Local script against `server.chat.prepare`, keyword search only, no model |
| Scanned textbook (445 pages) | Prose found and read in column order; exhibit dimensions only partly read | Same kind of local script, keyword search only, no model |
| Private starter pack | Wrong key rejected, right key downloads and indexes | Against a local stand-in for the GitHub API, not against GitHub itself |

## What has NOT been proven

These are the reasons this handover exists. Each one needs a machine that can download and run things.

1. **Installers on a real Windows PC.** Never run by a person. Unknown: SmartScreen behaviour, firewall prompt, autostart task, uninstall, upgrade over an older version, per-user install without admin rights.
2. **Two real PCs on one network.** Discovery has only been tested on a single machine.
3. **Worded answers on the two real documents.** No model has answered questions about the pipe guide or the textbook. Only retrieval and matching were checked.
4. **Meaning-based search on the real documents.** Embeddings could not be downloaded where the documents were tested; results there are keyword-only.
5. **The 30-question evaluation after the latest changes.** The matcher, search and ingest changed since the last measured run. Re-run with `[eval]` in a commit message.
6. **Scan reader on Windows.** It is bundled and the build passes, but the smoke test never feeds it a scanned page. Its speed on a slow office PC is unknown (about 2 s per page on 4 cores under Linux).
7. **Starter pack against real GitHub** with a real fine-grained token and the real private repository.
8. **Office model path end to end** (Ollama on a second PC, Tanglish judged by a Tamil speaker).
9. **GPU self-benchmark** on a real GeForce GT 710.
10. **Installer size** after adding the scan reader (it brings OpenCV).

## Known weaknesses

- **Numbers in scanned exhibits.** The reader drops numbers it is unsure of (`min_number_score` in `server/ocr.py`). Many exhibit values are therefore missing, and a misread one can still slip through. The number check compares against the same machine-read text, so it cannot catch a misread digit. Ideas: read exhibit regions at higher resolution, or require agreement between two passes before keeping a number.
- **Drawings.** Hand-lettered drawings are not read. Only the page image helps the engineer.
- **One eval miss.** Tanglish question "4 inch pipe with valve": the PC model answers 4.3 and does not apply the 25 percent reduction.
- **No checksum** is pinned for the model and runner downloads in `desktop/models.json`.
- **The starter-pack access key and the admin PIN hash** are stored in `settings.json` in the Library's data folder, unencrypted.
- **Chat history** on the engineer's PC is unencrypted browser storage.
- **Installers are not code-signed.**
- **Very large shared folders** index slowly (single thread).
- **Existing indexes are not rebuilt** when ingest logic changes. To re-read a document after an update, remove it and add it again, or delete the Library's `kb` folder.
- **Headings inside Word-made PDFs** are often not detected, so the section shown can be the document's cover line.

## Next work, in order

1. Install both installers from the latest release on a real Windows machine and walk the whole flow (see the checklist below). Fix what breaks.
2. Connect the Library to the private starter pack with a real key. Confirm both documents index, including the scanned book through the built-in reader on Windows.
3. Ask real questions with the real PC model and judge the answers against the pages. Start from the question lists in this file.
4. Re-run the 30-question evaluation with `[eval]` and compare with 29 of 30.
5. Extend the release smoke test so it covers a scanned page and a Word-style table, using documents generated in CI (never the third-party ones).
6. Then the weaknesses above, starting with numbers in scanned exhibits.

### Checklist for the real-machine pass

- [ ] `DexMentorLibrary-Setup.exe` installs without admin rights; the Library desk opens; firewall prompt appears once
- [ ] Add a PDF, a Word file and an Excel file by drag and drop; the register shows pages, passages and tables
- [ ] Link a folder with sub-folders; files appear; nothing in the folder is changed
- [ ] Starter pack: connect, documents appear under "Starter pack", "Check for new documents" works, disconnect removes them
- [ ] Scanned document: progress shows "reading scanned page n of N"; passages are readable in the inspection pane
- [ ] Set an admin PIN; manage the Library from a second PC with it; wrong PIN locks out after repeated tries
- [ ] `DexMentor-Setup.exe` on a second PC finds the Library without typing an address
- [ ] Model download completes (and resumes after a dropped connection); first answer appears; model unloads after 10 idle minutes
- [ ] An answer shows citation balloons; clicking one opens the right page with the passage highlighted; "Open original" opens the file
- [ ] A question outside the documents is refused with the closest passages listed
- [ ] Follow-up questions keep context; saved chats survive a restart
- [ ] Reboot the server PC: the Library starts by itself
- [ ] Upgrade: install a newer release over the old one; documents and index are kept
- [ ] Uninstall both programs cleanly

### Questions used so far

Pipe guide (expected facts are in the document):

| Question | Expected |
| --- | --- |
| What is the outside diameter of NPS 4 pipe? | 114 mm (sideways NPS / DN / OD table, page 8) |
| What is the outside diameter of 1 1/2 inch pipe? | 48.3 mm |
| Which NPS is DN 200? | NPS 8 |
| What is the minimum holding time for hydro test? | 5 seconds, ASTM A530 |
| OD tolerance for NPS 6 pipe as per ASTM A106M | +1.6 / -0.8 mm |
| Wall thickness tolerance for stainless steel pipe NPS 20 and over, welded | +17.5 / -12.5 % |
| Which material is used below -29 C? | A333 Gr 6 |
| Below what thickness is hard punching not allowed on stainless steel? | 12 mm |
| Straightness tolerance for welded stainless steel pipe | 3.2 mm over 3.0 m |
| What is the corrosion allowance for carbon steel pipe? | Refuse: not in the document |
| What is the design pressure of a pig launcher? | Refuse: not in the document |
| What is the OD of NPS 24 pipe? | Not in the table; the text says NPS 14 and above have OD equal to the size in inches |

Scanned textbook (check the wording against the page): types of plot plans; causes of pipe stress;
what NPSH is; where furnaces should be located; what a plant layout designer does; minimum
headroom over a walkway (exhibit value: expect "not found" or a number that must be checked).

## Working agreements

- Develop on a branch, open a pull request to `master`, merge when the build is green. Every push to `master` publishes a release, so batch small changes.
- Never skip or disable a test to get a green build.
- Never commit third-party documents here. Test documents for CI are generated by `eval/make_corpus.py`.
- Update `README.md` and this file with every change that alters behaviour or what is proven.
