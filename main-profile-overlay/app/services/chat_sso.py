"""SQLAlchemy adapter over existing UserSecurityToken rows; no new account store."""

import hmac
import json
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.chat_sso import (
    CLIENT_ID,
    MAIN_ROLES,
    ChatSsoCore,
    SecurityRecord,
    SessionState,
    SsoDenied,
    UserState,
    VerifiedLogin,
)
from app.core.config import Settings
from app.core.exceptions import AuthenticationError, PermissionDeniedError
from app.core.security import decode_token
from app.db.base import utcnow
from app.models.user import EmailVerificationCode, User, UserSecurityToken, UserSession


def verified_email_change(payload: str | None, current_email: str) -> bool:
    """Only the successfully redeemed change token gets this explicit proof."""
    try:
        data = json.loads(payload or "{}")
    except (TypeError, ValueError):
        return False
    return (
        isinstance(data, dict)
        and data.get("email_verified") is True
        and isinstance(data.get("new_email"), str)
        and data["new_email"].lower() == current_email
    )


class SqlChatSsoRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_user(self, user_id: str) -> UserState | None:
        user = await self.session.get(User, user_id, populate_existing=True)
        if user is None:
            return None
        # 从数据库读取当前权限；异常角色不得降级成普通用户或推断成管理员。
        if not isinstance(user.role, str) or user.role not in MAIN_ROLES:
            raise SsoDenied("invalid_main_role")
        email = (user.email or "").lower()
        verified = False
        if email:
            # Initial verification marks consumed_at only after a valid code.
            proof = await self.session.scalar(
                select(EmailVerificationCode.id).where(
                    EmailVerificationCode.user_id == user.id,
                    func.lower(EmailVerificationCode.email) == email,
                    EmailVerificationCode.consumed_at.is_not(None),
                    EmailVerificationCode.consumed_at <= EmailVerificationCode.expires_at,
                ).limit(1)
            )
            verified = proof is not None
            if not verified:
                # Other email-change tokens are consumed when invalidated: their
                # consumed_at alone must never be accepted as verification.
                payloads = await self.session.scalars(
                    select(UserSecurityToken.payload).where(
                        UserSecurityToken.user_id == user.id,
                        UserSecurityToken.purpose == "email_change",
                        func.lower(UserSecurityToken.email) == email,
                        UserSecurityToken.consumed_at.is_not(None),
                        UserSecurityToken.consumed_at <= UserSecurityToken.expires_at,
                    )
                )
                verified = any(verified_email_change(value, email) for value in payloads)
        return UserState(
            id=user.id,
            status=user.status,
            name=user.display_name or user.username,
            username=user.username,
            email=email if verified else None,
            email_verified=verified,
            role=user.role,
        )

    async def get_session(self, session_id: str) -> SessionState | None:
        row = await self.session.get(UserSession, session_id, populate_existing=True)
        return SessionState(row.id, row.user_id, row.revoked_at is not None) if row else None

    async def find_token(
        self, token_hash: str, purpose: str, *, for_update: bool = False
    ) -> SecurityRecord | None:
        statement = select(UserSecurityToken).where(
            UserSecurityToken.token_hash == token_hash,
            UserSecurityToken.purpose == purpose,
        )
        if for_update:
            statement = statement.with_for_update()
        row = await self.session.scalar(statement.execution_options(populate_existing=True))
        return (
            SecurityRecord(
                row.user_id,
                row.purpose,
                row.token_hash,
                row.payload or "",
                row.expires_at,
                row.consumed_at,
            )
            if row
            else None
        )

    async def add_token(self, token: SecurityRecord) -> None:
        self.session.add(
            UserSecurityToken(
                user_id=token.user_id,
                purpose=token.purpose,
                token_hash=token.token_hash,
                payload=token.payload,
                sent_at=utcnow(),
                expires_at=token.expires_at,
            )
        )

    async def consume(self, token: SecurityRecord, now: datetime) -> bool:
        result = await self.session.execute(
            update(UserSecurityToken)
            .where(
                UserSecurityToken.token_hash == token.token_hash,
                UserSecurityToken.purpose == token.purpose,
                UserSecurityToken.user_id == token.user_id,
                UserSecurityToken.consumed_at.is_(None),
                UserSecurityToken.expires_at > now,
            )
            .values(consumed_at=now)
        )
        return result.rowcount == 1

    async def commit(self) -> None:
        await self.session.commit()


class ChatSsoService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        if (
            not settings.chat_sso_enabled
            or settings.chat_sso_client_id != CLIENT_ID
            or len(settings.chat_sso_bridge_secret) < 32
        ):
            raise PermissionDeniedError("chat_sso_unavailable", "Chat SSO is not enabled")
        self.settings = settings
        try:
            self.core = ChatSsoCore(
                SqlChatSsoRepository(session),
                finish_url=settings.chat_sso_finish_url,
                login_policy=settings.chat_sso_login_policy,
                allowed_user_ids=settings.chat_sso_allowed_user_ids,
                approval_ttl_seconds=settings.chat_sso_approval_ttl_seconds,
            )
        except SsoDenied as exc:
            raise PermissionDeniedError("chat_sso_unavailable", "Chat SSO is not enabled") from exc

    def require_bridge(self, authorization: str | None) -> None:
        """Accept only the dedicated backend Bearer secret, never a second header channel."""
        scheme, separator, supplied = (authorization or "").partition(" ")
        if (
            not separator
            or scheme.lower() != "bearer"
            or not supplied
            or any(character.isspace() for character in supplied)
        ):
            raise AuthenticationError("invalid_chat_sso_client", "Invalid chat SSO client")
        expected = self.settings.chat_sso_bridge_secret.encode("utf-8")
        if not hmac.compare_digest(supplied.encode("utf-8"), expected):
            raise AuthenticationError("invalid_chat_sso_client", "Invalid chat SSO client")

    async def approve(
        self, token: str, current_user: User, interaction_id: str
    ) -> dict[str, object]:
        """Verify the main bearer locally and issue approval; never return that bearer."""
        data = decode_token(token, settings=self.settings, expected_type="access")
        sid, expiry = data.get("sid"), data.get("exp")
        if (
            data.get("sub") != current_user.id
            or not isinstance(sid, str)
            or not sid
            or isinstance(expiry, bool)
            or not isinstance(expiry, int)
        ):
            raise AuthenticationError("invalid_chat_sso", "Chat authentication required")
        try:
            return await self.core.approve(
                VerifiedLogin(current_user.id, sid, expiry),
                interaction_id,
            )
        except SsoDenied as exc:
            raise AuthenticationError("invalid_chat_sso", "Chat authentication required") from exc

    async def exchange(self, client_id: str, interaction_id: str, code: str) -> dict[str, object]:
        """Consume one approved code for the authenticated OP; writes only security-token state."""
        try:
            return await self.core.exchange(
                client_id=client_id,
                interaction_id=interaction_id,
                approval_code=code,
            )
        except SsoDenied as exc:
            raise AuthenticationError("invalid_chat_sso", "Chat authentication required") from exc

    async def revoke(self, client_id: str, binding_id: str) -> dict[str, object]:
        """Idempotently revoke one chat binding without revoking a main sid or account."""
        try:
            return await self.core.revoke(client_id=client_id, binding_id=binding_id)
        except SsoDenied as exc:
            raise AuthenticationError("invalid_chat_sso", "Chat authentication required") from exc

    async def session_state(self, client_id: str, binding_id: str) -> dict[str, object]:
        """Read authoritative active user/session state without changing accounts or grants."""
        try:
            return await self.core.session_state(client_id=client_id, binding_id=binding_id)
        except SsoDenied as exc:
            raise AuthenticationError("invalid_chat_sso", "Chat authentication required") from exc
