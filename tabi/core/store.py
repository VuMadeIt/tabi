"""Durable group state + funnel events (Postgres in prod, SQLite for dev).

Writes use optimistic concurrency: `save` only succeeds if the row's version
is still the one we loaded, so two workers can never silently clobber a group.
"""
from __future__ import annotations

import json
import time
from typing import Optional

from sqlalchemy import (Column, Float, Integer, MetaData, String, Table, Text,
                        create_engine, insert, select, update)

from tabi.core.models import GroupState

_meta = MetaData()

groups = Table(
    "tabi_groups", _meta,
    Column("id", String(96), primary_key=True),
    Column("data", Text, nullable=False),
    Column("version", Integer, nullable=False, default=0),
    Column("active", Integer, nullable=False, default=1),
    Column("updated_at", Float, nullable=False),
)

events = Table(
    "tabi_events", _meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("group_id", String(96), index=True, nullable=False),
    Column("kind", String(48), index=True, nullable=False),
    Column("data", Text, nullable=False, default="{}"),
    Column("ts", Float, nullable=False),
)


class Conflict(Exception):
    """Another worker saved this group first; reload and retry."""


class Store:
    def __init__(self, url: str):
        if url.startswith("postgres://"):
            url = "postgresql+psycopg://" + url[len("postgres://"):]
        elif url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        self.engine = create_engine(url, future=True, pool_pre_ping=True)
        _meta.create_all(self.engine)

    def get(self, group_id: str) -> Optional[GroupState]:
        with self.engine.connect() as c:
            row = c.execute(select(groups.c.data, groups.c.version)
                            .where(groups.c.id == group_id)).first()
        if row is None:
            return None
        g = GroupState.from_dict(json.loads(row.data))
        g.version = row.version
        return g

    def save(self, g: GroupState) -> None:
        payload = json.dumps(g.to_dict())
        now = time.time()
        with self.engine.begin() as c:
            if g.version == 0:
                exists = c.execute(select(groups.c.id).where(groups.c.id == g.group_id)).first()
                if exists:
                    raise Conflict(g.group_id)
                c.execute(insert(groups).values(id=g.group_id, data=payload, version=1,
                                                active=int(g.active), updated_at=now))
            else:
                res = c.execute(update(groups)
                                .where(groups.c.id == g.group_id, groups.c.version == g.version)
                                .values(data=payload, version=g.version + 1,
                                        active=int(g.active), updated_at=now))
                if res.rowcount != 1:
                    raise Conflict(g.group_id)
        g.version += 1

    def active_ids(self) -> list[str]:
        with self.engine.connect() as c:
            return [r.id for r in c.execute(select(groups.c.id).where(groups.c.active == 1))]

    def log(self, group_id: str, kind: str, **data) -> None:
        with self.engine.begin() as c:
            c.execute(insert(events).values(group_id=group_id, kind=kind,
                                            data=json.dumps(data), ts=time.time()))

    def funnel(self) -> dict[str, int]:
        """Distinct groups per funnel event kind (installed → plan → click → booked)."""
        from sqlalchemy import func
        with self.engine.connect() as c:
            rows = c.execute(select(events.c.kind, func.count(func.distinct(events.c.group_id)))
                             .group_by(events.c.kind))
            return {k: n for k, n in rows}
