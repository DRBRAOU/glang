"""Admin API: categories and speakers."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Category, Expression, Recording, Speaker, User
from app.schemas import CategoryIn, CategoryOut, SpeakerIn, SpeakerOut
from app.security.deps import admins, any_staff, editors
from app.services.audit import audit

router = APIRouter(prefix="/api/admin", tags=["admin: catalog"])


# ------------------------------------------------------------- categories
@router.get("/categories", response_model=list[CategoryOut])
def list_categories(db: Session = Depends(get_db), _: User = Depends(any_staff)):
    return db.scalars(select(Category).order_by(Category.sort_order, Category.name)).all()


@router.post("/categories", response_model=CategoryOut, status_code=201)
def create_category(body: CategoryIn, request: Request, db: Session = Depends(get_db), user: User = Depends(editors)):
    if db.scalar(select(Category).where(func.lower(Category.name) == body.name.lower())):
        raise HTTPException(409, "A category with this name already exists.")
    c = Category(name=body.name, sort_order=body.sort_order)
    db.add(c)
    audit(db, "category_created", user=user, entity="category", entity_id=body.name, request=request)
    db.commit()
    return c


@router.put("/categories/{category_id}", response_model=CategoryOut)
def update_category(category_id: int, body: CategoryIn, request: Request, db: Session = Depends(get_db),
                    user: User = Depends(admins)):
    c = db.get(Category, category_id)
    if c is None:
        raise HTTPException(404, "Category not found.")
    clash = db.scalar(select(Category).where(func.lower(Category.name) == body.name.lower(), Category.id != c.id))
    if clash:
        raise HTTPException(409, "A category with this name already exists.")
    c.name, c.sort_order = body.name, body.sort_order
    audit(db, "category_updated", user=user, entity="category", entity_id=c.id, request=request)
    db.commit()
    return c


@router.delete("/categories/{category_id}", status_code=204)
def delete_category(category_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(admins)):
    c = db.get(Category, category_id)
    if c is None:
        raise HTTPException(404, "Category not found.")
    if db.scalar(select(func.count(Expression.id)).where(Expression.category_id == c.id)):
        raise HTTPException(409, "Category is in use; move its expressions first.")
    db.delete(c)
    audit(db, "category_deleted", user=user, entity="category", entity_id=category_id, request=request)
    db.commit()


# ------------------------------------------------------------- speakers
@router.get("/speakers", response_model=list[SpeakerOut])
def list_speakers(db: Session = Depends(get_db), _: User = Depends(any_staff)):
    return db.scalars(select(Speaker).order_by(Speaker.display_name)).all()


@router.post("/speakers", response_model=SpeakerOut, status_code=201)
def create_speaker(body: SpeakerIn, request: Request, db: Session = Depends(get_db), user: User = Depends(editors)):
    s = Speaker(**body.model_dump())
    db.add(s)
    db.flush()
    audit(db, "speaker_created", user=user, entity="speaker", entity_id=s.id, detail=s.display_name, request=request)
    db.commit()
    return s


@router.put("/speakers/{speaker_id}", response_model=SpeakerOut)
def update_speaker(speaker_id: int, body: SpeakerIn, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(admins)):
    s = db.get(Speaker, speaker_id)
    if s is None:
        raise HTTPException(404, "Speaker not found.")
    for k, v in body.model_dump().items():
        setattr(s, k, v)
    audit(db, "speaker_updated", user=user, entity="speaker", entity_id=s.id, detail=s.display_name, request=request)
    db.commit()
    return s


@router.delete("/speakers/{speaker_id}", status_code=204)
def delete_speaker(speaker_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(admins)):
    s = db.get(Speaker, speaker_id)
    if s is None:
        raise HTTPException(404, "Speaker not found.")
    if db.scalar(select(func.count(Recording.id)).where(Recording.speaker_id == s.id)):
        raise HTTPException(409, "Speaker has recordings; mark the speaker inactive instead.")
    db.delete(s)
    audit(db, "speaker_deleted", user=user, entity="speaker", entity_id=speaker_id, request=request)
    db.commit()
