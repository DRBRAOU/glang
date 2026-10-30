"""Pydantic request/response schemas."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import Role


def _clean(v: str | None) -> str:
    return (v or "").strip()


# ----------------------------------------------------------------- public
class PublicCategory(BaseModel):
    id: int
    name: str
    count: int


class PublicExpression(BaseModel):
    id: int
    code: str
    gondi_text: str
    transliteration: str
    telugu: str
    english: str
    category: str | None
    category_id: int | None
    variety: str
    script: str
    usage_note: str
    audio_url: str
    duration: float | None
    speaker: str
    speaker_variety: str


class PublicList(BaseModel):
    items: list[PublicExpression]
    total: int


# ----------------------------------------------------------------- auth
class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class MeOut(BaseModel):
    id: int
    username: str
    full_name: str
    role: str
    csrf_token: str


class PasswordChangeIn(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)


# ----------------------------------------------------------------- users
class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    full_name: str
    role: str
    active: bool
    created_at: datetime
    last_login_at: datetime | None


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.@-]+$")
    full_name: str = Field(default="", max_length=128)
    role: Role
    password: str = Field(max_length=256)


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=128)
    role: Role | None = None
    active: bool | None = None
    password: str | None = Field(default=None, max_length=256)


# ----------------------------------------------------------------- categories
class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    sort_order: int


class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    sort_order: int = 0

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = _clean(v)
        if not v:
            raise ValueError("Name is required.")
        return v


# ----------------------------------------------------------------- speakers
class SpeakerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    display_name: str
    variety: str
    consent_scope: str
    consent_date: date | None
    reuse_restrictions: str
    attribution: str
    notes: str
    active: bool
    has_consent: bool


class SpeakerIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=128)
    variety: str = Field(default="", max_length=100)
    consent_scope: str = Field(default="", max_length=2000)
    consent_date: date | None = None
    reuse_restrictions: str = Field(default="", max_length=2000)
    attribution: str = Field(default="", max_length=255)
    notes: str = Field(default="", max_length=4000)
    active: bool = True


# ----------------------------------------------------------------- expressions
class RecordingOut(BaseModel):
    id: int
    speaker_id: int | None
    speaker_name: str
    original_filename: str
    mime_type: str
    size_bytes: int
    duration: float | None
    status: str
    is_default: bool
    created_at: datetime
    approved_at: datetime | None
    preview_url: str


class ReviewOut(BaseModel):
    id: int
    decision: str
    comments: str
    reviewer: str
    timestamp: datetime


class ExpressionIn(BaseModel):
    expression_code: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    gondi_text: str = Field(default="", max_length=1000)
    transliteration: str = Field(default="", max_length=1000)
    telugu: str = Field(default="", max_length=1000)
    english: str = Field(default="", max_length=1000)
    category_id: int | None = None
    variety: str = Field(default="", max_length=100)
    script: str = Field(default="", max_length=50)
    usage_note: str = Field(default="", max_length=2000)
    provenance: str = Field(default="", max_length=2000)

    @field_validator("*", mode="before")
    @classmethod
    def _strip_strings(cls, v):
        return v.strip() if isinstance(v, str) else v


class ExpressionUpdate(BaseModel):
    expression_code: str | None = Field(default=None, min_length=1, max_length=32, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    gondi_text: str | None = Field(default=None, max_length=1000)
    transliteration: str | None = Field(default=None, max_length=1000)
    telugu: str | None = Field(default=None, max_length=1000)
    english: str | None = Field(default=None, max_length=1000)
    category_id: int | None = None
    variety: str | None = Field(default=None, max_length=100)
    script: str | None = Field(default=None, max_length=50)
    usage_note: str | None = Field(default=None, max_length=2000)
    provenance: str | None = Field(default=None, max_length=2000)

    @field_validator("*", mode="before")
    @classmethod
    def _strip_strings(cls, v):
        return v.strip() if isinstance(v, str) else v


class ExpressionSummary(BaseModel):
    id: int
    expression_code: str
    gondi_text: str
    telugu: str
    english: str
    category: str | None
    variety: str
    status: str
    recordings: int
    has_default_audio: bool
    updated_at: datetime


class ExpressionDetail(BaseModel):
    id: int
    expression_code: str
    gondi_text: str
    transliteration: str
    telugu: str
    english: str
    category_id: int | None
    category: str | None
    variety: str
    script: str
    usage_note: str
    provenance: str
    status: str
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None
    recordings: list[RecordingOut]
    reviews: list[ReviewOut]
    publication_problems: list[str]
    allowed_actions: list[str]
    can_edit: bool


class TransitionIn(BaseModel):
    action: str = Field(max_length=20)
    comments: str = Field(default="", max_length=4000)


class RecordingUpdate(BaseModel):
    speaker_id: int | None = None
    is_default: bool | None = None
    status: str | None = Field(default=None, pattern=r"^(pending|approved|rejected)$")


class ImportCommitIn(BaseModel):
    skip_invalid: bool = False
