"""Per-group conversation state.

One `GroupState` per chat channel. It is the only thing the engine reads or
writes, it is plain JSON, and it lives in the database between messages, so
any worker can pick up any group (no in-process memory).
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Optional

MODES = ("any", "japan", "bachelorette", "grad")
STAGES = ("where", "when", "budget", "hotel", "commit", "booked")
STAGE_LABELS = {
    "where": "Where?",
    "when": "When?",
    "budget": "Budget",
    "hotel": "Hotel",
    "commit": "Who's in",
    "booked": "Booked!",
}
BUFFER_CAP = 30
RECENT_CAP = 12
SAID_CAP = 8


@dataclass
class Msg:
    user_id: str
    name: str
    text: str
    ts: float


@dataclass
class Member:
    user_id: str
    name: str
    budget: Optional[int] = None
    date_start: Optional[str] = None
    date_end: Optional[str] = None
    city: Optional[str] = None
    vibe: list[str] = field(default_factory=list)
    committed: bool = False
    messages: int = 0
    last_spoke: float = 0.0


@dataclass
class Plan:
    city: Optional[str] = None
    date_start: Optional[str] = None
    date_end: Optional[str] = None
    budget_pp: Optional[int] = None
    vibe: list[str] = field(default_factory=list)
    hotel: Optional[dict] = None
    notes: dict[str, str] = field(default_factory=dict)

    @property
    def nights(self) -> Optional[int]:
        from datetime import date
        if not (self.date_start and self.date_end):
            return None
        try:
            n = (date.fromisoformat(self.date_end) - date.fromisoformat(self.date_start)).days
        except ValueError:
            return None
        return n if n > 0 else None


@dataclass
class Pet:
    physical: int = 100   # Body — hotel market (prices up / rooms gone → hurts)
    mental: int = 100     # Mood — group-chat activity (silence → hurts)
    alive: bool = True
    deaths: int = 0

    def clamp(self) -> None:
        self.physical = max(0, min(100, int(round(self.physical))))
        self.mental = max(0, min(100, int(round(self.mental))))
        if self.physical == 0 or self.mental == 0:
            if self.alive:
                self.deaths += 1
            self.alive = False


@dataclass
class GroupState:
    group_id: str
    platform: str = "discord"
    channel_id: str = ""
    guild_id: str = ""
    mode: str = "any"
    active: bool = True
    created_at: float = field(default_factory=time.time)

    members: dict[str, Member] = field(default_factory=dict)
    plan: Plan = field(default_factory=Plan)
    pet: Pet = field(default_factory=Pet)
    stage: str = "where"
    blockers: list[str] = field(default_factory=list)

    buffer: list[Msg] = field(default_factory=list)   # trip-ish messages not yet extracted
    recent: list[Msg] = field(default_factory=list)   # rolling context for the voice
    said: list[str] = field(default_factory=list)     # Tabi's last lines (dedupe)
    asks: dict[str, float] = field(default_factory=dict)  # topic -> last time Tabi asked
    market: Optional[dict] = None                     # {price, count, ts}

    last_user_ts: float = 0.0
    last_spoke_ts: float = 0.0
    last_extract_ts: float = 0.0
    last_tick_ts: float = 0.0
    booked_at: float = 0.0
    version: int = 0

    # ─── helpers ────────────────────────────────────────────────────────────
    def member(self, user_id: str, name: str) -> Member:
        m = self.members.get(user_id)
        if m is None:
            m = self.members[user_id] = Member(user_id=user_id, name=name)
        elif name and m.name != name:
            m.name = name
        return m

    def member_by_name(self, name: str) -> Optional[Member]:
        low = (name or "").strip().lower()
        for m in self.members.values():
            if m.name.lower() == low:
                return m
        return None

    def push_message(self, msg: Msg, trip_signal: bool) -> None:
        self.recent = (self.recent + [msg])[-RECENT_CAP:]
        if trip_signal:
            self.buffer = (self.buffer + [msg])[-BUFFER_CAP:]

    def note_said(self, line: str) -> None:
        self.said = (self.said + [line])[-SAID_CAP:]

    @property
    def booked(self) -> bool:
        return self.stage == "booked"

    # ─── (de)serialisation ──────────────────────────────────────────────────
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "GroupState":
        d = dict(d)
        known = {f.name for f in fields(cls)}
        d = {k: v for k, v in d.items() if k in known}
        d["members"] = {k: _build(Member, v) for k, v in (d.get("members") or {}).items()}
        d["plan"] = _build(Plan, d.get("plan") or {})
        d["pet"] = _build(Pet, d.get("pet") or {})
        for key in ("buffer", "recent"):
            d[key] = [_build(Msg, m) for m in (d.get(key) or [])]
        return cls(**d)


def _build(kind, data: dict):
    known = {f.name for f in fields(kind)}
    return kind(**{k: v for k, v in data.items() if k in known})
