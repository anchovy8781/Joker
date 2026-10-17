"""Group chat: one global room plus one room per listed asset."""

import re
import unicodedata
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import AppError
from app.models import Channel, ChatMessage, MarketAsset, User
from app.security import hit_rate_limit, utcnow
from app.services.audit import write_audit

ROOM_RE = re.compile(r"^(global|asset:[1-9][0-9]{0,9})$")
MAX_LEN = 300
DUPLICATE_WINDOW = timedelta(seconds=30)


def validate_room(db: Session, room: str) -> str:
    if not ROOM_RE.match(room):
        raise AppError(404, "ROOM_NOT_FOUND", "Chat room not found")
    if room.startswith("asset:"):
        listed = db.execute(
            select(MarketAsset.id).join(Channel, Channel.id == MarketAsset.channel_id)
            .where(MarketAsset.id == int(room.split(":")[1]), Channel.listing_status == "listed")
        ).first()
        if not listed:
            raise AppError(404, "ROOM_NOT_FOUND", "Chat room not found")
    return room


def clean_body(body: str) -> str:
    # Drop control/format characters (incl. bidi overrides), collapse whitespace.
    text = "".join(ch for ch in body if unicodedata.category(ch)[0] != "C" or ch in "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        raise AppError(422, "EMPTY_MESSAGE", "Message is empty")
    if len(text) > MAX_LEN:
        raise AppError(422, "MESSAGE_TOO_LONG", f"Messages are limited to {MAX_LEN} characters")
    return text


def post_message(db: Session, user: User, room: str, body: str) -> ChatMessage:
    validate_room(db, room)
    if user.status != "active":
        raise AppError(403, "ACCOUNT_RESTRICTED", "Restricted accounts cannot post in chat")
    hit_rate_limit(f"chat:user:{user.id}", *get_settings().rl_chat_per_user)
    text = clean_body(body)
    last = db.execute(
        select(ChatMessage).where(ChatMessage.user_id == user.id, ChatMessage.room == room)
        .order_by(ChatMessage.id.desc()).limit(1)
    ).scalar_one_or_none()
    if last is not None and last.body == text and utcnow() - last.created_at < DUPLICATE_WINDOW:
        raise AppError(429, "DUPLICATE_MESSAGE", "Please don't repeat the same message")
    msg = ChatMessage(room=room, user_id=user.id, body=text)
    db.add(msg)
    db.flush()
    return msg


def list_messages(db: Session, room: str, after_id: int = 0, limit: int = 50) -> list[dict]:
    validate_room(db, room)
    q = (select(ChatMessage, User.username).join(User, User.id == ChatMessage.user_id)
         .where(ChatMessage.room == room, ChatMessage.hidden_at.is_(None)))
    if after_id:
        q = q.where(ChatMessage.id > after_id).order_by(ChatMessage.id.asc()).limit(limit)
        rows = db.execute(q).all()
    else:
        rows = list(reversed(db.execute(q.order_by(ChatMessage.id.desc()).limit(limit)).all()))
    return [{"id": m.id, "room": m.room, "username": name, "body": m.body, "created_at": m.created_at}
            for m, name in rows]


def hide_message(db: Session, message_id: int, admin_id: int, reason: str, request_id: str | None) -> ChatMessage:
    msg = db.get(ChatMessage, message_id)
    if msg is None:
        raise AppError(404, "NOT_FOUND", "Message not found")
    if msg.hidden_at is None:
        msg.hidden_at, msg.hidden_by, msg.hidden_reason = utcnow(), admin_id, reason
        write_audit(db, "chat.hide", actor_id=admin_id, target_type="chat_message", target_id=message_id,
                    request_id=request_id, metadata={"reason": reason, "author_id": msg.user_id,
                                                     "body": msg.body})
    return msg
