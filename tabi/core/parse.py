"""Free, deterministic fact extraction from a single chat line.

Handles the explicit stuff people actually type ("$1500 max", "tokyo!",
"march 10-15", "i'm in"), so the LLM only has to be called for the nuanced
leftovers, in batches.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Optional

JAPAN_CITIES = ["Tokyo", "Kyoto", "Osaka", "Nara", "Sapporo", "Fukuoka", "Okinawa",
                "Hakone", "Hiroshima", "Nagoya", "Yokohama", "Kobe", "Kanazawa", "Nikko"]
WORLD_CITIES = ["Toronto", "Montreal", "Vancouver", "Quebec City", "Banff", "Whistler",
                "New York", "NYC", "Miami", "Las Vegas", "Vegas", "Los Angeles", "LA", "Chicago",
                "Nashville", "New Orleans", "Austin", "San Francisco", "Seattle", "Boston",
                "Cancun", "Tulum", "Mexico City", "Punta Cana", "Havana", "Nassau",
                "London", "Paris", "Barcelona", "Madrid", "Lisbon", "Rome", "Amsterdam",
                "Berlin", "Prague", "Dublin", "Ibiza", "Mykonos", "Santorini", "Athens",
                "Seoul", "Bangkok", "Bali", "Singapore", "Hong Kong", "Taipei", "Hanoi",
                "Ho Chi Minh City", "Manila", "Dubai", "Sydney", "Melbourne", "Honolulu"]
ALIASES = {"nyc": "New York", "vegas": "Las Vegas", "la": "Los Angeles"}

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_MONTH_RE = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"

_IN = re.compile(r"\b(i'?m in|im in|count me in|i'?m down|im down|i'?ll go|booked my flight|i'?m so in)\b", re.I)
_OUT = re.compile(r"\b(i'?m out|im out|can'?t make it|cant make it|count me out|not going|i'?ll pass)\b", re.I)
_TRIP_WORDS = re.compile(
    r"\b(trip|travel|fly|flight|hotel|airbnb|book|booking|budget|\$|dates?|week(end)?|month|"
    r"go(ing)?|visit|vacation|vacay|holiday|plan|when|where|cheap|price|afford|days?|nights?|"
    r"bachelorette|bach|grad|reading week|spring break|tabi)\b", re.I)


def city_list(mode: str) -> list[str]:
    return JAPAN_CITIES if mode == "japan" else JAPAN_CITIES + WORLD_CITIES


def find_city(text: str, mode: str = "any") -> Optional[str]:
    best = None
    for c in city_list(mode):
        m = re.search(rf"(?<![\w]){re.escape(c)}(?![\w])", text, re.I)
        if m and (c not in ("LA",) or m.group(0) == "LA"):
            if best is None or m.start() < best[0]:
                best = (m.start(), ALIASES.get(c.lower(), c))
    return best[1] if best else None


def find_budget(text: str) -> Optional[int]:
    t = text.lower().replace(",", "")
    m = re.search(r"\$\s?(\d+(?:\.\d+)?)\s*(k)?\b", t) or \
        re.search(r"\b(\d+(?:\.\d+)?)\s*(k)\b(?!m)", t) or \
        re.search(r"\b(\d{3,5})\s*(?:max|tops|budget|dollars|bucks|usd|cad)\b", t) or \
        re.search(r"\b(?:budget|max|under|around|about)\s*(?:is|of)?\s*\$?(\d{3,5})\b", t)
    if not m:
        return None
    val = float(m.group(1))
    if len(m.groups()) > 1 and m.group(2) == "k":
        val *= 1000
    val = int(val)
    return val if 50 <= val <= 50_000 else None


def _year_for(month: int, today: date) -> int:
    return today.year if month >= today.month else today.year + 1


def _mk(y: int, mo: int, d: int) -> Optional[date]:
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def find_dates(text: str, today: Optional[date] = None) -> Optional[tuple[str, str]]:
    today = today or date.today()
    t = text.lower()
    # ISO range
    iso = re.findall(r"\b(20\d\d-\d\d-\d\d)\b", t)
    if len(iso) >= 2:
        return iso[0], iso[1]
    # "march 10-15", "mar 10 to 15", "march 28 - april 3"
    m = re.search(rf"\b{_MONTH_RE}\s+(\d{{1,2}})(?:st|nd|rd|th)?\s*(?:-|–|to|until|till)\s*(?:{_MONTH_RE}\s+)?(\d{{1,2}})\b", t)
    if m:
        mo1 = MONTHS[m.group(1)[:3]]
        mo2 = MONTHS[m.group(3)[:3]] if m.group(3) else mo1
        y = _year_for(mo1, today)
        a, b = _mk(y, mo1, int(m.group(2))), _mk(y if mo2 >= mo1 else y + 1, mo2, int(m.group(4)))
        if a and b and b > a:
            return a.isoformat(), b.isoformat()
    # "10-15 march"
    m = re.search(rf"\b(\d{{1,2}})\s*(?:-|–|to)\s*(\d{{1,2}})\s+{_MONTH_RE}\b", t)
    if m:
        mo = MONTHS[m.group(3)[:3]]
        y = _year_for(mo, today)
        a, b = _mk(y, mo, int(m.group(1))), _mk(y, mo, int(m.group(2)))
        if a and b and b > a:
            return a.isoformat(), b.isoformat()
    # "first/second/third/last week of april"
    m = re.search(rf"\b(first|1st|second|2nd|third|3rd|fourth|4th|last)\s+week\s+of\s+{_MONTH_RE}\b", t)
    if m:
        mo = MONTHS[m.group(2)[:3]]
        y = _year_for(mo, today)
        idx = {"first": 0, "1st": 0, "second": 1, "2nd": 1, "third": 2, "3rd": 2, "fourth": 3, "4th": 3}.get(m.group(1))
        if idx is None:
            end = (_mk(y + (mo == 12), mo % 12 + 1, 1) or date(y, 12, 31)) - timedelta(days=1)
            start = end - timedelta(days=6)
        else:
            start = date(y, mo, 1 + 7 * idx)
            end = start + timedelta(days=6)
        return start.isoformat(), end.isoformat()
    # "april works", "in may", "sometime in june" → whole month
    m = re.search(rf"\b(?:in|during|sometime in|early|mid|late)?\s*{_MONTH_RE}\b(?:\s+(?:works|is good|is fine|for me|pls|please))?", t)
    if m and re.search(rf"\b(in|during|early|mid|late|works|good|fine|for me)\b", t):
        mo = MONTHS[m.group(1)[:3]]
        if m.group(1) == "may" and not re.search(r"\b(in|during|early|mid|late)\s+may\b|\bmay\s+(works|is good|is fine|for me)\b", t):
            return None  # "may" the verb
        y = _year_for(mo, today)
        start = date(y, mo, 1)
        end = (_mk(y + (mo == 12), mo % 12 + 1, 1) or date(y, 12, 31)) - timedelta(days=1)
        if "early" in t:
            end = date(y, mo, 10)
        elif "mid" in t:
            start, end = date(y, mo, 10), date(y, mo, 20)
        elif "late" in t:
            start = date(y, mo, 20)
        return start.isoformat(), end.isoformat()
    return None


def commitment(text: str) -> Optional[bool]:
    if _OUT.search(text):
        return False
    if _IN.search(text):
        return True
    return None


def is_trip_signal(text: str, mode: str = "any") -> bool:
    return bool(_TRIP_WORDS.search(text) or find_city(text, mode) or find_budget(text)
                or find_dates(text) or commitment(text) is not None)


def facts(text: str, mode: str = "any", today: Optional[date] = None) -> dict:
    """→ {city?, budget?, dates?, committed?} for whatever the line states."""
    out: dict = {}
    if c := find_city(text, mode):
        out["city"] = c
    if b := find_budget(text):
        out["budget"] = b
    if d := find_dates(text, today):
        out["dates"] = d
    if (cm := commitment(text)) is not None:
        out["committed"] = cm
    return out
