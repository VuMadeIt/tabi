"""Runtime settings, read once from the environment."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    discord_token: str = field(default_factory=lambda: os.environ.get("DISCORD_BOT_TOKEN", ""))
    discord_app_id: str = field(default_factory=lambda: os.environ.get("DISCORD_APP_ID", ""))
    # Guild for instant slash-command sync while developing; blank = global sync.
    discord_dev_guild: str = field(default_factory=lambda: os.environ.get("DISCORD_DEV_GUILD_ID", ""))
    # Accept webhook-authored messages as chat members (lets tests script a fake group).
    accept_webhooks: bool = field(default_factory=lambda: os.environ.get("TABI_ACCEPT_WEBHOOKS", "") == "1")

    database_url: str = field(default_factory=lambda: os.environ.get("DATABASE_URL", "sqlite:///tabi.db"))
    redis_url: str = field(default_factory=lambda: os.environ.get("REDIS_URL", ""))

    # LLM: mock | gemini | openai (any OpenAI-compatible server, e.g. Ollama)
    llm_provider: str = field(default_factory=lambda: os.environ.get("TABI_LLM", "mock"))
    llm_model: str = field(default_factory=lambda: os.environ.get("TABI_LLM_MODEL", "gemini-2.5-flash-lite"))
    llm_base_url: str = field(default_factory=lambda: os.environ.get("TABI_LLM_BASE_URL", "http://localhost:11434/v1"))
    llm_api_key: str = field(default_factory=lambda: os.environ.get("TABI_LLM_API_KEY") or os.environ.get("GEMINI_API_KEY", ""))

    # Cost guards (per group, per UTC day)
    llm_calls_per_day: int = field(default_factory=lambda: _int("TABI_LLM_CALLS_PER_DAY", 40))
    llm_tokens_per_day: int = field(default_factory=lambda: _int("TABI_LLM_TOKENS_PER_DAY", 60_000))

    # Planner batching: extract once this many trip-ish messages pile up, or after quiet_s of silence.
    batch_messages: int = field(default_factory=lambda: _int("TABI_BATCH_MESSAGES", 4))
    batch_quiet_s: int = field(default_factory=lambda: _int("TABI_BATCH_QUIET_S", 45))

    # Speaking cadence
    speak_cooldown_s: int = field(default_factory=lambda: _int("TABI_SPEAK_COOLDOWN_S", 90))
    silence_nudge_s: int = field(default_factory=lambda: _int("TABI_SILENCE_NUDGE_S", 6 * 3600))
    tick_interval_s: int = field(default_factory=lambda: _int("TABI_TICK_INTERVAL_S", 600))

    stay22_aid: str = field(default_factory=lambda: os.environ.get("STAY22_AID", "tabi"))
    public_url: str = field(default_factory=lambda: os.environ.get("PUBLIC_URL", "https://github.com/VuMadeIt/tabi"))


settings = Settings()
