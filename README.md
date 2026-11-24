<p align="center">
  <a href="https://dexterity-admin.github.io/oil-gas-design-queries/"><img src="docs/banner.svg" alt="DexMentor: ask the standards, check the source" width="100%"></a>
</p>

<p align="center">
  <a href="https://github.com/dexterity-admin/oil-gas-design-queries/releases/latest"><img alt="Latest release" src="https://img.shields.io/github/v/release/dexterity-admin/oil-gas-design-queries?label=latest%20release&color=f9af46&labelColor=071a1d&style=for-the-badge"></a>
  <a href="https://github.com/dexterity-admin/oil-gas-design-queries/actions/workflows/release.yml"><img alt="Build" src="https://img.shields.io/github/actions/workflow/status/dexterity-admin/oil-gas-design-queries/release.yml?label=build&labelColor=071a1d&style=for-the-badge"></a>
  <a href="https://github.com/dexterity-admin/oil-gas-design-queries/releases"><img alt="Downloads" src="https://img.shields.io/github/downloads/dexterity-admin/oil-gas-design-queries/total?color=2c8c98&labelColor=071a1d&style=for-the-badge"></a>
  <a href="https://github.com/dexterity-admin/oil-gas-design-queries/commits"><img alt="Last update" src="https://img.shields.io/github/last-commit/dexterity-admin/oil-gas-design-queries/master?label=last%20update&color=2c8c98&labelColor=071a1d&style=for-the-badge"></a>
</p>

<h3 align="center">
  <a href="https://dexterity-admin.github.io/oil-gas-design-queries/">🌐 Landing page</a> &nbsp;·&nbsp;
  <a href="https://github.com/dexterity-admin/oil-gas-design-queries/releases/latest/download/DexMentorAsk-Setup.exe">⬇ DexMentor Ask for engineers</a> &nbsp;·&nbsp;
  <a href="https://github.com/dexterity-admin/oil-gas-design-queries/releases/latest/download/DexMentorLibrary-Setup.exe">⬇ DexMentor Library for the server PC</a>
</h3>

---

DexMentor answers engineers' questions from **your company's own standards, design bases and reference documents**. Every fact is pinned to the exact page it came from, and nothing leaves your office.

<p align="center"><img src="docs/demo.gif" alt="DexMentor answering a question and a follow-up, with the source page highlighted" width="92%"></p>

## What it does

| | |
| --- | --- |
| **Cited answers** | Each number in an answer carries a balloon. One click opens the original page with the passage highlighted. |
| **Reads the table, not just the words** | Code finds the row and column that match the question and does percentage sums itself. The model only words the answer. |
| **Reads real-world PDFs** | Tables drawn by Word, merged header cells, tables laid on their side and sizes written as fractions (1½", ¾") are all read and matched. "OD" finds "outside diameter" and the other way round. |
| **Reads scanned documents** | A 14 MB reader built into the Library reads scanned pages on the CPU, with nothing extra to install: about 11 seconds a page on an Intel i7-6700 office PC under the Library's CPU limit, so a 445-page scanned book takes about 1.5 hours once. While a book is being read, answers and page views keep working. |
| **Light on the PC** | Windows itself holds the Library under 15 % of the PC's processor and the model on an engineer's PC under 10 % (25 % only while an answer is being written). The model needs about 400 MB of its own memory and is refused, with a message, when the PC has too little free; the Library about 250 to 450 MB when idle. |
| **Starter pack, kept private** | Keep the company's reference documents in a private GitHub repository. Connect the Library to it once (repository name and a read-only key) and it downloads, indexes and keeps them up to date. Nothing from the pack is in the public installer. |
| **Says when it does not know** | If the library never mentions what is asked, DexMentor refuses and shows the closest passages instead of guessing. |
| **Numbers are checked** | Every number in an answer is checked against the sources. Anything not found is flagged in red. |
| **Chat freely** | Ask as many follow-ups as you like: recent questions travel with each new one, so "and for 300#?" just works. *New topic* starts fresh. Saved chats can be renamed (double-click the name or press the pencil). |
| **History that survives a restart** | Saved chats are written to the PC's disk the moment you ask (and every few seconds while an answer is written), safely: a power cut or a forced restart leaves the last good copy. After a restart the app opens on the chat you had open. |
| **Keeps itself up to date** | Both apps check for a new version every hour, download it in the background, check its fingerprint and ask before installing: *Restart and update* stops the app's background work, installs and reopens it. A version that misbehaves can be rolled back from the same place. |
| **Private** | Documents, index and models stay on your own PCs. No cloud service is used. |
| **Light** | DexMentor Ask is a small native Windows app (Rust, using the Edge WebView2 built into Windows). It runs a small model only while answering, then frees the memory. Starting it again brings the open window to the front instead of opening another. |

## The two programs

<p align="center"><img src="docs/library-desk.jpg" alt="The Library desk" width="92%"></p>

| Program | Install on | What it does |
| --- | --- | --- |
| **DexMentor Library** | One always-on PC | Holds and indexes the documents. Drop files in or link a shared network folder (sub-folders included, files are never changed). Shows exactly what was read from each document. Closing the desk keeps it serving from the tray; *Stop DexMentor Library* in the tray menu ends it. A ready update installs at night when the desk is closed. |
| **DexMentor Ask** | Every engineer's PC | Finds the Library on the office network by itself, offers a one-time model download, and answers questions. Earlier chats from the old DexMentor window are brought over on first start. |

Each program has one window and one copy: starting it again from the Start menu or the desktop shows the window that is already open.

### Set-up in three steps

1. Run **DexMentorLibrary-Setup.exe** on the server PC. On the Library desk, add documents or link the folder that holds them.
2. Run **DexMentorAsk-Setup.exe** on each engineer's PC. It installs over the earlier *DexMentor* app (model, settings and chats are kept) and connects to the Library automatically. Press *Download model* once.
3. Ask a question.

Neither installer needs administrator rights, and neither ever restarts Windows. Windows shows a "Windows protected your PC" notice for the installers because they are not code-signed: choose *More info*, then *Run anyway*. When Windows Firewall asks about DexMentor Library, allow it on private networks, or the engineers' PCs will not find it.

### Starter pack (optional)

Reference books and guides that must not be public can live in a **private** GitHub repository. The Library downloads and indexes them by itself.

1. Put the documents in a private repository.
2. On GitHub, create a fine-grained access token limited to that repository with **Contents: Read-only**.
3. On the Library desk, open **Starter pack**, type `owner/repository`, paste the key and press **Connect**.

### Office model

<a name="office-model"></a>English answers work on every PC with the small built-in model. For deeper answers, Simple English and Tanglish, run [Ollama](https://ollama.com) on one stronger PC and link it from the Library desk (*Office model*).

```bash
ollama pull gemma2:9b
```

Gemma 2 9B was the best of the 7 to 10B models we tested on our question set (29 of 30, with no invented or wrong values). Set the environment variable `OLLAMA_HOST=0.0.0.0` on that PC so the Library can reach it.

## How answers are kept honest

```
question ─► rewrite follow-ups ─► search (keywords + meaning)
         ─► code picks the table row / column and does the percent arithmetic
         ─► code refuses if the library never mentions the subject
         ─► model writes the answer from the sources only
         ─► code checks every number against the sources ─► stamp or red flag
```

The matcher, the arithmetic, the coverage check and the number check are plain code with unit tests (`tests/`), not model behaviour. When the matcher is unsure it says nothing rather than point at a row.

**Limits you should know.** The number check proves a value was not invented. When the matcher has picked a table row, a value the answer takes from one of the other rows is flagged in red ("check the row"); beyond that the check cannot prove a value came from the right place, so the highlighted page still matters. Scores come from a small set of test documents, not your library. Scanned pages are read by a small built-in reader (no extra install); it leaves out numbers it is unsure of, so always check dimensions from a scan against the page shown. Old `.doc`, `.xls` and `.ppt` files are not read.

## For developers

```bash
pip install -r requirements.txt
```

```bash
python -m server
```

```bash
python -m pytest -q tests
```

The two desktop apps are Rust (Tauri 2 on WebView2). They need the MSVC C++ build tools.

```bash
cd apps && cargo test -p dexwin && cargo build --release
```

Run the Library shell against the engine from source with `DEXMENTOR_ENGINE="python -m server"` (from the repository folder). `DEXMENTOR_HOME` gives DexMentor Ask a separate data folder for testing; `DEXMENTOR_UPDATE_FEED` points either app at another update feed (an `http` address or a local `latest.json`).

| Path | What |
| --- | --- |
| `apps/` | The desktop apps (Rust): `ask/` DexMentor Ask (window, local model, saved chats, updates), `library/` DexMentor Library (window, tray, runs the engine), `dexwin/` shared Windows parts (job objects, safe chat files, updater) |
| `server/` | The Library engine: ingestion, scanned-page reader, search, grounding, chat pipeline, starter pack, admin API |
| `starter/` | Company-owned documents placed here ship inside the Library installer |
| `web/` | The interface: built into DexMentor Ask, and served by the Library for its desk |
| `desktop/` | Model manifest, icons, the Library engine's entry point and CPU cap |
| `installer/` | Inno Setup scripts for both installers (our own wizard: `brand/make_wizard.py` draws its artwork) |
| `eval/` | Question set and harness used to compare models on GitHub Actions |
| `site/` | The landing page (GitHub Pages) |
| `legacy/` | The original PySide6 prototype |

**Project state, what is proven and what is not, and the next steps: [docs/HANDOVER.md](docs/HANDOVER.md).**

Every merge to `master` builds both apps and installers, smoke-tests them on Windows (single copy, saved chats, Library found) and publishes a [release] with `latest.json`, the feed the apps update from(https://github.com/dexterity-admin/oil-gas-design-queries/releases). The landing page is redeployed whenever `site/` changes on `master`.

<p align="center"><sub>Built by <b>Dexterity Design Services Pvt. Ltd.</b></sub></p>
