"""Test-only clock control; refuses to run against any application database."""

import json
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.engine import make_url

from eduflow.bootstrap.app import create_app
from eduflow.bootstrap.settings import ROOT, Settings


class FileClock:
    def now(self) -> datetime:
        return datetime.fromisoformat(json.loads((ROOT / ".runtime/e2e-clock.json").read_text())["now"])


def _create_e2e(portal):
    settings = Settings(
        portal=portal,
        admin_portal_origin="http://127.0.0.1:8001",
        public_portal_origin="http://127.0.0.1:8101",
        _env_file=ROOT / ".runtime/private/e2e-api.env",
        portal_origin="http://127.0.0.1:" + ("8001" if portal == "ADMIN" else "8101"),
        frontend_dir=ROOT / "frontend/dist" / portal.lower(),
    )
    if make_url(settings.database_url).database != "eduflow_ai_e2e_test":
        raise RuntimeError("Test-only application requires eduflow_ai_e2e_test")
    app = create_app(settings, FileClock())

    @app.post("/__test__/clock")
    def advance(body: dict):
        now = datetime.fromisoformat(body["now"])
        if now.tzinfo is None:
            raise HTTPException(422, "Timezone required")
        (ROOT / ".runtime/e2e-clock.json").write_text(json.dumps({"now": now.isoformat()}))
        return {"now": now.isoformat()}

    return app


def create_e2e_app():
    return _create_e2e("ADMIN")


def create_e2e_public():
    return _create_e2e("PUBLIC")
