"""Admin API: user management (administrators only)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Role, User
from app.schemas import UserCreate, UserOut, UserUpdate
from app.security.deps import admins
from app.security.passwords import hash_password, password_problems
from app.security.sessions import destroy_user_sessions
from app.services.audit import audit

router = APIRouter(prefix="/api/admin/users", tags=["admin: users"])


def _active_admins(db: Session) -> int:
    return db.scalar(select(func.count(User.id)).where(User.role == Role.admin.value, User.active.is_(True))) or 0


@router.get("", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), _: User = Depends(admins)):
    return db.scalars(select(User).order_by(User.username)).all()


@router.post("", response_model=UserOut, status_code=201)
def create_user(body: UserCreate, request: Request, db: Session = Depends(get_db), admin: User = Depends(admins)):
    username = body.username.lower()
    if db.scalar(select(User).where(func.lower(User.username) == username)):
        raise HTTPException(409, "That username is already taken.")
    problems = password_problems(body.password)
    if problems:
        raise HTTPException(422, " ".join(problems))
    u = User(username=username, full_name=body.full_name.strip(), role=body.role.value,
             password_hash=hash_password(body.password), active=True)
    db.add(u)
    db.flush()
    audit(db, "user_created", user=admin, entity="user", entity_id=username, detail=f"role={u.role}", request=request)
    db.commit()
    return u


@router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserUpdate, request: Request, db: Session = Depends(get_db),
                admin: User = Depends(admins)):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "User not found.")
    demoting = (body.role is not None and body.role.value != Role.admin.value) or body.active is False
    if u.role == Role.admin.value and u.active and demoting and _active_admins(db) <= 1:
        raise HTTPException(409, "At least one active administrator must remain.")
    changes = []
    if body.full_name is not None:
        u.full_name = body.full_name.strip()
        changes.append("full_name")
    if body.role is not None and body.role.value != u.role:
        u.role = body.role.value
        changes.append(f"role={u.role}")
    if body.active is not None and body.active != u.active:
        u.active = body.active
        changes.append(f"active={u.active}")
    if body.password:
        problems = password_problems(body.password)
        if problems:
            raise HTTPException(422, " ".join(problems))
        u.password_hash = hash_password(body.password)
        changes.append("password_reset")
    if any(c.startswith(("role", "active", "password")) for c in changes):
        destroy_user_sessions(db, u.id)  # force re-login with new rights
    audit(db, "user_updated", user=admin, entity="user", entity_id=u.username, detail=",".join(changes),
          request=request)
    db.commit()
    return u
