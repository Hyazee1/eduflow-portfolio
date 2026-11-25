import hashlib
import hmac
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore", hide_input_in_errors=True)
    database_url: str = Field(
        default="postgresql+psycopg://eduflow:eduflow_local@127.0.0.1:5432/eduflow_ai", repr=False
    )
    database_url_file: Path | None = None
    redis_url: str = Field(default="redis://127.0.0.1:6379/0", repr=False)
    redis_url_file: Path | None = None
    jwt_secret: str = Field(default="", repr=False)
    jwt_secret_file: Path | None = None
    portal: Literal["ADMIN", "PUBLIC", "TEST", "WORKER"] = "ADMIN"
    public_access_enabled: bool = False
    agent_mode: Literal["offline", "online"] = "offline"
    private_dir: Path = ROOT / ".runtime" / "private"
    backup_key_file: Path | None = None
    frontend_dir: Path | None = None
    login_limit: int = Field(default=12, ge=1, le=1000)
    login_window_seconds: int = Field(default=60, ge=1, le=3600)
    allowed_hosts: list[str] = Field(default_factory=lambda: ["127.0.0.1", "localhost"])
    jwt_issuer: str = "eduflow-ai"
    access_minutes: int = 15
    refresh_days: int = 7
    cookie_secure: bool = False
    portal_origin: str = ""
    admin_portal_origin: str = "http://127.0.0.1:8000"
    public_portal_origin: str = "http://127.0.0.1:8100"
    allowed_origins: list[str] = Field(default_factory=lambda: [])
    organization_slug: str = "demo"
    initialization_mode: Literal["demo", "blank"] = "demo"
    institution_name: str = "EduFlow 教学机构"
    initial_admin_username: str = "admin"
    initial_admin_password_file: Path | None = None
    demo_password: str = Field(default="EduFlow-Demo-2026!", repr=False)
    llm_base_url: str = ""
    llm_model: str = ""
    llm_api_key: str = Field(default="", repr=False)
    log_level: str = "INFO"

    @model_validator(mode="after")
    def private_defaults(self) -> Self:
        if self.public_access_enabled:
            raise ValueError("Public exposure is disabled in this local portfolio release")
        if self.database_url_file:
            self.database_url = self.database_url_file.read_text(encoding="utf-8").strip()
        if self.redis_url_file:
            self.redis_url = self.redis_url_file.read_text(encoding="utf-8").strip()
        if self.jwt_secret_file:
            self.jwt_secret = self.jwt_secret_file.read_text(encoding="utf-8").strip()
        if self.portal == "WORKER" and not self.jwt_secret:
            self.jwt_secret = "worker-has-no-http-authentication-capability"
        if len(self.jwt_secret) < 32:
            raise ValueError("A private signing key of at least 32 characters is required")
        if self.portal == "TEST":
            from sqlalchemy.engine import make_url

            if not (make_url(self.database_url).database or "").endswith("_test"):
                raise ValueError("The mixed compatibility adapter is test-only")
        else:
            origin = self.portal_origin or ("https" if self.cookie_secure else "http") + "://127.0.0.1:" + (
                "8100" if self.portal == "PUBLIC" else "8000"
            )
            self.allowed_origins = [origin, origin.replace("127.0.0.1", "localhost")]
        return self

    @property
    def signing_key(self) -> str:
        return hmac.new(self.jwt_secret.encode(), self.portal.encode(), hashlib.sha256).hexdigest()

    @property
    def audience(self) -> str:
        return "eduflow-" + self.portal.lower()

    @property
    def cookie_name(self) -> str:
        return "eduflow_refresh" if self.portal == "TEST" else "eduflow_" + self.portal.lower() + "_refresh"
