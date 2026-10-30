"""Editorial workflow: draft -> in_review -> approved -> published.

Rules are enforced here (backend) regardless of what the UI shows.
"""
from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models import (
    Expression,
    ExpressionStatus as ES,
    Recording,
    RecordingStatus as RS,
    Review,
    Role,
    User,
    utcnow,
)

# Fields whose change invalidates an expert review.
LINGUISTIC_FIELDS = ("gondi_text", "transliteration", "telugu", "english", "variety", "script")

# action -> (allowed roles, allowed source states, target state)
TRANSITIONS: dict[str, tuple[set[str], set[str], str]] = {
    "submit": ({Role.admin.value, Role.contributor.value}, {ES.draft.value}, ES.in_review.value),
    "approve": ({Role.admin.value, Role.reviewer.value}, {ES.in_review.value}, ES.approved.value),
    "reject": (
        {Role.admin.value, Role.reviewer.value},
        {ES.in_review.value, ES.approved.value},
        ES.draft.value,
    ),
    "publish": ({Role.admin.value}, {ES.approved.value}, ES.published.value),
    "unpublish": ({Role.admin.value}, {ES.published.value}, ES.approved.value),
    "archive": (
        {Role.admin.value},
        {ES.draft.value, ES.in_review.value, ES.approved.value, ES.published.value},
        ES.archived.value,
    ),
    "restore": ({Role.admin.value}, {ES.archived.value}, ES.draft.value),
}


def _fail(msg: str, code: int = status.HTTP_409_CONFLICT) -> None:
    raise HTTPException(code, msg)


def publication_problems(expr: Expression, recording_will_be_approved: bool = False) -> list[str]:
    """Return the reasons an expression cannot (yet) be shown to learners."""
    problems = []
    if not expr.gondi_text.strip():
        problems.append("Gondi text has not been supplied by an expert.")
    if not (expr.telugu.strip() or expr.english.strip()):
        problems.append("A Telugu or English meaning is required.")
    rec = expr.default_recording
    if rec is None:
        problems.append("No default recording is selected.")
    else:
        ok_states = {RS.approved.value} | ({RS.pending.value} if recording_will_be_approved else set())
        if rec.status not in ok_states:
            problems.append("The default recording has not been approved.")
        if rec.speaker is None:
            problems.append("The default recording has no speaker recorded.")
        elif not rec.speaker.has_consent:
            problems.append("The speaker's consent scope and consent date are not recorded.")
    return problems


def apply_transition(db: Session, expr: Expression, action: str, user: User, comments: str = "") -> Expression:
    if action not in TRANSITIONS:
        _fail(f"Unknown action '{action}'.", status.HTTP_400_BAD_REQUEST)
    roles, sources, target = TRANSITIONS[action]
    if user.role not in roles:
        _fail("Your role does not permit this action.", status.HTTP_403_FORBIDDEN)
    if expr.status not in sources:
        _fail(f"Cannot {action} an expression whose status is '{expr.status}'.")

    comments = (comments or "").strip()
    if action == "reject" and not comments:
        _fail("Please give comments explaining what must be revised.", 422)

    if action == "submit":
        if not expr.gondi_text.strip():
            _fail("Add the expert-supplied Gondi text before submitting for review.")
        if expr.default_recording is None:
            _fail("Add a recording and mark it as default before submitting for review.")

    if action == "approve":
        rec = expr.default_recording
        # Approving the entry also approves its pending default recording (the
        # reviewer has listened to it as part of the review).
        problems = publication_problems(expr, recording_will_be_approved=True)
        if problems:
            _fail("Cannot approve yet: " + " ".join(problems))
        if rec is not None and rec.status == RS.pending.value:
            rec.status = RS.approved.value
            rec.approved_by = user.id
            rec.approved_at = utcnow()

    if action == "publish":
        problems = publication_problems(expr)
        if problems:
            _fail("Cannot publish yet: " + " ".join(problems))
        expr.published_at = utcnow()

    if action in ("unpublish", "archive", "reject", "restore"):
        if expr.status == ES.published.value:
            expr.published_at = None

    expr.status = target
    expr.updated_by = user.id
    db.add(Review(expression_id=expr.id, reviewer_id=user.id, decision=action, comments=comments[:4000]))
    return expr


def note_content_change(db: Session, expr: Expression, user: User, changed: list[str]) -> bool:
    """If reviewed content changed, send the entry back to draft. Returns True if reset."""
    if not changed or expr.status not in (ES.in_review.value, ES.approved.value, ES.published.value):
        return False
    prior = expr.status
    expr.status = ES.draft.value
    expr.published_at = None
    db.add(
        Review(
            expression_id=expr.id,
            reviewer_id=user.id,
            decision="revised",
            comments=f"Edited {', '.join(changed)} while {prior}; returned to draft for re-review.",
        )
    )
    return True


def can_edit_content(user: User, expr: Expression) -> bool:
    if user.role == Role.admin.value:
        return expr.status != ES.archived.value
    if user.role == Role.contributor.value:
        return expr.status == ES.draft.value
    return False


def ensure_default_allowed(expr: Expression, rec: Recording) -> None:
    if expr.status in (ES.approved.value, ES.published.value) and rec.status != RS.approved.value:
        _fail("Only an approved recording can be the default for an approved or published expression.")
