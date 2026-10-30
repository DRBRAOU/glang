"""Server-side sessions stored in the database.

The browser holds only a random token in an HttpOnly cookie; the database
stores its SHA-256 hash plus a per-session CSRF token.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from fastapi import Request, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import User, UserSession, utcnow


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def client_ip(request: Request) -> str:
    # Nginx sets X-Real-IP; uvicorn --proxy-headers also rewrites client host.
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "")


def create_session(db: Session, user: User, request: Request, response: Response) -> UserSession:
    settings = get_settings()
    token = secrets.token_urlsafe(32)
    sess = UserSession(
        id=_hash_token(token),
        user_id=user.id,
        csrf_token=secrets.token_urlsafe(32),
        expires_at=utcnow() + timedelta(hours=settings.session_hours),
        ip=client_ip(request)[:64],
        user_agent=(request.headers.get("user-agent") or "")[:255],
    )
    db.add(sess)
    # Opportunistic cleanup of expired sessions
    db.execute(delete(UserSession).where(UserSession.expires_at < utcnow()))
    db.commit()
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.session_hours * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )
    return sess


def load_session(db: Session, request: Request) -> UserSession | None:
    token = request.cookies.get(get_settings().session_cookie_name)
    if not token or len(token) > 128:
        return None
    sess = db.get(UserSession, _hash_token(token))
    if sess is None:
        return None
    if sess.expires_at < utcnow() or not sess.user.active:
        db.delete(sess)
        db.commit()
        return None
    return sess


def destroy_session(db: Session, request: Request, response: Response) -> None:
    token = request.cookies.get(get_settings().session_cookie_name)
    if token:
        sess = db.get(UserSession, _hash_token(token))
        if sess:
            db.delete(sess)
            db.commit()
    s = get_settings()
    response.delete_cookie(s.session_cookie_name, path="/", secure=s.cookie_secure, httponly=True, samesite="strict")


def destroy_user_sessions(db: Session, user_id: int) -> None:
    db.execute(delete(UserSession).where(UserSession.user_id == user_id))


def active_session_count(db: Session, user_id: int) -> int:
    return len(db.scalars(select(UserSession.id).where(UserSession.user_id == user_id)).all())
