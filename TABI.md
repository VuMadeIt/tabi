# Tabi v1 — Discord

A sushi pet that lives in your group chat and stays healthy only while the trip moves forward.

- **Body** = hotel market. Prices up / rooms gone → body shrinks (full → half → low).
- **Mood** = chat energy. Progress and chatter cheer it up; silence makes it sad, then rotten, then dead.

## Run it

```bash
python -m venv .venv && ./.venv/bin/pip install -r requirements-tabi.txt
export DISCORD_BOT_TOKEN=...            # Discord dev portal → Bot → token
export DISCORD_DEV_GUILD_ID=...         # optional: instant slash-command sync in one server
./.venv/bin/python -m tabi all          # gateway + 2 workers, SQLite + in-memory queue
```

Invite: `https://discord.com/oauth2/authorize?client_id=<APP_ID>&scope=bot+applications.commands&permissions=275414895616`
and enable **Message Content Intent** on the Bot page.

| env | default | |
|---|---|---|
| `DATABASE_URL` | `sqlite:///tabi.db` | Postgres in prod (`postgresql://…`) |
| `REDIS_URL` | — | shared queue, locks, budgets; needed for >1 process |
| `TABI_LLM` | `mock` | `mock` (templates only, $0) · `gemini` · `openai` (any OpenAI-compatible, e.g. Ollama) |
| `TABI_LLM_MODEL` | `gemini-2.5-flash-lite` | pinned cheap model |
| `TABI_LLM_CALLS_PER_DAY` / `TABI_LLM_TOKENS_PER_DAY` | 40 / 60k | hard cap per group |
| `STAY22_API_KEY`, `STAY22_AID` | — | live hotel prices + affiliate links; without a key Tabi simulates the market and links to Booking.com search |

Scale: `python -m tabi bot` (one gateway) + `python -m tabi worker` × N, all sharing Postgres + Redis.
`python -m tabi funnel` prints installed → planning → hotels → booked counts.

## Architecture

```
Discord gateway ──► adapter (discord_bot.py) ──► queue (Redis) ──► workers ──► engine ──► REST send
  slash/buttons/modals answered in the adapter, same engine
engine = load GroupState (Postgres, versioned) ─► Planner agent ─► Voice agent ─► save (optimistic lock)
```

Two LangGraph agents, ≤4 nodes each:

- **Planner** `read → extract → reconcile → track` — regex facts on every message (free); one batched LLM call only when the chat is trip-ish and a batch fills or goes quiet.
- **Voice** `decide → compose → guard` — templates for ~all lines; LLM only for direct @tabi replies and the deathbed plea. Cooldowns, no repeats, no @everyone.

Conversation state (`tabi/core/models.py`) is one JSON doc per channel: plan, members (budget/dates/commit per person), pet, stage, blockers, unparsed buffer, rolling recent context, last lines said. Every update is load → mutate → version-checked save under a per-group lock, so any worker can handle any event.

## Commands

`/tabi start [mode]` · `/tabi status` · `/tabi where` · `/tabi dates` · `/tabi budget` · `/tabi in` · `/tabi out` · `/tabi booked` · `/tabi reset` · `/tabi stop` — plus buttons on Tabi's messages (I'm in, budget, dates, vote, revive).

## Tests

`python -m pytest -q tests/test_tabi.py` — parser, health, store conflicts, budgets, a full trip from "tokyo?" to booked with zero LLM calls, death + revive, card render for every state.
