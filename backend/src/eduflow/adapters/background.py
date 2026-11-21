from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from eduflow.adapters.models import (
    Confirmation,
    CourseSession,
    Notification,
    Organization,
    OutboxEvent,
    ScheduleVersion,
    SessionEnrollment,
    SolveJob,
    User,
    new_id,
)
from eduflow.domain.time_rules import SHANGHAI

logger = logging.getLogger(__name__)


def notify_backup_failure(factory: sessionmaker[Session], error_type: str) -> None:
    today = datetime.now(SHANGHAI).date()
    with factory() as session, session.begin():
        for org in session.scalars(select(Organization)):
            admins = list(
                session.scalars(
                    select(User.id).where(
                        User.organization_id == org.id, User.role == "ADMIN", User.active.is_(True)
                    )
                )
            )
            add_reminder(
                session,
                org.id,
                f"backup:failed:{today}",
                admins,
                "本机备份失败",
                f"自动重试后请检查私有备份状态。错误类型：{error_type}。已有备份保留。",
                "/notifications",
            )


def prepare_job_retry(factory: sessionmaker[Session], organization_id: str, job_id: str) -> None:
    with factory() as session, session.begin():
        job = session.scalar(
            select(SolveJob)
            .where(
                SolveJob.organization_id == organization_id,
                SolveJob.id == job_id,
            )
            .with_for_update()
        )
        if job and job.status == "RUNNING":
            job.status = "QUEUED"
            job.version += 1


def relay_outbox(factory: sessionmaker[Session], publisher: Any) -> int:
    now = datetime.now(UTC)
    delivered = 0
    with factory() as session, session.begin():
        events = session.scalars(
            select(OutboxEvent)
            .where(
                OutboxEvent.delivered_at.is_(None),
                (OutboxEvent.next_attempt_at.is_(None) | (OutboxEvent.next_attempt_at <= now)),
            )
            .order_by(OutboxEvent.created_at)
            .limit(100)
            .with_for_update(skip_locked=True)
        ).all()
        for event in events:
            try:
                with session.begin_nested():
                    if event.event_type == "SOLVE":
                        publisher.send_task(
                            "eduflow.solve",
                            args=[
                                event.organization_id,
                                event.payload["job_id"],
                            ],
                            task_id=event.id,
                        )
                    elif event.event_type == "NOTIFY":
                        for user_id in event.payload["user_ids"]:
                            user = session.get(User, user_id)
                            if not user or user.organization_id != event.organization_id:
                                raise ValueError("Notification recipient is outside organization")
                            session.execute(
                                insert(Notification)
                                .values(
                                    id=new_id(),
                                    organization_id=event.organization_id,
                                    user_id=user_id,
                                    title=event.payload["title"],
                                    content=event.payload["content"],
                                    link=event.payload.get("link", ""),
                                    dedup_key=f"{event.id}:{user_id}",
                                )
                                .on_conflict_do_nothing(index_elements=["user_id", "dedup_key"])
                            )
                    else:
                        raise ValueError("Unknown outbox event")
                    event.delivered_at = now
                    event.attempts += 1
                    event.last_error = ""
                    delivered += 1
            except Exception as error:
                # Transport boundary: durable exponential retry, no business transaction is undone.
                logger.warning("Outbox delivery failed: event=%s type=%s", event.id, type(error).__name__)
                event.attempts += 1
                event.last_error = type(error).__name__
                event.next_attempt_at = now + timedelta(seconds=min(3600, 2 ** min(event.attempts, 10)))
    return delivered


def add_reminder(
    session: Session,
    organization_id: str,
    dedup_key: str,
    user_ids: list[str],
    title: str,
    content: str,
    link: str,
) -> None:
    if user_ids:
        session.execute(
            insert(OutboxEvent)
            .values(
                id=new_id(),
                organization_id=organization_id,
                event_type="NOTIFY",
                dedup_key=dedup_key,
                payload={"user_ids": sorted(set(user_ids)), "title": title, "content": content, "link": link},
            )
            .on_conflict_do_nothing(index_elements=["organization_id", "dedup_key"])
        )


def schedule_reminders(factory: sessionmaker[Session], now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    count = 0
    with factory() as session, session.begin():
        pending = session.scalars(
            select(ScheduleVersion).where(
                ScheduleVersion.status == "PUBLISHED_PENDING_CONFIRMATION",
                ScheduleVersion.published_at <= now - timedelta(hours=12),
            )
        ).all()
        for version in pending:
            row_ids = list(
                session.scalars(select(CourseSession.id).where(CourseSession.version_id == version.id))
            )
            pending_users = list(
                session.scalars(
                    select(Confirmation.user_id).where(
                        Confirmation.session_id.in_(row_ids),
                        Confirmation.decision == "PENDING",
                    )
                )
            )
            add_reminder(
                session,
                version.organization_id,
                f"confirm:{version.id}:12h",
                pending_users,
                "课表确认提醒",
                "请查看并确认本人相关课次。",
                "/schedule",
            )
            if version.confirmation_deadline and now > version.confirmation_deadline:
                admins = list(
                    session.scalars(
                        select(User.id).where(
                            User.organization_id == version.organization_id,
                            User.role == "ADMIN",
                            User.active.is_(True),
                        )
                    )
                )
                add_reminder(
                    session,
                    version.organization_id,
                    f"confirm:{version.id}:overdue",
                    admins,
                    "课表确认超时",
                    f"版本 {version.id} 尚有 {len(set(pending_users))} 人未确认。",
                    "/scheduling",
                )
            count += 1
        today = now.astimezone(SHANGHAI).date()
        for version in session.scalars(select(ScheduleVersion).where(ScheduleVersion.status == "ACTIVE")):
            lessons = session.scalars(
                select(CourseSession).where(
                    CourseSession.version_id == version.id,
                    CourseSession.day == today,
                    CourseSession.status == "SCHEDULED",
                )
            ).all()
            for lesson in lessons:
                students = list(
                    session.scalars(
                        select(SessionEnrollment.student_id).where(
                            SessionEnrollment.session_id == lesson.id,
                        )
                    )
                )
                add_reminder(
                    session,
                    version.organization_id,
                    f"daily:{lesson.id}",
                    [lesson.teacher_id, *students],
                    "今日课程提醒",
                    f"{lesson.day} {lesson.slot_code} {lesson.start_time}—{lesson.end_time}",
                    "/schedule",
                )
                end = datetime.fromisoformat(f"{lesson.day}T{lesson.end_time}:00").replace(tzinfo=SHANGHAI)
                if now >= end:
                    add_reminder(
                        session,
                        version.organization_id,
                        f"feedback:{lesson.id}",
                        [lesson.teacher_id],
                        "出勤与课程反馈提醒",
                        "课程已结束，请完成出勤和教学反馈。",
                        "/classes",
                    )
        # Recover jobs after a worker crash. At-least-once jobs remain idempotent at completion.
        for job in session.scalars(
            select(SolveJob).where(
                SolveJob.status == "RUNNING",
                SolveJob.started_at < now - timedelta(minutes=6),
            )
        ):
            job.status = "QUEUED"
            add_key = f"recover:{job.id}:{int(now.timestamp()) // 360}"
            session.execute(
                insert(OutboxEvent)
                .values(
                    id=new_id(),
                    organization_id=job.organization_id,
                    event_type="SOLVE",
                    dedup_key=add_key,
                    payload={"job_id": job.id},
                )
                .on_conflict_do_nothing(index_elements=["organization_id", "dedup_key"])
            )
    return count


def mark_job_failed(factory: sessionmaker[Session], organization_id: str, job_id: str, reason: str) -> None:
    with factory() as session, session.begin():
        job = session.scalar(
            select(SolveJob)
            .where(SolveJob.id == job_id, SolveJob.organization_id == organization_id)
            .with_for_update()
        )
        if job and job.status not in {"COMPLETED", "STALE"}:
            job.status = "FAILED"
            job.error = reason[:500]
            job.completed_at = datetime.now(UTC)


def organization_ids(factory: sessionmaker[Session]) -> list[str]:
    with factory() as session:
        return list(session.scalars(select(Organization.id)))
