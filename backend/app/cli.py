"""Management commands: `python -m app.cli <command>`.

  create-admin <username>         prompts for a password (admins are never created via the API)
  seed-demo                       registers + lists clearly-labelled DEMO channels
  add-channel <UC...> <SYMBOL> <price> <shares> [title]   register + list a real channel (needs API key)
  run <job>                       run one job now: collect_data | price_update | reconcile | risk_scan | mm_requote
  reconcile                       print a reconciliation report
"""

import getpass
import json
import sys
import uuid

from sqlalchemy import select

from app.db import session_factory
from app.models import Account, Channel, User
from app.security import hash_password
from app.services import jobs
from app.services.auth import normalize_username, validate_credentials
from app.services.listing import approve_listing, register_channel

DEMO_CHANNELS = [
    ("DEMO_gaming_alpha", "[DEMO] Gaming Alpha", "DGAME", 12_000, "Gaming"),
    ("DEMO_cooking_lab", "[DEMO] Cooking Lab", "DCOOK", 8_500, "Food"),
    ("DEMO_tech_review", "[DEMO] Tech Review Daily", "DTECH", 15_000, "Science & Technology"),
    ("DEMO_music_wave", "[DEMO] Music Wave", "DMUSIC", 20_000, "Music"),
    ("DEMO_travel_log", "[DEMO] Travel Log", "DTRVL", 5_000, "Travel"),
    ("DEMO_study_with", "[DEMO] Study With Me", "DSTDY", 3_000, "Education"),
    ("DEMO_comedy_hub", "[DEMO] Comedy Hub", "DCMDY", 9_800, "Comedy"),
    ("DEMO_fitness_pro", "[DEMO] Fitness Pro", "DFIT", 6_400, "Sports"),
]


def create_admin(username: str) -> None:
    password = getpass.getpass("Admin password (10+ chars): ")
    validate_credentials(username, password)
    db = session_factory()()
    if db.execute(select(User).where(User.normalized_username == normalize_username(username))).first():
        sys.exit("user already exists")
    u = User(username=username, normalized_username=normalize_username(username),
             password_hash=hash_password(password), role="admin", verification_status="verified")
    db.add(u)
    db.flush()
    # Admins get an account row (balance 0) but no signup grant and do not appear on rankings.
    db.add(Account(user_id=u.id, cash_balance=0, reserved_cash=0))
    db.commit()
    print(f"admin {username} created (id={u.id})")


def seed_demo() -> None:
    db = session_factory()()
    for cid, title, symbol, price, cat in DEMO_CHANNELS:
        if db.execute(select(Channel.id).where(Channel.youtube_channel_id == cid)).first():
            print(f"skip {symbol}: exists")
            continue
        ch = register_channel(db, youtube_channel_id=cid, title=title, data_source="demo",
                              category=cat, actor_id=None, request_id="cli-seed")
        approve_listing(db, channel_id=ch.id, symbol=symbol, initial_price=price,
                        shares_outstanding=1_000_000, actor_id=None, request_id="cli-seed")
        db.commit()
        print(f"listed {symbol} ({title}) @ {price}")


def add_channel(channel_id: str, symbol: str, price: str, shares: str, title: str | None) -> None:
    from app.services.collector import collect_all
    from app.services.youtube import get_api_provider

    api = get_api_provider()
    if api is None:
        sys.exit("YEX_YOUTUBE_API_KEY is not set; real channels need the official API")
    db = session_factory()()
    ch = register_channel(db, youtube_channel_id=channel_id, title=title or channel_id,
                          data_source="youtube_api", category=None, actor_id=None, request_id="cli")
    db.commit()
    print(json.dumps(collect_all(db, api=api), default=str))
    db.commit()
    db.refresh(ch)
    if ch.data_quality_status == "unavailable" or ch.last_data_update_at is None:
        sys.exit("channel data could not be verified; not listing")
    a = approve_listing(db, channel_id=ch.id, symbol=symbol, initial_price=int(price),
                        shares_outstanding=int(shares), actor_id=None, request_id="cli")
    db.commit()
    print(f"listed {a.symbol} for {ch.title} (status={a.trading_status})")


def run_job(job_type: str) -> None:
    import app.worker  # noqa: F401  (registers handlers)

    db = session_factory()()
    key = f"cli:{job_type}:{uuid.uuid4().hex}"
    payload = {}
    if job_type == "price_update":
        from app.security import utcnow

        payload = {"effective_date": utcnow().date().isoformat()}
    job_id = jobs.enqueue(db, job_type, payload, dedup_key=key)
    db.commit()
    job = jobs.run_one(db, "cli", job_id)
    print(json.dumps({"job": job.job_type, "status": job.status, "result": job.result_json,
                      "error": job.last_error}, default=str, indent=2))


def main(argv: list[str]) -> None:
    if not argv:
        sys.exit(__doc__)
    cmd, *args = argv
    if cmd == "create-admin" and len(args) == 1:
        create_admin(args[0])
    elif cmd == "seed-demo":
        seed_demo()
    elif cmd == "add-channel" and len(args) >= 4:
        add_channel(args[0], args[1], args[2], args[3], " ".join(args[4:]) or None)
    elif cmd == "run" and len(args) == 1:
        run_job(args[0])
    elif cmd == "reconcile":
        run_job("reconcile")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
