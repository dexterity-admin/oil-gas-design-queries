# DexMentor: project state and handover

This page is the single place to pick the project up from. It says what exists, what has been
proven and how, what has **not** been proven, and what to do next. Keep it current: when you
verify or fix something, update the tables here in the same pull request.

## What DexMentor is

Engineers at Dexterity Design Services Pvt. Ltd. ask questions about the company's own standards, design
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
| `tests/` | Unit tests for the deterministic code (`test_grounding.py` matcher and checks, `test_ingest.py` scan-text filter) |
| `eval/smoke_docs.py` | Release smoke test: a generated scanned page and a Word table must be read in full |
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
| Deterministic code | 18 unit tests pass | `python -m pytest tests`, run on every release build (until this change CI ran only `test_grounding.py`) |
| PC model (Qwen2.5 1.5B Q4_K_M) | 29 of 30, nothing invented, no wrong value, 10.6 s average (max 19.4 s) | 23 Nov 2026, real office PC (i7-6700, 24 GB, Windows 10): `eval/run_eval.py` through the installed v1.0.10 `DexMentor.exe`. Same single miss as before (Tanglish valve span). Re-run from source with this change: 29 of 30, nothing invented, and the new row check flags 1 correct answer (a false alarm, see weaknesses) |
| Installers on a real PC | Both install per-user without admin, silently, to a chosen folder (`/DIR=`); Library 156 MB installer, 409 MB installed; engineer app 10.9 MB | 23 Nov 2026, same PC, installed by the agent from the command line (not by a person double-clicking, so SmartScreen was not seen) |
| Model download on a real PC | Downloads (3.5 min on the office line), resumes after the app is killed mid-download, loads | Same PC: killed at 324 MB, restarted, continued from 324 MB |
| GPU check on a GeForce GT 710 | Card found, Vulkan runner starts, first request fails with `ErrorDeviceLost` after about 37 s; the app correctly falls back to the CPU (10.7 s benchmark) | Same PC; the failure was reproduced by hand with the downloaded `runtime-gpu\llama-server.exe -ngl 99` |
| Admin PIN | Set, checked, cleared; five wrong PINs lock that address out for five minutes | Same PC, through the API |
| Linked network folder | A UNC folder with sub-folders: both documents found, a `.txt` skipped, files and timestamps unchanged | Same PC, `\\192.168.0.200\...` share |
| Scan reader on Windows | Works. Progress shows "reading scanned page n of N". About 6 s a page on the i7-6700. v1.0.10 lost a whole sentence and a whole table row from a clean generated scan; fixed in this change (see below) and now covered by the release smoke test | Same PC, generated scans of the synthetic tank and piping standards |
| Office model (Gemma 2 9B) | 29 of 30, best Tanglish | Same harness on a GitHub runner, also before the latest matcher changes |
| Model download and load in the engineer app | Works | "Windows in-app download and load" job in `model-eval.yml` |
| LAN auto-connect | Works on one machine | Release smoke test: launcher pointed at a dead address finds the Library by broadcast |
| Packaged programs start | Works | Release smoke test: Library starts, takes an Excel file, engineer app sees one document; then a generated scanned PDF must give back every line and number, and a Word table row must reach the prompt (`eval/smoke_docs.py`) |
| Real pipe guide (15 pages, Word-made PDF) | 23 of 23 retrieval and row-matching checks | Local script against `server.chat.prepare`, keyword search only, no model |
| Scanned textbook (445 pages) | Prose found and read in column order; exhibit dimensions only partly read | Same kind of local script, keyword search only, no model |
| Private starter pack | Wrong key rejected, right key downloads and indexes | Against a local stand-in for the GitHub API, not against GitHub itself |

## What has NOT been proven

These are the reasons this handover exists. Each one needs a machine that can download and run things.

1. **Installers run by a person.** Installed by the agent from the command line on a real PC (see above). Still unknown: SmartScreen behaviour for a browser download, whether the firewall prompt appears and what a non-admin user can answer (no firewall rule existed after the install), autostart after a reboot (it is a Startup-folder shortcut, not a scheduled task), uninstall, upgrade over an older version.
2. **Two real PCs on one network.** Discovery has only been tested on a single machine (on the real office network it found the Library at its LAN address in 5 s from a dead address).
3. **The book's prose answers against the page images**, and the book as read by the fixed reader. Worded answers on both real documents, with meaning-based search on, are now measured (see the two result sections below), but seven prose answers were judged only against the reader's text.
4. **The 30-question evaluation on GitHub after this change.** Measured locally on the real PC at 29 of 30 (above); the `[eval]` matrix on GitHub has not been re-run.
5. **Office model path end to end** (Ollama on a second PC, Tanglish judged by a Tamil speaker).

## Known weaknesses

- **Numbers in scanned exhibits.** The reader drops numbers it is unsure of (`min_number_score` in `server/ocr.py`). Many exhibit values are therefore missing, and a misread one can still slip through. The number check compares against the same machine-read text, so it cannot catch a misread digit. Ideas: read exhibit regions at higher resolution, or require agreement between two passes before keeping a number.
- **Words run together in scanned text.** The bundled reader (RapidOCR 1.4.4, PP-OCRv4 models) often drops the spaces in English lines ("Theminimumcorrosionallowance"). Numbers are not affected, but keyword search cannot find those words and the passage is hard to read. Per-character positions are too rough to put the spaces back safely. Check how often this happens on the real scanned book before choosing a fix (an English recognition model, or splitting lines at blank pixel columns).
- **Scan reader fixes in this change.** (a) RapidOCR shrank each page to about 90 dpi before looking for lines and silently missed whole lines of 10 pt print; `server/ocr.py` now sets `det_limit_side_len=1600` (+0.3 s a page). (b) `_readable` in `server/ingest.py` dropped scanned table rows made of numbers and short words ("Up to 15 24 8") while keeping their neighbours ("Above 15 48 16"), so a table looked complete with a row missing; such rows are now kept. `requirements.txt` now requires RapidOCR 1.4.x so running from source reads pages the same way as the installed program (1.4.x needs Python 3.12 or older).
- **Wrong row with a green stamp (found on the real PC, guarded in this change).** Asked "How long is the hydrotest hold for a 10 m diameter tank?", the installed v1.0.10 answered **48 hours** (right: 24) with "numbers found in sources". The matcher had given the right row; a scanned copy of the same standard, read by v1.0.10 with the "Up to 15" row missing, was also among the sources and the model took 48 from it. Besides fixing the scan reader, `grounding.other_row_numbers` now flags any number the answer takes from a row the matcher ruled out, and the app shows "CHECK THE ROW" in red instead of the green stamp. It cannot help when the matcher picked no row. Numbers the user typed are never flagged. Known false alarm (1 of 30 in the evaluation): the follow-up "and for smaller ones?" is rewritten to a query that still says "above 15 m", the matcher picks the Above 15 row, and the correct 24 h answer gets the red flag.
- **Wrong column.** The matcher picks rows, not columns. On the real pipe guide the model twice took the value from the wrong column (stainless instead of carbon steel; the wrong material in a sideways table) and nothing flagged it. Next deterministic step: when the question names exactly one column header ("carbon steel", "-29 C"), say "Value that answers the question" for that column only, as is already done for temperature-range headers.
- **Page view blocked while a big PDF is read (fixed in this change).** Indexing held the PyMuPDF lock for a whole document, so every citation page image in the app hung for the 45 minutes the scanned book takes. `parse_pdf` now lets go of the lock between pages; on the real PC a page image now comes back in 0.6 s while the book is being read.
- **GPU check leaves no reason.** On the GT 710 the GPU test fails (`ErrorDeviceLost`) and `settings.json` records `gpu_s: null` with no note, so nobody can tell later why the CPU was chosen.
- **Data folders follow `%LOCALAPPDATA%`.** Both programs keep the model (1.1 GB), index and page cache under the user's profile on C:. They can be moved only with environment variables (`DATA_DIR`, `LIBRARY_DIR` for the Library, `DEXMENTOR_HOME` for the engineer app); the installers do not ask.
- **Admin PIN also applies on the server PC.** Once a PIN is set, changes made on the server PC itself need it too (the comment in `server/config.py` says the server PC is always allowed).
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

1. Finish the checklist below: the items still open need a person at the PC (firewall prompt, SmartScreen, reboot), a second PC, or an upgrade.
2. Check the book's seven prose answers against the page images, and compare the book read by the fixed reader with the v1.0.10 read.
3. Same-sentence number check for prose sources (see the book results), then column selection for tables (see weaknesses).
4. Re-run the 30-question evaluation with `[eval]` on GitHub and compare with 29 of 30.
5. Then the weaknesses above: words run together in scanned text, then numbers in scanned exhibits.

### Checklist for the real-machine pass

Results of the 23 Nov 2026 pass on one office PC (i7-6700, Windows 10, v1.0.10), run by the agent:

- [~] `DexMentorLibrary-Setup.exe` installs without admin rights (yes, silently); the Library desk opens (yes); firewall prompt: not seen (installed from the command line) and no firewall rule exists afterwards, so other PCs may not reach the Library until someone allows it
- [~] Add a PDF, a Word file and an Excel file; the register shows pages, passages and tables: yes through the upload API the desk uses; drag and drop itself not tried
- [x] Link a folder with sub-folders; files appear; nothing in the folder is changed (UNC share, hashes and timestamps compared)
- [~] Starter pack: connect with a real fine-grained read-only key against real GitHub: yes, both documents downloaded and indexed under "Starter pack"; re-fetch after removing a document: yes; disconnect not tried
- [x] Scanned document: progress shows "reading scanned page n of N"; passages readable, but words often run together (see weaknesses); v1.0.10 lost lines, fixed here
- [~] Set an admin PIN; wrong PIN locks out after five tries for five minutes: yes; managing from a second PC not tried
- [ ] `DexMentor-Setup.exe` on a second PC finds the Library without typing an address (one PC only: found it at its LAN address from a dead address in 5 s)
- [~] Model download completes and resumes after the app is killed: yes; first answer appears: yes; unload after 10 idle minutes: not checked
- [~] An answer shows citation balloons; clicking one opens the right page with the passages highlighted: yes; "Open original" not clicked
- [x] A question outside the documents is refused with the closest passages listed ("pig launcher", 1 s)
- [~] Follow-up questions keep context: yes in the 30-question run (two follow-ups); saved chats survive a restart: not checked
- [ ] Reboot the server PC: the Library starts by itself
- [ ] Upgrade: install a newer release over the old one; documents and index are kept
- [ ] Uninstall both programs cleanly

### Real-model answers on the pipe guide (23 Nov 2026)

Installed v1.0.10, Qwen 1.5B on the i7-6700, starter pack downloaded from the real private repository with a fine-grained read-only key (connect, download and index: 11 s for the 15-page guide). 19 questions: the 12 below plus 7 more. Judged by reading the page.

| Result | Count | Questions |
| --- | --- | --- |
| Right | 11 | NPS 4 OD 114; 1½" OD 48.3; DN 200 = NPS 8; DN 25 = NPS 1; hydro hold 5 s (A530); NPS 6 OD tolerance A106M +1.6/-0.8; NPS 2 OD tolerance A106M ±0.8; stainless NPS 20+ welded +17.5/-12.5 %; no hard punching on stainless below 12 mm; straightness 3.2 mm over 3.0 m; A106 Gr B |
| Refused, correctly | 3 | corrosion allowance; pig launcher design pressure; length tolerance (by code, in under 1 s) |
| Wrong | 5 | DN 50 OD → 88.9 (right: 60.3; the matcher gave the right row, the model took the next one); NPS 24 OD → "60.3 mm or 2.375 in" (the matcher said no row covers NPS 24; the model quoted NPS 2); below -29 C → A335 (right: A333 Gr 6; sideways one-row table, model picked the wrong column); seamless carbon steel wall tolerance → +15.0/-12.5 % (that is the stainless column; carbon steel is -12.5 % minimum); Sch 40 wall of NPS 6 → "-12.5 %" (not in the guide, should be refused) |
| Invented | 0 | every number was in the sources, which is why all five wrong answers got the green stamp in v1.0.10 |

With this change the two wrong numbers (88.9 and 60.3) get the red "CHECK THE ROW" flag: re-checked against the real grounding text, and the 7 right numeric answers stay unflagged. The other three wrong answers pick the wrong **column** (material, carbon steel vs stainless) or answer a question the guide does not cover; no check catches them yet. The Sch 40 one is partly the document's own error: its wall-tolerance table has the first row's NPS and DN cells swapped (NPS "6 to 65", DN "⅛ to 2½"), so NPS 6 matches that row.

Meaning-based search was on (embeddings downloaded) for all of these.

### Real-model answers on the scanned book (23 Nov 2026, v1.0.10 reader)

Indexing the 445-page book through the starter pack took **45 minutes** on the i7-6700 (387 pages read by the built-in reader, front matter skipped, 647 passages, all embedded). 14 questions; the six with a number or a location were judged against the page image:

| Question | Answer | Against the page |
| --- | --- | --- |
| Minimum headroom over a walkway | "2.100 mm", then one source line repeated 20+ times | Value matches the platform headroom in the exhibit (2,100 mm), but written like 2.1 mm; the run-on repetition is a model failure |
| Minimum headroom under a pipe rack | 15 inches | **Wrong**: not on the cited page (a drawing page) |
| Access way width | 1.2 m (3 ft) | **Wrong**: not on the cited page; 1.2 m is not 3 ft |
| Where the control room goes | "next to equipment that needs servicing" | **Wrong**: the book says control houses sit at the unit edge by a plant road, 50 ft from the equipment (page 56), which search did not return |
| Usual pipe rack bent spacing | 16 ft (4,900 mm) | Value is on the page as an example; red-flagged because the reader misread "16" in the source text |
| Tube bundle removal clearance | 12 inches | **Wrong**: not on the cited page |
| Corrosion allowance | refused by code | Right |

The seven prose answers (plot plan types, pipe stress, NPSH, furnaces, designer role, pump arrangement, P&ID) read sensibly against the reader's text but were **not** checked against page images. Three wrong numbers carried the green stamp: each number exists somewhere in the four sources, just not about the thing asked. The row check cannot help (no tables are trusted on scanned pages). Next deterministic idea: a number only counts as found when it sits in the same sentence as the asked-about words.

### CPU and memory limits (owner's rule, 23 Nov 2026)

The owner's limits, measured as Task Manager shows them (share of the whole PC, one-second samples):
the **Library never above 15 %**; the **engineer app (with its model) never above 10 %, except up to 25 % while
an answer is being written**; RAM as low as possible.

- `desktop/winlimits.py` puts each program and the processes it starts into a Windows job object with a **hard
  CPU cap** enforced by the kernel, and lowers its priority. The cap overshoots by 2 to 4 points in single
  seconds, so the caps sit below the limits: Library 10 % (worst second measured 12.2 %), engineer app 6 % idle
  (8.1 %) and 20 % while the model loads or writes (23.8 %). `tests/test_winlimits.py` pins every core and checks
  the worst second for each, and that the answer boost goes back down. Environment variables:
  `DEXMENTOR_CPU_LIMIT`, `DEXMENTOR_ANSWER_CPU_LIMIT` (0 = no cap).
- The app windows (Edge/Chrome) are started outside the cap so the screen never freezes behind the model.
- Scan reader and search model use one thread (`WORK_THREADS`). Scanned pages are read in a **worker process per
  document** (`ocr.Reader`): the reader needs about 1 GB while it works and never gave it back, so the Library sat at
  1.25 GB after reading scans. Passages are embedded 4 at a time (batch 256 kept +816 MB for good).
- The model runner reads weights from the mapped model file (`--no-repack`), answers one question at a time and
  keeps no prompt cache in RAM (`-np 1 -cram 0`), with smaller batches (`-b/-ub 256`).

Measured on the i7-6700 (8 threads):

| | Before | After |
| --- | --- | --- |
| Library CPU while reading scans | 50 %+ | worst second 12.2 %, average 9.6 % |
| Library RAM idle after reading scans | 1,248 MB (1,637 private) | 412 MB (623 private); 237 MB at a fresh start |
| Engineer app + model CPU while answering | worst second 49 % | worst second 22.5 %, average 18 % |
| Model runner RAM (private) | 1,005 MB | 406 MB (the rest of its working set is the mapped model file) |
| Answer time (3 test questions) | about 14 s | 16 to 27 s (50 to 70 s with a flat 6 % cap) |
| Reading scanned pages | about 6 s a page | about 11 s a page (the 445-page book: about 1.5 h instead of 45 min) |

This PC has **no page file**: the commit limit is the 24 GB of RAM and other programs had committed about 22.7 GB,
so allocations failed now and then ("bad allocation", "paging file is too small"). A worker that fails costs one
page, which is then listed as unreadable. Setting a page file is the owner's call.

### Upgrade test (23 Nov 2026)

Installing v1.0.12 over a running v1.0.10 **aborted** (exit code 5): the Library and the engineer launcher run without a window, so Windows' Restart Manager cannot close them ("Setup was unable to automatically close all applications"). After stopping them by hand the upgrade succeeded and kept the documents, the index, the starter-pack key and the downloaded model. Both installers now stop the running program before copying files (and the engineer installer stops its own `llama-server`), and a silent upgrade starts the program again.

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
