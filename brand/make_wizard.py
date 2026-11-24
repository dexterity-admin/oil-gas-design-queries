"""Draw the installer wizard artwork for DexMentor Ask and DexMentor Library.

The side panel of the welcome and finish pages and the small header picture of the other pages are drawn here
from the DexMentor app icon (brand/dexmentor-icon-1024.png, never redrawn) and the app's own fonts
(web/fonts, the same Barlow Semi Condensed and IBM Plex Sans the app window uses), in the brand colours.
Every size Windows may ask for (100 % to 250 % display scaling) is written, so the wizard is sharp on any screen.

Needs fontTools and brotli (to read the .woff2 fonts):  pip install fonttools brotli
Run from the repository root:  python brand/make_wizard.py
"""
import io
from pathlib import Path

from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "installer/art"
INK, DEEP, TEAL, AMBER, MIST, WHITE = "#132b2e", "#0a1f22", "#2a8c98", "#f7af44", "#8fb9be", "#ffffff"
SCALES = (100, 125, 150, 175, 200, 250)
# Inno Setup's sizes at 100 %: the welcome/finish side panel and the header picture.
PANEL, SMALL = (164, 314), (55, 58)


def font(name: str, px: float) -> ImageFont.FreeTypeFont:
    t = TTFont(ROOT / "web/fonts" / name)
    t.flavor = None
    buf = io.BytesIO()
    t.save(buf)
    buf.seek(0)
    return ImageFont.truetype(buf, round(px))


def mark(px: int, tile: bool) -> Image.Image:
    m = Image.open(ROOT / "brand/dexmentor-icon-1024.png").convert("RGBA").resize((px, px), Image.LANCZOS)
    if not tile:
        return m
    out = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    big = Image.new("RGBA", (px * 4, px * 4), (0, 0, 0, 0))
    ImageDraw.Draw(big).rounded_rectangle((0, 0, px * 4 - 1, px * 4 - 1), radius=px * 4 * 0.22, fill=(28, 58, 62, 255))
    out.alpha_composite(big.resize((px, px), Image.LANCZOS))
    inner = round(px * 0.92)
    out.alpha_composite(mark(inner, False), ((px - inner) // 2, (px - inner) // 2))
    return out


def panel(word: str, library: bool, scale: int) -> Image.Image:
    k = 4  # drawn at 4x, then scaled down: smooth curves and text
    w, h = (round(PANEL[0] * scale / 100) * k, round(PANEL[1] * scale / 100) * k)
    u = w / 164  # one design unit at this size
    im = Image.new("RGB", (w, h), DEEP)
    d = ImageDraw.Draw(im)
    for y in range(h):  # ink at the top deepening downwards
        t = y / h
        c = tuple(round(a + (b - a) * t) for a, b in zip((19, 43, 46), (8, 27, 30)))
        d.line((0, y, w, y), fill=c)
    # "Line of flow": three quiet teal lines sweeping across the foot of the panel, one amber
    for i, (col, width) in enumerate(((TEAL, 1.4), (TEAL, 1.0), (AMBER, 1.2))):
        pts = []
        for x in range(0, w + 1, max(1, w // 120)):
            f = x / w
            pts.append((x, h * (0.80 + 0.05 * i) - h * 0.06 * (1 - (2 * f - 1) ** 2) + u * 6 * i * f))
        d.line(pts, fill=col, width=max(1, round(width * u)), joint="curve")
    m = mark(round(92 * u), library)
    im.paste(m, ((w - m.width) // 2, round(40 * u)), m)
    head = font("barlow-semi-condensed-latin-700-normal.woff2", 23 * u)
    sub = font("barlow-semi-condensed-latin-500-normal.woff2", 23 * u)
    small = font("ibm-plex-sans-latin-400-normal.woff2", 8.2 * u)
    y = 150 * u
    for text, f, col in (("DexMentor", head, WHITE), (word, sub, AMBER)):
        tw = d.textlength(text, font=f)
        d.text(((w - tw) / 2, y), text, font=f, fill=col)
        y += 25 * u
    for line in ("DEXTERITY DESIGN SERVICES", "PVT. LTD."):
        tw = d.textlength(line, font=small)
        d.text(((w - tw) / 2, y + 12 * u), line, font=small, fill=MIST)
        y += 11 * u
    return im.resize((w // k, h // k), Image.LANCZOS)


def small_pic(library: bool, scale: int) -> Image.Image:
    w, h = round(SMALL[0] * scale / 100), round(SMALL[1] * scale / 100)
    im = Image.new("RGB", (w, h), INK)  # the header strip is ink too (see the [Code] of the installers)
    m = mark(min(w, h), library)
    im.paste(m, ((w - m.width) // 2, (h - m.height) // 2), m)
    return im


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for slug, word, lib in (("ask", "Ask", False), ("library", "Library", True)):
        for s in SCALES:
            panel(word, lib, s).save(OUT / f"{slug}-panel-{s}.bmp")
            small_pic(lib, s).save(OUT / f"{slug}-small-{s}.bmp")


if __name__ == "__main__":
    main()
