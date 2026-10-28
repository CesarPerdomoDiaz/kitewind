"""python -m kitewind [--demo] [--config config.toml] [--data DIR] [--once]"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
from pathlib import Path

from . import config, server
from .notify import Notifier
from .scheduler import Scheduler
from .store import Store


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(prog="kitewind")
    ap.add_argument("--config", type=Path, default=root / "config.toml")
    ap.add_argument("--data", type=Path, default=root / "data",
                    help="folder for the database, log and secrets.env")
    ap.add_argument("--demo", action="store_true", help="made-up forecasts, no API key needed")
    ap.add_argument("--once", action="store_true", help="fetch what is due, print a summary, exit")
    ap.add_argument("--port", type=int)
    ap.add_argument("--telegram-chats", action="store_true", help="list the chats the alert bot has been added to")
    ap.add_argument("--telegram-test", action="store_true", help="send a test message to the alert chat")
    args = ap.parse_args()

    args.data.mkdir(parents=True, exist_ok=True)
    handlers = [logging.handlers.RotatingFileHandler(args.data / "kitewind.log", maxBytes=2_000_000,
                                                     backupCount=3, encoding="utf-8"),
                logging.StreamHandler()]
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)
    log = logging.getLogger("kitewind")

    cfg = config.load(args.config, args.data, demo=args.demo)
    if args.telegram_chats or args.telegram_test:
        n = Notifier(args.data, cfg.notify)
        if not n.token:
            raise SystemExit("no bot token: set TELEGRAM_BOT_TOKEN (or put it in secrets.env)")
        if args.telegram_chats:
            for chat_id, kind, title in n.chats() or [("-", "-", "none yet: add the bot to the channel as an admin")]:
                print(f"{chat_id}\t{kind}\t{title}")
        if args.telegram_test:
            raise SystemExit(0 if n.chat_id and n.test() else "test message not sent (is chat_id set?)")
        return
    if args.port:
        cfg.port = args.port
    elif cfg.demo:
        cfg.port += 10  # never take the real dashboard's port
    db = "demo.sqlite" if cfg.demo else "kitewind.sqlite"
    sched = Scheduler(cfg, Store(args.data / db))
    log.info("site %s (%.4f, %.4f), key: %s, planned %d requests/day, cap %d",
             cfg.site.name, cfg.site.lat, cfg.site.lon, cfg.key_mode, cfg.planned_requests_per_day, cfg.daily_cap)
    if cfg.planned_requests_per_day > cfg.daily_cap:
        log.warning("the model intervals ask for more requests than the cap; the cap wins")

    if args.once:
        for job in sched.jobs:
            job.due_ms = 0
        sched.tick()
        sched.rebuild()
        for m in sched.snapshot["models"]:
            print(f"{m['label']:<22} {str(m.get('runs', m.get('points'))):<12} {m['heights_from']:<34} {m['error'] or ''}")
        for h in sched.snapshot["hours"][:24]:
            print(h["t"], h["status"], h.get("primary"), h.get("wind"), h.get("wind_lo"), h.get("wind_hi"),
                  h.get("wind_prob"), [r["text"] for r in h["reasons"]])
        return
    sched.start()
    server.serve(sched, cfg.host, cfg.port)


if __name__ == "__main__":
    main()
