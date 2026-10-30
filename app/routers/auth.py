"""Login / logout / current user."""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import AuditLog, User, UserSession, utcnow
from app.schemas import LoginIn, MeOut, PasswordChangeIn
from app.security.deps import get_current_session
from app.security.passwords import hash_password, needs_rehash, password_problems, verify_password
from app.security.sessions import client_ip, create_session, destroy_session, destroy_user_sessions
from app.services.audit import audit

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _locked_out(db: Session, username: str, ip: str) -> bool:
    """Per-account limit, plus a looser per-IP limit (campus NAT shares one IP)."""
    s = get_settings()
    since = utcnow() - timedelta(minutes=s.login_lockout_minutes)
    base = select(func.count(AuditLog.id)).where(AuditLog.action == "login_failed", AuditLog.created_at >= since)
    by_user = db.scalar(base.where(AuditLog.username == username)) or 0
    by_ip = db.scalar(base.where(AuditLog.ip == ip)) or 0 if ip else 0
    return by_user >= s.login_max_failures or by_ip >= s.login_max_failures * 4


@router.post("/login", response_model=MeOut)
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    s = get_settings()
    username = body.username.strip().lower()
    ip = client_ip(request)
    if _locked_out(db, username, ip):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Too many failed attempts. Try again in {s.login_lockout_minutes} minutes.",
        )
    user = db.scalar(select(User).where(func.lower(User.username) == username))
    ok = verify_password(user.password_hash if user else None, body.password)
    if not ok or user is None or not user.active:
        audit(db, "login_failed", username=username, request=request, commit=True)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect username or password.")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    user.last_login_at = utcnow()
    sess = create_session(db, user, request, response)
    audit(db, "login", user=user, request=request, commit=True)
    response.headers["Cache-Control"] = "no-store"
    return MeOut(id=user.id, username=user.username, full_name=user.full_name, role=user.role,
                 csrf_token=sess.csrf_token)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, sess: UserSession = Depends(get_current_session),
           db: Session = Depends(get_db)):
    audit(db, "logout", user=sess.user, request=request, commit=True)
    destroy_session(db, request, response)
    response.status_code = 204
    return response


@router.get("/me", response_model=MeOut)
def me(response: Response, sess: UserSession = Depends(get_current_session)):
    response.headers["Cache-Control"] = "no-store"
    u = sess.user
    return MeOut(id=u.id, username=u.username, full_name=u.full_name, role=u.role, csrf_token=sess.csrf_token)


@router.post("/password", status_code=204)
def change_password(body: PasswordChangeIn, request: Request, response: Response,
                    sess: UserSession = Depends(get_current_session), db: Session = Depends(get_db)):
    user = db.get(User, sess.user_id)
    if not verify_password(user.password_hash, body.current_password):
        raise HTTPException(400, "Current password is incorrect.")
    problems = password_problems(body.new_password)
    if problems:
        raise HTTPException(422, " ".join(problems))
    user.password_hash = hash_password(body.new_password)
    destroy_user_sessions(db, user.id)  # sign out everywhere
    audit(db, "password_changed", user=user, request=request)
    db.commit()
    create_session(db, user, request, response)
    response.status_code = 204
    return response
