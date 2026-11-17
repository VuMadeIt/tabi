"""Core engine: one normalized event in → state updated → outgoing actions.

Platform adapters (Discord today) only translate events in and actions out.
Everything stateful goes through here under a per-group lock, and the group
is re-read from the store every time, so any number of stateless workers can
share the load.

Agents (2):  Planner (4 nodes)  →  Tabi voice (3 nodes)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from tabi.agents import planner, voice
from tabi.agents.llm import LLM
from tabi.config import Settings
from tabi.core import health, hotels, parse
from tabi.core.cache import Budget
from tabi.core.models import MODES, GroupState, Msg
from tabi.core.store import Conflict, Store

log = logging.getLogger("tabi.engine")
MARKET_EVERY_S = 6 * 3600


@dataclass
class Action:
    kind: str                       # say | hotels | ephemeral
    text: str = ""
    card: bool = False
    buttons: list[str] = field(default_factory=list)
    hotels: list[dict] = field(default_factory=list)
    deltas: dict = field(default_factory=dict)   # {"body": -8, "mood": +5} for the card


class Engine:
    def __init__(self, s: Settings, store: Store, cache):
        self.s, self.store, self.cache = s, store, cache
        self.llm = LLM(s, Budget(cache, s.llm_calls_per_day, s.llm_tokens_per_day))

    # ─── plumbing ───────────────────────────────────────────────────────────
    def load(self, group_id: str) -> Optional[GroupState]:
        return self.store.get(group_id)

    def _txn(self, group_id: str, fn, create: dict | None = None):
        """Load → fn(g) → save, under the group lock, retrying on write conflicts."""
        with self.cache.lock(group_id):
            for _ in range(3):
                g = self.store.get(group_id)
                if g is None:
                    if create is None:
                        return None, []
                    g = GroupState(group_id=group_id, **create)
                before = (g.pet.physical, g.pet.mental)
                actions = fn(g) or []
                for a in actions:
                    if a.card:
                        a.deltas = {"body": g.pet.physical - before[0], "mood": g.pet.mental - before[1]}
                try:
                    self.store.save(g)
                    return g, actions
                except Conflict:
                    continue
        raise RuntimeError(f"could not save {group_id}")

    def _speak(self, g: GroupState, **kw) -> list[Action]:
        out = voice.run(g, self.s, llm=self.llm, **kw)
        if not out["text"]:
            return []
        acts = [Action("say", out["text"], card=out["card"], buttons=out["buttons"])]
        if out["intent"] == "hotels":
            acts[0].hotels = self._hotel_options(g)
            acts[0].kind = "hotels"
        return acts

    def _hotel_options(self, g: GroupState) -> list[dict]:
        p = g.plan
        opts = hotels.options(p.city, p.date_start, p.date_end, self._guests(g), p.budget_pp, g.group_id)
        g.plan.options, g.plan.votes = opts, {}
        return opts

    @staticmethod
    def _guests(g: GroupState) -> int:
        return max(2, sum(1 for m in g.members.values() if m.messages or m.committed))

    # ─── events ─────────────────────────────────────────────────────────────
    def start(self, group_id: str, channel_id: str, guild_id: str = "", mode: str = "any",
              platform: str = "discord") -> list[Action]:
        mode = mode if mode in MODES else "any"

        def fn(g: GroupState):
            fresh = g.version == 0 or not g.active
            g.active, g.mode, g.channel_id, g.guild_id = True, mode, channel_id, guild_id
            if fresh:
                self.store.log(group_id, "installed", mode=mode)
                return self._speak(g, trigger="command", events=["hello"])
            return [Action("say", f"i'm already here 🍣 mode set to **{mode}**", card=True,
                           buttons=["in", "budget", "dates", "status"])]
        return self._txn(group_id, fn, create={"platform": platform, "channel_id": channel_id,
                                               "guild_id": guild_id, "mode": mode})[1]

    def message(self, group_id: str, user_id: str, name: str, text: str,
                addressed: bool = False, ts: Optional[float] = None) -> list[Action]:
        ts = ts or time.time()

        def fn(g: GroupState):
            if not g.active:
                return []
            m = g.member(user_id, name)
            m.messages += 1
            m.last_spoke = ts
            was_committed = m.committed
            signal = parse.is_trip_signal(text, g.mode) or addressed
            msg = Msg(user_id=user_id, name=name, text=text[:500], ts=ts)
            g.push_message(msg, trip_signal=signal)
            g.last_user_ts = ts
            if not g.pet.alive:
                return []
            progress = planner.run(g, self.s, llm=self.llm, msg=msg)
            events = []
            if m.committed and not was_committed:
                progress.append(f"commit:{m.name}")
            if was_committed and not m.committed:
                events.append("flake")
                g.pet.mental = health._clamp(g.pet.mental - 10)
            g.pet.mental = health.mood_after_activity(g.pet.mental, 1 if signal else 0, len(progress))
            self._log_progress(g, progress)
            return self._speak(g, trigger="message", text=text, addressed=addressed,
                               progress=progress, events=events)
        return self._txn(group_id, fn)[1]

    def set_fact(self, group_id: str, user_id: str, name: str, **facts) -> list[Action]:
        """Button / slash-command input: budget, dates, city, committed."""
        def fn(g: GroupState):
            m = g.member(user_id, name)
            m.messages = max(1, m.messages)
            was = m.committed
            planner._apply_facts(m, facts)
            if facts.get("committed") is True and g.stage not in ("commit", "booked"):
                pass  # early "I'm in" still counts once we reach commit
            progress = planner.run(g, self.s, llm=None, force=False)
            if m.committed and not was:
                progress.append(f"commit:{m.name}")
            events = ["flake"] if (was and not m.committed) else []
            g.pet.mental = health.mood_after_activity(g.pet.mental, 0, len(progress))
            self._log_progress(g, progress)
            acts = self._speak(g, trigger="command", progress=progress, events=events)
            return acts
        return self._txn(group_id, fn)[1]

    def vote_hotel(self, group_id: str, user_id: str, name: str, idx: int) -> list[Action]:
        def fn(g: GroupState):
            opts = g.plan.options
            if not (0 <= idx < len(opts)):
                return [Action("ephemeral", "that hotel option expired, run /tabi hotels again")]
            votes = g.plan.votes
            votes[user_id] = idx
            g.member(user_id, name).messages = max(1, g.member(user_id, name).messages)
            need = max(1, (self._guests(g) + 1) // 2)
            tally = sum(1 for v in votes.values() if v == idx)
            if tally >= need or g.stage == "commit":
                g.plan.hotel = opts[idx]
                progress = planner.run(g, self.s)
                self.store.log(group_id, "hotel_locked", name=opts[idx]["name"])
                return self._speak(g, trigger="command", progress=progress) or [
                    Action("say", f"locked in **{opts[idx]['name']}** 🏨", card=True, buttons=["in", "status"])]
            return [Action("ephemeral", f"vote saved ({tally}/{need} needed for option {idx + 1})")]
        return self._txn(group_id, fn)[1]

    def book(self, group_id: str, user_id: str) -> list[Action]:
        def fn(g: GroupState):
            committed = [m for m in g.members.values() if m.committed]
            if g.stage != "commit" or len(committed) < 2:
                return [Action("ephemeral", "not yet! need a hotel + at least 2 people tapping I'M IN")]
            g.stage = "booked"
            g.booked_at = time.time()
            g.pet.physical, g.pet.mental = 100, 100
            self.store.log(group_id, "booked", people=len(committed), city=g.plan.city)
            return self._speak(g, trigger="command", events=["booked"])
        return self._txn(group_id, fn)[1]

    def click(self, group_id: str, what: str) -> None:
        self.store.log(group_id, "click", what=what)

    def revive(self, group_id: str, user_id: str) -> list[Action]:
        def fn(g: GroupState):
            if g.pet.alive:
                return [Action("ephemeral", "tabi's still alive (barely) 🍣")]
            g.pet.alive, g.pet.physical, g.pet.mental = True, 60, 60
            self.store.log(group_id, "revived", by=user_id, deaths=g.pet.deaths)
            return self._speak(g, trigger="command", events=["revived"])
        return self._txn(group_id, fn)[1]

    def reset(self, group_id: str) -> list[Action]:
        def fn(g: GroupState):
            fresh = GroupState(group_id=g.group_id, platform=g.platform, channel_id=g.channel_id,
                               guild_id=g.guild_id, mode=g.mode)
            g.__dict__.update({k: v for k, v in fresh.__dict__.items() if k != "version"})
            return self._speak(g, trigger="command", events=["hello"])
        return self._txn(group_id, fn)[1]

    def stop(self, group_id: str) -> None:
        def fn(g: GroupState):
            g.active = False
        self._txn(group_id, fn)

    def tick(self, group_id: str, now: Optional[float] = None) -> list[Action]:
        """Periodic: hotel market → Body, silence → Mood, flush batched extraction."""
        now = now or time.time()

        def fn(g: GroupState):
            if not g.active or g.booked or not g.pet.alive:
                return []
            events: list[str] = []
            progress: list[str] = []
            if g.buffer and now - g.buffer[-1].ts >= self.s.batch_quiet_s:
                progress = planner.run(g, self.s, llm=self.llm, force=True)
            # Body: hotel market for the current plan
            p = g.plan
            if p.city and p.date_start and (not g.market or now - g.market.get("ts", 0) >= MARKET_EVERY_S):
                cur = hotels.market(p.city, p.date_start, p.date_end, self._guests(g), g.group_id)
                if cur:
                    if g.market and bool(g.market.get("live")) == bool(cur.get("live")):
                        before = g.pet.physical
                        g.pet.physical = health.market_delta(g.pet.physical, g.market, cur)
                        if g.pet.physical <= before - 5:
                            events.append("market_drop")
                        elif g.pet.physical >= before + 5:
                            events.append("market_good")
                    g.market = cur
            elif not p.city and g.last_tick_ts and g.members:
                g.pet.physical = health.body_drift(g.pet.physical, (now - g.last_tick_ts) / 86400)
            # Mood: silence
            last_talk = g.last_user_ts or g.created_at
            if g.last_tick_ts:
                grace_end = last_talk + health.MOOD_GRACE_HOURS * 3600
                decay_h = max(0.0, now - max(g.last_tick_ts, grace_end)) / 3600
                g.pet.mental = health._clamp(g.pet.mental - min(health.DAMAGE_CAP, decay_h * health.MOOD_DECAY_PER_HOUR))
            silent_h = (now - last_talk) / 3600
            if silent_h * 3600 >= self.s.silence_nudge_s and now - g.last_spoke_ts >= self.s.silence_nudge_s:
                events.append("lonely")
            g.last_tick_ts = now
            was_alive = g.pet.alive
            g.pet.clamp()
            if was_alive and not g.pet.alive:
                self.store.log(group_id, "died", deaths=g.pet.deaths)
                return self._speak(g, trigger="tick", events=["died"])
            self._log_progress(g, progress)
            return self._speak(g, trigger="tick", progress=progress, events=events)
        return self._txn(group_id, fn)[1]

    def _log_progress(self, g: GroupState, progress: list[str]) -> None:
        for p in progress:
            if p.startswith("stage:"):
                self.store.log(g.group_id, "stage", stage=p[6:])

