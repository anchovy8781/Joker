"""Append-only audit log helper."""

from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog


def write_audit(
    db: Session,
    action: str,
    *,
    actor_id: int | None = None,
    target_type: str | None = None,
    target_id: str | int | None = None,
    request_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditLog:
    entry = AuditLog(
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        request_id=request_id,
        metadata_json=metadata or {},
    )
    db.add(entry)
    return entry
