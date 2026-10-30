import base64
import binascii
import hmac
import json
import secrets
import struct
import time
from datetime import UTC, datetime, timedelta
from hashlib import sha1, sha256
from urllib.parse import quote

from sqlalchemy import delete, desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.core.config import Settings
from app.core.exceptions import (
    AuthenticationError,
    ConflictError,
    PermissionDeniedError,
    RateLimitError,
    ValidationError,
)
from app.core.security import create_token, hash_password, verify_password
from app.db.base import utcnow
from app.models.user import (
    EmailVerificationCode,
    User,
    UserRecoveryCode,
    UserSecurityToken,
    UserSession,
)
from app.schemas.auth import (
    ChangePasswordRequest,
    EmailChangeConfirmRequest,
    EmailChangeRequest,
    EmailChangeStartResponse,
    FableSpaceSsoExchangeRequest,
    FableSpaceSsoExchangeResponse,
    FableSpaceSsoIntrospectRequest,
    FableSpaceSsoIntrospectResponse,
    FableSpaceSsoTicketResponse,
    FableSpaceSsoUser,
    LoginRequest,
    LoginResponse,
    OAuthProviderResponse,
    PasswordResetConfirmRequest,
    PasswordResetRequest,
    PasswordResetStartResponse,
    RefreshRequest,
    RegisterRequest,
    RegistrationStartResponse,
    ResendVerificationRequest,
    TokenPair,
    TwoFactorDisableRequest,
    TwoFactorEnableRequest,
    TwoFactorLoginVerifyRequest,
    TwoFactorRecoveryCodesResponse,
    TwoFactorSetupRequest,
    TwoFactorSetupResponse,
    VerifyEmailRequest,
)
from app.schemas.users import UserPublic
from app.services.admin import SiteSettingService
from app.services.background_jobs import BackgroundJobService
from app.services.badges import BadgeTrustService
from app.services.growth import GrowthService
from app.services.integrations import IntegrationService
from app.services.product_access import ProductAccessService
from app.services.spam import SpamPreventionService

PASSWORD_RESET_PURPOSE = "password_reset"
EMAIL_CHANGE_PURPOSE = "email_change"
FABLESPACE_SSO_PURPOSE = "fablespace_sso"
TOTP_STEP_SECONDS = 30
TOTP_DIGITS = 6
RECOVERY_CODE_COUNT = 10


class AuthService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    async def issue_fablespace_sso_ticket(self, user: User) -> FableSpaceSsoTicketResponse:
        """Create a single-use FableSpace ticket for an active forum account."""
        authorization = await ProductAccessService(self.session).fablespace_authorization(user)
        if not authorization.allowed:
            raise PermissionDeniedError(
                "fablespace_access_required",
                "FableSpace access is required",
            )
        if len(self.settings.fablespace_sso_service_secret.strip()) < 32:
            raise ValidationError("fablespace_sso_unavailable", "FableSpace SSO is not configured")

        base_url = self.settings.fablespace_base_url.strip().rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            raise ValidationError("fablespace_sso_unavailable", "FableSpace URL is not configured")

        ttl_seconds = max(15, min(self.settings.fablespace_sso_ticket_ttl_seconds, 300))
        await self._consume_open_security_tokens(user.id, FABLESPACE_SSO_PURPOSE)
        ticket, ticket_hash = await self._new_security_token(user, FABLESPACE_SSO_PURPOSE)
        now = utcnow()
        self.session.add(
            UserSecurityToken(
                user_id=user.id,
                purpose=FABLESPACE_SSO_PURPOSE,
                token_hash=ticket_hash,
                email=None,
                payload=json.dumps(
                    {"authorization_version": authorization.authorization_version},
                    separators=(",", ":"),
                ),
                sent_at=now,
                expires_at=now + timedelta(seconds=ttl_seconds),
            )
        )
        await self.session.commit()
        return FableSpaceSsoTicketResponse(
            redirect_url=(
                f"{base_url}/api/v1/auth/parallellines/callback?code={quote(ticket, safe='')}"
            ),
            expires_in_seconds=ttl_seconds,
        )

    async def exchange_fablespace_sso_ticket(
        self,
        payload: FableSpaceSsoExchangeRequest,
        service_secret: str | None,
    ) -> FableSpaceSsoExchangeResponse:
        """Consume a FableSpace ticket server-to-server and return a minimal trusted identity."""
        self._require_fablespace_sso_client(service_secret)

        now = utcnow()
        token = await self.session.scalar(
            select(UserSecurityToken)
            .where(
                UserSecurityToken.purpose == FABLESPACE_SSO_PURPOSE,
                UserSecurityToken.token_hash == self._hash_token(payload.code),
                UserSecurityToken.consumed_at.is_(None),
            )
            .with_for_update()
        )
        if not token or _as_utc(token.expires_at) <= now:
            raise AuthenticationError("invalid_sso_ticket", "SSO ticket is invalid or expired")

        user = await self.session.get(User, token.user_id)
        authorization = (
            await ProductAccessService(self.session).fablespace_authorization(user)
            if user is not None
            else None
        )
        if user is None or authorization is None or not authorization.allowed:
            token.consumed_at = now
            await self.session.commit()
            raise AuthenticationError("invalid_sso_ticket", "SSO ticket is invalid or expired")

        token.consumed_at = now
        await self.session.commit()
        return FableSpaceSsoExchangeResponse(
            user=self._fablespace_sso_user(user),
            capabilities=list(authorization.capabilities),
            access_level=authorization.access_level or "access",
            authorization_version=authorization.authorization_version,
            access_expires_at=authorization.expires_at,
        )

    async def introspect_fablespace_access(
        self,
        payload: FableSpaceSsoIntrospectRequest,
        service_secret: str | None,
    ) -> FableSpaceSsoIntrospectResponse:
        """Return authoritative current account and capability state to FableSpace.

        Key parameters are the requested ParallelLines user ID and backend-only client
        secret. The return value is safe trusted-session state. Side effect: reads the user
        and product grant without mutating either.
        """

        self._require_fablespace_sso_client(service_secret)
        user = await self.session.get(User, payload.user_id)
        if user is None:
            return FableSpaceSsoIntrospectResponse(
                active=False,
                user=None,
                account_status=None,
                capabilities=[],
                access_level=None,
                authorization_version=0,
                access_expires_at=None,
            )

        authorization = await ProductAccessService(self.session).fablespace_authorization(user)
        return FableSpaceSsoIntrospectResponse(
            active=authorization.allowed,
            user=self._fablespace_sso_user(user),
            account_status=user.status,
            capabilities=list(authorization.capabilities),
            access_level=authorization.access_level,
            authorization_version=authorization.authorization_version,
            access_expires_at=authorization.expires_at,
        )

    def _require_fablespace_sso_client(self, service_secret: str | None) -> None:
        """Authenticate the backend-only FableSpace client with constant-time comparison.

        Key parameter `service_secret` is the supplied shared secret. Return value is none.
        Side effect: raises a typed authentication error when credentials are unavailable
        or invalid.
        """

        expected_secret = self.settings.fablespace_sso_service_secret.strip()
        supplied_secret = (service_secret or "").strip()
        if len(expected_secret) < 32 or not hmac.compare_digest(expected_secret, supplied_secret):
            raise AuthenticationError("invalid_sso_client", "Invalid SSO client credentials")

    def _fablespace_sso_user(self, user: User) -> FableSpaceSsoUser:
        """Project one forum account into the minimal trusted FableSpace identity.

        Key parameter `user` is the authoritative forum model. The return value omits
        email and security fields. This helper has no side effects.
        """

        return FableSpaceSsoUser(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            avatar_url=user.avatar_url,
            role=user.role,
            locale=user.locale,
        )

    async def register(
        self,
        payload: RegisterRequest,
        request: Request | None = None,
    ) -> RegistrationStartResponse:
        if not await SiteSettingService(self.session, self.settings).registration_enabled():
            raise PermissionDeniedError(
                "registration_disabled",
                "Registration is currently disabled",
            )
        email = str(payload.email).lower()
        await SpamPreventionService(self.session, self.settings).enforce_registration(
            request,
            email=email,
        )
        existing = await self.session.scalar(
            select(User).where(or_(User.email == email, User.username == payload.username))
        )
        if existing:
            raise ConflictError(
                "account_exists",
                "Username or email is already registered",
                {"username": payload.username, "email": payload.email},
            )

        user = User(
            username=payload.username,
            email=email,
            hashed_password=hash_password(payload.password),
            status="pending_verification",
        )
        try:
            self.session.add(user)
            await self.session.flush()
            await IntegrationService(self.session).enqueue_event(
                "user.created",
                {
                    "user_id": user.id,
                    "username": user.username,
                    "status": user.status,
                    "created_at": user.created_at.isoformat(),
                },
            )
            code = await self._create_and_send_verification_code(user)
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise
        return self._registration_response(user.email, code)

    async def login(self, payload: LoginRequest, request: Request | None = None) -> LoginResponse:
        account = payload.account.lower()
        await SpamPreventionService(self.session, self.settings).enforce_login(
            request,
            account=account,
        )
        user = await self.session.scalar(
            select(User).where(or_(User.email == account, User.username == payload.account))
        )
        if not user or not verify_password(payload.password, user.hashed_password):
            raise AuthenticationError("invalid_credentials", "Invalid account or password")
        if user.status == "pending_verification":
            raise AuthenticationError("email_not_verified", "Email verification is required")
        if user.status != "active":
            raise AuthenticationError("account_disabled", "This account is not active")
        if user.two_factor_enabled:
            return LoginResponse(
                two_factor_required=True,
                challenge_token=create_token(
                    subject=user.id,
                    token_type="two_factor",
                    settings=self.settings,
                    expires_delta=timedelta(minutes=self.settings.two_factor_challenge_minutes),
                ),
            )
        await self._award_daily_login(user)
        token_pair = await self._token_pair(user, request)
        return LoginResponse(**token_pair.model_dump(), two_factor_required=False)

    async def verify_two_factor_login(
        self,
        payload: TwoFactorLoginVerifyRequest,
        request: Request | None = None,
    ) -> TokenPair:
        token_payload = self._decode_two_factor_challenge(payload.challenge_token)
        user = await self.session.get(User, token_payload["sub"])
        if not user or user.status != "active" or not user.two_factor_enabled:
            raise AuthenticationError("invalid_token", "Invalid or expired token")
        if not await self._verify_two_factor_code(user, payload.code):
            raise AuthenticationError("invalid_two_factor_code", "Invalid two-factor code")
        await self._award_daily_login(user)
        return await self._token_pair(user, request)

    async def verify_email(
        self, payload: VerifyEmailRequest, request: Request | None = None
    ) -> TokenPair:
        user = await self._pending_user_by_email(str(payload.email).lower())
        verification = await self._latest_open_verification(user.id)
        now = utcnow()

        if verification is None:
            raise ValidationError("verification_code_not_found", "Verification code was not found")
        if verification.attempt_count >= self.settings.email_verification_max_attempts:
            raise RateLimitError("verification_attempts_exceeded", "Too many verification attempts")
        if _as_utc(verification.expires_at) <= now:
            raise ValidationError("verification_code_expired", "Verification code has expired")
        if not hmac.compare_digest(
            verification.code_hash,
            self._hash_verification_code(user.id, payload.code),
        ):
            verification.attempt_count += 1
            await self.session.commit()
            raise ValidationError(
                "invalid_verification_code",
                "Verification code is invalid",
                {"attempts_remaining": self._attempts_remaining(verification)},
            )

        verification.consumed_at = now
        user.status = "active"
        await GrowthService(self.session).award(
            user.id,
            "email_verified",
            source_id=user.id,
            actor_id=user.id,
            note="邮箱验证激活奖励",
        )
        badge_service = BadgeTrustService(self.session)
        await badge_service.grant_badge(
            user_id=user.id,
            badge_slug="verified-member",
            source_type="email_verified",
            source_id=user.id,
            actor_id=user.id,
            note="完成邮箱验证",
            idempotency_key=f"badge:verified-member:{user.id}",
        )
        await badge_service.recompute_trust(
            user,
            source_type="email_verified",
            source_id=user.id,
            actor_id=user.id,
            note="邮箱验证后重算信任等级",
        )
        await IntegrationService(self.session).enqueue_event(
            "user.verified",
            {
                "user_id": user.id,
                "username": user.username,
                "status": user.status,
                "verified_at": now.isoformat(),
            },
        )
        return await self._token_pair(user, request)

    async def resend_verification(
        self, payload: ResendVerificationRequest
    ) -> RegistrationStartResponse:
        user = await self._pending_user_by_email(str(payload.email).lower())
        latest = await self._latest_open_verification(user.id)
        remaining_seconds = self._resend_remaining_seconds(latest)
        if remaining_seconds > 0:
            raise RateLimitError(
                "verification_resend_limited",
                "Verification email was sent too recently",
            )

        try:
            code = await self._create_and_send_verification_code(user)
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise
        return self._registration_response(user.email, code)

    async def request_password_reset(
        self,
        payload: PasswordResetRequest,
    ) -> PasswordResetStartResponse:
        email = str(payload.email).lower()
        user = await self.session.scalar(select(User).where(User.email == email))
        if user and user.status == "active":
            token = await self._create_security_token(
                user,
                purpose=PASSWORD_RESET_PURPOSE,
                email=user.email,
                ttl_minutes=self.settings.password_reset_token_ttl_minutes,
            )
            await BackgroundJobService(self.session).enqueue_email(
                kind=PASSWORD_RESET_PURPOSE,
                to_email=user.email,
                username=user.username,
                secret=token,
                idempotency_key=(
                    f"email:{PASSWORD_RESET_PURPOSE}:"
                    f"{self._hash_user_security_token(user.id, PASSWORD_RESET_PURPOSE, token)}"
                ),
                commit=False,
            )
            await self.session.commit()
        return PasswordResetStartResponse(
            expires_in_seconds=self.settings.password_reset_token_ttl_minutes * 60
        )

    async def confirm_password_reset(self, payload: PasswordResetConfirmRequest) -> None:
        token = await self._consume_password_reset_token(payload)
        user = await self.session.get(User, token.user_id)
        if not user or user.status != "active":
            raise ValidationError("invalid_reset_token", "Password reset token is invalid")
        user.hashed_password = hash_password(payload.new_password)
        await self._consume_open_security_tokens(user.id, PASSWORD_RESET_PURPOSE)
        await self._revoke_sessions(user.id)
        await self.session.commit()

    async def change_password(
        self,
        payload: ChangePasswordRequest,
        current_user: User,
        current_session_id: str | None,
    ) -> None:
        if not verify_password(payload.current_password, current_user.hashed_password):
            raise AuthenticationError("invalid_credentials", "Invalid account or password")
        current_user.hashed_password = hash_password(payload.new_password)
        await self._revoke_sessions(current_user.id, except_session_id=current_session_id)
        await self.session.commit()

    async def request_email_change(
        self,
        payload: EmailChangeRequest,
        current_user: User,
    ) -> EmailChangeStartResponse:
        if not verify_password(payload.password, current_user.hashed_password):
            raise AuthenticationError("invalid_credentials", "Invalid account or password")
        new_email = str(payload.new_email).lower()
        existing = await self.session.scalar(select(User).where(User.email == new_email))
        if existing and existing.id != current_user.id:
            raise ConflictError("email_exists", "Email is already registered", {"email": new_email})
        token = await self._create_security_token(
            current_user,
            purpose=EMAIL_CHANGE_PURPOSE,
            email=new_email,
            payload={"new_email": new_email},
            ttl_minutes=self.settings.email_change_token_ttl_minutes,
        )
        await BackgroundJobService(self.session).enqueue_email(
            kind=EMAIL_CHANGE_PURPOSE,
            to_email=new_email,
            username=current_user.username,
            secret=token,
            idempotency_key=f"email:{EMAIL_CHANGE_PURPOSE}:{self._hash_token(token)}",
            commit=False,
        )
        await self.session.commit()
        return EmailChangeStartResponse(
            email=new_email,
            expires_in_seconds=self.settings.email_change_token_ttl_minutes * 60,
        )

    async def confirm_email_change(self, payload: EmailChangeConfirmRequest) -> User:
        token = await self._consume_security_token(payload.token, purpose=EMAIL_CHANGE_PURPOSE)
        user = await self.session.get(User, token.user_id)
        if not user or user.status != "active":
            raise ValidationError("invalid_email_change_token", "Email change token is invalid")
        data = json.loads(token.payload or "{}")
        new_email = str(data.get("new_email") or "").lower()
        if not new_email:
            raise ValidationError("invalid_email_change_token", "Email change token is invalid")
        existing = await self.session.scalar(select(User).where(User.email == new_email))
        if existing and existing.id != user.id:
            raise ConflictError("email_exists", "Email is already registered", {"email": new_email})
        user.email = new_email
        # Mark only this successfully redeemed token, never the other tokens
        # invalidated below. SSO uses this proof for the current verified email.
        data["email_verified"] = True
        token.payload = json.dumps(data, separators=(",", ":"))
        await self._consume_open_security_tokens(user.id, EMAIL_CHANGE_PURPOSE)
        await self.session.commit()
        await self.session.refresh(user)
        return user

    async def setup_two_factor(
        self,
        payload: TwoFactorSetupRequest,
        current_user: User,
    ) -> TwoFactorSetupResponse:
        if not verify_password(payload.password, current_user.hashed_password):
            raise AuthenticationError("invalid_credentials", "Invalid account or password")
        secret = generate_totp_secret()
        return TwoFactorSetupResponse(
            secret=secret,
            otpauth_url=totp_otpauth_url(
                issuer=self.settings.two_factor_issuer,
                username=current_user.username,
                secret=secret,
            ),
        )

    async def enable_two_factor(
        self,
        payload: TwoFactorEnableRequest,
        current_user: User,
    ) -> TwoFactorRecoveryCodesResponse:
        secret = normalize_totp_secret(payload.secret)
        if not verify_totp(secret, payload.code):
            raise ValidationError("invalid_two_factor_code", "Invalid two-factor code")
        current_user.two_factor_secret = secret
        current_user.two_factor_enabled = True
        recovery_codes = await self._replace_recovery_codes(current_user)
        await self.session.commit()
        return TwoFactorRecoveryCodesResponse(recovery_codes=recovery_codes)

    async def disable_two_factor(
        self,
        payload: TwoFactorDisableRequest,
        current_user: User,
    ) -> None:
        if not verify_password(payload.password, current_user.hashed_password):
            raise AuthenticationError("invalid_credentials", "Invalid account or password")
        if not current_user.two_factor_enabled:
            return
        if not await self._verify_two_factor_code(current_user, payload.code):
            raise ValidationError("invalid_two_factor_code", "Invalid two-factor code")
        current_user.two_factor_enabled = False
        current_user.two_factor_secret = None
        await self.session.execute(
            delete(UserRecoveryCode).where(UserRecoveryCode.user_id == current_user.id)
        )
        await self.session.commit()

    async def regenerate_recovery_codes(
        self,
        payload: TwoFactorDisableRequest,
        current_user: User,
    ) -> TwoFactorRecoveryCodesResponse:
        if not verify_password(payload.password, current_user.hashed_password):
            raise AuthenticationError("invalid_credentials", "Invalid account or password")
        if not current_user.two_factor_enabled:
            raise ValidationError(
                "two_factor_not_enabled", "Two-factor authentication is not enabled"
            )
        if not await self._verify_two_factor_code(current_user, payload.code):
            raise ValidationError("invalid_two_factor_code", "Invalid two-factor code")
        recovery_codes = await self._replace_recovery_codes(current_user)
        await self.session.commit()
        return TwoFactorRecoveryCodesResponse(recovery_codes=recovery_codes)

    async def list_sessions(
        self,
        current_user: User,
        current_session_id: str | None,
    ) -> list[UserSession]:
        result = await self.session.scalars(
            select(UserSession)
            .where(
                UserSession.user_id == current_user.id,
                UserSession.revoked_at.is_(None),
            )
            .order_by(desc(UserSession.last_seen_at))
        )
        sessions = list(result)
        for session in sessions:
            session.current = session.id == current_session_id
        return sessions

    async def revoke_session(self, session_id: str, current_user: User) -> None:
        session = await self.session.get(UserSession, session_id)
        if not session or session.user_id != current_user.id:
            raise ValidationError("session_not_found", "Session not found")
        session.revoked_at = session.revoked_at or utcnow()
        await self.session.commit()

    async def revoke_other_sessions(
        self,
        current_user: User,
        current_session_id: str | None,
    ) -> int:
        return await self._revoke_sessions(
            current_user.id, except_session_id=current_session_id, commit=True
        )

    async def refresh(self, payload: RefreshRequest) -> dict[str, str]:
        token_payload = self._decode_refresh(payload.refresh_token)
        user = await self.session.get(User, token_payload["sub"])
        if not user or user.status != "active":
            raise AuthenticationError("invalid_token", "Invalid or expired token")
        session_id = token_payload.get("sid")
        if session_id:
            session = await self.session.get(UserSession, session_id)
            if (
                not session
                or session.user_id != user.id
                or session.revoked_at is not None
                or not hmac.compare_digest(
                    session.refresh_token_hash, self._hash_token(payload.refresh_token)
                )
            ):
                raise AuthenticationError("invalid_token", "Invalid or expired token")
            session.last_seen_at = utcnow()
            await self.session.commit()
        access_token = create_token(
            subject=user.id,
            token_type="access",
            settings=self.settings,
            expires_delta=timedelta(minutes=self.settings.access_token_minutes),
            session_id=session_id,
        )
        return {"access_token": access_token, "token_type": "bearer"}

    def oauth_providers(self) -> OAuthProviderResponse:
        return OAuthProviderResponse(providers=self.settings.oauth_enabled_providers)

    async def _token_pair(self, user: User, request: Request | None = None) -> TokenPair:
        now = utcnow()
        session = UserSession(
            user_id=user.id,
            refresh_token_hash="pending",
            user_agent=self._request_user_agent(request),
            ip_address=self._request_ip(request),
            last_seen_at=now,
        )
        self.session.add(session)
        await self.session.flush()
        access_token = create_token(
            subject=user.id,
            token_type="access",
            settings=self.settings,
            expires_delta=timedelta(minutes=self.settings.access_token_minutes),
            session_id=session.id,
        )
        refresh_token = create_token(
            subject=user.id,
            token_type="refresh",
            settings=self.settings,
            expires_delta=timedelta(days=self.settings.refresh_token_days),
            session_id=session.id,
        )
        session.refresh_token_hash = self._hash_token(refresh_token)
        user.last_seen_at = now
        await self.session.commit()
        await self.session.refresh(user)
        return TokenPair(
            access_token=access_token,
            refresh_token=refresh_token,
            session_id=session.id,
            user=UserPublic.model_validate(user),
        )

    async def _award_daily_login(self, user: User) -> None:
        login_day = utcnow().date().isoformat()
        await GrowthService(self.session).award(
            user.id,
            "daily_login",
            source_id=login_day,
            actor_id=user.id,
            note="每日登录奖励",
            idempotency_key=f"daily_login:{user.id}:{login_day}",
        )

    async def _pending_user_by_email(self, email: str) -> User:
        user = await self.session.scalar(select(User).where(User.email == email))
        if not user:
            raise ValidationError("verification_not_found", "No pending verification was found")
        if user.status == "active":
            raise ConflictError(
                "account_already_active", "This account is already active", {"email": email}
            )
        if user.status != "pending_verification":
            raise ValidationError("verification_not_found", "No pending verification was found")
        return user

    async def _latest_open_verification(self, user_id: str) -> EmailVerificationCode | None:
        return await self.session.scalar(
            select(EmailVerificationCode)
            .where(
                EmailVerificationCode.user_id == user_id,
                EmailVerificationCode.consumed_at.is_(None),
            )
            .order_by(desc(EmailVerificationCode.sent_at))
            .limit(1)
        )

    async def _create_and_send_verification_code(self, user: User) -> str:
        code = f"{secrets.randbelow(1_000_000):06d}"
        now = utcnow()
        verification = EmailVerificationCode(
            user_id=user.id,
            email=user.email,
            code_hash=self._hash_verification_code(user.id, code),
            sent_at=now,
            expires_at=now + timedelta(minutes=self.settings.email_verification_code_ttl_minutes),
        )
        self.session.add(verification)
        idempotency_key = (
            f"email:verification:{user.id}:{self._hash_verification_code(user.id, code)}"
        )
        await BackgroundJobService(self.session).enqueue_email(
            kind="email_verification",
            to_email=user.email,
            username=user.username,
            secret=code,
            idempotency_key=idempotency_key,
            commit=False,
        )
        return code

    async def _create_security_token(
        self,
        user: User,
        *,
        purpose: str,
        email: str | None,
        ttl_minutes: int,
        payload: dict[str, object] | None = None,
    ) -> str:
        token, token_hash = await self._new_security_token(user, purpose)
        now = utcnow()
        self.session.add(
            UserSecurityToken(
                user_id=user.id,
                purpose=purpose,
                token_hash=token_hash,
                email=email,
                payload=json.dumps(payload) if payload else None,
                sent_at=now,
                expires_at=now + timedelta(minutes=ttl_minutes),
            )
        )
        return token

    async def _new_security_token(self, user: User, purpose: str) -> tuple[str, str]:
        if purpose != PASSWORD_RESET_PURPOSE:
            token = secrets.token_urlsafe(32)
            return token, self._hash_token(token)

        for _ in range(20):
            token = f"{secrets.randbelow(1_000_000):06d}"
            token_hash = self._hash_user_security_token(user.id, purpose, token)
            existing = await self.session.scalar(
                select(UserSecurityToken.id)
                .where(UserSecurityToken.token_hash == token_hash)
                .limit(1)
            )
            if existing is None:
                return token, token_hash

        token = secrets.token_urlsafe(32)
        return token, self._hash_token(token)

    async def _consume_password_reset_token(
        self, payload: PasswordResetConfirmRequest
    ) -> UserSecurityToken:
        raw_token = payload.token.strip()
        email = str(payload.email).lower() if payload.email else None
        if email and raw_token.isdigit() and len(raw_token) == 6:
            return await self._consume_password_reset_code(email=email, code=raw_token)
        return await self._consume_security_token(raw_token, purpose=PASSWORD_RESET_PURPOSE)

    async def _consume_password_reset_code(
        self, *, email: str, code: str
    ) -> UserSecurityToken:
        now = utcnow()
        token = await self.session.scalar(
            select(UserSecurityToken)
            .where(
                UserSecurityToken.purpose == PASSWORD_RESET_PURPOSE,
                UserSecurityToken.email == email,
                UserSecurityToken.consumed_at.is_(None),
                UserSecurityToken.expires_at > now,
            )
            .order_by(desc(UserSecurityToken.sent_at), desc(UserSecurityToken.id))
            .limit(1)
        )
        if not token:
            raise ValidationError("invalid_reset_token", "Password reset token is invalid")
        if token.attempt_count >= self.settings.password_reset_code_max_attempts:
            raise ValidationError("invalid_reset_token", "Password reset token is invalid")
        if not hmac.compare_digest(
            token.token_hash,
            self._hash_user_security_token(token.user_id, PASSWORD_RESET_PURPOSE, code),
        ):
            token.attempt_count += 1
            await self.session.commit()
            raise ValidationError(
                "invalid_reset_token",
                "Password reset token is invalid",
                {"attempts_remaining": self._password_reset_attempts_remaining(token)},
            )
        token.consumed_at = utcnow()
        return token

    async def _consume_security_token(self, raw_token: str, *, purpose: str) -> UserSecurityToken:
        token = await self.session.scalar(
            select(UserSecurityToken).where(
                UserSecurityToken.purpose == purpose,
                UserSecurityToken.token_hash == self._hash_token(raw_token),
                UserSecurityToken.consumed_at.is_(None),
            )
        )
        if not token or _as_utc(token.expires_at) <= utcnow():
            raise ValidationError(
                "invalid_reset_token"
                if purpose == PASSWORD_RESET_PURPOSE
                else "invalid_email_change_token",
                "Security token is invalid or expired",
            )
        token.consumed_at = utcnow()
        return token

    async def _consume_open_security_tokens(self, user_id: str, purpose: str) -> None:
        result = await self.session.scalars(
            select(UserSecurityToken).where(
                UserSecurityToken.user_id == user_id,
                UserSecurityToken.purpose == purpose,
                UserSecurityToken.consumed_at.is_(None),
            )
        )
        now = utcnow()
        for token in result:
            token.consumed_at = now

    async def _replace_recovery_codes(self, user: User) -> list[str]:
        await self.session.execute(
            delete(UserRecoveryCode).where(UserRecoveryCode.user_id == user.id)
        )
        codes = [format_recovery_code(secrets.token_hex(4)) for _ in range(RECOVERY_CODE_COUNT)]
        for code in codes:
            self.session.add(
                UserRecoveryCode(
                    user_id=user.id,
                    code_hash=self._hash_security_secret(user.id, "recovery", normalize_code(code)),
                )
            )
        return codes

    async def _verify_two_factor_code(self, user: User, code: str) -> bool:
        normalized = normalize_code(code)
        if user.two_factor_secret and verify_totp(user.two_factor_secret, normalized):
            return True
        recovery = await self.session.scalar(
            select(UserRecoveryCode).where(
                UserRecoveryCode.user_id == user.id,
                UserRecoveryCode.used_at.is_(None),
                UserRecoveryCode.code_hash
                == self._hash_security_secret(user.id, "recovery", normalized),
            )
        )
        if recovery:
            recovery.used_at = utcnow()
            return True
        return False

    async def _revoke_sessions(
        self,
        user_id: str,
        *,
        except_session_id: str | None = None,
        commit: bool = False,
    ) -> int:
        result = await self.session.scalars(
            select(UserSession).where(
                UserSession.user_id == user_id, UserSession.revoked_at.is_(None)
            )
        )
        now = utcnow()
        count = 0
        for session in result:
            if except_session_id and session.id == except_session_id:
                continue
            session.revoked_at = now
            count += 1
        if commit:
            await self.session.commit()
        return count

    def _decode_refresh(self, refresh_token: str) -> dict[str, object]:
        from app.core.security import decode_token

        return decode_token(refresh_token, settings=self.settings, expected_type="refresh")

    def _decode_two_factor_challenge(self, challenge_token: str) -> dict[str, object]:
        from app.core.security import decode_token

        return decode_token(challenge_token, settings=self.settings, expected_type="two_factor")

    def _hash_token(self, token: str) -> str:
        return hmac.new(self.settings.jwt_secret_key.encode(), token.encode(), sha256).hexdigest()

    def _hash_user_security_token(self, user_id: str, purpose: str, token: str) -> str:
        return self._hash_security_secret(user_id, purpose, token)

    def _hash_verification_code(self, user_id: str, code: str) -> str:
        return self._hash_security_secret(user_id, "email_verification", code)

    def _hash_security_secret(self, user_id: str, purpose: str, secret: str) -> str:
        return hmac.new(
            self.settings.jwt_secret_key.encode(),
            f"{purpose}:{user_id}:{secret}".encode(),
            sha256,
        ).hexdigest()

    def _registration_response(
        self, email: str, verification_code: str
    ) -> RegistrationStartResponse:
        return RegistrationStartResponse(
            email=email,
            expires_in_seconds=self.settings.email_verification_code_ttl_minutes * 60,
            resend_after_seconds=self.settings.email_verification_resend_seconds,
            dev_verification_code=(
                verification_code if self.settings.email_delivery_mode == "memory" else None
            ),
        )

    def _resend_remaining_seconds(self, verification: EmailVerificationCode | None) -> int:
        if verification is None:
            return 0
        elapsed_seconds = int((utcnow() - _as_utc(verification.sent_at)).total_seconds())
        return max(0, self.settings.email_verification_resend_seconds - elapsed_seconds)

    def _attempts_remaining(self, verification: EmailVerificationCode) -> int:
        return max(0, self.settings.email_verification_max_attempts - verification.attempt_count)

    def _password_reset_attempts_remaining(self, token: UserSecurityToken) -> int:
        return max(0, self.settings.password_reset_code_max_attempts - token.attempt_count)

    def _request_user_agent(self, request: Request | None) -> str | None:
        if request is None:
            return None
        user_agent = request.headers.get("user-agent")
        return user_agent[:256] if user_agent else None

    def _request_ip(self, request: Request | None) -> str | None:
        if request is None or request.client is None:
            return None
        return request.client.host[:64]


def generate_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def normalize_totp_secret(secret: str) -> str:
    normalized = secret.replace(" ", "").upper()
    try:
        base64.b32decode(_pad_base32(normalized), casefold=True)
    except (binascii.Error, ValueError) as exc:
        raise ValidationError("invalid_two_factor_secret", "Invalid two-factor secret") from exc
    return normalized


def verify_totp(secret: str, code: str, *, at_time: int | None = None, window: int = 1) -> bool:
    normalized_code = normalize_code(code)
    if not normalized_code.isdigit() or len(normalized_code) != TOTP_DIGITS:
        return False
    now = int(at_time if at_time is not None else time.time())
    counter = now // TOTP_STEP_SECONDS
    for offset in range(-window, window + 1):
        if hmac.compare_digest(hotp(secret, counter + offset), normalized_code):
            return True
    return False


def hotp(secret: str, counter: int) -> str:
    key = base64.b32decode(_pad_base32(secret), casefold=True)
    counter_bytes = struct.pack(">Q", counter)
    digest = hmac.new(key, counter_bytes, sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{code % (10**TOTP_DIGITS):0{TOTP_DIGITS}d}"


def totp_otpauth_url(*, issuer: str, username: str, secret: str) -> str:
    label = quote(f"{issuer}:{username}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}&digits={TOTP_DIGITS}&period={TOTP_STEP_SECONDS}"


def format_recovery_code(value: str) -> str:
    normalized = value.upper()
    return f"{normalized[:4]}-{normalized[4:8]}"


def normalize_code(code: str) -> str:
    return code.replace("-", "").replace(" ", "").upper()


def _pad_base32(value: str) -> str:
    return value + "=" * ((8 - len(value) % 8) % 8)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
