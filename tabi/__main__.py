"""python -m tabi [all|bot|worker|funnel]

all     gateway adapter + N in-process workers (single box / dev)
bot     gateway adapter only (pushes events to Redis)
worker  queue consumer only (scale these horizontally; needs REDIS_URL)
funnel  print install → plan → booked counts
"""
from __future__ import annotations

import logging
import os
import sys
import threading

from tabi.config import settings
from tabi.core.cache import make_cache
from tabi.core.store import Store
from tabi.engine import Engine


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    engine = Engine(settings, Store(settings.database_url), make_cache(settings.redis_url))
    if mode == "funnel":
        print(engine.store.funnel())
        return
    if mode in ("bot", "all") and not settings.discord_token:
        sys.exit("DISCORD_BOT_TOKEN is not set")
    from tabi.adapters.rest import RestSender
    from tabi import worker
    if mode in ("worker", "all"):
        n = int(os.environ.get("TABI_WORKERS", "2"))
        for _ in range(n):
            threading.Thread(target=worker.run, args=(engine, RestSender(settings.discord_token)),
                             daemon=True).start()
        if mode == "worker":
            threading.Event().wait()
    from tabi.adapters.discord_bot import TabiBot
    TabiBot(settings, engine).run(settings.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
