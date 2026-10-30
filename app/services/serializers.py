"""Convert ORM objects to API schemas."""
from __future__ import annotations

from app.models import Expression, Recording, Review, User
from app.schemas import (
    ExpressionDetail,
    ExpressionSummary,
    PublicExpression,
    RecordingOut,
    ReviewOut,
)
from app.services.workflow import TRANSITIONS, can_edit_content, publication_problems


def public_expression(e: Expression) -> PublicExpression:
    rec = e.default_recording
    assert rec is not None
    spk = rec.speaker
    return PublicExpression(
        id=e.id,
        code=e.expression_code,
        gondi_text=e.gondi_text,
        transliteration=e.transliteration,
        telugu=e.telugu,
        english=e.english,
        category=e.category.name if e.category else None,
        category_id=e.category_id,
        variety=e.variety,
        script=e.script,
        usage_note=e.usage_note,
        audio_url=f"/api/public/audio/{rec.id}",
        duration=rec.duration,
        speaker=(spk.attribution or spk.display_name) if spk else "",
        speaker_variety=spk.variety if spk else "",
    )


def recording_out(r: Recording) -> RecordingOut:
    return RecordingOut(
        id=r.id,
        speaker_id=r.speaker_id,
        speaker_name=r.speaker.display_name if r.speaker else "",
        original_filename=r.original_filename,
        mime_type=r.mime_type,
        size_bytes=r.size_bytes,
        duration=r.duration,
        status=r.status,
        is_default=r.is_default,
        created_at=r.created_at,
        approved_at=r.approved_at,
        preview_url=f"/api/admin/recordings/{r.id}/audio",
    )


def review_out(r: Review) -> ReviewOut:
    return ReviewOut(
        id=r.id,
        decision=r.decision,
        comments=r.comments,
        reviewer=(r.reviewer.full_name or r.reviewer.username) if r.reviewer else "",
        timestamp=r.timestamp,
    )


def expression_summary(e: Expression) -> ExpressionSummary:
    return ExpressionSummary(
        id=e.id,
        expression_code=e.expression_code,
        gondi_text=e.gondi_text,
        telugu=e.telugu,
        english=e.english,
        category=e.category.name if e.category else None,
        variety=e.variety,
        status=e.status,
        recordings=len(e.recordings),
        has_default_audio=e.default_recording is not None,
        updated_at=e.updated_at,
    )


def expression_detail(e: Expression, user: User) -> ExpressionDetail:
    allowed = [a for a, (roles, sources, _) in TRANSITIONS.items() if user.role in roles and e.status in sources]
    return ExpressionDetail(
        id=e.id,
        expression_code=e.expression_code,
        gondi_text=e.gondi_text,
        transliteration=e.transliteration,
        telugu=e.telugu,
        english=e.english,
        category_id=e.category_id,
        category=e.category.name if e.category else None,
        variety=e.variety,
        script=e.script,
        usage_note=e.usage_note,
        provenance=e.provenance,
        status=e.status,
        created_at=e.created_at,
        updated_at=e.updated_at,
        published_at=e.published_at,
        recordings=[recording_out(r) for r in e.recordings],
        reviews=[review_out(r) for r in reversed(e.reviews)],
        publication_problems=publication_problems(e),
        allowed_actions=allowed,
        can_edit=can_edit_content(user, e),
    )
