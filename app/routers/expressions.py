"""Admin API: expressions and editorial workflow."""
from __future__ import annotations

import csv
import io
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.database import get_db
from app.models import Category, Expression, ExpressionStatus, Recording, Review, User
from app.schemas import ExpressionDetail, ExpressionIn, ExpressionSummary, ExpressionUpdate, TransitionIn
from app.security.deps import admins, any_staff, editors
from app.services.audit import audit
from app.services.serializers import expression_detail, expression_summary
from app.services.workflow import LINGUISTIC_FIELDS, apply_transition, can_edit_content, note_content_change

router = APIRouter(prefix="/api/admin/expressions", tags=["admin: expressions"])


def load_expression(db: Session, expression_id: int) -> Expression:
    e = db.scalars(
        select(Expression)
        .where(Expression.id == expression_id)
        .options(
            joinedload(Expression.category),
            selectinload(Expression.recordings).joinedload(Recording.speaker),
            selectinload(Expression.reviews).joinedload(Review.reviewer),
        )
        .execution_options(populate_existing=True)  # always reflect just-committed changes
    ).first()
    if e is None:
        raise HTTPException(404, "Expression not found.")
    return e


def _check_category(db: Session, category_id: int | None) -> None:
    if category_id is not None and db.get(Category, category_id) is None:
        raise HTTPException(422, "Unknown category.")


def _code_taken(db: Session, code: str, exclude_id: int | None = None) -> bool:
    stmt = select(Expression.id).where(func.lower(Expression.expression_code) == code.lower())
    if exclude_id:
        stmt = stmt.where(Expression.id != exclude_id)
    return db.scalar(stmt) is not None


@router.get("", response_model=list[ExpressionSummary])
def list_expressions(
    q: str = Query(default="", max_length=200),
    status: str = Query(default="", max_length=20),
    category_id: int | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(any_staff),
):
    stmt = select(Expression).options(joinedload(Expression.category), selectinload(Expression.recordings))
    if status:
        stmt = stmt.where(Expression.status == status)
    else:
        stmt = stmt.where(Expression.status != ExpressionStatus.archived.value)
    if category_id:
        stmt = stmt.where(Expression.category_id == category_id)
    if q.strip():
        like = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        stmt = stmt.where(or_(*[col.ilike(like, escape="\\") for col in (
            Expression.expression_code, Expression.gondi_text, Expression.telugu,
            Expression.english, Expression.transliteration)]))
    stmt = stmt.order_by(Expression.expression_code)
    return [expression_summary(e) for e in db.scalars(stmt).unique().all()]


@router.post("", response_model=ExpressionDetail, status_code=201)
def create_expression(body: ExpressionIn, request: Request, db: Session = Depends(get_db),
                      user: User = Depends(editors)):
    if _code_taken(db, body.expression_code):
        raise HTTPException(409, f"Expression ID '{body.expression_code}' already exists.")
    _check_category(db, body.category_id)
    e = Expression(**body.model_dump(), status=ExpressionStatus.draft.value, created_by=user.id, updated_by=user.id)
    db.add(e)
    db.flush()
    db.add(Review(expression_id=e.id, reviewer_id=user.id, decision="created", comments=""))
    audit(db, "expression_created", user=user, entity="expression", entity_id=e.expression_code, request=request)
    db.commit()
    return expression_detail(load_expression(db, e.id), user)


@router.get("/export.csv")
def export_csv(db: Session = Depends(get_db), user: User = Depends(any_staff)):
    """Full export in the import column format (+ status). Cells are formula-escaped for Excel."""

    def safe(v: str) -> str:
        v = v or ""
        return "'" + v if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v

    exprs = db.scalars(
        select(Expression).options(joinedload(Expression.category), selectinload(Expression.recordings)
                                   .joinedload(Recording.speaker)).order_by(Expression.expression_code)
    ).unique().all()
    buf = io.StringIO()
    buf.write("﻿")  # BOM so Excel opens UTF-8 Telugu/Gondi correctly
    w = csv.writer(buf)
    w.writerow(["expression_id", "category", "english", "telugu", "gondi_text", "variety", "script",
                "audio_filename", "transliteration", "usage_note", "speaker", "status", "published_at"])
    for e in exprs:
        rec = e.default_recording
        w.writerow([safe(x) for x in (
            e.expression_code, e.category.name if e.category else "", e.english, e.telugu, e.gondi_text,
            e.variety, e.script, rec.original_filename if rec else "", e.transliteration, e.usage_note,
            rec.speaker.display_name if rec and rec.speaker else "", e.status,
            e.published_at.isoformat(timespec="seconds") if e.published_at else "")])
    name = f"gondi-vaani-export-{datetime.now():%Y%m%d-%H%M}.csv"
    return StreamingResponse(iter([buf.getvalue().encode("utf-8")]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/{expression_id}", response_model=ExpressionDetail)
def get_expression(expression_id: int, db: Session = Depends(get_db), user: User = Depends(any_staff)):
    return expression_detail(load_expression(db, expression_id), user)


@router.patch("/{expression_id}", response_model=ExpressionDetail)
def update_expression(expression_id: int, body: ExpressionUpdate, request: Request,
                      db: Session = Depends(get_db), user: User = Depends(editors)):
    e = load_expression(db, expression_id)
    if not can_edit_content(user, e):
        raise HTTPException(403, "Contributors can only edit draft entries; ask an administrator.")
    data = body.model_dump(exclude_unset=True)
    if "expression_code" in data:
        if data["expression_code"] is None:
            data.pop("expression_code")
        elif _code_taken(db, data["expression_code"], exclude_id=e.id):
            raise HTTPException(409, f"Expression ID '{data['expression_code']}' already exists.")
    if "category_id" in data:
        _check_category(db, data["category_id"])
    changed = []
    for k, v in data.items():
        if v is None and k != "category_id":
            continue
        if getattr(e, k) != v:
            setattr(e, k, v)
            changed.append(k)
    if not changed:
        return expression_detail(e, user)
    e.updated_by = user.id
    reset = note_content_change(db, e, user, [c for c in changed if c in LINGUISTIC_FIELDS])
    audit(db, "expression_updated", user=user, entity="expression", entity_id=e.expression_code,
          detail=f"fields={','.join(changed)}" + (" (returned to draft)" if reset else ""), request=request)
    db.commit()
    return expression_detail(load_expression(db, e.id), user)


@router.post("/{expression_id}/transition", response_model=ExpressionDetail)
def transition(expression_id: int, body: TransitionIn, request: Request,
               db: Session = Depends(get_db), user: User = Depends(any_staff)):
    e = load_expression(db, expression_id)
    apply_transition(db, e, body.action, user, body.comments)
    audit(db, f"expression_{body.action}", user=user, entity="expression", entity_id=e.expression_code,
          detail=body.comments[:500], request=request)
    db.commit()
    return expression_detail(load_expression(db, e.id), user)


@router.delete("/{expression_id}", status_code=204)
def delete_expression(expression_id: int, request: Request, db: Session = Depends(get_db),
                      user: User = Depends(admins)):
    """Permanent deletion is allowed only for archived entries (admin)."""
    from app.services.audio import delete_audio

    e = load_expression(db, expression_id)
    if e.status != ExpressionStatus.archived.value:
        raise HTTPException(409, "Archive the expression before deleting it permanently.")
    files = [n for r in e.recordings for n in (r.stored_filename, r.source_filename)]
    code = e.expression_code
    db.delete(e)
    audit(db, "expression_deleted", user=user, entity="expression", entity_id=code, request=request)
    db.commit()
    delete_audio(*files)
