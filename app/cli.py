"""Command-line administration.

    python -m app.cli create-admin --username admin --name "Dr. ..."
    python -m app.cli reset-password --username admin
    python -m app.cli seed-categories
    python -m app.cli import-csv /data/prompts.csv --as admin [--zip audio.zip] [--update] [--commit]
    python -m app.cli backup
    python -m app.cli restore storage/backups/gondi-vaani-backup-YYYYMMDD-HHMMSS.tar.gz
    python -m app.cli list-users
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import Category, Role, User
from app.security.passwords import hash_password, password_problems

# Matches samples/gondi-vaani-100-prompts.csv (BRAOU recording prompts)
DEFAULT_CATEGORIES = [
    "Greetings", "Introductions", "Family", "Numbers", "Everyday Words",
    "Everyday Conversations", "Useful Sentences",
]


def _ask_password(env_var: str = "GV_ADMIN_PASSWORD") -> str:
    pw = os.environ.get(env_var)
    if pw:
        return pw
    while True:
        pw = getpass.getpass("New password: ")
        problems = password_problems(pw)
        if problems:
            print("  " + " ".join(problems))
            continue
        if getpass.getpass("Repeat password: ") != pw:
            print("  Passwords did not match.")
            continue
        return pw


def create_admin(args) -> int:
    username = args.username.strip().lower()
    with SessionLocal() as db:
        if db.scalar(select(User).where(func.lower(User.username) == username)):
            print(f"User '{username}' already exists. Use reset-password instead.")
            return 1
        pw = _ask_password()
        problems = password_problems(pw)
        if problems:
            print("Password rejected: " + " ".join(problems))
            return 1
        db.add(User(username=username, full_name=args.name or "", role=Role.admin.value,
                    password_hash=hash_password(pw), active=True))
        db.commit()
    print(f"Administrator '{username}' created.")
    return 0


def reset_password(args) -> int:
    username = args.username.strip().lower()
    with SessionLocal() as db:
        u = db.scalar(select(User).where(func.lower(User.username) == username))
        if not u:
            print(f"No user '{username}'.")
            return 1
        pw = _ask_password()
        problems = password_problems(pw)
        if problems:
            print("Password rejected: " + " ".join(problems))
            return 1
        u.password_hash = hash_password(pw)
        u.active = True
        db.commit()
    print(f"Password for '{username}' reset and account activated.")
    return 0


def list_users(_args) -> int:
    with SessionLocal() as db:
        for u in db.scalars(select(User).order_by(User.username)):
            print(f"{u.id:>4}  {u.username:<24} {u.role:<12} {'active' if u.active else 'disabled'}")
    return 0


def seed_categories(_args) -> int:
    with SessionLocal() as db:
        existing = {c.lower() for c in db.scalars(select(Category.name))}
        added = 0
        for i, name in enumerate(DEFAULT_CATEGORIES, start=1):
            if name.lower() not in existing:
                db.add(Category(name=name, sort_order=i * 10))
                added += 1
        db.commit()
    print(f"{added} categories added.")
    return 0


def import_csv(args) -> int:
    """Dry-run (and optionally commit) a CSV [+ ZIP] import from the command line."""
    import json

    from app.services import importer

    with SessionLocal() as db:
        user = db.scalar(select(User).where(func.lower(User.username) == args.as_user.lower()))
        if user is None or not user.active:
            print(f"No active user '{args.as_user}'.")
            return 1
        csv_path = Path(args.csv)
        zip_raw = Path(args.zip).read_bytes() if args.zip else None
        try:
            job = importer.dry_run(db, user, csv_path.name, csv_path.read_bytes(),
                                   Path(args.zip).name if args.zip else "", zip_raw,
                                   mode="update" if args.update else "create")
        except importer.ImportError_ as exc:
            print(f"Rejected: {exc}")
            return 1
        rep = json.loads(job.report)
        print(f"Preview #{job.id}: {job.total} rows, {job.accepted} valid "
              f"({rep.get('creates', 0)} new, {rep.get('updates', 0)} updates), {job.rejected} with errors.")
        for w in rep.get("warnings", []):
            print(f"  ! {w}")
        for row in rep["rows"]:
            for e in row["errors"]:
                print(f"  line {row['line']} {row['expression_id']}: {e}")
        if not args.commit:
            print("Dry run only. Add --commit to import.")
            return 0 if not job.rejected else 2
        try:
            job = importer.commit(db, user, job, skip_invalid=args.skip_invalid)
        except importer.ImportError_ as exc:
            print(f"Not imported: {exc}")
            return 1
        print(f"Imported {job.accepted} rows (new entries are drafts).")
    return 0


def backup(args) -> int:
    from app.services.backup import create_backup

    out = create_backup(Path(args.dest) if args.dest else None)
    print(f"Backup written: {out}")
    return 0


def restore(args) -> int:
    from app.services.backup import restore_backup

    if not args.yes:
        ans = input("Restore will REPLACE the current database and add audio files. Stop the app first. Continue? [y/N] ")
        if ans.strip().lower() != "y":
            print("Cancelled.")
            return 1
    result = restore_backup(Path(args.archive))
    print(f"Restored. Audio files: {result['restored_audio']}. Safety backup of previous state: {result['safety_backup']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.cli", description="Gondi Vaani administration")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("create-admin", help="create the first administrator")
    a.add_argument("--username", required=True)
    a.add_argument("--name", default="")
    a.set_defaults(func=create_admin)
    r = sub.add_parser("reset-password", help="reset a user's password")
    r.add_argument("--username", required=True)
    r.set_defaults(func=reset_password)
    sub.add_parser("list-users").set_defaults(func=list_users)
    sub.add_parser("seed-categories", help="add a starter set of categories").set_defaults(func=seed_categories)
    ic = sub.add_parser("import-csv", help="import a CSV (+ optional audio ZIP)")
    ic.add_argument("csv")
    ic.add_argument("--zip", default="")
    ic.add_argument("--as", dest="as_user", required=True, help="username recorded as the importer")
    ic.add_argument("--update", action="store_true", help="also update existing entries (blank cells keep values)")
    ic.add_argument("--commit", action="store_true", help="import after a clean dry run")
    ic.add_argument("--skip-invalid", action="store_true", help="import valid rows even if others have errors")
    ic.set_defaults(func=import_csv)
    b = sub.add_parser("backup", help="write a .tar.gz backup of database + audio")
    b.add_argument("--dest", default="")
    b.set_defaults(func=backup)
    rs = sub.add_parser("restore", help="restore from a backup archive (app must be stopped)")
    rs.add_argument("archive")
    rs.add_argument("--yes", action="store_true")
    rs.set_defaults(func=restore)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
