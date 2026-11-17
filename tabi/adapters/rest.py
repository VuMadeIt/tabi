"""Outbound Discord REST: lets any stateless worker post to a channel."""
from __future__ import annotations

import json
import logging
import time
from typing import Optional

import requests

from tabi.engine import Action

log = logging.getLogger("tabi.discord.rest")
API = "https://discord.com/api/v10"

BUTTONS = {
    "in": {"label": "I'm in ✓", "style": 3, "custom_id": "tabi:in"},
    "budget": {"label": "Set my budget", "style": 2, "custom_id": "tabi:budget"},
    "dates": {"label": "My dates", "style": 2, "custom_id": "tabi:dates"},
    "status": {"label": "Plan", "style": 2, "custom_id": "tabi:status"},
    "revive": {"label": "Revive Tabi ⚡", "style": 1, "custom_id": "tabi:revive"},
    "book": {"label": "We booked it! 🎉", "style": 3, "custom_id": "tabi:book"},
}


def components(a: Action) -> list[dict]:
    rows: list[dict] = []
    if a.hotels:
        vote = [{"type": 2, "style": 1, "label": f"Vote {i + 1}", "custom_id": f"tabi:vote:{i}"}
                for i, h in enumerate(a.hotels) if h.get("price_total") is not None]
        links = [{"type": 2, "style": 5, "label": (f"{i + 1}. " + h["name"])[:78], "url": h["url"]}
                 for i, h in enumerate(a.hotels) if h.get("url")]
        if vote:
            rows.append({"type": 1, "components": vote[:5]})
        if links:
            rows.append({"type": 1, "components": links[:5]})
        if not vote:  # link-out mode: someone books, then the group confirms here
            rows.append({"type": 1, "components": [{"type": 2, **BUTTONS["book"]}, {"type": 2, **BUTTONS["in"]}]})
    keys = [b for b in a.buttons if b in BUTTONS and b != "vote"]
    if keys:
        rows.append({"type": 1, "components": [{"type": 2, **BUTTONS[k]} for k in keys][:5]})
    return rows[:5]


def hotel_lines(hotels: list[dict]) -> str:
    out = []
    for i, h in enumerate(hotels):
        if h.get("price_total") is None:
            continue
        stars = f" · ★{h['rating']:.1f}" if h.get("rating") else ""
        out.append(f"**{i + 1}. {h['name']}** · ${h['price_pp']:,}/person{stars}")
    return "\n".join(out)


class RestSender:
    def __init__(self, token: str):
        self.h = {"Authorization": f"Bot {token}", "User-Agent": "TabiBot (https://github.com/VuMadeIt/tabi, 1.0)"}

    def send(self, channel_id: str, a: Action, card_png: Optional[bytes] = None) -> Optional[dict]:
        text = a.text
        if a.hotels and (hl := hotel_lines(a.hotels)):
            text = f"{text}\n{hl}"
        payload = {"content": text[:2000], "components": components(a),
                   "allowed_mentions": {"parse": []}}
        files = None
        if card_png:
            payload["attachments"] = [{"id": 0, "filename": "tabi.png"}]
            files = {"files[0]": ("tabi.png", card_png, "image/png")}
        for _ in range(4):
            if files:
                r = requests.post(f"{API}/channels/{channel_id}/messages", headers=self.h,
                                  data={"payload_json": json.dumps(payload)}, files=files, timeout=20)
            else:
                r = requests.post(f"{API}/channels/{channel_id}/messages", headers=self.h,
                                  json=payload, timeout=20)
            if r.status_code == 429:
                time.sleep(float(r.json().get("retry_after", 1)) + 0.1)
                continue
            if r.status_code >= 400:
                log.warning("discord send failed %s: %s", r.status_code, r.text[:300])
                return None
            return r.json()
        return None
