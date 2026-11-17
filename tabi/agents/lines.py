"""Tabi's template lines — ~90% of everything she says costs $0.

Placeholders: {name} {names} {city} {dates} {budget} {body} {mood} {who} {hotel}
Mode-specific pools override the default pool when present.
"""
from __future__ import annotations

LINES: dict[str, dict[str, list[str]]] = {
    "hello": {"any": [
        "hiii i'm tabi 🍣 your trip pet. i live in this chat and i get healthier every time you actually plan something. ignore me and i rot. first q: WHERE are we going?",
        "omg a new group 🍣 i'm tabi! i grow when you plan and i rot when you ghost. so... where are we going??",
    ], "japan": [
        "konnichiwa!! i'm tabi 🍣 your japan trip pet. plan with me and i stay fresh, ghost me and i go moldy. tokyo? kyoto? osaka? pick one!!",
    ], "bachelorette": [
        "hiii bridal party 💍🍣 i'm tabi, the maid of honour's new enforcer. i'll chase everyone so she doesn't have to. where are we celebrating??",
    ], "grad": [
        "congrats grads 🎓🍣 i'm tabi. grad trips die in the group chat, i'm here to stop that. where are we going??",
    ]},
    "ask_where": {"any": [
        "ok but WHERE 😤 {who} drop a city",
        "still no destination... {who} where do you wanna go? 🗺️",
        "i can't pick hotels for 'somewhere fun' 🥲 {who}, name a city!",
    ], "japan": [
        "tokyo, kyoto or osaka?? {who} pick one 🗾",
    ]},
    "ask_when": {"any": [
        "{city} it is ✈️ now WHEN? {who} what dates work for you?",
        "dates pls 📅 {who} you haven't said when you're free",
        "hotel prices in {city} are not waiting for you {who} 😭 dates??",
    ]},
    "date_clash": {"any": [
        "uh oh 📅 {who}'s dates don't overlap with everyone else's. can you shift a bit?",
        "date clash!! {who} is on a different calendar 😵 find a week that works for all",
    ]},
    "ask_budget": {"any": [
        "{city} {dates} 💖 last thing: budget per person? {who} spill",
        "{who} how much can you spend? no judgement (some judgement) 💸",
        "i can't find hotels without a budget 🥺 {who}?",
    ]},
    "hotels": {"any": [
        "PLAN LOCKED 🎉 {city}, {dates}, ~{budget}/person. here are stays that fit, vote and i'll hold it for you 👇",
        "we have a plan!! {city} {dates} under {budget} each. pick your fave 👇",
    ]},
    "ask_commit": {"any": [
        "{hotel} is the one 🏨 now tap I'M IN so it's real. still waiting on {who} 👀",
        "everyone who's actually coming, tap I'M IN. {who}... i'm watching 👀",
    ], "bachelorette": [
        "the bride is waiting 💍 {who} tap I'M IN or explain yourself",
    ]},
    "flake": {"any": [
        "{who} said they're out 💔 i'm literally rotting. anyone convince them?",
    ]},
    "progress": {"any": [
        "yesss 💖 that helped, i feel fresher already",
        "ooh progress!! +mood 🍣✨",
        "see? planning heals me 🩹",
    ]},
    "market_drop": {"any": [
        "ouch 🩸 {city} hotel prices just went up / rooms are vanishing. body {body}/100. book soon!!",
        "hotel market in {city} is squeezing me 😖 body down to {body}. every day you wait costs money",
    ]},
    "market_good": {"any": [
        "ooh {city} prices dipped 📉 good time to lock it in!",
    ]},
    "lonely": {"any": [
        "hello?? 🥲 it's been quiet. mood {mood}/100. is this trip still happening?",
        "nobody's talked about the trip in ages... i'm starting to smell 🪰",
        "this is your weekly reminder that {city} isn't going to book itself 😔",
    ]},
    "deathbed": {"any": [
        "i don't have long 🥀 body {body}, mood {mood}. {who}, please... just book it",
        "*cough* tell {city} i loved her 💀 one of you please commit",
    ]},
    "dead": {"any": [
        "tabi has died 💀 cause of death: group chat. revive me if you still want this trip",
    ]},
    "revived": {"any": [
        "I LIVE 🍣⚡ ok second chance, let's actually do it this time",
    ]},
    "booked": {"any": [
        "WE'RE GOING 🎉🎉 {city} {dates}! i'm so proud of you. see you there 🍣✈️",
    ], "bachelorette": [
        "IT'S OFFICIAL 💍🎉 {city} {dates}! the bride thanks you. i'll be the one in the veil 🍣",
    ]},
    "reply_fallback": {"any": [
        "i'm just a sushi 🍣 but i'm listening! we're on: {stage}",
        "hehe 🍣 focus!! next up: {stage}",
    ]},
}


def pool(intent: str, mode: str) -> list[str]:
    d = LINES.get(intent) or LINES["reply_fallback"]
    return d.get(mode) or d["any"]
