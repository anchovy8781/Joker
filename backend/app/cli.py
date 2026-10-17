"""Management commands: `python -m app.cli <command>`.

  create-admin <username>         prompts for a password (admins are never created via the API)
  seed-demo [--approve-by <admin>] registers clearly-labelled DEMO channels (pending until approved)
  add-channel <UC...> [title]     registers a real channel as pending + collects its data (needs API key)
  approve <channel_id> <SYMBOL> <price> <shares> --admin <name> [--celebrity]
                                  approve a pending listing as that admin (every listing needs approval)
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


def _admin(db, username: str) -> User:
    u = db.execute(select(User).where(User.normalized_username == normalize_username(username))).scalar_one_or_none()
    if u is None or u.role != "admin":
        sys.exit(f"'{username}' is not an administrator (create one with create-admin)")
    return u


def seed_demo(approve_by: str | None) -> None:
    """Registers DEMO channels as pending. Listing still needs an admin decision: either in
    the admin UI, or here with --approve-by <admin> (recorded under that admin's id)."""
    db = session_factory()()
    admin = _admin(db, approve_by) if approve_by else None
    for cid, title, symbol, price, cat in DEMO_CHANNELS:
        ch = db.execute(select(Channel).where(Channel.youtube_channel_id == cid)).scalar_one_or_none()
        if ch is None:
            ch = register_channel(db, youtube_channel_id=cid, title=title, data_source="demo",
                                  category=cat, actor_id=admin.id if admin else None, request_id="cli-seed")
            db.commit()
            print(f"registered {title} (pending)")
        if admin and ch.listing_status == "pending":
            approve_listing(db, channel_id=ch.id, symbol=symbol, initial_price=price,
                            shares_outstanding=1_000_000, actor_id=admin.id, request_id="cli-seed")
            db.commit()
            print(f"approved {symbol} @ {price} by {admin.username}")
    if not admin:
        print("Channels are pending approval: approve them in the admin page (/admin) "
              "or rerun with --approve-by <admin>.")


def add_channel(channel_id: str, title: str | None) -> None:
    """Register a real channel (pending) and collect its data so an admin can review it."""
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
        sys.exit("channel data could not be verified")
    print(f"registered '{ch.title}' (channel id {ch.id}) as PENDING — approve it in /admin "
          f"or: python -m app.cli approve {ch.id} SYMBOL PRICE SHARES --admin <name> [--celebrity]")


def approve(args: list[str]) -> None:
    flags = [a for a in args if a.startswith("--")]
    pos = [a for a in args if not a.startswith("--")]
    admin_name = None
    if "--admin" in args:
        admin_name = args[args.index("--admin") + 1]
        pos.remove(admin_name)
    if len(pos) != 4 or not admin_name:
        sys.exit("usage: approve <channel_id> <SYMBOL> <price> <shares> --admin <name> [--celebrity]")
    db = session_factory()()
    admin = _admin(db, admin_name)
    a = approve_listing(db, channel_id=int(pos[0]), symbol=pos[1], initial_price=int(pos[2]),
                        shares_outstanding=int(pos[3]), actor_id=admin.id, request_id="cli",
                        celebrity_new_channel="--celebrity" in flags)
    db.commit()
    print(f"approved {a.symbol} (status={a.trading_status}, celebrity_surcharge={a.celebrity_until is not None})")


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
        seed_demo(args[args.index("--approve-by") + 1] if "--approve-by" in args else None)
    elif cmd == "add-channel" and len(args) >= 1:
        add_channel(args[0], " ".join(args[1:]) or None)
    elif cmd == "approve":
        approve(args)
    elif cmd == "run" and len(args) == 1:
        run_job(args[0])
    elif cmd == "reconcile":
        run_job("reconcile")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
