"""Slice a 3x3 Tabi sprite sheet (plain white background) into transparent PNGs.

usage: python scripts/slice_sprites.py SHEET.png OUT_DIR [prefix]
Columns = body (full, half, low), rows = mood (happy, meh, sad).
Background is removed by flood-filling near-white from the cell edges, so
white rice inside the dark outline is kept (rembg tends to eat it).
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter
from scipy import ndimage

BODY = ["full", "half", "low"]
MOOD = ["happy", "meh", "sad"]


def cut(cell: Image.Image) -> Image.Image:
    a = np.asarray(cell.convert("RGB")).astype(int)
    light = (a.min(axis=2) > 225) & (a.max(axis=2) - a.min(axis=2) < 40)
    lab, _ = ndimage.label(light)
    edge = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))) - {0}
    bg = np.isin(lab, list(edge))
    fg = ndimage.binary_opening(~bg, iterations=1)
    lab2, n = ndimage.label(fg)
    if n:
        sizes = ndimage.sum(fg, lab2, range(1, n + 1))
        keep = [i + 1 for i, s in enumerate(sizes) if s > 150]
        fg = np.isin(lab2, keep)
    alpha = Image.fromarray((fg * 255).astype("uint8")).filter(ImageFilter.GaussianBlur(0.8))
    out = cell.convert("RGBA")
    out.putalpha(alpha)
    return out


def main(sheet: str, out: str, prefix: str = "") -> None:
    im = Image.open(sheet).convert("RGB")
    w, h = im.size
    cw, ch = w // 3, h // 3
    Path(out).mkdir(parents=True, exist_ok=True)
    for r, mood in enumerate(MOOD):
        for c, body in enumerate(BODY):
            spr = cut(im.crop((c * cw, r * ch, (c + 1) * cw, (r + 1) * ch)))
            bbox = spr.getchannel("A").point(lambda v: 255 if v > 24 else 0).getbbox()
            if bbox:
                spr = spr.crop(bbox)
            side = max(spr.size) + 16
            canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
            canvas.paste(spr, ((side - spr.width) // 2, side - spr.height - 8), spr)
            canvas.resize((384, 384), Image.LANCZOS).save(Path(out) / f"{prefix}{body}_{mood}.png", optimize=True)


if __name__ == "__main__":
    main(*sys.argv[1:])
