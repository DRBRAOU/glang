"""Admin API: reporting, audit log and backups."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AuditLog, Category, Expression, Recording, RecordingStatus, Speaker, User
from app.security.deps import admins, any_staff
from app.services import backup as backup_svc
from app.services.audit import audit

router = APIRouter(prefix="/api/admin", tags=["admin: reports"])


@router.get("/reports/summary")
def summary(db: Session = Depends(get_db), _: User = Depends(any_staff)):
    by_status = dict(db.execute(select(Expression.status, func.count()).group_by(Expression.status)).all())
    by_category = [
        {"category": name or "(none)", "total": total}
        for name, total in db.execute(
            select(Category.name, func.count(Expression.id))
            .select_from(Expression)
            .outerjoin(Category, Category.id == Expression.category_id)
            .group_by(Category.name)
            .order_by(Category.name)
        ).all()
    ]
    rec_status = dict(db.execute(select(Recording.status, func.count()).group_by(Recording.status)).all())
    missing_gondi = db.scalar(select(func.count(Expression.id)).where(
        func.trim(Expression.gondi_text) == "", Expression.status != "archived")) or 0
    no_audio = db.scalar(select(func.count(Expression.id)).where(
        ~Expression.recordings.any(), Expression.status != "archived")) or 0
    speakers_without_consent = db.scalar(select(func.count(Speaker.id)).where(
        (func.trim(Speaker.consent_scope) == "") | Speaker.consent_date.is_(None))) or 0
    total_seconds = db.scalar(select(func.sum(Recording.duration)).where(
        Recording.status == RecordingStatus.approved.value)) or 0
    return {
        "expressions_by_status": by_status,
        "expressions_by_category": by_category,
        "recordings_by_status": rec_status,
        "missing_gondi_text": missing_gondi,
        "without_audio": no_audio,
        "speakers_without_consent": speakers_without_consent,
        "approved_audio_minutes": round(total_seconds / 60, 1),
    }


@router.get("/audit")
def audit_log(limit: int = Query(default=200, le=1000), action: str = Query(default="", max_length=64),
              db: Session = Depends(get_db), _: User = Depends(admins)):
    stmt = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    return [
        {"id": a.id, "at": a.created_at.isoformat(timespec="seconds"), "user": a.username, "action": a.action,
         "entity": a.entity, "entity_id": a.entity_id, "detail": a.detail, "ip": a.ip}
        for a in db.scalars(stmt).all()
    ]


@router.get("/backups")
def backups(_: User = Depends(admins)):
    return backup_svc.list_backups()


@router.post("/backups", status_code=201)
def make_backup(request: Request, db: Session = Depends(get_db), user: User = Depends(admins)):
    path = backup_svc.create_backup()
    audit(db, "backup_created", user=user, entity="backup", entity_id=path.name, request=request, commit=True)
    return {"name": path.name, "size_bytes": path.stat().st_size}


@router.get("/backups/{name}")
def download_backup(name: str, request: Request, db: Session = Depends(get_db), user: User = Depends(admins)):
    try:
        path = backup_svc.backup_path(name)
    except FileNotFoundError:
        raise HTTPException(404, "Backup not found.") from None
    audit(db, "backup_downloaded", user=user, entity="backup", entity_id=name, request=request, commit=True)
    return FileResponse(path, media_type="application/gzip", filename=path.name,
                        headers={"Cache-Control": "no-store"})
