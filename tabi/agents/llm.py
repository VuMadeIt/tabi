"""One small LLM client with hard per-group budgets.

Providers: `mock` (no network; callers fall back to deterministic code),
`gemini` (REST, pinned cheap model, JSON mode) and `openai` (any
OpenAI-compatible server — Ollama locally, OpenRouter, etc.).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

import requests

from tabi.config import Settings
from tabi.core.cache import Budget

log = logging.getLogger("tabi.llm")


def _json_from(raw: str) -> Optional[dict]:
    raw = (raw or "").strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else None
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
    return None


class LLM:
    def __init__(self, s: Settings, budget: Budget):
        self.s, self.budget = s, budget

    @property
    def enabled(self) -> bool:
        return self.s.llm_provider != "mock"

    def json(self, group_id: str, system: str, prompt: str, max_tokens: int = 400) -> Optional[dict]:
        """Returns parsed JSON or None (disabled, over budget, or failed)."""
        if not self.enabled:
            return None
        if not self.budget.allow(group_id):
            log.info("llm budget exhausted for %s", group_id)
            return None
        try:
            raw, tokens = (self._gemini if self.s.llm_provider == "gemini" else self._openai)(
                system, prompt, max_tokens)
        except Exception as e:  # network/provider errors must never break the chat
            log.warning("llm call failed: %s", e)
            return None
        self.budget.spend(group_id, tokens)
        return _json_from(raw)

    def _gemini(self, system: str, prompt: str, max_tokens: int) -> tuple[str, int]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.s.llm_model}:generateContent"
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json",
                                 "maxOutputTokens": max_tokens, "temperature": 0.4},
        }
        r = requests.post(url, params={"key": self.s.llm_api_key}, json=body, timeout=20)
        r.raise_for_status()
        d = r.json()
        text = "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"])
        tokens = int((d.get("usageMetadata") or {}).get("totalTokenCount") or 0)
        return text, tokens

    def _openai(self, system: str, prompt: str, max_tokens: int) -> tuple[str, int]:
        r = requests.post(
            self.s.llm_base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {self.s.llm_api_key or 'none'}"},
            json={"model": self.s.llm_model, "max_tokens": max_tokens, "temperature": 0.4,
                  "response_format": {"type": "json_object"},
                  "messages": [{"role": "system", "content": system},
                               {"role": "user", "content": prompt}]},
            timeout=60)
        r.raise_for_status()
        d = r.json()
        return d["choices"][0]["message"]["content"], int((d.get("usage") or {}).get("total_tokens") or 0)
