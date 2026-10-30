"""Bulk import of UTF-8 CSV + audio ZIP.

Flow:
  dry_run()  -> validates everything, stages the two uploaded files under
                storage/imports/<token>/ and returns a row-by-row report.
  commit()   -> re-validates the staged files and imports accepted rows in a
                single database transaction. Audio files written during a failed
                commit are removed again. All imported entries start as drafts.

CSV content is treated purely as text: nothing is evaluated or executed.
"""
from __future__ import annotations

import csv
import io
import json
import re
import secrets
import shutil
import stat
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    Category,
    Expression,
    ExpressionStatus,
    ImportJob,
    Recording,
    RecordingStatus,
    Review,
    Speaker,
    User,
    utcnow,
)
from app.services import audio as audio_svc

REQUIRED_COLUMNS = [
    "expression_id", "category", "english", "telugu", "gondi_text", "variety", "script", "audio_filename",
]
# review_status / recording_notes: informational columns from recording-prompt sheets (kept in provenance)
OPTIONAL_COLUMNS = ["transliteration", "usage_note", "speaker", "review_status", "recording_notes"]
CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$")
FIELD_LIMITS = {
    "english": 1000, "telugu": 1000, "gondi_text": 1000, "transliteration": 1000,
    "usage_note": 2000, "category": 100, "variety": 100, "script": 50, "speaker": 128,
    "review_status": 200, "recording_notes": 1000,
}


class ImportError_(ValueError):
    """File-level failure (whole upload rejected)."""


@dataclass
class RowResult:
    line: int
    expression_id: str
    english: str = ""
    telugu: str = ""
    category: str = ""
    audio_filename: str = ""
    duration: float | None = None
    action: str = "create"  # create | update
    changes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass
class Report:
    mode: str = "create"
    total: int = 0
    creates: int = 0
    updates: int = 0
    accepted: int = 0
    rejected: int = 0
    file_errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    rows: list[RowResult] = field(default_factory=list)
    unused_audio: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        for r, rr in zip(d["rows"], self.rows):
            r["ok"] = rr.ok
        return d


# --------------------------------------------------------------------------- CSV
def parse_csv(raw: bytes) -> tuple[list[str], list[tuple[int, dict[str, str]]]]:
    settings = get_settings()
    if len(raw) > settings.max_csv_bytes:
        raise ImportError_(f"CSV exceeds the {settings.max_csv_bytes // 1024} KB limit.")
    if b"\x00" in raw:
        raise ImportError_("CSV contains binary data; save it as UTF-8 CSV text.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ImportError_(
            f"CSV is not valid UTF-8 (byte {exc.start}). In Excel use 'CSV UTF-8 (Comma delimited)'."
        ) from exc
    try:
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        rows = list(reader)
    except csv.Error as exc:
        raise ImportError_(f"Malformed CSV: {exc}.") from exc
    if not rows:
        raise ImportError_("CSV is empty.")
    header = [h.strip().lower() for h in rows[0]]
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing:
        raise ImportError_("CSV is missing required column(s): " + ", ".join(missing) + ".")
    unknown = [h for h in header if h and h not in REQUIRED_COLUMNS + OPTIONAL_COLUMNS]
    if unknown:
        raise ImportError_("CSV has unknown column(s): " + ", ".join(unknown) + ".")
    if len(set(header)) != len(header):
        raise ImportError_("CSV has duplicate column names.")
    out: list[tuple[int, dict[str, str]]] = []
    for idx, row in enumerate(rows[1:], start=2):
        if not any(cell.strip() for cell in row):
            continue  # skip blank lines
        if len(row) != len(header):
            out.append((idx, {"__error__": f"has {len(row)} fields; expected {len(header)}",
                              "expression_id": row[0].strip() if row else ""}))
            continue
        out.append((idx, {h: (v or "").strip() for h, v in zip(header, row)}))
    if len(out) > settings.max_import_rows:
        raise ImportError_(f"CSV has {len(out)} rows; the limit is {settings.max_import_rows}.")
    if not out:
        raise ImportError_("CSV has a header but no data rows.")
    return header, out


# --------------------------------------------------------------------------- ZIP
def _is_unsafe_member(info: zipfile.ZipInfo) -> str | None:
    name = info.filename
    if "\x00" in name:
        return "contains a NUL byte"
    if name.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", name):
        return "is an absolute path"
    parts = PurePosixPath(name.replace("\\", "/")).parts
    if ".." in parts:
        return "contains '..' (path traversal)"
    mode = (info.external_attr >> 16) & 0xFFFF
    if mode and stat.S_ISLNK(mode):
        return "is a symbolic link"
    if info.flag_bits & 0x1:
        return "is encrypted"
    return None


def open_zip(raw: bytes) -> tuple[zipfile.ZipFile, dict[str, zipfile.ZipInfo]]:
    """Validate a ZIP without extracting it to disk. Returns basename -> member map."""
    settings = get_settings()
    if len(raw) > settings.max_zip_bytes:
        raise ImportError_(f"ZIP exceeds the {settings.max_zip_bytes // (1024 * 1024)} MB limit.")
    bio = io.BytesIO(raw)
    if not zipfile.is_zipfile(bio):
        raise ImportError_("The audio upload is not a valid ZIP file.")
    try:
        zf = zipfile.ZipFile(bio)
        infos = zf.infolist()
    except (zipfile.BadZipFile, ValueError) as exc:
        raise ImportError_(f"The ZIP file is damaged: {exc}.") from exc
    if len(infos) > settings.max_zip_members:
        raise ImportError_(f"ZIP has {len(infos)} entries; the limit is {settings.max_zip_members}.")
    total = 0
    members: dict[str, zipfile.ZipInfo] = {}
    for info in infos:
        problem = _is_unsafe_member(info)
        if problem:
            raise ImportError_(f"Unsafe ZIP entry '{info.filename[:120]}' {problem}; upload rejected.")
        if info.is_dir():
            continue
        name = info.filename.replace("\\", "/")
        base = PurePosixPath(name).name
        if name.startswith("__MACOSX/") or base.startswith(".") or base in ("Thumbs.db", "desktop.ini"):
            continue
        total += info.file_size
        if total > settings.max_zip_uncompressed_bytes:
            raise ImportError_("ZIP expands to more data than allowed; upload rejected.")
        if info.file_size > settings.max_audio_bytes:
            raise ImportError_(f"'{base}' is larger than the per-file audio limit.")
        if info.compress_size and info.file_size / info.compress_size > 200:
            raise ImportError_(f"'{base}' has a suspicious compression ratio; upload rejected.")
        key = base
        if key in members:
            raise ImportError_(f"ZIP contains two files named '{base}' in different folders.")
        members[key] = info
    return zf, members


def _read_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    limit = get_settings().max_audio_bytes
    with zf.open(info) as fh:
        data = fh.read(limit + 1)
    if len(data) > limit:
        raise audio_svc.AudioError("audio exceeds size limit")
    return data


# --------------------------------------------------------------------------- validation
MODES = ("create", "update")
# Text fields an update-mode import may change. Blank cells never overwrite existing values,
# so expert-entered Gondi text is not lost when a revised meanings file is re-imported.
UPDATABLE = ("english", "telugu", "gondi_text", "transliteration", "usage_note", "variety", "script")


def validate(db: Session, csv_raw: bytes, zip_raw: bytes | None, *, check_audio: bool = True,
             mode: str = "create", user: User | None = None
             ) -> tuple[Report, list[tuple[RowResult, dict]], tuple]:
    if mode not in MODES:
        raise ImportError_(f"Unknown import mode '{mode}'.")
    report = Report()
    _, rows = parse_csv(csv_raw)
    zf, members = (None, {})
    if zip_raw:
        zf, members = open_zip(zip_raw)
    lower_members = {k.lower(): k for k in members}

    existing = {e.expression_code.lower(): e for e in db.scalars(select(Expression)).all()}
    speakers = {s.display_name.strip().lower(): s for s in db.scalars(select(Speaker)).all()}
    seen_codes: dict[str, int] = {}
    seen_audio: dict[str, int] = {}
    used_members: set[str] = set()
    accepted: list[tuple[RowResult, dict]] = []
    is_admin = user is not None and user.role == "admin"

    for line, row in rows:
        code = row.get("expression_id", "")
        rr = RowResult(line=line, expression_id=code, english=row.get("english", "")[:200],
                       telugu=row.get("telugu", "")[:200], category=row.get("category", ""),
                       audio_filename=row.get("audio_filename", ""))
        report.rows.append(rr)
        if "__error__" in row:
            rr.errors.append(f"Row {row['__error__']}.")
            continue
        target: Expression | None = None
        # ---- identifiers & duplicates
        if not code:
            rr.errors.append("expression_id is required.")
        elif not CODE_RE.match(code):
            rr.errors.append("expression_id may contain only letters, digits, '-', '_' or '.' (max 32).")
        else:
            key = code.lower()
            if key in seen_codes:
                rr.errors.append(f"Duplicate expression_id; also on line {seen_codes[key]}.")
            else:
                seen_codes[key] = line
            target = existing.get(key)
            if target is not None and mode == "create":
                rr.errors.append("expression_id already exists in the library "
                                 "(choose 'add new and update existing' to update it).")
            elif target is not None:
                rr.action = "update"
                if target.status == ExpressionStatus.archived.value:
                    rr.errors.append("This entry is archived; restore it before updating.")
                elif target.status != ExpressionStatus.draft.value and not is_admin:
                    rr.errors.append(f"Entry is '{target.status}'; only an administrator can update it.")
        # ---- text fields
        for name, limit in FIELD_LIMITS.items():
            if len(row.get(name, "")) > limit:
                rr.errors.append(f"{name} is longer than {limit} characters.")
        for name in FIELD_LIMITS:
            val = row.get(name, "")
            if any(ord(ch) < 32 and ch not in "\t\r\n" for ch in val):
                rr.errors.append(f"{name} contains control characters.")
        if rr.action == "create":
            if not row.get("category"):
                rr.errors.append("category is required.")
            if not (row.get("english") or row.get("telugu")):
                rr.errors.append("At least one meaning (english or telugu) is required.")
            if not row.get("gondi_text"):
                rr.warnings.append("gondi_text is blank; an expert must add it before review.")
        elif target is not None:
            changes = [f for f in UPDATABLE if row.get(f) and row[f] != getattr(target, f)]
            if row.get("category") and (target.category is None or target.category.name.lower() != row["category"].lower()):
                changes.append("category")
            rr.changes = changes
            if changes and target.status in ("in_review", "approved", "published") and \
                    any(c in ("english", "telugu", "gondi_text", "transliteration", "variety", "script") for c in changes):
                rr.warnings.append(f"Text changes will return this '{target.status}' entry to draft for re-review.")
        for name in ("english", "telugu", "gondi_text"):
            if row.get(name, "")[:1] in ("=", "+", "@") or row.get(name, "").startswith("-="):
                rr.warnings.append(f"{name} starts with a formula character; it will be stored as plain text.")
        if row.get("speaker") and row["speaker"].strip().lower() not in speakers:
            rr.errors.append(f"Speaker '{row['speaker']}' is not registered; add the speaker first.")
        # ---- audio
        fname = row.get("audio_filename", "")
        if not fname:
            if rr.action == "create":
                rr.warnings.append("No audio_filename; entry will be imported without a recording.")
        elif "/" in fname or "\\" in fname or fname in (".", ".."):
            rr.errors.append("audio_filename must be a plain file name, not a path.")
        else:
            try:
                audio_svc.check_extension(fname)
                if PurePosixPath(fname).suffix == "":
                    raise audio_svc.AudioError("audio_filename needs an extension such as .mp3 or .wav")
            except audio_svc.AudioError as exc:
                rr.errors.append(str(exc))
            akey = fname.lower()
            if akey in seen_audio:
                rr.errors.append(f"audio_filename is also used on line {seen_audio[akey]}.")
            else:
                seen_audio[akey] = line
            member_name = fname if fname in members else lower_members.get(akey)
            if zip_raw is None:
                # Text-only import (e.g. recording prompts): audio is added later in the console.
                if rr.action == "create":
                    rr.warnings.append("No audio ZIP; imported without a recording (record or upload it later).")
            elif member_name is None:
                rr.errors.append(f"'{fname}' was not found in the ZIP.")
            else:
                used_members.add(member_name)
                if member_name != fname:
                    rr.warnings.append(f"Matched ZIP file '{member_name}' ignoring letter case.")
                if check_audio and not rr.errors:
                    try:
                        _, rr.duration = audio_svc.validate_audio_bytes(_read_member(zf, members[member_name]), member_name)
                    except audio_svc.AudioError as exc:
                        rr.errors.append(f"Audio problem: {exc}.")
                row["__member__"] = member_name
        if rr.action == "update" and not rr.changes and not row.get("__member__"):
            rr.warnings.append("No changes for this entry.")
        if rr.ok:
            accepted.append((rr, row))

    report.mode = mode
    report.total = len(report.rows)
    report.accepted = len(accepted)
    report.rejected = report.total - report.accepted
    report.creates = sum(1 for rr, _ in accepted if rr.action == "create")
    report.updates = sum(1 for rr, _ in accepted if rr.action == "update")
    report.unused_audio = sorted(set(members) - used_members)
    if report.unused_audio:
        report.warnings.append(f"{len(report.unused_audio)} file(s) in the ZIP are not referenced by the CSV.")
    if zip_raw is None and any(r.audio_filename for r in report.rows):
        report.warnings.append("No audio ZIP was uploaded: entries are imported as text only. "
                               "Add recordings later from the Expressions page, or import again with a ZIP in update mode.")
    return report, accepted, (zf, members)


# --------------------------------------------------------------------------- staging
def _job_dir(token: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,64}", token):
        raise ImportError_("Invalid import token.")
    return get_settings().imports_dir / token


def dry_run(db: Session, user: User, csv_name: str, csv_raw: bytes, zip_name: str, zip_raw: bytes | None,
            mode: str = "create") -> ImportJob:
    report, _, (zf, _) = validate(db, csv_raw, zip_raw, mode=mode, user=user)
    if zf is not None:
        zf.close()
    token = secrets.token_urlsafe(24)
    d = _job_dir(token)
    d.mkdir(parents=True)
    (d / "data.csv").write_bytes(csv_raw)
    if zip_raw:
        (d / "audio.zip").write_bytes(zip_raw)
    job = ImportJob(
        token=token,
        filename=csv_name[:255],
        zip_filename=(zip_name or "")[:255],
        status="previewed",
        total=report.total,
        accepted=report.accepted,
        rejected=report.rejected,
        report=json.dumps(report.to_dict(), ensure_ascii=False),
        created_by=user.id,
    )
    db.add(job)
    db.commit()
    return job


def _provenance(job: ImportJob, rr: RowResult, row: dict) -> str:
    text = f"Bulk import #{job.id} ({job.filename}), line {rr.line}"
    if row.get("review_status"):
        text += f". Source status: {row['review_status']}"
    if row.get("recording_notes"):
        text += f". Recording notes: {row['recording_notes']}"
    return text[:2000]


def commit(db: Session, user: User, job: ImportJob, *, skip_invalid: bool = False) -> ImportJob:
    if job.status != "previewed":
        raise ImportError_(f"This import is already {job.status}.")
    d = _job_dir(job.token)
    if not (d / "data.csv").exists():
        raise ImportError_("Staged files for this import are no longer available; run the preview again.")
    csv_raw = (d / "data.csv").read_bytes()
    zip_raw = (d / "audio.zip").read_bytes() if (d / "audio.zip").exists() else None
    mode = json.loads(job.report or "{}").get("mode", "create")

    report, accepted, (zf, members) = validate(db, csv_raw, zip_raw, check_audio=False, mode=mode, user=user)
    if report.rejected and not skip_invalid:
        raise ImportError_(
            f"{report.rejected} row(s) have errors. Fix the CSV/ZIP and preview again, "
            "or choose to import only the valid rows."
        )
    if not accepted:
        raise ImportError_("There are no valid rows to import.")

    from app.services.workflow import LINGUISTIC_FIELDS, note_content_change

    categories = {c.name.lower(): c for c in db.scalars(select(Category)).all()}
    next_order = (db.scalar(select(func.max(Category.sort_order))) or 0) + 1
    speakers = {s.display_name.strip().lower(): s for s in db.scalars(select(Speaker)).all()}
    written: list[str] = []
    late_errors: list[str] = []

    def category_for(name: str) -> Category:
        nonlocal next_order
        cat = categories.get(name.lower())
        if cat is None:
            cat = Category(name=name, sort_order=next_order)
            next_order += 1
            db.add(cat)
            db.flush()
            categories[name.lower()] = cat
        return cat

    try:
        for rr, row in accepted:
            if rr.action == "update":
                expr = db.scalar(select(Expression).where(
                    func.lower(Expression.expression_code) == row["expression_id"].lower()))
                changed = []
                for f in UPDATABLE:
                    if row.get(f) and row[f] != getattr(expr, f):
                        setattr(expr, f, row[f])
                        changed.append(f)
                if row.get("category"):
                    cat = category_for(row["category"])
                    if expr.category_id != cat.id:
                        expr.category_id = cat.id
                        changed.append("category")
                if changed:
                    expr.updated_by = user.id
                    note_content_change(db, expr, user, [c for c in changed if c in LINGUISTIC_FIELDS])
                    db.add(Review(expression_id=expr.id, reviewer_id=user.id, decision="updated",
                                  comments=f"Updated {', '.join(changed)} from {job.filename} line {rr.line}."))
                make_default = expr.status in (ExpressionStatus.draft.value, ExpressionStatus.in_review.value)
            else:
                expr = Expression(
                    expression_code=row["expression_id"],
                    gondi_text=row.get("gondi_text", ""),
                    transliteration=row.get("transliteration", ""),
                    telugu=row.get("telugu", ""),
                    english=row.get("english", ""),
                    category_id=category_for(row["category"]).id,
                    variety=row.get("variety", ""),
                    script=row.get("script", ""),
                    usage_note=row.get("usage_note", ""),
                    provenance=_provenance(job, rr, row),
                    status=ExpressionStatus.draft.value,
                    created_by=user.id,
                    updated_by=user.id,
                )
                db.add(expr)
                db.flush()
                db.add(Review(expression_id=expr.id, reviewer_id=user.id, decision="imported",
                              comments=f"Imported from {job.filename} line {rr.line}."))
                make_default = True
            member = row.get("__member__")
            if member:
                try:
                    stored = audio_svc.store_audio(_read_member(zf, members[member]), member)
                except audio_svc.AudioError as exc:
                    late_errors.append(f"line {rr.line}: {exc}")
                    raise
                written.extend(n for n in (stored.stored_filename, stored.source_filename) if n)
                spk = speakers.get(row.get("speaker", "").strip().lower()) if row.get("speaker") else None
                if make_default:
                    for r in db.scalars(select(Recording).where(Recording.expression_id == expr.id)):
                        r.is_default = False
                db.add(Recording(
                    expression_id=expr.id,
                    speaker_id=spk.id if spk else None,
                    stored_filename=stored.stored_filename,
                    source_filename=stored.source_filename,
                    original_filename=member[:255],
                    mime_type=stored.mime_type,
                    size_bytes=stored.size_bytes,
                    duration=stored.duration,
                    status=RecordingStatus.pending.value,
                    is_default=make_default,
                    uploaded_by=user.id,
                ))
        job.status = "committed"
        job.accepted = len(accepted)
        job.rejected = report.rejected
        job.committed_at = utcnow()
        rep = report.to_dict()
        rep["committed_rows"] = [rr.expression_id for rr, _ in accepted]
        job.report = json.dumps(rep, ensure_ascii=False)
        db.commit()
    except Exception as exc:
        db.rollback()
        audio_svc.delete_audio(*written)
        if late_errors:
            raise ImportError_("Import cancelled, nothing was saved: " + "; ".join(late_errors)) from exc
        raise
    finally:
        if zf is not None:
            zf.close()
    shutil.rmtree(d, ignore_errors=True)
    return job


def discard(db: Session, job: ImportJob) -> None:
    shutil.rmtree(_job_dir(job.token), ignore_errors=True)
    if job.status == "previewed":
        job.status = "discarded"
    db.commit()
