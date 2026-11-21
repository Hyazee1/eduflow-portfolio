from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from eduflow.adapters.models import ScheduleVersion
from eduflow.domain.errors import Conflict, StaleVersion

from .helpers import candidate, confirm_all, execute, publish

pytestmark = pytest.mark.integration


def expired(pg):
    previous = publish(pg, candidate(pg))
    pg.clock.current += timedelta(hours=25)
    return previous


def test_only_explicit_expired_reference_allows_new_solve_job(pg):
    previous = expired(pg)
    with pytest.raises(Conflict):
        execute(pg, "SolveWeek", {"week": str(pg.snapshot.week)})
    job = execute(
        pg,
        "SolveWeek",
        {
            "week": str(pg.snapshot.week),
            "optimization_profile": "preference_first",
            "reference_version_id": previous["id"],
        },
    )
    detail = pg.platform.queries.job(pg.admin, job["id"])
    assert detail["result"]["request_options"]["reference_version_id"] == previous["id"]
    with Session(pg.engine) as session:
        assert session.get(ScheduleVersion, previous["id"]).status == "PUBLISHED_PENDING_CONFIRMATION"


def test_atomic_replacement_remains_pending_until_normal_confirmations(pg):
    previous = expired(pg)
    new = candidate(pg)
    key = str(uuid4())
    payload = {
        "version_id": new["id"],
        "version": new["version"],
        "replace_expired_version_id": previous["id"],
        "replace_expired_version_revision": previous["version"],
    }
    result = execute(pg, "PublishVersion", payload, key=key)
    assert result["status"] == "PUBLISHED_PENDING_CONFIRMATION"
    assert execute(pg, "PublishVersion", payload, key=key) == result
    with Session(pg.engine) as session:
        assert session.get(ScheduleVersion, previous["id"]).status == "CANCELLED"
    assert confirm_all(pg, new["id"])["status"] == "ACTIVE"


def test_failed_replacement_rolls_back_old_version(pg):
    previous = expired(pg)
    new = candidate(pg)
    # Publishing an already-started week fails after the old row was tentatively cancelled.
    pg.clock.current += timedelta(days=5)
    with pytest.raises(Conflict, match="已开始"):
        execute(
            pg,
            "PublishVersion",
            {
                "version_id": new["id"],
                "version": new["version"],
                "replace_expired_version_id": previous["id"],
                "replace_expired_version_revision": previous["version"],
            },
        )
    with Session(pg.engine) as session:
        assert session.get(ScheduleVersion, previous["id"]).status == "PUBLISHED_PENDING_CONFIRMATION"
        assert session.get(ScheduleVersion, new["id"]).status == "VALIDATED"


def test_replacement_rejects_stale_revision(pg):
    previous = expired(pg)
    new = candidate(pg)
    with pytest.raises(StaleVersion):
        execute(
            pg,
            "PublishVersion",
            {
                "version_id": new["id"],
                "version": new["version"],
                "replace_expired_version_id": previous["id"],
                "replace_expired_version_revision": previous["version"] + 1,
            },
        )


def test_replacement_rejects_preference_regression(pg):
    previous = expired(pg)
    adjusted = replace(pg.lessons[0], slot_code="S2", start="08:55", end="09:40")
    new = candidate(pg, (adjusted, *pg.lessons[1:]))
    with pytest.raises(Conflict, match="低于原方案"):
        execute(
            pg,
            "PublishVersion",
            {
                "version_id": new["id"],
                "version": new["version"],
                "replace_expired_version_id": previous["id"],
                "replace_expired_version_revision": previous["version"],
            },
        )
    with Session(pg.engine) as session:
        assert session.get(ScheduleVersion, previous["id"]).status == "PUBLISHED_PENDING_CONFIRMATION"
