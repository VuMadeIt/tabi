"""Pixel chat card (default): kawaii Tabi in a little scene, two stat panels
(BODY = hotel prices, MOOD = group energy) and Tabi's line in a speech strip.
Buttons are native Discord buttons under the image.
"""
from __future__ import annotations

import os
import re
import textwrap
from functools import lru_cache
from io import BytesIO
from typing import Optional

from PIL import Image, ImageDraw

from tabi.core.models import GroupState
from tabi.render.card import _font, sprite_name

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SPRITES = os.path.join(ROOT, "assets", os.environ.get("TABI_SPRITES", "kawaii"))

W, H = 960, 450
P = 6  # "pixel" size
WOOD, WOOD_D, WOOD_L = (150, 98, 62), (104, 64, 40), (196, 140, 92)
CREAM, CREAM_D = (253, 240, 218), (236, 214, 180)
INK, MUTED = (82, 50, 32), (150, 112, 86)
BODY_C, BODY_L = (246, 150, 60), (252, 196, 120)
MOOD_C, MOOD_L = (240, 128, 160), (250, 186, 206)
TRACK = (110, 72, 48)
GOOD, BAD = (84, 160, 84), (214, 72, 60)
_EMOJI = re.compile("[\U0001F000-\U0001FFFF\u2600-\u27BF\uFE0F\u200D]")
STAGES = ["where", "when", "budget", "hotel", "commit"]


def _px_box(d: ImageDraw.ImageDraw, box, fill, edge, light=None, dark=None, s: int = P) -> None:
    """Chunky box with stepped (notched) pixel corners and a bevel."""
    x0, y0, x1, y1 = box
    d.rectangle((x0 + s, y0, x1 - s, y1), fill=edge)
    d.rectangle((x0, y0 + s, x1, y1 - s), fill=edge)
    d.rectangle((x0 + s, y0 + s, x1 - s, y1 - s), fill=fill)
    if light:
        d.rectangle((x0 + s, y0 + s, x1 - s, y0 + 2 * s - 1), fill=light)
    if dark:
        d.rectangle((x0 + s, y1 - 2 * s + 1, x1 - s, y1 - s), fill=dark)


def _px_heart(d, x, y, c, s=4) -> None:
    rows = ["01100110", "11111111", "11111111", "01111110", "00111100", "00011000"]
    for j, row in enumerate(rows):
        for i, b in enumerate(row):
            if b == "1":
                d.rectangle((x + i * s, y + j * s, x + i * s + s - 1, y + j * s + s - 1), fill=c)


def _px_bed(d, x, y, c, s=4) -> None:
    rows = ["1000000000", "1000000000", "1011000000", "1011111111", "1111111111", "1000000001", "1000000001"]
    for j, row in enumerate(rows):
        for i, b in enumerate(row):
            if b == "1":
                d.rectangle((x + i * s, y + j * s, x + i * s + s - 1, y + j * s + s - 1), fill=c)


def _px_paw(d, x, y, c, s=3) -> None:
    rows = ["0101", "1010", "0000", "0110", "1111", "0110"]
    for j, row in enumerate(rows):
        for i, b in enumerate(row):
            if b == "1":
                d.rectangle((x + i * s, y + j * s, x + i * s + s - 1, y + j * s + s - 1), fill=c)


@lru_cache(maxsize=4)
def _scene(w: int, h: int, rotten: bool) -> Image.Image:
    """Pink banded sky, pixel sakura petals, a wooden table."""
    im = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(im)
    top, bot = ((214, 214, 196), (196, 204, 176)) if rotten else ((255, 214, 226), (255, 238, 222))
    bands = 8
    for i in range(bands):
        t = i / (bands - 1)
        col = tuple(int(top[k] + (bot[k] - top[k]) * t) for k in range(3))
        d.rectangle((0, i * h // bands, w, (i + 1) * h // bands), fill=col)
    # distant hills
    for x in range(0, w, P):
        y = int(h * 0.58 - 18 * abs(((x / w) * 2.2) % 2 - 1))
        d.rectangle((x, y, x + P - 1, h), fill=(236, 190, 206) if not rotten else (186, 192, 164))
    petals = [(30, 30), (90, 70), (210, 24), (250, 96), (150, 50), (60, 120), (280, 40)]
    for px, py in petals:
        if px < w - 10:
            c = (250, 170, 196) if not rotten else (150, 168, 120)
            d.rectangle((px, py, px + P, py + P), fill=c)
            d.rectangle((px + P, py + P, px + 2 * P - 1, py + 2 * P - 1), fill=c)
    ty = int(h * 0.76)
    d.rectangle((0, ty, w, h), fill=WOOD_L)
    d.rectangle((0, ty, w, ty + P - 1), fill=(222, 170, 118))
    for x in range(0, w, 48):
        d.rectangle((x, ty + 2 * P, x + 24, ty + 2 * P + 2), fill=WOOD)
    return im


@lru_cache(maxsize=32)
def _sprite(name: str, size: int) -> Optional[Image.Image]:
    path = os.path.join(SPRITES, name)
    if not os.path.exists(path):
        return None
    return Image.open(path).convert("RGBA").resize((size, size), Image.LANCZOS)


def _bar(d, x, y, w, value, fill, light) -> None:
    h = 30
    _px_box(d, (x, y, x + w, y + h), TRACK, WOOD_D, s=4)
    inner = w - 16
    n = 20
    seg = inner / n
    on = round(n * max(0, min(100, value)) / 100)
    for i in range(on):
        sx = x + 8 + int(i * seg)
        d.rectangle((sx, y + 8, sx + int(seg) - 2, y + h - 8), fill=fill)
        d.rectangle((sx, y + 8, sx + int(seg) - 2, y + 11), fill=light)


def _stat(card, d, box, icon, label, sub, value, delta, fill, light) -> None:
    x0, y0, x1, y1 = box
    _px_box(d, box, CREAM, WOOD_D, light=(255, 250, 238), dark=CREAM_D)
    icon(d, x0 + 22, y0 + 24, fill if icon is _px_heart else WOOD_D)
    d.text((x0 + 80, y0 + 14), f"{label} {value}%", font=_font(34, "Bold"), fill=INK)
    d.text((x0 + 80, y0 + 52), sub, font=_font(22, "Medium"), fill=MUTED)
    if delta:
        d.text((x1 - 22, y0 + 18), f"{delta:+d}", font=_font(28, "Bold"),
               fill=GOOD if delta > 0 else BAD, anchor="ra")
    _bar(d, x0 + 80, y0 + 86, x1 - x0 - 104, value, fill, light)


def _stage_pips(d, x, y, stage: str) -> None:
    cur = len(STAGES) if stage == "booked" else STAGES.index(stage) if stage in STAGES else 0
    for i in range(len(STAGES)):
        c = (240, 128, 160) if i < cur else (255, 255, 255) if i == cur else (230, 206, 200)
        d.rectangle((x + i * 22, y, x + i * 22 + 13, y + 13), fill=c, outline=WOOD_D, width=3)


def render(g: GroupState, deltas: Optional[dict] = None, line: str = "") -> bytes:
    deltas = deltas or {}
    card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(card)
    _px_box(d, (0, 0, W - 1, H - 1), WOOD, WOOD_D, light=WOOD_L, dark=WOOD_D, s=P * 2)
    _px_box(d, (14, 14, W - 15, H - 15), CREAM, WOOD_D, s=P)
    for cx, cy in ((26, 24), (W - 58, 24), (26, H - 46), (W - 58, H - 46)):
        _px_heart(d, cx, cy, (240, 128, 160), s=3)

    # scene + Tabi
    rotten = sprite_name(g.pet.physical, g.pet.mental, g.pet.alive).startswith("rotten_")
    sx0, sy0, sx1, sy1 = 34, 40, 334, 300
    _px_box(d, (sx0 - 6, sy0 - 6, sx1 + 6, sy1 + 6), WOOD_D, WOOD_D, s=P)
    card.paste(_scene(sx1 - sx0, sy1 - sy0, rotten), (sx0, sy0))
    spr = _sprite(sprite_name(g.pet.physical, g.pet.mental, g.pet.alive), 250)
    if spr is not None:
        card.alpha_composite(spr, (sx0 + 25, sy1 - 250 + 6))
    tag = (g.plan.city or "somewhere?").upper()
    if g.booked:
        tag += " ✓"
    if not g.pet.alive:
        tag = "R.I.P."
    f = _font(22, "Bold")
    tw = int(f.getlength(tag))
    _px_box(d, (sx0 + 8, sy0 + 8, sx0 + 32 + tw, sy0 + 44), CREAM, WOOD_D, s=4)
    d.text((sx0 + 20, sy0 + 26), tag, font=f, fill=INK, anchor="lm")
    _stage_pips(d, sx1 - 118, sy0 + 18, g.stage)

    # stat panels
    _stat(card, d, (350, 34, W - 34, 164), _px_bed, "BODY", "Hotel prices", g.pet.physical,
          deltas.get("body", 0), BODY_C, BODY_L)
    _stat(card, d, (350, 176, W - 34, 306), _px_heart, "MOOD", "Group energy", g.pet.mental,
          deltas.get("mood", 0), MOOD_C, MOOD_L)

    # speech strip
    bx0, by0, bx1, by1 = 34, 320, W - 34, H - 34
    _px_box(d, (bx0, by0, bx1, by1), (255, 250, 240), WOOD_D, dark=CREAM_D)
    _px_paw(d, bx0 + 18, by1 - 34, MUTED)
    _px_paw(d, bx1 - 32, by1 - 34, MUTED)
    text = _EMOJI.sub("", line).strip() or _EMOJI.sub("", _default_line(g)).strip()
    text = " ".join(text.split())
    lines = textwrap.wrap(text, 52)[:2]
    fs = _font(28 if len(lines) == 1 else 25, "SemiBold")
    cy = (by0 + by1) // 2
    for i, ln in enumerate(lines):
        off = 0 if len(lines) == 1 else (-16 if i == 0 else 16)
        d.text(((bx0 + bx1) // 2, cy + off), ln, font=fs, fill=INK, anchor="mm")

    out = BytesIO()
    card.save(out, format="PNG", optimize=True)
    return out.getvalue()


def _default_line(g: GroupState) -> str:
    if not g.pet.alive:
        return "tabi is gone… revive her and book the trip 💀"
    if g.booked:
        return "WE'RE GOING!! best group chat ever"
    if g.blockers:
        who = ", ".join(g.blockers[:2])
        need = {"where": "where to go", "when": "their dates", "budget": "a budget",
                "commit": "if they're in"}.get(g.stage, "")
        verb = "haven't" if len(g.blockers) > 1 else "hasn't"
        return f"{who} still {verb} said {need}…" if need else f"waiting on {who}…"
    return "keep talking, i'm listening 🍣"
