"""Agent 2 — Tabi (the voice). Decides IF she speaks and WHAT she says.

LangGraph, 3 nodes:

    decide ──► compose ──► guard

decide   deterministic: trigger + state + cooldowns → one intent (or silence)
compose  template line (free). LLM only for direct replies & the deathbed
         moment, ONE draft, within the group's daily budget
guard    length cap, no mass pings, no repeats, attach card/buttons
"""
from __future__ import annotations

import random
import re
import time
from datetime import date
from typing import Any, Optional, TypedDict

from langgraph.graph import END, StateGraph

from tabi.agents import lines
from tabi.core.models import STAGE_LABELS, GroupState

ASK_GAP_S = 30 * 60          # don't re-ask the same question within 30 min
LLM_INTENTS = {"reply", "deathbed"}
VOICE_SYSTEM = """You are Tabi, a tiny kawaii sushi who lives in a friend group's chat and
gets healthier when they plan their trip and rots when they stall. Lowercase, playful,
1-2 short sentences, at most 2 emoji, never mean about money. Always steer back to the
next planning step. Return JSON {"text": "..."}."""


class VoiceState(TypedDict, total=False):
    g: GroupState
    trigger: str            # message | tick | command | progress
    text: str               # the user line that triggered us (if any)
    addressed: bool         # @mention / "tabi" in the line
    progress: list[str]
    events: list[str]       # engine-level: market_drop, market_good, died, revived, booked, hello, flake
    llm: Any
    settings: Any
    intent: Optional[str]
    line: Optional[str]
    card: bool
    buttons: list[str]


def _fmt_dates(g: GroupState) -> str:
    p = g.plan
    if not (p.date_start and p.date_end):
        return "soon"
    a, b = date.fromisoformat(p.date_start), date.fromisoformat(p.date_end)
    return f"{a:%b} {a.day}–{b.day}" if a.month == b.month else f"{a:%b} {a.day}–{b:%b} {b.day}"


def _who(g: GroupState) -> str:
    names = g.blockers[:2]
    return " & ".join(names) if names else "everyone"


def _asked_recently(g: GroupState, topic: str) -> bool:
    return time.time() - g.asks.get(topic, 0) < ASK_GAP_S


def decide(s: VoiceState) -> VoiceState:
    g, st = s["g"], s["settings"]
    ev, prog = s.get("events") or [], s.get("progress") or []
    now = time.time()
    cooled = now - g.last_spoke_ts >= st.speak_cooldown_s
    intent: Optional[str] = None

    for e in ("hello", "booked", "died", "revived", "flake"):
        if e == "flake" and "stage:hotel" in prog:
            continue                        # hotel choices matter more than the guilt trip
        if e in ev:
            intent = {"died": "dead"}.get(e, e)
            break
    if intent is None and g.booked:
        intent = None
    elif intent is None and not g.pet.alive:
        intent = None                       # dead pets don't talk until revived
    elif intent is None:
        if "stage:hotel" in prog:
            intent = "hotels"
        elif "stage:commit" in prog:
            intent = "ask_commit"
        elif s.get("addressed"):
            intent = "reply"
        elif min(g.pet.physical, g.pet.mental) <= 20 and cooled and not _asked_recently(g, "deathbed"):
            intent = "deathbed"
        elif "market_drop" in ev and cooled:
            intent = "market_drop"
        elif "market_good" in ev and cooled:
            intent = "market_good"
        elif s.get("trigger") == "tick" and "lonely" in ev:
            intent = "lonely"
        elif prog and cooled and g.stage in ("when", "budget"):
            intent = f"ask_{g.stage}"       # celebrate implicitly + ask the next thing
        elif cooled and g.stage in ("where", "when", "budget", "commit") and g.blockers \
                and not _asked_recently(g, g.stage) and (s.get("trigger") == "tick" or len(g.recent) >= 6):
            intent = f"ask_{g.stage}"
        if intent == "ask_when" and g.plan.notes.get("dates") == "no overlap":
            intent = "date_clash"
    s["intent"] = intent
    return s


def _vars(g: GroupState) -> dict:
    budget = f"${g.plan.budget_pp:,}" if g.plan.budget_pp else "?"
    return {"name": "", "names": ", ".join(m.name for m in g.members.values()),
            "city": g.plan.city or "the trip", "dates": _fmt_dates(g), "budget": budget,
            "body": g.pet.physical, "mood": g.pet.mental, "who": _who(g),
            "hotel": (g.plan.hotel or {}).get("name", "the hotel"),
            "stage": STAGE_LABELS.get(g.stage, g.stage)}


def compose(s: VoiceState) -> VoiceState:
    g, intent = s["g"], s.get("intent")
    if not intent:
        s["line"] = None
        return s
    line = None
    llm = s.get("llm")
    if intent in LLM_INTENTS and llm is not None and llm.enabled:
        recent = "\n".join(f"{m.name}: {m.text[:200]}" for m in g.recent[-6:])
        plan = (f"stage={g.stage} city={g.plan.city} dates={_fmt_dates(g)} budget={g.plan.budget_pp} "
                f"blockers={g.blockers} body={g.pet.physical} mood={g.pet.mental} mode={g.mode}")
        ask = "Reply to the last line." if intent == "reply" else \
              "You are nearly dead. Make one dramatic, funny plea naming the blockers."
        out = llm.json(g.group_id, VOICE_SYSTEM, f"PLAN: {plan}\nCHAT:\n{recent}\n{ask}", max_tokens=120)
        line = (out or {}).get("text")
    if not line:
        options = [t for t in lines.pool("reply_fallback" if intent == "reply" else intent, g.mode)]
        fresh = [t for t in options if t.format(**_vars(g)) not in g.said] or options
        line = random.choice(fresh).format(**_vars(g))
    s["line"] = line
    return s


def guard(s: VoiceState) -> VoiceState:
    g, line, intent = s["g"], s.get("line"), s.get("intent")
    if not line:
        return s
    line = " ".join(str(line).split())
    for bad in ("@everyone", "@here"):
        line = line.replace(bad, bad[1:])
    if len(line) > 200:
        parts = re.split(r"(?<=[.!?…])\s+", line)
        line = " ".join(parts[:2])
    if len(line) > 300:
        line = line[:297].rstrip() + "…"
    if line in g.said and intent not in ("hotels", "booked", "dead", "hello"):
        s["line"] = None
        return s
    s["line"] = line
    s["card"] = intent in ("hello", "hotels", "ask_commit", "market_drop", "deathbed", "dead",
                           "revived", "booked", "lonely", "date_clash")
    s["buttons"] = {"dead": ["revive"], "hotels": ["vote"], "booked": []}.get(
        intent, ["in", "budget", "dates", "status"] if s["card"] else [])
    now = time.time()
    g.last_spoke_ts = now
    g.note_said(line)
    if intent and (intent.startswith("ask_") or intent in ("deathbed", "date_clash")):
        g.asks[intent.removeprefix("ask_")] = now
    return s


def build():
    sg = StateGraph(VoiceState)
    sg.add_node("decide", decide)
    sg.add_node("compose", compose)
    sg.add_node("guard", guard)
    sg.set_entry_point("decide")
    sg.add_edge("decide", "compose")
    sg.add_edge("compose", "guard")
    sg.add_edge("guard", END)
    return sg.compile()


GRAPH = build()


def run(g: GroupState, settings, llm=None, trigger: str = "message", text: str = "",
        addressed: bool = False, progress: Optional[list[str]] = None,
        events: Optional[list[str]] = None) -> dict:
    out = GRAPH.invoke({"g": g, "settings": settings, "llm": llm, "trigger": trigger, "text": text,
                        "addressed": addressed, "progress": progress or [], "events": events or []})
    return {"intent": out.get("intent"), "text": out.get("line"),
            "card": bool(out.get("card")), "buttons": out.get("buttons") or []}
