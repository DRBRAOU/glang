"""Admin API: bulk CSV + ZIP import with dry-run preview."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import ImportJob, Role, User
from app.schemas import ImportCommitIn
from app.security.deps import editors
from app.services import importer
from app.services.audit import audit

router = APIRouter(prefix="/api/admin/imports", tags=["admin: import"])

TEMPLATE = (
    "expression_id,category,english,telugu,gondi_text,variety,script,audio_filename\n"
    "GON-001,Greetings,Hello,నమస్కారం,,Adilabad,,GON-001.mp3\n"
    "GON-002,Greetings,How are you?,మీరు ఎలా ఉన్నారు?,,Adilabad,,GON-002.mp3\n"
)


def _read_limited(upload: UploadFile, limit: int, label: str) -> bytes:
    data = upload.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"{label} exceeds the {limit // (1024 * 1024) or 1} MB limit.")
    return data


def _job_out(job: ImportJob, with_report: bool = True) -> dict:
    out = {
        "id": job.id, "filename": job.filename, "zip_filename": job.zip_filename, "status": job.status,
        "total": job.total, "accepted": job.accepted, "rejected": job.rejected,
        "timestamp": job.timestamp.isoformat(timespec="seconds"),
        "committed_at": job.committed_at.isoformat(timespec="seconds") if job.committed_at else None,
    }
    if with_report:
        out["report"] = json.loads(job.report or "{}")
    return out


def _get_job(db: Session, job_id: int, user: User) -> ImportJob:
    job = db.get(ImportJob, job_id)
    if job is None or (user.role != Role.admin.value and job.created_by != user.id):
        raise HTTPException(404, "Import not found.")
    return job


@router.get("/template.csv")
def template(_: User = Depends(editors)):
    return Response(("﻿" + TEMPLATE).encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="gondi-vaani-import-template.csv"'})


@router.post("/preview", status_code=201)
def preview(
    request: Request,
    csv_file: UploadFile = File(...),
    zip_file: UploadFile | None = File(default=None),
    mode: str = Form(default="create", pattern="^(create|update)$"),
    db: Session = Depends(get_db),
    user: User = Depends(editors),
):
    s = get_settings()
    csv_raw = _read_limited(csv_file, s.max_csv_bytes, "CSV")
    zip_raw = _read_limited(zip_file, s.max_zip_bytes, "ZIP") if zip_file and zip_file.filename else None
    try:
        job = importer.dry_run(db, user, csv_file.filename or "import.csv", csv_raw,
                               zip_file.filename if zip_file else "", zip_raw, mode=mode)
    except importer.ImportError_ as exc:
        raise HTTPException(422, str(exc)) from None
    audit(db, "import_previewed", user=user, entity="import", entity_id=job.id,
          detail=f"{job.filename} ({mode}): {job.accepted}/{job.total} valid", request=request, commit=True)
    return _job_out(job)


@router.get("")
def list_jobs(db: Session = Depends(get_db), user: User = Depends(editors)):
    stmt = select(ImportJob).order_by(ImportJob.id.desc()).limit(50)
    if user.role != Role.admin.value:
        stmt = stmt.where(ImportJob.created_by == user.id)
    return [_job_out(j, with_report=False) for j in db.scalars(stmt).all()]


@router.get("/{job_id}")
def get_job(job_id: int, db: Session = Depends(get_db), user: User = Depends(editors)):
    return _job_out(_get_job(db, job_id, user))


@router.post("/{job_id}/commit")
def commit(job_id: int, body: ImportCommitIn, request: Request, db: Session = Depends(get_db),
           user: User = Depends(editors)):
    job = _get_job(db, job_id, user)
    try:
        job = importer.commit(db, user, job, skip_invalid=body.skip_invalid)
    except importer.ImportError_ as exc:
        raise HTTPException(409, str(exc)) from None
    audit(db, "import_committed", user=user, entity="import", entity_id=job.id,
          detail=f"{job.accepted} rows imported, {job.rejected} skipped", request=request, commit=True)
    return _job_out(job)


@router.delete("/{job_id}", status_code=204)
def discard(job_id: int, db: Session = Depends(get_db), user: User = Depends(editors)):
    importer.discard(db, _get_job(db, job_id, user))
    return Response(status_code=204)
