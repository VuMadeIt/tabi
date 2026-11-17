import time
from datetime import date

import pytest

from tabi.config import Settings
from tabi.core import health, parse
from tabi.core.cache import Budget, MemoryCache
from tabi.core.models import GroupState
from tabi.core.store import Conflict, Store
from tabi.engine import Engine
from tabi.render import card

TODAY = date(2026, 11, 16)


def test_parse_budget():
    assert parse.find_budget("i can do $1,500 max") == 1500
    assert parse.find_budget("1.5k tops") == 1500
    assert parse.find_budget("2000 max") == 2000
    assert parse.find_budget("lol 3 cats") is None


def test_parse_dates():
    assert parse.find_dates("march 10-15 works", TODAY) == ("2027-03-10", "2027-03-15")
    assert parse.find_dates("second week of april", TODAY) == ("2027-04-08", "2027-04-14")
    assert parse.find_dates("may i ask something", TODAY) is None
    assert parse.find_dates("in may", TODAY) == ("2027-05-01", "2027-05-31")


def test_parse_city_and_commit():
    assert parse.find_city("down for tokyo probably") == "Tokyo"
    assert parse.find_city("vegas baby") == "Las Vegas"
    assert parse.find_city("maybe lisbon or barcelona") is None
    assert parse.has_date_hint("march-ish") and not parse.has_date_hint("barcelona!!")
    assert parse.commitment("ok i'm in") is True
    assert parse.commitment("sorry i'm out") is False


def test_health_buckets_and_market():
    assert health.body_tier(80) == "full" and health.body_tier(50) == "half" and health.body_tier(10) == "low"
    assert health.market_delta(80, {"price": 200, "count": 100}, {"price": 240, "count": 80}) < 80
    assert health.market_delta(50, {"price": 240, "count": 80}, {"price": 200, "count": 100}) > 50


def test_store_roundtrip_and_conflict(tmp_path):
    st = Store(f"sqlite:///{tmp_path}/t.db")
    g = GroupState(group_id="discord:1")
    g.member("u1", "alice").budget = 900
    st.save(g)
    a, b = st.get("discord:1"), st.get("discord:1")
    assert a.members["u1"].budget == 900
    st.save(a)
    with pytest.raises(Conflict):
        st.save(b)


def test_budget_caps():
    b = Budget(MemoryCache(), calls_per_day=2, tokens_per_day=10_000)
    assert b.allow("g")
    b.spend("g", 10); b.spend("g", 10)
    assert not b.allow("g")


@pytest.fixture
def engine(tmp_path):
    s = Settings(database_url=f"sqlite:///{tmp_path}/e.db", llm_provider="mock", speak_cooldown_s=0)
    return Engine(s, Store(s.database_url), MemoryCache())


def test_full_trip_flow_without_llm(engine):
    gid = "discord:42"
    acts = engine.start(gid, "42", "7", "japan")
    assert acts and acts[0].card
    say = lambda u, t: engine.message(gid, u, u, t)
    say("alice", "tokyo trip!!")
    say("bob", "yes tokyo")
    g = engine.load(gid)
    assert g.plan.city == "Tokyo" and g.stage == "when"
    say("alice", "march 10-17 works for me")
    say("bob", "march 12-20")
    g = engine.load(gid)
    assert (g.plan.date_start, g.plan.date_end) == (f"{g.plan.date_start[:4]}-03-12", f"{g.plan.date_start[:4]}-03-17")
    assert g.stage == "budget" and set(g.blockers) == {"alice", "bob"}
    acts = say("alice", "i can do $1500") + say("bob", "1200 max")
    g = engine.load(gid)
    assert g.plan.budget_pp == 1200 and g.stage == "hotel"
    assert any(a.kind == "hotels" for a in acts)
    assert g.plan.options
    # offline mode → link-out; confirm booking after everyone's in
    engine.set_fact(gid, "alice", "alice", committed=True)
    engine.set_fact(gid, "bob", "bob", committed=True)
    g = engine.load(gid)
    g.plan.hotel = g.plan.options[0]
    engine.store.save(g)
    engine.set_fact(gid, "alice", "alice", committed=True)
    acts = engine.book(gid, "alice")
    g = engine.load(gid)
    assert g.booked and g.pet.physical == 100
    funnel = engine.store.funnel()
    assert funnel.get("installed") == 1 and funnel.get("booked") == 1


def test_silence_rots_and_kills(engine):
    gid = "discord:9"
    engine.start(gid, "9")
    engine.message(gid, "a", "a", "we should go to paris")
    g = engine.load(gid)
    t0 = g.last_user_ts
    engine.tick(gid, now=t0 + 60)
    for day in range(1, 12):
        engine.tick(gid, now=t0 + day * 86400)
    g = engine.load(gid)
    assert not g.pet.alive
    assert engine.revive(gid, "a")
    assert engine.load(gid).pet.alive


def test_no_llm_calls_in_mock_mode(engine):
    gid = "discord:5"
    engine.start(gid, "5")
    for i in range(10):
        engine.message(gid, "u", "u", f"trip to bali budget ${1000 + i}")
    assert engine.cache.get_int(f"calls:_all:{time.strftime('%Y%m%d', time.gmtime())}") == 0


def test_card_renders_every_state():
    g = GroupState(group_id="x")
    for phys in (90, 50, 10):
        for ment in (90, 60, 45, 10):
            g.pet.physical, g.pet.mental = phys, ment
            png = card.render(g, {"body": -5}, "hotels vote below!! 🏨")
            assert png[:8] == b"\x89PNG\r\n\x1a\n"
            assert card._sprite(card.sprite_name(phys, ment), 64) is not None


def test_paper_card_style_still_renders(monkeypatch):
    monkeypatch.setenv("TABI_CARD", "paper")
    g = GroupState(group_id="y")
    assert card.render(g)[:4] == b"\x89PNG"


def test_hotels_wait_for_every_budget_and_refresh_on_change(engine):
    gid = "discord:43"
    engine.start(gid, "43", "7", "japan")
    say = lambda u, t: engine.message(gid, u, u, t)
    say("alice", "tokyo trip!!")
    say("bob", "yes tokyo")
    say("alice", "march 10-17 works")
    say("bob", "march 12-20 for me")
    say("alice", "i can do $1500")
    assert engine.load(gid).stage == "budget"
    assert engine.load(gid).blockers == ["bob"]
    acts = say("bob", "1200 max")
    g = engine.load(gid)
    assert g.stage == "hotel" and g.plan.budget_pp == 1200
    assert any(a.hotels for a in acts)
    acts = say("bob", "actually 900 max")
    assert engine.load(gid).plan.budget_pp == 900
    assert any(a.hotels for a in acts) and any("900" in a.text for a in acts)


def test_opted_out_member_does_not_block_and_trip_edit_drops_old_hotels(engine):
    gid = "discord:44"
    engine.start(gid, "44", "7", "japan")
    say = lambda u, t: engine.message(gid, u, u, t)
    for u, t in [("alice", "tokyo trip!!"), ("bob", "yes tokyo"), ("alice", "march 10-17 works"),
                 ("bob", "march 12-20 for me"), ("alice", "i can do $1500"), ("bob", "1200 max")]:
        say(u, t)
    assert engine.load(gid).plan.options
    say("carol", "i can only do march 13-17")
    g = engine.load(gid)
    assert g.stage == "budget" and not g.plan.options and not g.plan.votes
    engine.set_fact(gid, "carol", "carol", committed=False)
    assert engine.load(gid).stage == "hotel"


def test_withdrawal_that_unblocks_hotels_still_posts_choices(engine):
    gid = "discord:45"
    engine.start(gid, "45", "7", "japan")
    say = lambda u, t: engine.message(gid, u, u, t)
    for u, t in [("alice", "tokyo trip!!"), ("bob", "yes tokyo"), ("alice", "march 10-17 works"),
                 ("bob", "march 12-20 for me"), ("alice", "i can do $1500")]:
        say(u, t)
    engine.set_fact(gid, "bob", "bob", committed=True)
    acts = engine.set_fact(gid, "bob", "bob", committed=False)
    g = engine.load(gid)
    assert g.stage == "hotel" and g.plan.options and any(a.hotels for a in acts)
