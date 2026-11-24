"""Build every DexMentor icon and logo image from the DexMentor app icon.

The app icon is DexMentor's own: a source sheet with an amber citation badge on a teal rounded diamond. Its master
is brand/dexmentor-icon.svg; brand/dexmentor-icon-1024.png is that SVG rendered at 1024 px by Microsoft Edge, which
draws its gradient and shadows exactly (PyMuPDF draws gradients black). After editing the SVG, render it again:
  msedge --headless=new --default-background-color=00000000 --window-size=1024,1024 --screenshot=<png> <page with the svg>
Every size here is that master scaled down (Lanczos), never redrawn.

  DexMentor Ask      the icon as it is.
  DexMentor Library  the same icon, unaltered, on a dark ink tile, so the two programs can be told apart on the
                     taskbar.

Run from the repository root:  python brand/make_icon.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)
INK = (19, 43, 46, 255)  # brand ink #132b2e


def mark(px: int) -> Image.Image:
    """The app icon at `px` pixels, scaled down from the 1024 px master."""
    return Image.open(ROOT / "brand/dexmentor-icon-1024.png").convert("RGBA").resize((px, px), Image.LANCZOS)


def library(px: int) -> Image.Image:
    """The unaltered mark on a dark ink tile. The tile is drawn at 8x and scaled down so its edges stay smooth."""
    k = 8
    tile = Image.new("RGBA", (px * k, px * k), (0, 0, 0, 0))
    ImageDraw.Draw(tile).rounded_rectangle((0, 0, px * k - 1, px * k - 1), radius=px * k * 0.22, fill=INK)
    out = tile.resize((px, px), Image.LANCZOS)
    inner = round(px * 0.92)  # the mark keeps its own transparent margin, so it sits clear of the tile edge
    m = mark(inner)
    out.alpha_composite(m, ((px - inner) // 2, (px - inner) // 2))
    return out


def ico(path: Path, make) -> None:
    frames = [make(px) for px in ICO_SIZES]
    frames[-1].save(path, sizes=[im.size for im in frames], append_images=frames[:-1])


def main() -> None:
    ico(ROOT / "desktop/dexmentor.ico", mark)  # DexMentor Ask: the .exe, installer and window icon
    ico(ROOT / "desktop/library.ico", library)  # DexMentor Library
    fav = [mark(p) for p in (16, 24, 32, 48)]
    for name in ("web/img/favicon.ico", "site/img/favicon.ico"):
        fav[-1].save(ROOT / name, sizes=[im.size for im in fav], append_images=fav[:-1])
    for name, px in (("web/img/icon-48.png", 48), ("web/img/icon-192.png", 192), ("web/img/icon-512.png", 512),
                     ("site/img/icon.png", 512), ("apps/ask/dist/mark.png", 512)):
        mark(px).save(ROOT / name, optimize=True)
    library(512).save(ROOT / "apps/library/dist/mark.png", optimize=True)


if __name__ == "__main__":
    main()
