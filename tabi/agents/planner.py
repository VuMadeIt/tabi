"""Agent 1 — Planner. Turns chat into one reconciled group plan.

LangGraph, 4 nodes, at most ONE LLM call per batch:

    read ──► (batch ready & budget?) ──► extract ──► reconcile ──► track
         └───────────────────────────────────────────┘

read       free regex facts from the newest line (budget, city, dates, in/out)
extract    one cheap LLM call over the *buffered* lines + current plan (delta only)
reconcile  deterministic rules: city majority, dates intersection, lowest budget
track      stage machine, blockers, progress events (which heal Mood)
"""
from __future__ import annotations

import json
import re
import time
from collections import Counter
from datetime import date
from typing import Any, Optional, TypedDict

from langgraph.graph import END, StateGraph

from tabi.core import parse
from tabi.core.models import STAGES, GroupState, Member, Msg

EXTRACT_SYSTEM = """You update a group-trip plan from new chat lines.
Return ONLY JSON: {"people": {"<name>": {"budget": int|null, "date_start": "YYYY-MM-DD"|null,
"date_end": "YYYY-MM-DD"|null, "city": str|null, "vibe": [str], "committed": true|false|null}},
"consensus_city": str|null}
Only include facts a person clearly stated about THEMSELVES in the new lines. Budgets are per
person in USD. Resolve vague dates ("second week of april", "reading week") to concrete days,
assuming the next upcoming occurrence after TODAY. Ignore off-topic chatter. Omit unknowns."""


class PlannerState(TypedDict, total=False):
    g: GroupState
    msg: Optional[Msg]
    force: bool
    llm: Any
    settings: Any
    progress: list[str]
    extracted: bool


def _apply_facts(m: Member, f: dict) -> None:
    if f.get("budget"):
        m.budget = int(f["budget"])
    if f.get("city"):
        m.city = str(f["city"]).strip().title() if str(f["city"]).islower() else str(f["city"]).strip()
    d = f.get("dates")
    if d and d[0] and d[1]:
        m.date_start, m.date_end = d[0], d[1]
    if f.get("date_start") and f.get("date_end"):
        m.date_start, m.date_end = f["date_start"], f["date_end"]
    if f.get("vibe"):
        m.vibe = sorted(set(m.vibe) | {str(v).lower() for v in f["vibe"]})[:6]
    if f.get("committed") is not None:
        m.committed = bool(f["committed"])


def _sane_window(start, end) -> bool:
    try:
        a, b = date.fromisoformat(str(start)), date.fromisoformat(str(end))
    except ValueError:
        return False
    return date.today() <= a < b and (b - a).days <= 60


# ─── nodes ──────────────────────────────────────────────────────────────────
def read(s: PlannerState) -> PlannerState:
    g, msg = s["g"], s.get("msg")
    if msg:
        f = parse.facts(msg.text, g.mode)
        if f:
            _apply_facts(g.member(msg.user_id, msg.name), f)
    return s


def batch_ready(s: PlannerState) -> str:
    g, st, llm = s["g"], s["settings"], s.get("llm")
    if not g.buffer or llm is None or not llm.enabled:
        return "reconcile"
    quiet = time.time() - g.buffer[-1].ts >= st.batch_quiet_s
    if s.get("force") or len(g.buffer) >= st.batch_messages or quiet:
        return "extract"
    return "reconcile"


def extract(s: PlannerState) -> PlannerState:
    g = s["g"]
    known = {m.name: {"budget": m.budget, "date_start": m.date_start, "date_end": m.date_end,
                      "city": m.city, "committed": m.committed}
             for m in g.members.values() if m.messages}
    batch = list(g.buffer)
    lines = "\n".join(f"{m.name}: {m.text[:300]}" for m in batch)
    prompt = (f"TODAY: {date.today().isoformat()}\nMODE: {g.mode}\n"
              f"KNOWN: {json.dumps(known, separators=(',', ':'))}\nNEW LINES:\n{lines}")
    out = s["llm"].json(g.group_id, EXTRACT_SYSTEM, prompt, max_tokens=350)
    g.buffer = []
    g.last_extract_ts = time.time()
    s["extracted"] = True
    if not out:
        return s
    said: dict[str, str] = {}
    for b in batch:
        said[b.name] = said.get(b.name, "") + " " + b.text
    for name, f in (out.get("people") or {}).items():
        m = g.member_by_name(name)
        if not (m and isinstance(f, dict)):
            continue
        f = {k: v for k, v in f.items() if v not in (None, "", [])}
        text = said.get(m.name, "")
        # Small models hallucinate; only accept facts this person actually hinted at.
        if not (parse.has_date_hint(text) and _sane_window(f.get("date_start"), f.get("date_end"))):
            f.pop("date_start", None), f.pop("date_end", None)
        if f.get("city") and str(f["city"]).lower().split(",")[0] not in text.lower():
            f.pop("city")
        if f.get("budget") and not re.search(r"\d", text):
            f.pop("budget")
        _apply_facts(m, f)
    if out.get("consensus_city") and not g.plan.city:
        g.plan.notes["consensus_city"] = str(out["consensus_city"])
    return s


def reconcile(s: PlannerState) -> PlannerState:
    g = s["g"]
    p = g.plan
    trip = (p.city, p.date_start, p.date_end)
    people = [m for m in g.members.values() if m.messages or m.committed]

    cities = Counter(m.city for m in people if m.city)
    if cities:
        top = cities.most_common()
        best_n = top[0][1]
        tied = [c for c, n in top if n == best_n]
        p.city = p.city if p.city in tied else tied[0]
        p.notes["city"] = (f"{p.city} ({best_n}/{sum(cities.values())} votes)"
                           if len(cities) > 1 else f"{p.city}")
    elif p.notes.get("consensus_city"):
        p.city = p.notes["consensus_city"]

    windows = [(m.date_start, m.date_end) for m in people if m.date_start and m.date_end]
    if windows:
        start, end = max(w[0] for w in windows), min(w[1] for w in windows)
        if start < end:
            p.date_start, p.date_end = start, end
            p.notes["dates"] = f"overlap of {len(windows)}"
        else:
            p.date_start = p.date_end = None
            p.notes["dates"] = "no overlap"

    budgets = [m.budget for m in people if m.budget]
    if budgets:
        p.budget_pp = min(budgets)
        low = min(people, key=lambda m: m.budget or 10**9)
        p.notes["budget"] = f"lowest stated ({low.name})" if len(budgets) > 1 else "stated"

    p.vibe = sorted({v for m in people for v in m.vibe})[:6]
    if (p.city, p.date_start, p.date_end) != trip:
        # Different trip: old hotel picks and market snapshot no longer apply.
        p.hotel, p.options, p.votes = None, [], {}
        g.market = {} if isinstance(g.market, dict) else None
    return s


def _stage(g: GroupState) -> str:
    if g.stage == "booked":
        return "booked"
    p = g.plan
    if not p.city:
        return "where"
    if not (p.date_start and p.date_end):
        return "when"
    if not p.budget_pp:
        return "budget"
    if not p.hotel:
        return "hotel"
    return "commit"


def _blockers(g: GroupState, stage: str) -> list[str]:
    talkers = [m for m in g.members.values() if m.messages]
    if stage == "where":
        return [m.name for m in talkers if not m.city]
    if stage == "when":
        if g.plan.notes.get("dates") == "no overlap":
            windows = [m for m in talkers if m.date_start]
            # the person whose window overlaps least with everyone else
            def clash(m: Member) -> int:
                return sum(1 for o in windows if o is not m and
                           (o.date_end <= m.date_start or o.date_start >= m.date_end))
            worst = max(windows, key=clash, default=None)
            return [worst.name] if worst and clash(worst) else []
        return [m.name for m in talkers if not m.date_start]
    if stage == "budget":
        return [m.name for m in talkers if not m.budget]
    if stage == "commit":
        return [m.name for m in talkers if not m.committed]
    return []


def track(s: PlannerState) -> PlannerState:
    g = s["g"]
    before = {"stage": g.stage, "city": g.plan.city, "dates": (g.plan.date_start, g.plan.date_end),
              "budget": g.plan.budget_pp}
    stage = _stage(g)
    progress: list[str] = list(s.get("progress") or [])
    if g.plan.city and g.plan.city != before["city"]:
        progress.append("city")
    if g.plan.date_start and (g.plan.date_start, g.plan.date_end) != before["dates"]:
        progress.append("dates")
    if g.plan.budget_pp and g.plan.budget_pp != before["budget"]:
        progress.append("budget")
    if STAGES.index(stage) > STAGES.index(before["stage"]):
        progress.append(f"stage:{stage}")
    g.stage = stage
    g.blockers = _blockers(g, stage)
    s["progress"] = progress
    return s


def build():
    sg = StateGraph(PlannerState)
    sg.add_node("read", read)
    sg.add_node("extract", extract)
    sg.add_node("reconcile", reconcile)
    sg.add_node("track", track)
    sg.set_entry_point("read")
    sg.add_conditional_edges("read", batch_ready, {"extract": "extract", "reconcile": "reconcile"})
    sg.add_edge("extract", "reconcile")
    sg.add_edge("reconcile", "track")
    sg.add_edge("track", END)
    return sg.compile()


GRAPH = build()


def run(g: GroupState, settings, llm=None, msg: Optional[Msg] = None, force: bool = False,
        progress: Optional[list[str]] = None) -> list[str]:
    out = GRAPH.invoke({"g": g, "msg": msg, "force": force, "llm": llm, "settings": settings,
                        "progress": progress or []})
    return out.get("progress") or []
