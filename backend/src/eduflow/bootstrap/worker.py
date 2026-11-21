import json
import logging
from datetime import UTC, datetime
from typing import Any

from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_ready
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from eduflow.adapters.background import (
    mark_job_failed,
    notify_backup_failure,
    prepare_job_retry,
    relay_outbox,
    schedule_reminders,
)
from eduflow.adapters.cp_sat import CpSatScheduler
from eduflow.adapters.repository import SqlUnitOfWork
from eduflow.adapters.system import SystemClock
from eduflow.application.scheduling import RunSolveJob
from eduflow.bootstrap.settings import Settings

settings = Settings(portal="WORKER")
engine = create_engine(settings.database_url, pool_pre_ping=True)
factory = sessionmaker(engine, expire_on_commit=False)
celery_app = Celery("eduflow", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    result_expires=3600,
    broker_connection_retry_on_startup=True,
    beat_schedule={
        "outbox-relay": {"task": "eduflow.outbox", "schedule": 3.0},
        "reminders": {"task": "eduflow.reminders", "schedule": 60.0},
        "heartbeat": {"task": "eduflow.heartbeat", "schedule": 15.0},
        "daily-local-backup": {
            "task": "eduflow.backup",
            "schedule": crontab(hour=3, minute=0),
            "kwargs": {"force": True},
        },
    },
)
runner = RunSolveJob(lambda org: SqlUnitOfWork(factory, org), SystemClock(), CpSatScheduler())


@celery_app.task(name="eduflow.solve", bind=True, max_retries=3)
def solve(self: Any, organization_id: str, job_id: str) -> dict[str, str]:
    client = Redis.from_url(settings.redis_url)
    lock = client.lock("eduflow:solver:capacity", timeout=900, blocking_timeout=0)
    if not lock.acquire(blocking=False):
        client.close()
        raise self.retry(countdown=5, max_retries=100000)
    try:
        if self.request.retries:
            prepare_job_retry(factory, organization_id, job_id)
        result = runner.execute(organization_id, job_id)
        return {"id": job_id, "status": result["status"]}
    except (OperationalError, ConnectionError, TimeoutError) as error:
        if self.request.retries < self.max_retries:
            raise self.retry(exc=error, countdown=min(60, 5 * 2**self.request.retries))
        mark_job_failed(factory, organization_id, job_id, type(error).__name__)
        raise
    except Exception as error:
        logging.getLogger(__name__).error("Solver task failed: job=%s type=%s", job_id, type(error).__name__)
        mark_job_failed(factory, organization_id, job_id, type(error).__name__)
        raise
    finally:
        if lock.owned():
            lock.release()
        client.close()


@celery_app.task(name="eduflow.outbox", autoretry_for=(ConnectionError,), retry_backoff=True, max_retries=5)
def outbox() -> int:
    return relay_outbox(factory, celery_app)


@celery_app.task(name="eduflow.reminders")
def reminders() -> int:
    return schedule_reminders(factory)


@celery_app.task(name="eduflow.heartbeat")
def heartbeat() -> bool:
    Redis.from_url(settings.redis_url).set("eduflow:worker:heartbeat", datetime.now(UTC).isoformat(), ex=60)
    return True


@worker_ready.connect
def enqueue_backup_on_startup(**kwargs: Any) -> None:
    if settings.backup_key_file is not None:
        celery_app.send_task("eduflow.backup", countdown=3)


@celery_app.task(
    name="eduflow.backup", autoretry_for=(RuntimeError, RedisError), retry_backoff=True, max_retries=3
)
def local_backup(force: bool = False) -> dict[str, str | int]:
    from eduflow.adapters.backup import backup_database, backup_due

    if settings.backup_key_file is None:
        return {"status": "NOT_CONFIGURED"}
    directory = settings.private_dir / "backups"
    client = Redis.from_url(settings.redis_url)
    lock = client.lock("eduflow:backup:capacity", timeout=600, blocking_timeout=0)
    try:
        if not lock.acquire(blocking=False):
            return {"status": "ALREADY_RUNNING"}
        if not force and not backup_due(directory, settings.backup_key_file):
            return {"status": "RECENT_BACKUP_VERIFIED"}
        result = backup_database(settings.database_url, directory, settings.backup_key_file, purpose="daily")
        health = {"status": "SUCCESS", "last_success": datetime.now(UTC).isoformat(), **result}
        (directory / "health.tmp").write_text(json.dumps(health), encoding="utf-8")
        (directory / "health.tmp").replace(directory / "health.json")
        return result
    except Exception as error:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "health.json").write_text(
            json.dumps(
                {"status": "FAILED", "error_type": type(error).__name__, "at": datetime.now(UTC).isoformat()}
            ),
            encoding="utf-8",
        )
        try:
            notify_backup_failure(factory, type(error).__name__)
        except Exception as notice_error:
            logging.getLogger(__name__).warning("Backup alert deferred: type=%s", type(notice_error).__name__)
        raise RuntimeError("Local encrypted backup failed; existing archives retained") from None
    finally:
        if lock.owned():
            lock.release()
        client.close()
