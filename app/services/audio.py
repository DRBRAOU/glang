"""Audio validation, probing, transcoding and storage.

Every uploaded file is:
  1. size-checked,
  2. identified by its file signature (magic bytes) - the client's MIME type is ignored,
  3. probed with ffprobe (or mutagen as fallback) to confirm it decodes and to read duration,
  4. stored under a random internal filename in storage/audio (never publicly served).

Browser recordings (WebM/Ogg/MP4) are converted to MP3 so they play on every
device (older iPhones cannot play WebM); the original is kept alongside.
"""
from __future__ import annotations

import json
import secrets
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings

ALLOWED_EXTENSIONS = {".mp3", ".wav", ".webm", ".ogg", ".oga", ".m4a", ".mp4"}

_EXT_FOR_KIND = {"mp3": ".mp3", "wav": ".wav", "webm": ".webm", "ogg": ".ogg", "mp4": ".m4a"}
MIME_FOR_KIND = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "webm": "audio/webm",
    "ogg": "audio/ogg",
    "mp4": "audio/mp4",
}


class AudioError(ValueError):
    """Raised when an audio file is rejected. Message is safe to show users."""


@dataclass
class StoredAudio:
    stored_filename: str
    source_filename: str
    mime_type: str
    size_bytes: int
    duration: float | None


def sniff_kind(data: bytes) -> str | None:
    """Identify audio container from its leading bytes."""
    if len(data) < 12:
        return None
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav"
    if data[:3] == b"ID3":
        return "mp3"
    if data[0] == 0xFF and (data[1] & 0xE0) == 0xE0 and (data[1] & 0x06) != 0:
        return "mp3"  # MPEG audio frame sync, layer bits non-zero
    if data[:4] == b"\x1aE\xdf\xa3":
        return "webm"
    if data[:4] == b"OggS":
        return "ogg"
    if data[4:8] == b"ftyp":
        return "mp4"
    return None


def ffmpeg_available() -> bool:
    return shutil.which("ffprobe") is not None and shutil.which("ffmpeg") is not None


def _ffprobe(path: Path) -> dict:
    proc = subprocess.run(
        [
            "ffprobe", "-v", "error", "-print_format", "json",
            "-show_entries", "format=duration,format_name:stream=codec_type,duration",
            str(path),
        ],
        capture_output=True, timeout=30, check=False,
    )
    if proc.returncode != 0:
        raise AudioError("The audio file could not be decoded; it may be damaged.")
    try:
        return json.loads(proc.stdout or b"{}")
    except json.JSONDecodeError as exc:  # pragma: no cover
        raise AudioError("The audio file could not be analysed.") from exc


def probe_duration(path: Path) -> float | None:
    """Return duration in seconds, None if unknown. Raises AudioError if undecodable."""
    if shutil.which("ffprobe"):
        info = _ffprobe(path)
        streams = info.get("streams") or []
        if not any(s.get("codec_type") == "audio" for s in streams):
            raise AudioError("The file contains no audio stream.")
        for candidate in [info.get("format", {}).get("duration")] + [s.get("duration") for s in streams]:
            try:
                if candidate not in (None, "N/A"):
                    return round(float(candidate), 2)
            except (TypeError, ValueError):
                continue
        return None
    try:  # fallback without ffmpeg
        import mutagen

        f = mutagen.File(str(path))
        if f is None or not getattr(f, "info", None):
            return None
        return round(float(f.info.length), 2)
    except Exception as exc:  # noqa: BLE001
        raise AudioError("The audio file could not be decoded; it may be damaged.") from exc


def _transcode_to_mp3(src: Path, dst: Path) -> None:
    proc = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(src), "-vn", "-ac", "1",
         "-ar", "44100", "-codec:a", "libmp3lame", "-b:a", "128k", str(dst)],
        capture_output=True, timeout=120, check=False,
    )
    if proc.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
        dst.unlink(missing_ok=True)
        raise AudioError("The recording could not be converted to MP3.")


def check_extension(filename: str) -> None:
    ext = Path(filename).suffix.lower()
    if ext and ext not in ALLOWED_EXTENSIONS:
        raise AudioError(f"Unsupported audio file type '{ext}'. Use MP3, WAV, WebM, Ogg or M4A.")


def store_audio(data: bytes, original_filename: str = "") -> StoredAudio:
    """Validate audio bytes and store them. Returns metadata. Raises AudioError."""
    settings = get_settings()
    if not data:
        raise AudioError("The audio file is empty.")
    if len(data) > settings.max_audio_bytes:
        raise AudioError(f"Audio exceeds the {settings.max_audio_bytes // (1024 * 1024)} MB limit.")
    if original_filename:
        check_extension(original_filename)
    kind = sniff_kind(data)
    if kind is None:
        raise AudioError("Unsupported or unrecognised audio format. Use MP3, WAV, WebM, Ogg or M4A.")

    audio_dir = settings.audio_dir
    audio_dir.mkdir(parents=True, exist_ok=True)
    stem = secrets.token_hex(16)
    src_path = audio_dir / f"{stem}{_EXT_FOR_KIND[kind]}"
    src_path.write_bytes(data)
    created = [src_path]
    try:
        duration = probe_duration(src_path)
        play_path, mime, source_name = src_path, MIME_FOR_KIND[kind], ""
        if kind in ("webm", "ogg", "mp4") and settings.transcode_recordings and ffmpeg_available():
            mp3_path = audio_dir / f"{stem}.mp3"
            _transcode_to_mp3(src_path, mp3_path)
            created.append(mp3_path)
            duration = probe_duration(mp3_path) or duration
            play_path, mime, source_name = mp3_path, "audio/mpeg", src_path.name
        if duration is not None and duration > settings.max_audio_seconds:
            raise AudioError(f"Audio is {duration:.0f}s long; the limit is {settings.max_audio_seconds:.0f}s.")
        if duration is not None and duration < 0.2:
            raise AudioError("Audio is too short to be a usable recording.")
        return StoredAudio(
            stored_filename=play_path.name,
            source_filename=source_name,
            mime_type=mime,
            size_bytes=play_path.stat().st_size,
            duration=duration,
        )
    except Exception:
        for p in created:
            p.unlink(missing_ok=True)
        raise


def audio_path(stored_filename: str) -> Path:
    """Resolve an internal filename safely inside the audio directory."""
    base = get_settings().audio_dir.resolve()
    p = (base / stored_filename).resolve()
    if p.parent != base:
        raise AudioError("Invalid audio path.")
    return p


def delete_audio(*filenames: str) -> None:
    for name in filenames:
        if name:
            try:
                audio_path(name).unlink(missing_ok=True)
            except AudioError:
                pass


def store_audio_from_tempfile(fileobj, original_filename: str) -> StoredAudio:
    """Read an uploaded file with a hard size cap before validation."""
    limit = get_settings().max_audio_bytes
    data = fileobj.read(limit + 1)
    if len(data) > limit:
        raise AudioError(f"Audio exceeds the {limit // (1024 * 1024)} MB limit.")
    return store_audio(data, original_filename)


def validate_audio_bytes(data: bytes, filename: str) -> tuple[str, float | None]:
    """Validate without keeping the file (used by import dry-run). Returns (kind, duration)."""
    settings = get_settings()
    if not data:
        raise AudioError("audio file is empty")
    if len(data) > settings.max_audio_bytes:
        raise AudioError(f"audio exceeds {settings.max_audio_bytes // (1024 * 1024)} MB")
    check_extension(filename)
    kind = sniff_kind(data)
    if kind is None:
        raise AudioError("file is not a recognised MP3/WAV/WebM/Ogg/M4A audio file")
    with tempfile.NamedTemporaryFile(suffix=_EXT_FOR_KIND[kind], dir=settings.imports_dir) as tmp:
        tmp.write(data)
        tmp.flush()
        duration = probe_duration(Path(tmp.name))
    if duration is not None and duration > settings.max_audio_seconds:
        raise AudioError(f"audio is {duration:.0f}s long; limit is {settings.max_audio_seconds:.0f}s")
    return kind, duration
