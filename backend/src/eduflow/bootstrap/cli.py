import argparse
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from argon2 import PasswordHasher
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from eduflow.adapters.demo import next_week, seed_demo
from eduflow.adapters.initialization import initialize_blank
from eduflow.adapters.models import Organization, User
from eduflow.adapters.repository import SqlRecords
from eduflow.bootstrap.settings import Settings
from eduflow.domain.time_rules import SHANGHAI, Rules, validate_templates


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=["migrate", "initialize", "seed", "check", "blank", "backup", "restore", "full-day-demo"],
    )
    parser.add_argument("--organization-name", default="EduFlow 教学机构")
    parser.add_argument("--admin-username", default="admin")
    parser.add_argument("--password-file", type=Path)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--target")
    parser.add_argument("--source-week", type=date.fromisoformat)
    parser.add_argument("--week", type=date.fromisoformat)
    args = parser.parse_args()
    settings = Settings()
    if args.action == "initialize":
        args.action = "seed" if settings.initialization_mode == "demo" else "blank"
        args.organization_name = settings.institution_name
        args.admin_username = settings.initial_admin_username
        args.password_file = settings.initial_admin_password_file
    if args.action in {"backup", "restore"}:
        from eduflow.adapters.backup import backup_database, restore_database

        if settings.backup_key_file is None:
            raise RuntimeError("A separately stored private backup key is required")
        if args.action == "backup":
            print(
                backup_database(
                    settings.database_url, settings.private_dir / "backups", settings.backup_key_file
                )
            )
        else:
            if args.archive is None or not args.target:
                raise RuntimeError(
                    "Restore requires --archive and a NEW --target *_test or *_restored database"
                )
            print(
                restore_database(settings.database_url, args.archive, settings.backup_key_file, args.target)
            )
        return
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    if args.action == "migrate":
        backend = Path(__file__).resolve().parents[3]
        config = Config(str(backend / "alembic.ini"))
        command.upgrade(config, "head")
        print("Database migrations applied.")
    elif args.action == "blank":
        with Session(engine) as session, session.begin():
            # An existing institution needs no bootstrap password on later restarts.
            if session.scalar(select(Organization.id).where(Organization.slug == settings.organization_slug)):
                print("Existing institution preserved.")
                return
            if args.password_file is None:
                raise RuntimeError("Blank initialization requires --password-file pointing to a private file")
            changed = initialize_blank(
                session,
                settings.organization_slug,
                args.organization_name,
                args.admin_username,
                args.password_file.read_text(encoding="utf-8").strip(),
            )
        print("Blank institution initialized." if changed else "Existing institution preserved.")
    elif args.action == "full-day-demo":
        from eduflow.adapters.demo_week import create_full_day_demo

        if settings.initialization_mode != "demo":
            raise RuntimeError("独立演示周只允许在 demo 初始化模式创建")
        with Session(engine) as session, session.begin():
            org = session.scalar(select(Organization).where(Organization.slug == settings.organization_slug))
            if org is None:
                raise RuntimeError("演示机构尚未初始化")
            print(create_full_day_demo(session, org.id, args.source_week, args.week))
    elif args.action == "seed":
        with Session(engine) as session, session.begin():
            session.execute(text("SELECT pg_advisory_xact_lock(27112026)"))
            seeded = seed_demo(
                session,
                next_week(datetime.now(SHANGHAI).date()),
                PasswordHasher().hash(settings.demo_password),
            )
        print("Demo data initialized." if seeded else "Existing data preserved; initialization skipped.")
    else:
        with Session(engine) as session:
            org = session.scalar(select(Organization).where(Organization.slug == settings.organization_slug))
            if not org:
                raise RuntimeError("Database has not been initialized. Run setup.ps1.")
            records = SqlRecords(session, org.id)
            snapshot = records.snapshot(next_week(datetime.now(SHANGHAI).date()))
            validate_templates(snapshot.slots)
            print(
                {
                    "students": len(snapshot.students),
                    "teachers": len(snapshot.teachers),
                    "rooms": len(snapshot.rooms),
                    "slots": [asdict(s) for s in snapshot.slots],
                    "rules": asdict(Rules(**org.rules)),
                }
            )
            assert session.scalar(select(User.id).where(User.role == "ADMIN")) is not None
    engine.dispose()


if __name__ == "__main__":
    main()
