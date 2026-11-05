"""Small repository-driven main-session bridge; no main JWT leaves this service."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from urllib.parse import urlsplit

CLIENT_ID = "parallellines-chat"
APPROVAL_PURPOSE = "chat_oidc_approval"
BINDING_PURPOSE = "chat_oidc_binding"
OPAQUE_ID = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
MAIN_ROLES = frozenset({"user", "moderator", "admin"})


class SsoDenied(Exception):
    """Fail closed without including tokens, credentials, or identity in the error."""


@dataclass(frozen=True)
class VerifiedLogin:
    user_id: str
    session_id: str
    auth_expires_at: int


@dataclass(frozen=True)
class UserState:
    id: str
    status: str
    name: str
    username: str = ""
    email: str | None = None
    email_verified: bool = False
    role: str | None = None


@dataclass(frozen=True)
class SessionState:
    id: str
    user_id: str
    revoked: bool


@dataclass
class SecurityRecord:
    user_id: str
    purpose: str
    token_hash: str
    payload: str
    expires_at: datetime
    consumed_at: datetime | None = None


class Repository(Protocol):
    async def get_user(self, user_id: str) -> UserState | None: ...
    async def get_session(self, session_id: str) -> SessionState | None: ...
    async def find_token(
        self, token_hash: str, purpose: str, *, for_update: bool = False
    ) -> SecurityRecord | None: ...
    async def add_token(self, token: SecurityRecord) -> None: ...
    async def consume(self, token: SecurityRecord, now: datetime) -> bool: ...
    async def commit(self) -> None: ...


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def epoch_datetime(value: object) -> datetime:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SsoDenied("invalid_expiry")
    try:
        return datetime.fromtimestamp(value, UTC)
    except (ValueError, OverflowError, OSError) as exc:
        raise SsoDenied("invalid_expiry") from exc


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def valid_finish_url(value: str) -> bool:
    """Allow only the fixed local OP finish path or the proposed same-site production path."""
    if not value or "\\" in value or any(ord(c) < 32 for c in value):
        return False
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        return False
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return False
    if parsed.scheme == "https" and parsed.netloc == "pingxingxian.space":
        return parsed.path == "/auth/oidc/interaction/finish"
    return (
        parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        and parsed.path == "/auth/oidc/interaction/finish"
    )


def profile_claims(user: UserState) -> dict[str, object]:
    """Expose current profile attributes without using them as identity keys."""
    # 角色只来自当前主站账户；旧调用未提供时不补默认角色，非法值明确拒绝。
    if user.role is not None and (not isinstance(user.role, str) or user.role not in MAIN_ROLES):
        raise SsoDenied("invalid_main_role")
    verified = user.email_verified is True and bool(user.email)
    return {
        "name": user.name,
        "username": user.username,
        "email": user.email if verified else None,
        "email_verified": verified,
        **({"role": user.role} if user.role is not None else {}),
    }


class ChatSsoCore:
    def __init__(
        self,
        repository: Repository,
        *,
        finish_url: str,
        login_policy: str = "disabled",
        allowed_user_ids: tuple[str, ...] | list[str] | frozenset[str] = (),
        approval_ttl_seconds: int = 60,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        issue_token: Callable[[], str] = lambda: secrets.token_urlsafe(32),
    ) -> None:
        if not valid_finish_url(finish_url):
            raise SsoDenied("invalid_finish_configuration")
        if login_policy not in ("disabled", "active_main", "allowlist"):
            raise SsoDenied("invalid_login_policy")
        self.repo = repository
        self.finish_url = finish_url
        self.login_policy = login_policy
        self.allowed_user_ids = frozenset(allowed_user_ids)
        self.approval_ttl = max(15, min(approval_ttl_seconds, 120))
        self.clock = clock
        self.issue_token = issue_token

    async def _identity(self, login: VerifiedLogin) -> UserState:
        if self.login_policy == "disabled" or (
            self.login_policy == "allowlist" and login.user_id not in self.allowed_user_ids
        ):
            raise SsoDenied("chat_access_not_granted")
        if not login.user_id or not login.session_id:
            raise SsoDenied("session_required")
        if epoch_datetime(login.auth_expires_at) <= self.clock():
            raise SsoDenied("expired_authentication")
        user = await self.repo.get_user(login.user_id)
        session = await self.repo.get_session(login.session_id)
        if not user or user.id != login.user_id or user.status != "active":
            raise SsoDenied("inactive_account")
        if (
            not session
            or session.id != login.session_id
            or session.user_id != user.id
            or session.revoked
        ):
            raise SsoDenied("invalid_session")
        return user

    async def approve(self, login: VerifiedLogin, interaction_id: str) -> dict[str, object]:
        """Validate main identity/session and persist bounded hashed code + binding rows."""
        if not OPAQUE_ID.fullmatch(interaction_id):
            raise SsoDenied("invalid_interaction")
        await self._identity(login)
        now = self.clock()
        code, binding_id = self.issue_token(), self.issue_token()
        if code == binding_id or not all(OPAQUE_ID.fullmatch(v) for v in (code, binding_id)):
            raise SsoDenied("invalid_token_source")
        await self.repo.add_token(
            SecurityRecord(
                user_id=login.user_id,
                purpose=BINDING_PURPOSE,
                token_hash=token_hash(binding_id),
                payload=json.dumps(
                    {
                        "v": 1,
                        "user_id": login.user_id,
                        "sid": login.session_id,
                        "auth_expires_at": login.auth_expires_at,
                        "client_id": CLIENT_ID,
                    },
                    separators=(",", ":"),
                ),
                expires_at=epoch_datetime(login.auth_expires_at),
            )
        )
        await self.repo.add_token(
            SecurityRecord(
                user_id=login.user_id,
                purpose=APPROVAL_PURPOSE,
                token_hash=token_hash(code),
                payload=json.dumps(
                    {
                        "v": 1,
                        "binding_id": binding_id,
                        "interaction_id": interaction_id,
                        "client_id": CLIENT_ID,
                    },
                    separators=(",", ":"),
                ),
                expires_at=min(
                    now + timedelta(seconds=self.approval_ttl),
                    epoch_datetime(login.auth_expires_at),
                ),
            )
        )
        await self.repo.commit()
        return {
            "approval_code": code,
            "interaction_id": interaction_id,
            "finish_url": self.finish_url,
        }

    async def _binding(self, binding_id: str) -> tuple[SecurityRecord, VerifiedLogin, UserState]:
        if not OPAQUE_ID.fullmatch(binding_id):
            raise SsoDenied("invalid_binding")
        record = await self.repo.find_token(token_hash(binding_id), BINDING_PURPOSE)
        if not record or record.consumed_at or utc(record.expires_at) <= self.clock():
            raise SsoDenied("invalid_binding")
        try:
            data = json.loads(record.payload)
            if (
                data["v"] != 1
                or data["client_id"] != CLIENT_ID
                or data["user_id"] != record.user_id
                or not isinstance(data["sid"], str)
            ):
                raise SsoDenied("invalid_binding")
            expiry = min(data["auth_expires_at"], int(utc(record.expires_at).timestamp()))
            epoch_datetime(data["auth_expires_at"])
            login = VerifiedLogin(record.user_id, data["sid"], expiry)
        except (ValueError, TypeError, KeyError) as exc:
            raise SsoDenied("invalid_binding") from exc
        return record, login, await self._identity(login)

    async def exchange(
        self, *, client_id: str, interaction_id: str, approval_code: str
    ) -> dict[str, object]:
        """Redeem one context-bound code under a repository lock and return minimal identity."""
        if client_id != CLIENT_ID or not OPAQUE_ID.fullmatch(approval_code):
            raise SsoDenied("invalid_approval")
        record = await self.repo.find_token(
            token_hash(approval_code), APPROVAL_PURPOSE, for_update=True
        )
        if not record or record.consumed_at or utc(record.expires_at) <= self.clock():
            raise SsoDenied("invalid_approval")
        try:
            data = json.loads(record.payload)
            if (
                data["v"] != 1
                or data["client_id"] != client_id
                or data["interaction_id"] != interaction_id
            ):
                raise SsoDenied("invalid_approval")
            _, login, user = await self._binding(data["binding_id"])
            if record.user_id != user.id:
                raise SsoDenied("invalid_approval")
        except (ValueError, TypeError, KeyError) as exc:
            raise SsoDenied("invalid_approval") from exc
        if not await self.repo.consume(record, self.clock()):
            raise SsoDenied("invalid_approval")
        await self.repo.commit()
        return {
            "active": True,
            "sub": user.id,
            "binding_id": data["binding_id"],
            "auth_expires_at": login.auth_expires_at,
            **profile_claims(user),
        }

    async def revoke(self, *, client_id: str, binding_id: str) -> dict[str, object]:
        """Revoke only this chat binding; never alter its original main session."""
        if client_id != CLIENT_ID:
            raise SsoDenied("invalid_client")
        if not OPAQUE_ID.fullmatch(binding_id):
            raise SsoDenied("invalid_binding")
        record = await self.repo.find_token(
            token_hash(binding_id), BINDING_PURPOSE, for_update=True
        )
        if record is not None:
            now = self.clock()
            if record.consumed_at is None and utc(record.expires_at) > now:
                await self.repo.consume(record, now)
            await self.repo.commit()
        return {"active": False, "sub": None, "binding_id": binding_id, "auth_expires_at": None}

    async def session_state(self, *, client_id: str, binding_id: str) -> dict[str, object]:
        """Read primary user/sid/expiry state; unavailable authority never grants access."""
        if client_id != CLIENT_ID:
            raise SsoDenied("invalid_client")
        try:
            _, login, user = await self._binding(binding_id)
            return {
                "active": True,
                "sub": user.id,
                "binding_id": binding_id,
                "auth_expires_at": login.auth_expires_at,
                **profile_claims(user),
            }
        except SsoDenied:
            return {"active": False, "sub": None, "binding_id": binding_id, "auth_expires_at": None}
