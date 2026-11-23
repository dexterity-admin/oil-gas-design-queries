"""Render the DexMentor app icon (web/img/icon.svg) into the .ico and PNG sizes.

The icon is drawn for the taskbar first: a full-bleed teal tile, a solid white sheet and an amber
citation badge. Sizes of 32 px and below use a simpler drawing (no text lines, no letter) so they
stay crisp. Run from the repository root:  python brand/make_icon.py
"""
from pathlib import Path
import io

import pymupdf as fitz  # renders the SVG
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent

TEAL, DEEP, INK, AMBER, MIST = "#2a8c98", "#1e6066", "#132b2e", "#f7af44", "#cfe6e9"


def svg(small: bool) -> str:
    # 512 x 512 canvas. The tile fills the frame; the sheet and badge fill the tile.
    lines = "" if small else f"""
  <g stroke-linecap="round" stroke-width="26">
    <path d="M150 196h120M150 324h70" stroke="{DEEP}" opacity=".35"/>
    <path d="M150 260h150" stroke="{TEAL}"/>
  </g>"""
    letter = "" if small else f"""
  <path d="M338 318h26a50 50 0 010 100h-26z" fill="none" stroke="{INK}" stroke-width="24" stroke-linejoin="round"/>"""
    badge_r, ring = (118, 26) if small else (110, 22)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="512" height="512">
  <!-- DexMentor: a source sheet with a citation badge, on a Dexterity teal tile.
       Flat fills only: PyMuPDF, which renders this, draws gradients black. -->
  <rect x="8" y="8" width="496" height="496" rx="112" fill="{TEAL}"/>
  <path d="M104 72h168l88 88v280H104z" fill="#fff" stroke-linejoin="round"/>
  <path d="M272 72v88h88z" fill="{MIST}"/>{lines}
  <circle cx="366" cy="366" r="{badge_r}" fill="{AMBER}" stroke="{INK}" stroke-width="{ring}"/>{letter}
</svg>
"""


def render(src: str, px: int) -> Image.Image:
    doc = fitz.open("svg", src.encode("utf-8"))
    pix = doc[0].get_pixmap(matrix=fitz.Matrix(px * 4 / 512, px * 4 / 512), alpha=True)
    im = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGBA")
    return im.resize((px, px), Image.LANCZOS)  # 4x supersample, then downsample


def main() -> None:
    full, small = svg(False), svg(True)
    (ROOT / "web/img/icon.svg").write_text(full, encoding="utf-8")
    frames = {px: render(small if px <= 32 else full, px) for px in (16, 20, 24, 32, 40, 48, 64, 128, 256)}
    big = frames[256]
    big.save(ROOT / "desktop/dexmentor.ico", sizes=[(p, p) for p in frames], append_images=list(frames.values()))
    # The app windows are Edge/Chrome --app windows: their taskbar icon is the page favicon, not the .exe icon.
    fav = [frames[p] for p in (16, 24, 32, 48)]
    for name in ("web/img/favicon.ico", "site/img/favicon.ico"):
        fav[-1].save(ROOT / name, sizes=[im.size for im in fav], append_images=fav)
    for name, px in (("web/img/icon-48.png", 48), ("web/img/icon-192.png", 192),
                     ("web/img/icon-512.png", 512), ("site/img/icon.png", 512), ("brand/logo-icon.png", 512)):
        (frames.get(px) or render(full, px)).save(ROOT / name)


if __name__ == "__main__":
    main()
