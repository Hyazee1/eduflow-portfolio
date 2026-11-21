"""Create a clean institution without fictional people or shared credentials."""

from dataclasses import asdict
from uuid import uuid4

from argon2 import PasswordHasher
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from eduflow.adapters.models import Organization, TimeSlot, User
from eduflow.domain.time_rules import DEFAULT_SLOTS, Rules


def initialize_blank(session: Session, slug: str, name: str, username: str, password: str) -> bool:
    if len(password) < 12:
        raise ValueError("The administrator password must contain at least 12 characters")
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": "initialize:" + slug}
    )
    if session.scalar(select(Organization.id).where(Organization.slug == slug)):
        return False
    org = Organization(id=str(uuid4()), name=name, slug=slug, rules=asdict(Rules()), seeded=False)
    session.add(org)
    session.flush()
    session.add(
        User(
            organization_id=org.id,
            username=username,
            name="机构管理员",
            role="ADMIN",
            active=True,
            password_hash=PasswordHasher().hash(password),
        )
    )
    for slot in DEFAULT_SLOTS:
        session.add(TimeSlot(organization_id=org.id, **asdict(slot), duration_minutes=slot.minutes))
    session.flush()
    return True
