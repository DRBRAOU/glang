"""Application configuration, loaded from environment variables / .env."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="GV_", extra="ignore")

    app_name: str = "BRAOU Gondi Vaani"
    environment: str = "production"  # "development" relaxes cookie Secure flag

    # Database: SQLite by default; any SQLAlchemy URL works (schema is migration-ready).
    database_url: str = f"sqlite:///{BASE_DIR / 'storage' / 'gondi_vaani.db'}"

    # Storage (never served directly by the web server)
    storage_dir: Path = BASE_DIR / "storage"
    frontend_dir: Path = BASE_DIR / "frontend"

    # Sessions & cookies
    session_cookie_name: str = "gv_session"
    session_hours: int = 8
    cookie_secure: bool = True

    # Upload limits
    max_audio_bytes: int = 15 * 1024 * 1024        # 15 MB per recording
    max_audio_seconds: float = 120.0               # expressions are short
    max_csv_bytes: int = 2 * 1024 * 1024           # 2 MB
    max_zip_bytes: int = 300 * 1024 * 1024         # 300 MB
    max_zip_members: int = 2000
    max_zip_uncompressed_bytes: int = 600 * 1024 * 1024
    max_import_rows: int = 2000

    # Login protection
    login_max_failures: int = 5
    login_lockout_minutes: int = 15

    # Who may embed the learner page in a frame (e.g. your Moodle origin).
    # Space separated CSP sources, e.g. "'self' https://lms.braou.ac.in"
    frame_ancestors: str = "'self'"

    # Convert browser recordings (WebM/Ogg/MP4) to MP3 for universal playback
    transcode_recordings: bool = True

    @property
    def audio_dir(self) -> Path:
        return self.storage_dir / "audio"

    @property
    def imports_dir(self) -> Path:
        return self.storage_dir / "imports"

    @property
    def backups_dir(self) -> Path:
        return self.storage_dir / "backups"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    for d in (s.storage_dir, s.audio_dir, s.imports_dir, s.backups_dir):
        d.mkdir(parents=True, exist_ok=True)
    return s
