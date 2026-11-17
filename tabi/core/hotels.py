"""Hotel market + options for any destination.

With STAY22_API_KEY: live Stay22 search (affiliate links baked in by Stay22,
tagged per group). Without it: a deterministic offline market so the Body bar
still moves, and plain search links so people can still book.
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
import urllib.parse
from statistics import median
from typing import Optional

import requests

log = logging.getLogger("tabi.hotels")
ENDPOINT = "https://api.stay22.com/v2/accommodations"
_last_call = 0.0


def live() -> bool:
    return bool(os.environ.get("STAY22_API_KEY", "").strip())


def _tag(group_id: str) -> str:
    return "tabi-" + hashlib.sha256(group_id.encode()).hexdigest()[:10]


def _search(city: str, checkin: str, checkout: str, guests: int, group_id: str) -> Optional[list[dict]]:
    global _last_call
    wait = 0.5 - (time.time() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.time()
    params = {"address": city, "checkin": checkin, "checkout": checkout,
              "adults": max(1, guests), "rooms": max(1, guests // 2), "pageSize": 30,
              "currency": os.environ.get("STAY22_CURRENCY", "USD")}
    if aid := os.environ.get("STAY22_AID", "").strip():
        params.update(aid=aid, campaign=_tag(group_id))
    try:
        r = requests.get(ENDPOINT, params=params, timeout=15,
                         headers={"X-API-KEY": os.environ["STAY22_API_KEY"], "Accept": "application/json"})
        r.raise_for_status()
        return r.json().get("results") or []
    except Exception as e:
        log.warning("stay22 search failed: %s", e)
        return None


def _cheapest(r: dict) -> tuple[Optional[str], Optional[float], str]:
    best = (None, None, "")
    for name, s in (r.get("suppliers") or {}).items():
        p = (s.get("price") or {}).get("total")
        try:
            p = float(p)
        except (TypeError, ValueError):
            continue
        if best[1] is None or p < best[1]:
            best = (name, p, s.get("link") or r.get("url") or "")
    return best


def search_link(city: str, checkin: str, checkout: str, guests: int) -> str:
    q = urllib.parse.urlencode({"ss": city, "checkin": checkin, "checkout": checkout,
                                "group_adults": guests, "no_rooms": max(1, guests // 2)})
    return f"https://www.booking.com/searchresults.html?{q}"


def _offline_market(city: str, checkin: str, now: float) -> dict:
    """Deterministic stand-in: prices creep up and rooms thin out as the trip nears."""
    from datetime import date
    seed = int(hashlib.md5(city.lower().encode()).hexdigest()[:6], 16)
    base = 120 + seed % 180
    try:
        days_out = max(1.0, (date.fromisoformat(checkin).toordinal() - date.fromordinal(
            int(now // 86400) + 719163).toordinal()))
    except ValueError:
        days_out = 60.0
    pressure = max(0.0, min(1.0, 1 - days_out / 120))
    wobble = ((int(now // 21600) * 2654435761 + seed) % 7 - 3) / 100  # ±3% every 6h
    return {"price": round(base * (1 + 0.6 * pressure + wobble), 2),
            "count": round(100 * (1 - 0.55 * pressure - wobble)), "ts": now, "live": False}


def market(city: str, checkin: str, checkout: str, guests: int, group_id: str) -> Optional[dict]:
    """→ {price (median nightly-ish total), count, ts, live}"""
    now = time.time()
    if not (city and checkin and checkout):
        return None
    if not live():
        return _offline_market(city, checkin, now)
    res = _search(city, checkin, checkout, guests, group_id)
    if res is None:
        return None
    prices = [p for p in (_cheapest(r)[1] for r in res) if p]
    return {"price": float(median(prices)) if prices else 0.0, "count": len(res), "ts": now, "live": True}


def options(city: str, checkin: str, checkout: str, guests: int, budget_pp: Optional[int],
            group_id: str, n: int = 3) -> list[dict]:
    """Top `n` bookable stays: in-budget by rating, else cheapest. Each:
    {name, price_total, price_pp, rating, url, supplier}."""
    guests = max(1, guests)
    if not live():
        return [{"name": f"Browse stays in {city}", "price_total": None, "price_pp": None,
                 "rating": None, "url": search_link(city, checkin, checkout, guests), "supplier": "search"}]
    res = _search(city, checkin, checkout, guests, group_id) or []
    rows = []
    for r in res:
        sup, price, url = _cheapest(r)
        if price is None or not url:
            continue
        rating = float((r.get("rating") or {}).get("value") or 0)
        rows.append({"name": r.get("name") or "Hotel", "price_total": price,
                     "price_pp": round(price / guests), "rating": rating, "url": url, "supplier": sup})
    cap = budget_pp * guests if budget_pp else None
    within = sorted([x for x in rows if cap is None or x["price_total"] <= cap],
                    key=lambda x: (-x["rating"], x["price_total"]))
    pool = within or sorted(rows, key=lambda x: x["price_total"])
    return pool[:n] or [{"name": f"Browse stays in {city}", "price_total": None, "price_pp": None,
                         "rating": None, "url": search_link(city, checkin, checkout, guests),
                         "supplier": "search"}]
