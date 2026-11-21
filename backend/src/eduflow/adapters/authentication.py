from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from uuid import uuid4

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session, sessionmaker

from eduflow.adapters.models import (
    AccountToken,
    AuditLog,
    AuthSession,
    Organization,
    StudentProfile,
    TeacherProfile,
    User,
)
from eduflow.application.ports import Clock
from eduflow.domain.entities import Principal
from eduflow.domain.errors import BusinessError, Conflict, Forbidden, NotFound, Unauthenticated


def user_view(user: User) -> dict[str, Any]:
    return {
        "id": user.id,
        "organization_id": user.organization_id,
        "role": user.role,
        "name": user.name,
        "username": user.username,
        "version": user.version,
    }


@dataclass(frozen=True)
class AuthConfig:
    secret: str = field(repr=False)
    issuer: str
    organization_slug: str
    access_minutes: int = 15
    refresh_days: int = 7
    portal: str = "ADMIN"
    audience: str = "eduflow-admin"


class Authentication:
    def __init__(self, factory: sessionmaker[Session], config: AuthConfig, clock: Clock) -> None:
        self.factory = factory
        self.config = config
        self.clock = clock
        self.hasher = PasswordHasher(time_cost=2, memory_cost=65536, parallelism=2)
        self._dummy_hash = self.hasher.hash(secrets.token_urlsafe(32))

    def login(self, username: str, password: str, role: str) -> dict[str, Any]:
        if role not in self.allowed_roles:
            raise Unauthenticated("用户名、密码或所选身份不正确")
        with self.factory() as session, session.begin():
            org = session.scalar(
                select(Organization).where(Organization.slug == self.config.organization_slug)
            )
            user = (
                session.scalar(select(User).where(User.username == username, User.organization_id == org.id))
                if org
                else None
            )
            valid: bool = False
            try:
                valid = self.hasher.verify(user.password_hash if user else self._dummy_hash, password)
            except (VerificationError, InvalidHashError):
                valid = False
            if not user or not valid or not user.active or user.role != role:
                raise Unauthenticated("用户名、密码或所选身份不正确")
            result = self._issue(session, user, str(uuid4()))
            session.add(
                AuditLog(
                    organization_id=user.organization_id,
                    actor_id=user.id,
                    action="auth.login",
                    entity_type="users",
                    entity_id=user.id,
                    detail={},
                )
            )
            return result

    @property
    def allowed_roles(self) -> set[str]:
        return (
            {"ADMIN"}
            if self.config.portal == "ADMIN"
            else {"TEACHER", "STUDENT"}
            if self.config.portal == "PUBLIC"
            else {"ADMIN", "TEACHER", "STUDENT"}
            if self.config.portal == "TEST"
            else set()
        )

    def _issue(self, session: Session, user: User, family_id: str) -> dict[str, Any]:
        raw = secrets.token_urlsafe(48)
        now = self.clock.now()
        auth = AuthSession(
            id=str(uuid4()),
            organization_id=user.organization_id,
            user_id=user.id,
            token_hash=hashlib.sha256(raw.encode()).hexdigest(),
            expires_at=now + timedelta(days=self.config.refresh_days),
            family_id=family_id,
            portal=self.config.portal,
        )
        session.add(auth)
        session.flush()
        token = jwt.encode(
            {
                "sub": user.id,
                "sid": auth.id,
                "org": user.organization_id,
                "iat": now,
                "nbf": now,
                "exp": now + timedelta(minutes=self.config.access_minutes),
                "iss": self.config.issuer,
                "aud": self.config.audience,
                "portal": self.config.portal,
                "jti": str(uuid4()),
            },
            self.config.secret,
            algorithm="HS256",
        )
        return {"access_token": token, "token_type": "bearer", "user": user_view(user), "refresh_token": raw}

    def refresh(self, raw: str) -> dict[str, Any]:
        invalid = False
        result: dict[str, Any] = {}
        with self.factory() as session, session.begin():
            auth = session.scalar(
                select(AuthSession)
                .where(AuthSession.token_hash == hashlib.sha256(raw.encode()).hexdigest())
                .with_for_update()
            )
            if not auth or auth.portal != self.config.portal:
                invalid = True
            elif auth.revoked_at:
                session.execute(
                    update(AuthSession)
                    .where(AuthSession.family_id == auth.family_id)
                    .values(revoked_at=self.clock.now())
                )
                invalid = True
            elif auth.expires_at <= self.clock.now():
                auth.revoked_at = self.clock.now()
                invalid = True
            else:
                user = session.get(User, auth.user_id)
                if not user or not user.active or user.role not in self.allowed_roles:
                    invalid = True
                else:
                    auth.revoked_at = self.clock.now()
                    result = self._issue(session, user, auth.family_id)
        if invalid:
            raise Unauthenticated("登录会话已失效，请重新登录")
        return result

    def resolve(self, token: str) -> Principal:
        try:
            claims = jwt.decode(
                token,
                self.config.secret,
                algorithms=["HS256"],
                issuer=self.config.issuer,
                audience=self.config.audience,
                options={
                    "require": ["exp", "sub", "sid", "org", "iat", "nbf"],
                    "verify_exp": False,
                    "verify_nbf": False,
                    "verify_iat": False,
                },
            )
            now = self.clock.now().timestamp()
            if float(claims["nbf"]) > now or float(claims["iat"]) > now + 5 or float(claims["exp"]) <= now:
                raise jwt.InvalidTokenError("Token outside its validity interval")
        except (jwt.InvalidTokenError, ValueError, TypeError) as error:
            raise Unauthenticated("访问令牌无效或已过期") from error
        with self.factory() as session:
            auth = session.get(AuthSession, claims["sid"])
            user = session.get(User, claims["sub"])
            if (
                not user
                or not user.active
                or not auth
                or auth.revoked_at
                or auth.user_id != user.id
                or user.organization_id != claims["org"]
                or auth.organization_id != user.organization_id
                or auth.portal != self.config.portal
                or claims.get("portal") != self.config.portal
                or user.role not in self.allowed_roles
                or auth.expires_at <= self.clock.now()
            ):
                raise Unauthenticated("会话已撤销，请重新登录")
            return Principal(user.id, user.organization_id, user.role, user.name)

    def logout(self, raw: str) -> None:
        with self.factory() as session, session.begin():
            session.execute(
                update(AuthSession)
                .where(
                    AuthSession.token_hash == hashlib.sha256(raw.encode()).hexdigest(),
                    AuthSession.portal == self.config.portal,
                )
                .values(revoked_at=self.clock.now())
            )

    def _audit(self, session: Session, actor: Principal, action: str, target: str) -> None:
        session.add(
            AuditLog(
                organization_id=actor.organization_id,
                actor_id=actor.id,
                action=action,
                entity_type="users",
                entity_id=target,
                detail={},
            )
        )

    def sessions(self, actor: Principal) -> list[dict[str, Any]]:
        with self.factory() as session:
            rows = session.scalars(
                select(AuthSession)
                .where(AuthSession.organization_id == actor.organization_id, AuthSession.user_id == actor.id)
                .order_by(AuthSession.created_at.desc())
                .limit(100)
            )
            return [
                {
                    "id": row.id,
                    "portal": row.portal,
                    "created_at": row.created_at,
                    "expires_at": row.expires_at,
                    "revoked_at": row.revoked_at,
                }
                for row in rows
            ]

    def revoke(
        self, actor: Principal, *, session_id: str | None = None, user_id: str | None = None
    ) -> dict[str, str]:
        target = user_id or actor.id
        if target != actor.id and actor.role != "ADMIN":
            raise Forbidden("只能撤销本人的登录会话")
        with self.factory() as session, session.begin():
            user = session.scalar(
                select(User)
                .where(User.id == target, User.organization_id == actor.organization_id)
                .with_for_update()
            )
            if user is None:
                raise NotFound("账号不存在")
            query = update(AuthSession).where(
                AuthSession.user_id == target, AuthSession.organization_id == actor.organization_id
            )
            if session_id:
                owned = session.scalar(
                    select(AuthSession.id).where(
                        AuthSession.id == session_id,
                        AuthSession.user_id == target,
                        AuthSession.organization_id == actor.organization_id,
                    )
                )
                if not owned:
                    raise NotFound("会话不存在")
                query = query.where(AuthSession.id == session_id)
            session.execute(query.values(revoked_at=self.clock.now()))
            self._audit(session, actor, "auth.sessions_revoked", target)
        return {"status": "REVOKED"}

    def change_password(self, actor: Principal, current: str, password: str) -> dict[str, str]:
        with self.factory() as session, session.begin():
            user = session.scalar(
                select(User)
                .where(User.id == actor.id, User.organization_id == actor.organization_id)
                .with_for_update()
            )
            if not user or not user.active:
                raise Unauthenticated("账号已停用")
            try:
                self.hasher.verify(user.password_hash, current)
            except (VerificationError, InvalidHashError) as error:
                raise BusinessError("原密码不正确") from error
            user.password_hash = self.hasher.hash(password)
            user.version += 1
            session.execute(
                update(AuthSession).where(AuthSession.user_id == user.id).values(revoked_at=self.clock.now())
            )
            session.execute(
                update(AccountToken)
                .where(AccountToken.user_id == user.id, AccountToken.used_at.is_(None))
                .values(used_at=self.clock.now())
            )
            self._audit(session, actor, "auth.password_changed", user.id)
        return {"status": "PASSWORD_CHANGED"}

    def _credential(
        self, session: Session, actor: Principal, key: str, payload: dict[str, Any]
    ) -> tuple[str, str, dict[str, Any] | None]:
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": actor.organization_id + ":credential:" + actor.id + ":" + key},
        )
        raw = hmac.new(
            self.config.secret.encode(),
            (actor.organization_id + ":" + actor.id + ":" + key + ":" + fingerprint).encode(),
            hashlib.sha384,
        ).hexdigest()
        previous = session.scalar(
            select(AccountToken).where(
                AccountToken.organization_id == actor.organization_id,
                AccountToken.created_by == actor.id,
                AccountToken.request_key == key,
            )
        )
        if previous:
            if previous.request_fingerprint != fingerprint:
                raise Conflict("相同幂等键不能用于不同账号操作")
            user = session.get(User, previous.user_id)
            return (
                raw,
                fingerprint,
                {
                    "token": raw,
                    "purpose": previous.purpose,
                    "expires_at": previous.expires_at,
                    "portal": "ADMIN" if user and user.role == "ADMIN" else "PUBLIC",
                    "user_id": previous.user_id,
                },
            )
        return raw, fingerprint, None

    def issue_account_token(
        self, actor: Principal, user_id: str, purpose: str, key: str = ""
    ) -> dict[str, Any]:
        if actor.role != "ADMIN" or purpose not in {"INVITE", "RESET"}:
            raise Forbidden("仅管理员可以管理账号邀请")
        with self.factory() as session, session.begin():
            key = key or str(uuid4())
            raw, fingerprint, replay = self._credential(
                session, actor, key, {"user_id": user_id, "purpose": purpose}
            )
            if replay:
                return replay
            user = session.scalar(
                select(User)
                .where(User.id == user_id, User.organization_id == actor.organization_id)
                .with_for_update()
            )
            if not user:
                raise NotFound("账号不存在")
            if purpose == "INVITE" and user.active:
                raise Conflict("已启用账号请使用密码重置")
            session.execute(
                update(AccountToken)
                .where(AccountToken.user_id == user.id, AccountToken.used_at.is_(None))
                .values(used_at=self.clock.now())
            )
            expires = self.clock.now() + timedelta(hours=24)
            session.add(
                AccountToken(
                    organization_id=actor.organization_id,
                    user_id=user.id,
                    token_hash=hashlib.sha256(raw.encode()).hexdigest(),
                    purpose=purpose,
                    expires_at=expires,
                    created_by=actor.id,
                    request_key=key,
                    request_fingerprint=fingerprint,
                )
            )
            self._audit(session, actor, "auth." + purpose.lower() + "_issued", user.id)
            return {
                "token": raw,
                "purpose": purpose,
                "expires_at": expires,
                "portal": "ADMIN" if user.role == "ADMIN" else "PUBLIC",
            }

    def accept_account_token(self, raw: str, password: str, purpose: str) -> dict[str, str]:
        digest = hashlib.sha256(raw.encode()).hexdigest()
        # Read the target first, then lock user -> token everywhere to avoid lock inversion.
        with self.factory() as session, session.begin():
            target = session.scalar(select(AccountToken.user_id).where(AccountToken.token_hash == digest))
            user = session.scalar(select(User).where(User.id == target).with_for_update()) if target else None
            token = session.scalar(
                select(AccountToken).where(AccountToken.token_hash == digest).with_for_update()
            )
            if (
                not user
                or not token
                or token.organization_id != user.organization_id
                or token.used_at
                or token.expires_at <= self.clock.now()
                or token.purpose != purpose
                or user.role not in self.allowed_roles
            ):
                raise BusinessError("链接无效、已使用或已过期")
            org = session.get(Organization, user.organization_id)
            if (
                not org
                or org.slug != self.config.organization_slug
                or (purpose == "RESET" and not user.active)
            ):
                raise BusinessError("链接无效、已使用或已过期")
            user.password_hash = self.hasher.hash(password)
            user.active = True
            user.version += 1
            session.execute(
                update(AccountToken)
                .where(AccountToken.user_id == user.id, AccountToken.used_at.is_(None))
                .values(used_at=self.clock.now())
            )
            session.execute(
                update(AuthSession).where(AuthSession.user_id == user.id).values(revoked_at=self.clock.now())
            )
            self._audit(
                session,
                Principal(user.id, user.organization_id, user.role),
                "auth." + purpose.lower() + "_accepted",
                user.id,
            )
        return {"status": "ACCEPTED"}

    def invite_user(self, actor: Principal, values: dict[str, Any], key: str = "") -> dict[str, Any]:
        if actor.role != "ADMIN":
            raise Forbidden("仅管理员可以邀请账号")
        with self.factory() as session, session.begin():
            key = key or str(uuid4())
            raw, fingerprint, replay = self._credential(
                session, actor, key, {**values, "purpose": "INVITE_NEW"}
            )
            if replay:
                return replay
            session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": actor.organization_id + ":invite:" + values["username"]},
            )
            user = session.scalar(
                select(User).where(
                    User.organization_id == actor.organization_id, User.username == values["username"]
                )
            )
            if user:
                raise Conflict("登录账号已存在")
            user = User(
                id=str(uuid4()),
                organization_id=actor.organization_id,
                username=values["username"],
                name=values["name"],
                role=values["role"],
                active=False,
                password_hash=self.hasher.hash(secrets.token_urlsafe(48)),
            )
            session.add(user)
            session.flush()
            if user.role == "STUDENT":
                session.add(
                    StudentProfile(
                        organization_id=actor.organization_id,
                        user_id=user.id,
                        student_code="S-" + user.id[:8],
                        age=values["age"],
                        grade=values["grade"],
                    )
                )
            else:
                session.add(
                    TeacherProfile(
                        organization_id=actor.organization_id,
                        user_id=user.id,
                        teacher_code="T-" + user.id[:8],
                    )
                )
            expires = self.clock.now() + timedelta(hours=24)
            session.add(
                AccountToken(
                    organization_id=actor.organization_id,
                    user_id=user.id,
                    token_hash=hashlib.sha256(raw.encode()).hexdigest(),
                    purpose="INVITE",
                    expires_at=expires,
                    created_by=actor.id,
                    request_key=key,
                    request_fingerprint=fingerprint,
                )
            )
            self._audit(session, actor, "auth.invitation_created", user.id)
            return {
                "token": raw,
                "purpose": "INVITE",
                "expires_at": expires,
                "portal": "PUBLIC",
                "user_id": user.id,
            }
