"""Fast shared bits: per-group locks, daily AI budgets, the event queue.

Redis when REDIS_URL is set (multi-worker), in-process otherwise (dev/tests).
"""
from __future__ import annotations

import json
import queue
import threading
import time
from contextlib import contextmanager
from typing import Iterator, Optional


def _day() -> str:
    return time.strftime("%Y%m%d", time.gmtime())


class MemoryCache:
    def __init__(self) -> None:
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()
        self._counters: dict[str, int] = {}
        self._q: "queue.Queue[str]" = queue.Queue()

    @contextmanager
    def lock(self, key: str, timeout: float = 30) -> Iterator[None]:
        with self._guard:
            lk = self._locks.setdefault(key, threading.Lock())
        if not lk.acquire(timeout=timeout):
            raise TimeoutError(key)
        try:
            yield
        finally:
            lk.release()

    def incr(self, key: str, by: int = 1, ttl: int = 0) -> int:
        with self._guard:
            self._counters[key] = self._counters.get(key, 0) + by
            return self._counters[key]

    def get_int(self, key: str) -> int:
        return self._counters.get(key, 0)

    def push(self, event: dict) -> None:
        self._q.put(json.dumps(event))

    def pop(self, timeout: float = 1.0) -> Optional[dict]:
        try:
            return json.loads(self._q.get(timeout=timeout))
        except queue.Empty:
            return None


class RedisCache:
    QUEUE = "tabi:events"

    def __init__(self, url: str) -> None:
        import redis
        self.r = redis.Redis.from_url(url, decode_responses=True)

    @contextmanager
    def lock(self, key: str, timeout: float = 30) -> Iterator[None]:
        lk = self.r.lock(f"tabi:lock:{key}", timeout=120, blocking_timeout=timeout)
        if not lk.acquire():
            raise TimeoutError(key)
        try:
            yield
        finally:
            try:
                lk.release()
            except Exception:
                pass

    def incr(self, key: str, by: int = 1, ttl: int = 0) -> int:
        k = f"tabi:n:{key}"
        n = self.r.incrby(k, by)
        if ttl and n == by:
            self.r.expire(k, ttl)
        return int(n)

    def get_int(self, key: str) -> int:
        return int(self.r.get(f"tabi:n:{key}") or 0)

    def push(self, event: dict) -> None:
        self.r.rpush(self.QUEUE, json.dumps(event))

    def pop(self, timeout: float = 1.0) -> Optional[dict]:
        item = self.r.blpop(self.QUEUE, timeout=max(1, int(timeout)))
        return json.loads(item[1]) if item else None


class Budget:
    """Hard per-group daily caps on LLM calls and tokens."""

    def __init__(self, cache, calls_per_day: int, tokens_per_day: int) -> None:
        self.cache, self.calls, self.tokens = cache, calls_per_day, tokens_per_day

    def allow(self, group_id: str) -> bool:
        d = _day()
        return (self.cache.get_int(f"calls:{group_id}:{d}") < self.calls
                and self.cache.get_int(f"tok:{group_id}:{d}") < self.tokens)

    def spend(self, group_id: str, tokens: int) -> None:
        d = _day()
        self.cache.incr(f"calls:{group_id}:{d}", 1, ttl=172800)
        self.cache.incr(f"tok:{group_id}:{d}", max(0, tokens), ttl=172800)
        self.cache.incr(f"calls:_all:{d}", 1, ttl=172800)
        self.cache.incr(f"tok:_all:{d}", max(0, tokens), ttl=172800)


def make_cache(url: str):
    return RedisCache(url) if url else MemoryCache()
