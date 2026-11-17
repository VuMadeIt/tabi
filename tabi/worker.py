"""Stateless worker: pops events off the queue, runs the engine, posts replies."""
from __future__ import annotations

import logging
import threading
import time

from tabi.adapters.rest import RestSender
from tabi.engine import Action, Engine
from tabi.render import card

log = logging.getLogger("tabi.worker")


def deliver(engine: Engine, sender: RestSender, group_id: str, actions: list[Action]) -> None:
    g = engine.load(group_id)
    if g is None:
        return
    for a in actions:
        if a.kind == "ephemeral":
            continue
        png = card.render(g, a.deltas, a.text) if a.card else None
        sender.send(g.channel_id, a, png)


def handle(engine: Engine, ev: dict) -> list[Action]:
    kind, gid = ev.get("type"), ev.get("group_id", "")
    if kind == "message":
        return engine.message(gid, ev["user_id"], ev["name"], ev["text"],
                              addressed=ev.get("addressed", False), ts=ev.get("ts"))
    if kind == "tick":
        return engine.tick(gid)
    log.warning("unknown event %s", kind)
    return []


def run(engine: Engine, sender: RestSender, stop: threading.Event | None = None) -> None:
    stop = stop or threading.Event()
    log.info("worker started")
    while not stop.is_set():
        try:
            ev = engine.cache.pop(timeout=1.0)
        except Exception:
            log.exception("queue pop failed; retrying")  # e.g. Redis timeout after a network blip
            time.sleep(1.0)
            continue
        if not ev:
            continue
        try:
            deliver(engine, sender, ev["group_id"], handle(engine, ev))
        except Exception:
            log.exception("event failed: %s", {k: ev.get(k) for k in ("type", "group_id")})
            time.sleep(0.2)
