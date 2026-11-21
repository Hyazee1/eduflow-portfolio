from sqlalchemy import select
from sqlalchemy.orm import Session

from eduflow.adapters.background import notify_backup_failure, relay_outbox
from eduflow.adapters.models import Notification, OutboxEvent


def test_repeated_backup_failures_notify_only_the_own_administrator_once(pg):
    factory = pg.platform.factory
    notify_backup_failure(factory, "OSError")
    notify_backup_failure(factory, "TimeoutError")
    with Session(pg.engine) as session:
        events = session.scalars(select(OutboxEvent)).all()
        assert len(events) == 1
        assert events[0].payload["user_ids"] == [pg.admin.id]
        assert events[0].organization_id == pg.admin.organization_id
    assert relay_outbox(factory, None) == 1
    assert relay_outbox(factory, None) == 0
    with Session(pg.engine) as session:
        notifications = session.scalars(select(Notification)).all()
        assert len(notifications) == 1
        assert notifications[0].user_id == pg.admin.id
        assert notifications[0].title == "本机备份失败"
