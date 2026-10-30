"""Public learner API. Exposes ONLY published expressions with an approved default recording."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.database import get_db
from app.models import Category, Expression, ExpressionStatus, Recording, RecordingStatus
from app.schemas import PublicCategory, PublicExpression, PublicList
from app.services.audio import AudioError, audio_path
from app.services.serializers import public_expression

router = APIRouter(prefix="/api/public", tags=["public"])


def _visible_query():
    """Published expressions whose default recording is approved."""
    return (
        select(Expression)
        .join(Recording, (Recording.expression_id == Expression.id) & (Recording.is_default.is_(True)))
        .where(Expression.status == ExpressionStatus.published.value)
        .where(Recording.status == RecordingStatus.approved.value)
    )


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("/categories", response_model=list[PublicCategory])
def categories(db: Session = Depends(get_db)):
    visible_ids = _visible_query().with_only_columns(Expression.id).subquery()
    rows = db.execute(
        select(Category.id, Category.name, func.count(Expression.id))
        .join(Expression, Expression.category_id == Category.id)
        .where(Expression.id.in_(select(visible_ids.c.id)))
        .group_by(Category.id)
        .order_by(Category.sort_order, Category.name)
    ).all()
    return [PublicCategory(id=i, name=n, count=c) for i, n, c in rows]


@router.get("/expressions", response_model=PublicList)
def list_expressions(
    q: str = Query(default="", max_length=200),
    category_id: int | None = None,
    db: Session = Depends(get_db),
):
    stmt = _visible_query().options(
        joinedload(Expression.category),
        selectinload(Expression.recordings).joinedload(Recording.speaker),
    )
    if category_id:
        stmt = stmt.where(Expression.category_id == category_id)
    term = q.strip()
    if term:
        like = f"%{_escape_like(term)}%"
        stmt = stmt.where(
            or_(
                Expression.gondi_text.ilike(like, escape="\\"),
                Expression.transliteration.ilike(like, escape="\\"),
                Expression.telugu.ilike(like, escape="\\"),
                Expression.english.ilike(like, escape="\\"),
                Expression.expression_code.ilike(like, escape="\\"),
            )
        )
    stmt = stmt.outerjoin(Category, Category.id == Expression.category_id).order_by(
        Category.sort_order, Expression.expression_code
    )
    items = [public_expression(e) for e in db.scalars(stmt).unique().all()]
    return PublicList(items=items, total=len(items))


@router.get("/expressions/{expression_id}", response_model=PublicExpression)
def get_expression(expression_id: int, db: Session = Depends(get_db)):
    e = db.scalars(_visible_query().where(Expression.id == expression_id)).first()
    if e is None:
        raise HTTPException(404, "Expression not found.")
    return public_expression(e)


@router.get("/audio/{recording_id}")
def stream_audio(recording_id: int, db: Session = Depends(get_db)):
    """Stream an approved default recording of a published expression (supports Range)."""
    rec = db.scalars(
        select(Recording)
        .join(Expression, Expression.id == Recording.expression_id)
        .where(Recording.id == recording_id)
        .where(Recording.status == RecordingStatus.approved.value)
        .where(Recording.is_default.is_(True))
        .where(Expression.status == ExpressionStatus.published.value)
    ).first()
    if rec is None:
        raise HTTPException(404, "Recording not found.")
    try:
        path = audio_path(rec.stored_filename)
    except AudioError:
        raise HTTPException(404, "Recording not found.") from None
    if not path.exists():
        raise HTTPException(404, "Recording file is missing.")
    return FileResponse(
        path,
        media_type=rec.mime_type,
        headers={
            # Revalidate every time so unpublished audio stops being served at once.
            "Cache-Control": "no-cache",
            "Content-Disposition": "inline",
            "X-Content-Type-Options": "nosniff",
        },
    )
