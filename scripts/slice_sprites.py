"""Slice a 3x3 Tabi sprite sheet into transparent per-state PNGs.

usage: python scripts/slice_sprites.py SHEET.png OUT_DIR [prefix]
Columns = body (full, half, low), rows = mood (happy, meh, sad).
"""
import sys
from pathlib import Path

from PIL import Image
from rembg import new_session, remove

BODY = ["full", "half", "low"]
MOOD = ["happy", "meh", "sad"]


def main(sheet: str, out: str, prefix: str = "") -> None:
    im = Image.open(sheet).convert("RGB")
    w, h = im.size
    cw, ch = w // 3, h // 3
    sess = new_session("isnet-general-use")
    Path(out).mkdir(parents=True, exist_ok=True)
    for r, mood in enumerate(MOOD):
        for c, body in enumerate(BODY):
            cell = im.crop((c * cw, r * ch, (c + 1) * cw, (r + 1) * ch))
            cut = remove(cell, session=sess, post_process_mask=True)
            bbox = cut.getchannel("A").point(lambda a: 255 if a > 24 else 0).getbbox()
            if bbox:
                cut = cut.crop(bbox)
            side = max(cut.size) + 16
            canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
            canvas.paste(cut, ((side - cut.width) // 2, side - cut.height - 8), cut)
            canvas = canvas.resize((384, 384), Image.LANCZOS)
            canvas.save(Path(out) / f"{prefix}{body}_{mood}.png", optimize=True)
            print("saved", f"{prefix}{body}_{mood}.png")


if __name__ == "__main__":
    main(*sys.argv[1:])
