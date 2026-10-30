"""FastAPI dependencies for authentication, CSRF and role enforcement."""
from __future__ import annotations

import hmac
from collections.abc import Callable

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Role, User, UserSession
from app.security.sessions import load_session

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def get_current_session(request: Request, db: Session = Depends(get_db)) -> UserSession:
    sess = load_session(db, request)
    if sess is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Login required.")
    if request.method not in SAFE_METHODS:
        sent = request.headers.get("x-csrf-token", "")
        if not sent or not hmac.compare_digest(sent, sess.csrf_token):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing or invalid CSRF token.")
    return sess


def get_current_user(sess: UserSession = Depends(get_current_session)) -> User:
    return sess.user


def require_roles(*roles: Role) -> Callable[..., User]:
    allowed = {r.value for r in roles}

    def checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Your role does not permit this action.")
        return user

    return checker


# Convenience role sets
any_staff = require_roles(Role.admin, Role.contributor, Role.reviewer)
editors = require_roles(Role.admin, Role.contributor)
reviewers = require_roles(Role.admin, Role.reviewer)
admins = require_roles(Role.admin)
