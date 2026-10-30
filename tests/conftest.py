"""Test fixtures. Each test gets a fresh SQLite database and storage folder."""
from __future__ import annotations

import io
import math
import os
import shutil
import struct
import subprocess
import tempfile
import wave
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="gv-tests-"))
os.environ["GV_STORAGE_DIR"] = str(_TMP / "storage")
os.environ["GV_DATABASE_URL"] = f"sqlite:///{_TMP / 'test.db'}"
os.environ["GV_ENVIRONMENT"] = "test"
os.environ.setdefault("GV_COOKIE_SECURE", "true")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Role, Speaker, User  # noqa: E402
from app.security.passwords import hash_password  # noqa: E402

PASSWORD = "Correct-Horse-42"


def make_wav(seconds: float = 1.0, freq: float = 440.0, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = b"".join(
            struct.pack("<h", int(8000 * math.sin(2 * math.pi * freq * i / rate))) for i in range(int(rate * seconds))
        )
        w.writeframes(frames)
    return buf.getvalue()


def ffmpeg_encode(fmt: str, seconds: float = 1.0) -> bytes | None:
    if not shutil.which("ffmpeg"):
        return None
    codec = {"mp3": ["-codec:a", "libmp3lame"], "webm": ["-codec:a", "libopus"], "m4a": ["-codec:a", "aac"]}[fmt]
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / f"x.{fmt}"
        proc = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                               *codec, str(out)], capture_output=True)
        return out.read_bytes() if proc.returncode == 0 else None


@pytest.fixture(autouse=True)
def fresh_db():
    s = get_settings()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    shutil.rmtree(s.storage_dir, ignore_errors=True)
    for d in (s.storage_dir, s.audio_dir, s.imports_dir, s.backups_dir):
        d.mkdir(parents=True, exist_ok=True)
    yield


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


def create_user(username: str, role: Role) -> User:
    with SessionLocal() as db:
        u = User(username=username, full_name=username.title(), role=role.value,
                 password_hash=hash_password(PASSWORD), active=True)
        db.add(u)
        db.commit()
        return u


class Staff:
    """A logged-in TestClient that adds the CSRF header automatically."""

    def __init__(self, username: str, role: Role):
        create_user(username, role)
        self.client = TestClient(app, base_url="https://testserver")
        r = self.client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
        assert r.status_code == 200, r.text
        self.csrf = r.json()["csrf_token"]

    def _h(self, kw):
        kw.setdefault("headers", {})["X-CSRF-Token"] = self.csrf
        return kw

    def get(self, url, **kw):
        return self.client.get(url, **kw)

    def post(self, url, **kw):
        return self.client.post(url, **self._h(kw))

    def patch(self, url, **kw):
        return self.client.patch(url, **self._h(kw))

    def put(self, url, **kw):
        return self.client.put(url, **self._h(kw))

    def delete(self, url, **kw):
        return self.client.delete(url, **self._h(kw))


@pytest.fixture
def admin():
    return Staff("admin1", Role.admin)


@pytest.fixture
def contributor():
    return Staff("contrib1", Role.contributor)


@pytest.fixture
def reviewer():
    return Staff("review1", Role.reviewer)


@pytest.fixture
def public():
    return TestClient(app, base_url="https://testserver")


@pytest.fixture
def consenting_speaker() -> int:
    from datetime import date

    with SessionLocal() as db:
        s = Speaker(display_name="Test Speaker", variety="Adilabad", consent_scope="BRAOU educational use",
                    consent_date=date(2026, 1, 15), attribution="Voice: Test Speaker")
        db.add(s)
        db.commit()
        return s.id
