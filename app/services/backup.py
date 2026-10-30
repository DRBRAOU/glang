"""Backup and restore of the database and audio files.

A backup is a single .tar.gz containing:
  gondi_vaani.db   - consistent SQLite snapshot (online backup API)
  audio/...        - every stored recording
  manifest.json    - counts and timestamp
"""
from __future__ import annotations

import json
import sqlite3
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path

from app.config import get_settings


def _sqlite_path() -> Path:
    url = get_settings().database_url
    if not url.startswith("sqlite:///"):
        raise RuntimeError("Built-in backup supports SQLite; use your database's own tools otherwise.")
    return Path(url.removeprefix("sqlite:///"))


def create_backup(dest_dir: Path | None = None) -> Path:
    settings = get_settings()
    dest_dir = dest_dir or settings.backups_dir
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = dest_dir / f"gondi-vaani-backup-{stamp}.tar.gz"
    with tempfile.TemporaryDirectory(dir=settings.storage_dir) as tmp:
        snap = Path(tmp) / "gondi_vaani.db"
        src = sqlite3.connect(_sqlite_path())
        dst = sqlite3.connect(snap)
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
        audio_files = sorted(p for p in settings.audio_dir.iterdir() if p.is_file())
        manifest = {
            "app": settings.app_name,
            "created": datetime.now().isoformat(timespec="seconds"),
            "audio_files": len(audio_files),
            "format": 1,
        }
        (Path(tmp) / "manifest.json").write_text(json.dumps(manifest, indent=2))
        with tarfile.open(out, "w:gz") as tar:
            tar.add(Path(tmp) / "manifest.json", arcname="manifest.json")
            tar.add(snap, arcname="gondi_vaani.db")
            for p in audio_files:
                tar.add(p, arcname=f"audio/{p.name}")
    out.chmod(0o600)
    return out


def list_backups() -> list[dict]:
    d = get_settings().backups_dir
    return [
        {"name": p.name, "size_bytes": p.stat().st_size,
         "created": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")}
        for p in sorted(d.glob("gondi-vaani-backup-*.tar.gz"), reverse=True)
    ]


def backup_path(name: str) -> Path:
    d = get_settings().backups_dir.resolve()
    p = (d / name).resolve()
    if p.parent != d or not p.name.startswith("gondi-vaani-backup-") or not p.exists():
        raise FileNotFoundError(name)
    return p


def restore_backup(archive: Path) -> dict:
    """Restore DB and audio from an archive. Run only while the app is stopped."""
    settings = get_settings()
    db_path = _sqlite_path()
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        names = {m.name for m in members}
        if "gondi_vaani.db" not in names or "manifest.json" not in names:
            raise ValueError("Not a Gondi Vaani backup archive.")
        for m in members:
            if not m.isfile() or m.name.startswith("/") or ".." in Path(m.name).parts:
                raise ValueError(f"Unsafe entry in archive: {m.name}")
            if not (m.name in ("gondi_vaani.db", "manifest.json") or
                    (m.name.startswith("audio/") and m.name.count("/") == 1)):
                raise ValueError(f"Unexpected entry in archive: {m.name}")
        # Keep a safety copy of the current state first.
        safety = create_backup(settings.backups_dir) if db_path.exists() else None
        restored_audio = 0
        for m in members:
            fh = tar.extractfile(m)
            if fh is None:
                continue
            if m.name == "gondi_vaani.db":
                tmp = db_path.with_suffix(".restoring")
                tmp.write_bytes(fh.read())
                for suffix in ("-wal", "-shm"):
                    Path(str(db_path) + suffix).unlink(missing_ok=True)
                tmp.replace(db_path)
            elif m.name.startswith("audio/"):
                (settings.audio_dir / Path(m.name).name).write_bytes(fh.read())
                restored_audio += 1
    return {"restored_audio": restored_audio, "safety_backup": str(safety) if safety else None}
