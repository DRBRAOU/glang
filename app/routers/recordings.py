"""Admin API: upload / record / approve / replace audio recordings."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ExpressionStatus as ES, Recording, RecordingStatus as RS, Role, Speaker, User, utcnow
from app.routers.expressions import load_expression
from app.schemas import ExpressionDetail, RecordingUpdate
from app.security.deps import any_staff, editors
from app.services import audio as audio_svc
from app.services.audit import audit
from app.services.serializers import expression_detail
from app.services.workflow import ensure_default_allowed

router = APIRouter(prefix="/api/admin", tags=["admin: recordings"])


def _speaker_or_422(db: Session, speaker_id: int | None) -> Speaker | None:
    if speaker_id is None:
        return None
    spk = db.get(Speaker, speaker_id)
    if spk is None:
        raise HTTPException(422, "Unknown speaker.")
    return spk


@router.post("/expressions/{expression_id}/recordings", response_model=ExpressionDetail, status_code=201)
def upload_recording(
    expression_id: int,
    request: Request,
    file: UploadFile = File(...),
    speaker_id: int | None = Form(default=None),
    make_default: bool = Form(default=True),
    db: Session = Depends(get_db),
    user: User = Depends(editors),
):
    e = load_expression(db, expression_id)
    if e.status == ES.archived.value:
        raise HTTPException(409, "Restore the expression before adding recordings.")
    if user.role == Role.contributor.value and e.status != ES.draft.value:
        raise HTTPException(403, "Contributors can add recordings only to draft entries.")
    _speaker_or_422(db, speaker_id)
    try:
        stored = audio_svc.store_audio_from_tempfile(file.file, file.filename or "recording")
    except audio_svc.AudioError as exc:
        raise HTTPException(422, str(exc)) from None
    rec = Recording(
        expression_id=e.id,
        speaker_id=speaker_id,
        stored_filename=stored.stored_filename,
        source_filename=stored.source_filename,
        original_filename=(file.filename or "browser-recording")[:255],
        mime_type=stored.mime_type,
        size_bytes=stored.size_bytes,
        duration=stored.duration,
        status=RS.pending.value,
        is_default=False,
        uploaded_by=user.id,
    )
    db.add(rec)
    db.flush()
    # A pending recording only becomes default automatically while the entry is
    # still being edited; approved/published entries keep their approved audio.
    current = e.default_recording
    if (make_default or current is None) and e.status in (ES.draft.value, ES.in_review.value):
        for r in e.recordings:
            r.is_default = False
        rec.is_default = True
    audit(db, "recording_added", user=user, entity="expression", entity_id=e.expression_code,
          detail=f"recording={rec.id} {stored.mime_type} {stored.duration}s", request=request)
    db.commit()
    db.expire_all()
    return expression_detail(load_expression(db, e.id), user)


def _load_recording(db: Session, recording_id: int) -> Recording:
    rec = db.get(Recording, recording_id)
    if rec is None:
        raise HTTPException(404, "Recording not found.")
    return rec


@router.patch("/recordings/{recording_id}", response_model=ExpressionDetail)
def update_recording(recording_id: int, body: RecordingUpdate, request: Request,
                     db: Session = Depends(get_db), user: User = Depends(any_staff)):
    rec = _load_recording(db, recording_id)
    e = load_expression(db, rec.expression_id)
    rec = next(r for r in e.recordings if r.id == recording_id)
    data = body.model_dump(exclude_unset=True)
    is_reviewer = user.role in (Role.admin.value, Role.reviewer.value)
    is_editor = user.role == Role.admin.value or (user.role == Role.contributor.value and e.status == ES.draft.value)
    if e.status == ES.archived.value:
        raise HTTPException(409, "This expression is archived.")

    if "speaker_id" in data:
        if not (is_editor or is_reviewer):
            raise HTTPException(403, "Your role cannot change the speaker on this entry.")
        if rec.status == RS.approved.value and user.role == Role.contributor.value:
            raise HTTPException(403, "Approved recordings can only be changed by a reviewer or administrator.")
        spk = _speaker_or_422(db, data["speaker_id"])
        if rec.is_default and e.status in (ES.approved.value, ES.published.value) and not (spk and spk.has_consent):
            raise HTTPException(409, "The live recording must keep a speaker with recorded consent.")
        rec.speaker_id = data["speaker_id"]
        db.flush()
        db.refresh(rec)

    if data.get("status"):
        new = data["status"]
        if not is_reviewer:
            raise HTTPException(403, "Only reviewers or administrators can approve or reject recordings.")
        if new != RS.approved.value and rec.is_default and e.status in (ES.approved.value, ES.published.value):
            raise HTTPException(409, "Choose another approved default recording first.")
        if new == RS.approved.value and rec.status != RS.approved.value:
            rec.approved_by, rec.approved_at = user.id, utcnow()
        elif new != RS.approved.value:
            rec.approved_by, rec.approved_at = None, None
        rec.status = new

    if data.get("is_default"):
        if not (is_editor or is_reviewer):
            raise HTTPException(403, "Your role cannot change the default recording on this entry.")
        ensure_default_allowed(e, rec)
        for r in e.recordings:
            r.is_default = r.id == rec.id

    audit(db, "recording_updated", user=user, entity="recording", entity_id=rec.id,
          detail=str(data), request=request)
    db.commit()
    db.expire_all()
    return expression_detail(load_expression(db, e.id), user)


@router.delete("/recordings/{recording_id}", response_model=ExpressionDetail)
def delete_recording(recording_id: int, request: Request, db: Session = Depends(get_db),
                     user: User = Depends(editors)):
    rec = _load_recording(db, recording_id)
    e = load_expression(db, rec.expression_id)
    rec = next(r for r in e.recordings if r.id == recording_id)
    if user.role == Role.contributor.value and (e.status != ES.draft.value or rec.status == RS.approved.value):
        raise HTTPException(403, "Contributors can delete only unapproved recordings on draft entries.")
    if rec.is_default and e.status in (ES.approved.value, ES.published.value):
        raise HTTPException(409, "This is the live default recording; choose another default first.")
    files = (rec.stored_filename, rec.source_filename)
    e.recordings.remove(rec)
    db.delete(rec)
    if not any(r.is_default for r in e.recordings) and e.recordings:
        e.recordings[-1].is_default = True
    audit(db, "recording_deleted", user=user, entity="expression", entity_id=e.expression_code,
          detail=f"recording={recording_id}", request=request)
    db.commit()
    audio_svc.delete_audio(*files)
    db.expire_all()
    return expression_detail(load_expression(db, e.id), user)


@router.get("/recordings/{recording_id}/audio")
def preview_audio(recording_id: int, db: Session = Depends(get_db), _: User = Depends(any_staff)):
    """Staff-only preview of any recording (pending, rejected or approved)."""
    rec = _load_recording(db, recording_id)
    try:
        path = audio_svc.audio_path(rec.stored_filename)
    except audio_svc.AudioError:
        raise HTTPException(404, "Recording not found.") from None
    if not path.exists():
        raise HTTPException(404, "Recording file is missing.")
    return FileResponse(path, media_type=rec.mime_type,
                        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})
