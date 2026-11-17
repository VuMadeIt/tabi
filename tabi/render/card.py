"""Tabi status card — one small textured paper card (Pillow → PNG bytes).

Sticker Tabi on the left (sprite picked by Body × Mood), two bars on the
right, a 5-stamp quest path underneath, and an optional blocker tag. Kept
deliberately sparse so it reads in a Discord embed at a glance.
"""
from __future__ import annotations

import os
import random
from functools import lru_cache
from io import BytesIO
from typing import Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from tabi.core import health
from tabi.core.models import GroupState

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SPRITES = os.path.join(ROOT, "assets", os.environ.get("TABI_SPRITES", "kawaii"))
FONT = os.path.join(ROOT, "assets", "fonts", "Fredoka.ttf")

W, H = 960, 400
PAPER = (251, 244, 230)
INK = (92, 58, 40)
MUTED = (160, 128, 104)
TRACK = (240, 226, 204)
BODY = (238, 104, 86)
MOOD = (240, 136, 170)
GOOD = (96, 170, 96)
BAD = (214, 72, 60)
TAPE = (246, 206, 214)
PATH = [("where", "Where"), ("when", "When"), ("budget", "Budget"), ("hotel", "Hotel"), ("commit", "Go!")]


@lru_cache(maxsize=16)
def _font(size: int, weight: str = "SemiBold") -> ImageFont.FreeTypeFont:
    try:
        f = ImageFont.truetype(FONT, size)
        f.set_variation_by_name(weight)
        return f
    except (OSError, ValueError):
        return ImageFont.load_default(size)


@lru_cache(maxsize=1)
def _paper() -> Image.Image:
    rnd = random.Random(7)
    base = Image.new("RGB", (W, H), PAPER)
    noise = Image.effect_noise((W, H), 18).convert("L").filter(ImageFilter.GaussianBlur(0.6))
    grain = Image.merge("RGB", [noise] * 3)
    base = Image.blend(base, Image.composite(grain, base, Image.new("L", (W, H), 40)), 0.35)
    d = ImageDraw.Draw(base)
    for _ in range(140):  # paper fibres
        x, y = rnd.randrange(W), rnd.randrange(H)
        d.line((x, y, x + rnd.randint(-6, 6), y + rnd.randint(-3, 3)), fill=(236, 224, 202), width=1)
    for x in range(24, W, 32):  # faint dot grid like a journal page
        for y in range(24, H, 32):
            d.point((x, y), fill=(232, 216, 192))
    return base


def sprite_name(physical: int, mental: int, alive: bool = True) -> str:
    body, mood = health.body_tier(physical), health.mood_tier(mental)
    if not alive:
        body, mood = "low", "sad"
    prefix = "rotten_" if (health.is_rotten(mental) or not alive) else ""
    return f"{prefix}{body}_{mood}.png"


@lru_cache(maxsize=32)
def _sprite(name: str, size: int) -> Optional[Image.Image]:
    path = os.path.join(SPRITES, name)
    if not os.path.exists(path):
        return None
    return Image.open(path).convert("RGBA").resize((size, size), Image.LANCZOS)


def _tape(card: Image.Image, xy: tuple[int, int], w: int, h: int, angle: float, color) -> None:
    t = Image.new("RGBA", (w, h), color + (210,))
    d = ImageDraw.Draw(t)
    for x in range(0, w, 14):  # gingham-ish stripes
        d.rectangle((x, 0, x + 6, h), fill=tuple(min(255, c + 12) for c in color) + (220,))
    for i in range(0, h, 4):  # torn ends
        d.point((0, i), fill=(0, 0, 0, 0))
        d.point((w - 1, i + 2), fill=(0, 0, 0, 0))
    t = t.rotate(angle, expand=True, resample=Image.BICUBIC)
    card.alpha_composite(t, xy)


def _bar(d: ImageDraw.ImageDraw, x: int, y: int, w: int, label: str, value: int, color, delta: int) -> None:
    d.text((x, y), label, font=_font(24, "Medium"), fill=INK)
    d.text((x + w, y), f"{value}%", font=_font(26, "Bold"), fill=INK, anchor="ra")
    if delta:
        d.text((x + w - 72, y + 2), f"{delta:+d}", font=_font(22, "Bold"),
               fill=GOOD if delta > 0 else BAD, anchor="ra")
    by, bh = y + 36, 26
    d.rounded_rectangle((x, by, x + w, by + bh), radius=13, fill=TRACK, outline=(226, 206, 178), width=2)
    fw = int((w - 6) * max(0, min(100, value)) / 100)
    if fw > 10:
        d.rounded_rectangle((x + 3, by + 3, x + 3 + fw, by + bh - 3), radius=10, fill=color)
        d.rounded_rectangle((x + 8, by + 6, x + max(9, fw - 6), by + 10), radius=3,
                            fill=tuple(min(255, c + 45) for c in color))
    for i in range(1, 5):  # segment ticks
        tx = x + w * i // 5
        d.line((tx, by + 5, tx, by + bh - 5), fill=(255, 255, 255), width=2)


def _icon_heart(d: ImageDraw.ImageDraw, cx: int, cy: int, r: int, color) -> None:
    d.ellipse((cx - r, cy - r, cx, cy), fill=color)
    d.ellipse((cx, cy - r, cx + r, cy), fill=color)
    d.polygon([(cx - r, cy - r // 2 + 2), (cx + r, cy - r // 2 + 2), (cx, cy + r)], fill=color)


def _icon_bubble(d: ImageDraw.ImageDraw, cx: int, cy: int, r: int, color) -> None:
    d.rounded_rectangle((cx - r, cy - r + 2, cx + r, cy + r - 4), radius=r // 2, fill=color)
    d.polygon([(cx - r // 2, cy + r - 6), (cx - r // 2 - 2, cy + r + 4), (cx + 2, cy + r - 6)], fill=color)
    for i in (-1, 0, 1):
        d.ellipse((cx + i * 8 - 3, cy - 3, cx + i * 8 + 3, cy + 3), fill=(255, 255, 255))


def _path(d: ImageDraw.ImageDraw, x: int, y: int, w: int, stage: str) -> None:
    keys = [k for k, _ in PATH]
    cur = len(keys) if stage == "booked" else keys.index(stage) if stage in keys else 0
    step = w // (len(PATH) - 1)
    for i in range(len(PATH) - 1):
        x0 = x + i * step
        for dx in range(18, step - 14, 12):
            d.line((x0 + dx, y, x0 + dx + 6, y), fill=MUTED if i >= cur else BODY, width=3)
    for i, (_, label) in enumerate(PATH):
        cx = x + i * step
        done, here = i < cur, i == cur
        r = 15 if here else 12
        fill = BODY if done else (255, 255, 255)
        d.ellipse((cx - r, y - r, cx + r, y + r), fill=fill,
                  outline=BODY if (done or here) else (214, 190, 160), width=4 if here else 3)
        if done:
            d.line((cx - 6, y, cx - 1, y + 5, cx + 7, y - 5), fill=(255, 255, 255), width=4, joint="curve")
        d.text((cx, y + 24), label, font=_font(20, "SemiBold" if here else "Medium"),
               fill=INK if (here or done) else MUTED, anchor="ma")


def _blocker(card: Image.Image, x: int, y: int, text: str) -> None:
    f = _font(21, "SemiBold")
    tw = int(f.getlength(text))
    tag = Image.new("RGBA", (tw + 44, 38), (0, 0, 0, 0))
    td = ImageDraw.Draw(tag)
    td.rounded_rectangle((0, 0, tw + 44, 38), radius=10, fill=(222, 86, 72))
    td.text((22, 19), text, font=f, fill=(255, 246, 236), anchor="lm")
    card.alpha_composite(tag.rotate(-2, expand=True, resample=Image.BICUBIC), (x, y))


def render(g: GroupState, deltas: Optional[dict] = None, line: str = "",
           title: Optional[str] = None) -> bytes:
    if os.environ.get("TABI_CARD", "pixel") == "pixel":
        from tabi.render import pixel
        return pixel.render(g, deltas, line)
    return render_paper(g, deltas, title)


def render_paper(g: GroupState, deltas: Optional[dict] = None, title: Optional[str] = None) -> bytes:
    deltas = deltas or {}
    card = _paper().convert("RGBA").copy()
    d = ImageDraw.Draw(card)
    d.rounded_rectangle((6, 6, W - 7, H - 7), radius=28, outline=(232, 214, 186), width=3)
    _tape(card, (40, -6), 150, 34, 6, TAPE)
    _tape(card, (W - 190, H - 40), 150, 32, -5, (206, 226, 196))

    # sticker Tabi on a soft halo
    halo = Image.new("RGBA", (300, 300), (0, 0, 0, 0))
    halo = Image.new("RGBA", (300, 300), (255, 232, 226, 0))
    ImageDraw.Draw(halo).ellipse((14, 14, 286, 286), fill=(255, 232, 226, 255))
    card.alpha_composite(halo.filter(ImageFilter.GaussianBlur(8)), (24, 50))
    spr = _sprite(sprite_name(g.pet.physical, g.pet.mental, g.pet.alive), 290)
    if spr is not None:
        card.alpha_composite(spr, (28, 46))

    x0, bw = 360, 560
    head = title or (f"Tabi · {g.plan.city}" if g.plan.city else "Tabi")
    if not g.pet.alive:
        head = "Tabi · R.I.P."
    elif g.booked:
        head = f"Tabi · {g.plan.city} booked!"
    d.text((x0, 30), head, font=_font(40, "Bold"), fill=INK)

    _icon_heart(d, x0 + 14, 126, 13, BODY)
    _bar(d, x0 + 40, 96, bw - 40, "Body · hotel prices", g.pet.physical, BODY, deltas.get("body", 0))
    _icon_bubble(d, x0 + 14, 210, 14, MOOD)
    _bar(d, x0 + 40, 180, bw - 40, "Mood · chat energy", g.pet.mental, MOOD, deltas.get("mood", 0))

    _path(d, x0 + 30, 290, bw - 60, g.stage)

    if g.blockers and g.pet.alive and not g.booked:
        need = {"where": "city", "when": "dates", "budget": "budget", "commit": "tap I'm in"}.get(g.stage, "")
        names = ", ".join(g.blockers[:2]) + (f" +{len(g.blockers) - 2}" if len(g.blockers) > 2 else "")
        _blocker(card, 40, 338, f"waiting on {names} · {need}" if need else f"waiting on {names}")

    out = BytesIO()
    card.convert("RGB").save(out, format="PNG", optimize=True)
    return out.getvalue()
