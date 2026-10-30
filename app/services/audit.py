"""Audit logging (who did what, when)."""
from __future__ import annotations

import logging

from fastapi import Request
from sqlalchemy.orm import Session

from app.models import AuditLog, User
from app.security.sessions import client_ip

logger = logging.getLogger("gondi_vaani.audit")


def audit(
    db: Session,
    action: str,
    *,
    user: User | None = None,
    username: str = "",
    entity: str = "",
    entity_id: object = "",
    detail: str = "",
    request: Request | None = None,
    commit: bool = False,
) -> None:
    entry = AuditLog(
        user_id=user.id if user else None,
        username=(user.username if user else username)[:64],
        action=action,
        entity=entity,
        entity_id=str(entity_id),
        detail=detail[:2000],
        ip=client_ip(request)[:64] if request else "",
    )
    db.add(entry)
    logger.info("%s user=%s %s:%s %s", action, entry.username, entity, entity_id, detail[:200])
    if commit:
        db.commit()
