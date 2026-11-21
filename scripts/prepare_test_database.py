"""Create only the dedicated test database; never drop or reset the application database."""
# ruff: noqa: E402 -- project imports intentionally follow the standalone path bootstrap.

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from eduflow.bootstrap.settings import Settings


def test_database_url() -> str:
    application = make_url(Settings().database_url)
    target = make_url(
        os.environ.get("EDUFLOW_TEST_DATABASE_URL")
        or application.set(database="eduflow_ai_test").render_as_string(hide_password=False)
    )
    if (
        not target.database
        or not target.database.endswith("_test")
        or target.database == application.database
    ):
        raise RuntimeError("拒绝测试数据库操作：名称必须以 _test 结尾，且不能是应用数据库。")
    if target.get_backend_name() != "postgresql":
        raise RuntimeError("集成验收必须使用真实 PostgreSQL。")
    return target.render_as_string(hide_password=False)


def main() -> None:
    target = make_url(test_database_url())
    engine = create_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            exists = connection.scalar(
                text("SELECT 1 FROM pg_database WHERE datname=:name"), {"name": target.database}
            )
            if not exists:
                identifier = connection.dialect.identifier_preparer.quote_identifier(target.database)
                connection.exec_driver_sql("CREATE DATABASE " + identifier)
        print("Dedicated PostgreSQL test database ready: " + target.database)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
