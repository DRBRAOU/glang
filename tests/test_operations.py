"""Backups, reports, migrations, pages."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect

from app.database import Base
from tests.conftest import make_wav

ROOT = Path(__file__).resolve().parent.parent


def test_backup_and_restore_roundtrip(admin, tmp_path):
    from app.services.backup import create_backup, restore_backup

    e = admin.post("/api/admin/expressions", json={"expression_code": "GON-001", "english": "Hello"}).json()
    admin.post(f"/api/admin/expressions/{e['id']}/recordings", files={"file": ("a.wav", make_wav(), "audio/wav")})
    archive = create_backup(tmp_path)
    assert archive.exists() and archive.stat().st_size > 1000

    # damage current state
    admin.post(f"/api/admin/expressions/{e['id']}/transition", json={"action": "archive"})
    admin.delete(f"/api/admin/expressions/{e['id']}")
    assert admin.get("/api/admin/expressions?status=archived").json() == []

    result = restore_backup(archive)
    assert result["restored_audio"] == 1 and result["safety_backup"]
    from app.database import engine

    engine.dispose()
    restored = admin.get("/api/admin/expressions").json()
    assert [x["expression_code"] for x in restored] == ["GON-001"]


def test_backup_api(admin):
    r = admin.post("/api/admin/backups")
    assert r.status_code == 201
    name = r.json()["name"]
    assert admin.get("/api/admin/backups").json()[0]["name"] == name
    dl = admin.get(f"/api/admin/backups/{name}")
    assert dl.status_code == 200 and dl.content[:2] == b"\x1f\x8b"
    assert admin.get("/api/admin/backups/..%2F..%2Fetc%2Fpasswd").status_code == 404


def test_reports_summary(admin, reviewer):
    admin.post("/api/admin/expressions", json={"expression_code": "GON-001", "english": "Hello"})
    s = reviewer.get("/api/admin/reports/summary").json()
    assert s["expressions_by_status"]["draft"] == 1
    assert s["missing_gondi_text"] == 1 and s["without_audio"] == 1


def test_migrations_match_models(tmp_path):
    db = tmp_path / "mig.db"
    env = {**os.environ, "GV_DATABASE_URL": f"sqlite:///{db}"}
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env, check=True,
                   capture_output=True)
    insp = inspect(create_engine(f"sqlite:///{db}"))
    tables = set(insp.get_table_names()) - {"alembic_version"}
    assert tables == set(Base.metadata.tables)
    for name, table in Base.metadata.tables.items():
        cols = {c["name"] for c in insp.get_columns(name)}
        assert cols == {c.name for c in table.columns}, name


def test_pages_served(public):
    assert "Gondi Vaani" in public.get("/").text
    assert "Editorial console" in public.get("/admin").text
    assert public.get("/js/learner.js").status_code == 200
    assert public.get("/css/base.css").status_code == 200
    assert public.get("/fonts/noto-sans-telugu-telugu-400-normal.woff2").status_code == 200
    assert public.get("/healthz").json() == {"status": "ok"}


def test_cli_create_admin(tmp_path):
    from app.cli import main

    os.environ["GV_ADMIN_PASSWORD"] = "Strong-Admin-Pass-1"
    try:
        assert main(["create-admin", "--username", "Chief", "--name", "Chief Admin"]) == 0
        assert main(["create-admin", "--username", "chief"]) == 1  # duplicate
        assert main(["seed-categories"]) == 0
    finally:
        del os.environ["GV_ADMIN_PASSWORD"]
