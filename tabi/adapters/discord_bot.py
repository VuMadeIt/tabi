"""Discord adapter (gateway): turns Discord events into engine events.

Chat messages and ticks go onto the queue for workers. Slash commands,
buttons and modals are answered here directly (they need a reply within 3s),
then any public follow-up is posted through the same REST sender workers use.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time

import discord
from discord import app_commands

from tabi.adapters.rest import RestSender
from tabi.config import Settings
from tabi.core import parse
from tabi.engine import Action, Engine
from tabi.worker import deliver

log = logging.getLogger("tabi.discord")


def gid(channel_id: int | str) -> str:
    return f"discord:{channel_id}"


class BudgetModal(discord.ui.Modal, title="Your budget (per person)"):
    amount = discord.ui.TextInput(label="Max you'll spend on the trip, USD", placeholder="1500", max_length=8)

    def __init__(self, bot: "TabiBot"):
        super().__init__()
        self.bot = bot

    async def on_submit(self, it: discord.Interaction) -> None:
        val = parse.find_budget(f"${self.amount.value}")
        if not val:
            await it.response.send_message("that doesn't look like a budget 🥲 try `1500`", ephemeral=True)
            return
        await it.response.send_message(f"saved: ${val:,} 💸 (only the lowest budget is shown to the group)", ephemeral=True)
        await self.bot.run(it, self.bot.engine.set_fact, gid(it.channel_id), str(it.user.id),
                           it.user.display_name, budget=val)


class DatesModal(discord.ui.Modal, title="When are you free?"):
    when = discord.ui.TextInput(label="Dates", placeholder="march 10-17  ·  first week of may  ·  2027-03-10 2027-03-17")

    def __init__(self, bot: "TabiBot"):
        super().__init__()
        self.bot = bot

    async def on_submit(self, it: discord.Interaction) -> None:
        d = parse.find_dates(self.when.value) or parse.find_dates(f"in {self.when.value}")
        if not d:
            await it.response.send_message("couldn't read those dates 📅 try `march 10-17`", ephemeral=True)
            return
        await it.response.send_message(f"saved: {d[0]} → {d[1]} 📅", ephemeral=True)
        await self.bot.run(it, self.bot.engine.set_fact, gid(it.channel_id), str(it.user.id),
                           it.user.display_name, dates=d)


class TabiBot(discord.Client):
    def __init__(self, s: Settings, engine: Engine):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.s, self.engine = s, engine
        self.sender = RestSender(s.discord_token)
        self.tree = app_commands.CommandTree(self)
        self._register()

    # ─── helpers ────────────────────────────────────────────────────────────
    async def run(self, it: discord.Interaction | None, fn, group_id: str, *args, **kw) -> list[Action]:
        actions = await asyncio.to_thread(fn, group_id, *args, **kw)
        eph = [a for a in actions if a.kind == "ephemeral"]
        if it is not None:
            for a in eph:
                if it.response.is_done():
                    await it.followup.send(a.text, ephemeral=True)
                else:
                    await it.response.send_message(a.text, ephemeral=True)
        await asyncio.to_thread(deliver, self.engine, self.sender, group_id, actions)
        return actions

    async def _ack(self, it: discord.Interaction) -> None:
        if not it.response.is_done():
            await it.response.defer(ephemeral=True, thinking=False)

    async def _need_group(self, it: discord.Interaction) -> bool:
        g = await asyncio.to_thread(self.engine.load, gid(it.channel_id))
        if g is None or not g.active:
            await it.response.send_message("no tabi in this channel yet 🍣 run `/tabi start`", ephemeral=True)
            return False
        return True

    # ─── slash commands ─────────────────────────────────────────────────────
    def _register(self) -> None:
        grp = app_commands.Group(name="tabi", description="your group-trip pet 🍣")

        @grp.command(name="start", description="Adopt Tabi in this channel")
        @app_commands.describe(mode="trip type")
        @app_commands.choices(mode=[app_commands.Choice(name=n, value=v) for n, v in (
            ("anywhere", "any"), ("japan", "japan"), ("bachelorette", "bachelorette"), ("grad trip", "grad"))])
        async def start(it: discord.Interaction, mode: app_commands.Choice[str] | None = None):
            await it.response.send_message("🍣 hatching…", ephemeral=True)
            await self.run(None, self.engine.start, gid(it.channel_id), str(it.channel_id),
                           str(it.guild_id or ""), mode.value if mode else "any")

        @grp.command(name="status", description="Show Tabi and the plan")
        async def status(it: discord.Interaction):
            if await self._need_group(it):
                await it.response.send_message(**await self._status_payload(it.channel_id))

        @grp.command(name="where", description="Suggest a destination")
        async def where(it: discord.Interaction, city: str):
            if await self._need_group(it):
                await it.response.send_message(f"voted {city} 🗺️", ephemeral=True)
                await self.run(it, self.engine.set_fact, gid(it.channel_id), str(it.user.id),
                               it.user.display_name, city=city.strip()[:60])

        @grp.command(name="budget", description="Set your max budget (per person, USD)")
        async def budget(it: discord.Interaction, amount: app_commands.Range[int, 50, 50000]):
            if await self._need_group(it):
                await it.response.send_message(f"saved: ${amount:,} 💸", ephemeral=True)
                await self.run(it, self.engine.set_fact, gid(it.channel_id), str(it.user.id),
                               it.user.display_name, budget=amount)

        @grp.command(name="dates", description="Tell Tabi when you're free")
        async def dates(it: discord.Interaction):
            if await self._need_group(it):
                await it.response.send_modal(DatesModal(self))

        @grp.command(name="in", description="I'm coming!")
        async def in_(it: discord.Interaction):
            if await self._need_group(it):
                await self._ack(it)
                await self.run(it, self.engine.set_fact, gid(it.channel_id), str(it.user.id),
                               it.user.display_name, committed=True)

        @grp.command(name="out", description="I can't make it")
        async def out(it: discord.Interaction):
            if await self._need_group(it):
                await self._ack(it)
                await self.run(it, self.engine.set_fact, gid(it.channel_id), str(it.user.id),
                               it.user.display_name, committed=False)

        @grp.command(name="booked", description="Mark the trip as booked 🎉")
        async def booked(it: discord.Interaction):
            if await self._need_group(it):
                await self._ack(it)
                await self.run(it, self.engine.book, gid(it.channel_id), str(it.user.id))

        @grp.command(name="reset", description="Start a fresh trip in this channel")
        async def reset(it: discord.Interaction):
            if await self._need_group(it):
                await it.response.send_message("fresh start 🍣", ephemeral=True)
                await self.run(None, self.engine.reset, gid(it.channel_id))

        @grp.command(name="stop", description="Put Tabi to sleep in this channel")
        async def stop(it: discord.Interaction):
            await asyncio.to_thread(self.engine.stop, gid(it.channel_id))
            await it.response.send_message("tabi is napping 💤 `/tabi start` to wake her up")

        self.tree.add_command(grp)

    async def _status_payload(self, channel_id: int) -> dict:
        from tabi.render import card
        from tabi.adapters.rest import BUTTONS
        g = await asyncio.to_thread(self.engine.load, gid(channel_id))
        p = g.plan
        bits = [f"**{p.city or '?'}**"]
        if p.date_start:
            bits.append(f"{p.date_start} → {p.date_end}")
        if p.budget_pp:
            bits.append(f"≤ ${p.budget_pp:,}/person")
        if p.hotel:
            bits.append(f"🏨 {p.hotel['name']}")
        going = [m.name for m in g.members.values() if m.committed]
        if going:
            bits.append("going: " + ", ".join(going))
        png = await asyncio.to_thread(card.render, g)
        view = discord.ui.View()
        for k in ("in", "budget", "dates"):
            b = BUTTONS[k]
            view.add_item(discord.ui.Button(label=b["label"], custom_id=b["custom_id"],
                                            style=discord.ButtonStyle(b["style"])))
        import io
        return {"content": " · ".join(bits), "file": discord.File(io.BytesIO(png), "tabi.png"),
                "view": view, "allowed_mentions": discord.AllowedMentions.none()}

    # ─── gateway events ─────────────────────────────────────────────────────
    async def setup_hook(self) -> None:
        if self.s.discord_dev_guild:
            guild = discord.Object(id=int(self.s.discord_dev_guild))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        self.loop.create_task(self._ticker())

    async def on_ready(self) -> None:
        log.info("discord ready as %s in %d guilds", self.user, len(self.guilds))

    async def on_message(self, m: discord.Message) -> None:
        if m.author.id == (self.user.id if self.user else 0):
            return
        if m.author.bot and not (self.s.accept_webhooks and m.webhook_id):
            return
        if not m.content:
            return
        g = await asyncio.to_thread(self.engine.load, gid(m.channel.id))
        if g is None or not g.active:
            return
        mentioned = bool(self.user and self.user in m.mentions)
        addressed = mentioned or bool(re.search(r"\btabi\b", m.content, re.I))
        text = re.sub(r"<@!?\d+>", "", m.content).strip() or m.content
        uid = f"wh:{m.author.name}" if m.webhook_id else str(m.author.id)
        name = m.author.display_name if not m.webhook_id else m.author.name
        self.engine.cache.push({"type": "message", "group_id": gid(m.channel.id), "user_id": uid,
                                "name": name, "text": text, "addressed": addressed,
                                "ts": m.created_at.timestamp()})

    async def on_interaction(self, it: discord.Interaction) -> None:
        if it.type != discord.InteractionType.component:
            return
        cid = (it.data or {}).get("custom_id", "")
        if not cid.startswith("tabi:"):
            return
        g_id, uid, name = gid(it.channel_id), str(it.user.id), it.user.display_name
        action = cid.split(":")[1]
        if action == "budget":
            await it.response.send_modal(BudgetModal(self))
        elif action == "dates":
            await it.response.send_modal(DatesModal(self))
        elif action == "status":
            await it.response.send_message(**await self._status_payload(it.channel_id))
        elif action in ("in", "out"):
            await it.response.send_message("you're in! 🎉" if action == "in" else "noted 💔", ephemeral=True)
            await self.run(it, self.engine.set_fact, g_id, uid, name, committed=action == "in")
        elif action == "vote":
            await self._ack(it)
            await self.run(it, self.engine.vote_hotel, g_id, uid, name, int(cid.split(":")[2]))
        elif action == "book":
            await self._ack(it)
            await self.run(it, self.engine.book, g_id, uid)
        elif action == "revive":
            await self._ack(it)
            await asyncio.to_thread(self.engine.click, g_id, "revive")
            await self.run(it, self.engine.revive, g_id, uid)

    async def _ticker(self) -> None:
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                ids = await asyncio.to_thread(self.engine.store.active_ids)
                for i in ids:
                    if i.startswith("discord:"):
                        self.engine.cache.push({"type": "tick", "group_id": i, "ts": time.time()})
            except Exception:
                log.exception("ticker failed")
            await asyncio.sleep(self.s.tick_interval_s)
