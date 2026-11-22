"""Starter pack: the company's own reference documents, kept in a PRIVATE GitHub repository.

The public installer contains none of them. The librarian connects the Library to the private
repository once (its name and a read-only access key); the Library then downloads the documents,
shelves them under "Starter pack", and picks up later additions by itself.
"""
import io
import re
import time
import zipfile
from pathlib import Path

import httpx

from . import config

SHELF = "Starter pack"
state = {"checked": 0, "error": ""}


def clean_repo(text):
    """"owner/name", or any GitHub link to the repository -> "owner/name" ("" if it is neither)."""
    m = re.search(r"(?:github\.com[/:])?([\w.-]+)/([\w.-]+?)(?:\.git)?/?$", (text or "").strip())
    return "%s/%s" % m.groups() if m else ""


def status(full=False):
    out = {"connected": bool(config.PACK_REPO), "documents": len(config.PACK_FILES), "error": state["error"],
           "checked": state["checked"]}
    if full:
        out.update(repo=config.PACK_REPO, files=config.PACK_FILES)
    return out


def _problem(code):
    return {401: "GitHub did not accept the access key. It may be mistyped or expired.",
            403: "The access key is not allowed to read this repository (it needs Contents: Read-only).",
            404: "GitHub cannot see that repository with this access key. Check the name and that the key covers it.",
            }.get(code, "GitHub answered with error %s." % code)


def fetch(force=False):
    """Bring the shelf in line with the repository. Returns True when documents were added or removed."""
    if not config.PACK_REPO:
        return False
    head = {"Authorization": "Bearer " + config.PACK_TOKEN, "Accept": "application/vnd.github+json",
            "User-Agent": "DexMentor-Library"}
    base = config.PACK_API + "/repos/" + config.PACK_REPO
    try:
        with httpx.Client(timeout=httpx.Timeout(120, connect=10), follow_redirects=True, headers=head) as c:
            r = c.get(base + "/commits", params={"per_page": 1})
            if r.status_code == 409:  # an empty repository
                state.update(checked=time.time(), error="The repository is empty: add your documents to it.")
                return False
            if r.status_code != 200:
                state.update(checked=time.time(), error=_problem(r.status_code))
                return False
            sha = r.json()[0]["sha"]
            if sha == config.PACK_SHA and not force:
                state.update(checked=time.time(), error="")
                return False
            r = c.get(base + "/zipball")
            if r.status_code != 200:
                state.update(checked=time.time(), error=_problem(r.status_code))
                return False
            data = r.content
    except (httpx.HTTPError, ValueError, KeyError, IndexError):
        state.update(checked=time.time(), error="Could not reach GitHub. The Library will try again later.")
        return False
    shelf = config.LIBRARY_DIR / SHELF
    shelf.mkdir(parents=True, exist_ok=True)
    kept = []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for info in z.infolist():
                parts = [p for p in info.filename.split("/")[1:] if p]  # the first folder is GitHub's wrapper
                if info.is_dir() or not parts or Path(parts[-1]).suffix.lower() not in config.SUPPORTED:
                    continue
                if any(p in ("..", ".") or p.startswith(".") or re.search(r'[<>:"|?*\\]', p) for p in parts):
                    continue
                if info.file_size > 300 * 1024 * 1024:
                    continue
                dest = shelf.joinpath(*parts)
                dest.parent.mkdir(parents=True, exist_ok=True)
                body = z.read(info)
                if not dest.exists() or dest.read_bytes() != body:
                    dest.write_bytes(body)
                kept.append("/".join(parts))
    except (zipfile.BadZipFile, OSError):
        state.update(checked=time.time(), error="The download from GitHub was damaged. The Library will try again later.")
        return False
    for old in config.PACK_FILES:  # a document removed from the repository leaves the shelf too
        if old not in kept:
            try:
                shelf.joinpath(*old.split("/")).unlink()
            except OSError:
                pass
    config.save(pack_sha=sha, pack_files=kept)
    state.update(checked=time.time(), error="" if kept else "Connected, but the repository holds no PDF, Word, Excel or PowerPoint files yet.")
    return True


def disconnect():
    shelf = config.LIBRARY_DIR / SHELF
    for old in config.PACK_FILES:
        try:
            shelf.joinpath(*old.split("/")).unlink()
        except OSError:
            pass
    config.save(pack_repo="", pack_token="", pack_sha="", pack_files=[])
    state.update(error="")
