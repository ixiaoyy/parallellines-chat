"""Offline contract tests: in-memory SQLite only; no settings, secrets or network."""

import ast
import importlib.util
import json
import sys
import types
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import Column, DateTime, Integer, String, create_engine, select
from sqlalchemy.orm import Session, declarative_base

ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


core = load("app.core.chat_sso", "app/core/chat_sso.py")
schema = load("profile_schema", "app/schemas/chat_sso.py")
Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True)
    status = Column(String)
    username = Column(String)
    display_name = Column(String)
    email = Column(String)
    role = Column(String, default="user")


class EmailVerificationCode(Base):
    __tablename__ = "email_verification_codes"
    id = Column(Integer, primary_key=True)
    user_id = Column(String)
    email = Column(String)
    consumed_at = Column(DateTime)
    expires_at = Column(DateTime)


class UserSecurityToken(Base):
    __tablename__ = "user_security_tokens"
    id = Column(Integer, primary_key=True)
    user_id = Column(String)
    purpose = Column(String)
    email = Column(String)
    payload = Column(String)
    consumed_at = Column(DateTime)
    expires_at = Column(DateTime)


class UserSession:
    pass


def stub(name, **attributes):
    module = types.ModuleType(name)
    module.__dict__.update(attributes)
    sys.modules[name] = module


stub("app.core.config", Settings=object)
stub("app.core.exceptions", AuthenticationError=RuntimeError, PermissionDeniedError=RuntimeError)
stub("app.core.security", decode_token=lambda *args, **kwargs: None)
stub("app.db.base", utcnow=lambda: datetime.now(UTC))
stub("app.models.user", User=User, EmailVerificationCode=EmailVerificationCode,
     UserSecurityToken=UserSecurityToken, UserSession=UserSession)
service = load("profile_service", "app/services/chat_sso.py")


class AsyncSessionAdapter:
    def __init__(self, session):
        self.sync = session

    async def get(self, *args, **kwargs):
        return self.sync.get(*args, **kwargs)

    async def scalar(self, statement):
        return self.sync.scalar(statement)

    async def scalars(self, statement):
        return self.sync.scalars(statement)

    async def commit(self):
        self.sync.commit()

    async def refresh(self, value):
        self.sync.refresh(value)


class ProofTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.user = User(id="subject-1", status="active", username="primary-name",
                         display_name="Display name", email="current@example.invalid")
        self.session.add(self.user)
        self.session.commit()
        self.repo = service.SqlChatSsoRepository(AsyncSessionAdapter(self.session))
        self.now = datetime(2026, 1, 1, 12)

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def initial_proof(self, **changes):
        data = dict(user_id=self.user.id, email=self.user.email,
                    consumed_at=self.now, expires_at=self.now + timedelta(minutes=10))
        data.update(changes)
        self.session.add(EmailVerificationCode(**data))
        self.session.commit()

    def change_proof(self, **changes):
        data = dict(user_id=self.user.id, purpose="email_change", email=self.user.email,
                    consumed_at=self.now, expires_at=self.now + timedelta(minutes=10),
                    payload=json.dumps({"new_email": self.user.email, "email_verified": True}))
        data.update(changes)
        self.session.add(UserSecurityToken(**data))
        self.session.commit()

    async def test_active_status_is_not_verification(self):
        state = await self.repo.get_user(self.user.id)
        self.assertEqual((state.name, state.username), ("Display name", "primary-name"))
        self.assertIsNone(state.email)
        self.assertFalse(state.email_verified)
        self.assertEqual(self.session.scalar(select(User)).id, self.user.id)

    async def test_completed_matching_initial_verification(self):
        self.initial_proof(email="CURRENT@EXAMPLE.INVALID")
        state = await self.repo.get_user(self.user.id)
        self.assertEqual(state.email, self.user.email)
        self.assertTrue(state.email_verified)

    async def test_wrong_subject_email_unconsumed_or_expired_not_proof(self):
        self.initial_proof(user_id="subject-2")
        self.initial_proof(email="old@example.invalid")
        self.initial_proof(consumed_at=None)
        self.initial_proof(consumed_at=self.now + timedelta(hours=1))
        self.assertFalse((await self.repo.get_user(self.user.id)).email_verified)

    async def test_marked_matching_email_change_is_proof(self):
        self.change_proof()
        self.assertTrue((await self.repo.get_user(self.user.id)).email_verified)

    async def test_invalidated_legacy_and_wrong_context_changes_not_proof(self):
        self.change_proof(payload=json.dumps({"new_email": self.user.email}))
        self.change_proof(purpose="password_reset")
        self.change_proof(user_id="subject-2")
        self.change_proof(email="old@example.invalid")
        self.change_proof(consumed_at=None)
        self.change_proof(consumed_at=self.now + timedelta(hours=1))
        self.assertFalse((await self.repo.get_user(self.user.id)).email_verified)

    async def test_changes_are_read_fresh_and_old_proof_does_not_follow_email(self):
        self.initial_proof()
        self.assertTrue((await self.repo.get_user(self.user.id)).email_verified)
        self.user.email = "changed@example.invalid"
        self.user.username = "new-name"
        self.user.display_name = "New display"
        self.session.commit()
        state = await self.repo.get_user(self.user.id)
        self.assertEqual((state.id, state.name, state.username),
                         ("subject-1", "New display", "new-name"))
        self.assertIsNone(state.email)
        self.assertFalse(state.email_verified)

    async def test_missing_user(self):
        self.assertIsNone(await self.repo.get_user("missing"))

    async def test_database_role_is_current_and_strict(self):
        for role in ("user", "moderator", "admin"):
            self.user.role = role
            self.session.commit()
            self.assertEqual((await self.repo.get_user(self.user.id)).role, role)
        for role in (None, "root", "ADMIN", ""):
            self.user.role = role
            self.session.commit()
            with self.assertRaisesRegex(core.SsoDenied, "invalid_main_role"):
                await self.repo.get_user(self.user.id)


class MarkerTests(unittest.TestCase):
    def test_strict_marker_and_email(self):
        email = "current@example.invalid"
        for payload in (None, "not json", "[]", "true", "{}",
                        json.dumps({"new_email": email, "email_verified": "true"}),
                        json.dumps({"new_email": "other@example.invalid", "email_verified": True}),
                        json.dumps({"new_email": 12, "email_verified": True})):
            with self.subTest(payload=payload):
                self.assertFalse(service.verified_email_change(payload, email))
        self.assertTrue(service.verified_email_change(
            json.dumps({"new_email": email.upper(), "email_verified": True}), email))


class MemoryRepository:
    def __init__(self):
        self.user = core.UserState("subject-1", "active", "Display", "primary",
                                   "current@example.invalid", True, "user")
        self.session = core.SessionState("session-1", "subject-1", False)
        self.tokens = {}

    async def get_user(self, _):
        return self.user

    async def get_session(self, _):
        return self.session

    async def find_token(self, token_hash, purpose, **kwargs):
        return self.tokens.get((token_hash, purpose))

    async def add_token(self, token):
        self.tokens[(token.token_hash, token.purpose)] = token

    async def consume(self, token, now):
        if token.consumed_at:
            return False
        token.consumed_at = now
        return True

    async def commit(self):
        pass


class CoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.repo = MemoryRepository()
        self.now = datetime(2026, 1, 1, tzinfo=UTC)
        tokens = iter(("a" * 32, "b" * 32))
        self.core = core.ChatSsoCore(self.repo, finish_url="http://localhost/auth/oidc/interaction/finish",
                                    login_policy="active_main", clock=lambda: self.now,
                                    issue_token=lambda: next(tokens))
        self.login = core.VerifiedLogin("subject-1", "session-1", int(self.now.timestamp()) + 3600)
        await self.core.approve(self.login, "interaction-1")

    async def test_exchange_and_fresh_session_profile_preserve_identity(self):
        exchanged = await self.core.exchange(client_id=core.CLIENT_ID,
                                              interaction_id="interaction-1", approval_code="a" * 32)
        parsed = schema.ChatSsoExchangeResponse(**exchanged)
        self.assertEqual(parsed.username, "primary")
        self.assertTrue(parsed.email_verified)
        self.assertEqual(parsed.role, "user")
        self.repo.user = replace(self.repo.user, username="changed", name="Changed",
                                 email="changed@example.invalid", role="admin")
        current = await self.core.session_state(client_id=core.CLIENT_ID, binding_id="b" * 32)
        self.assertEqual((current["sub"], current["binding_id"], current["auth_expires_at"]),
                         (exchanged["sub"], exchanged["binding_id"], exchanged["auth_expires_at"]))
        self.assertEqual(current["username"], "changed")
        self.assertEqual(current["email"], "changed@example.invalid")
        self.assertEqual(current["role"], "admin")
        with self.assertRaises(core.SsoDenied):
            await self.core.exchange(client_id=core.CLIENT_ID,
                                     interaction_id="interaction-1", approval_code="a" * 32)

    async def test_unverified_email_not_exposed(self):
        self.repo.user = replace(self.repo.user, email_verified=False)
        state = await self.core.session_state(client_id=core.CLIENT_ID, binding_id="b" * 32)
        self.assertIsNone(state["email"])
        self.assertFalse(state["email_verified"])

    async def test_revoked_session_has_no_profile(self):
        self.repo.session = replace(self.repo.session, revoked=True)
        state = schema.ChatSsoStateResponse(**await self.core.session_state(
            client_id=core.CLIENT_ID, binding_id="b" * 32))
        self.assertFalse(state.active)
        self.assertIsNone(state.sub)
        self.assertIsNone(state.email)
        self.assertFalse(state.email_verified)
        self.assertIsNone(state.role)

    async def test_wrong_client_and_cross_subject_session_remain_denied(self):
        with self.assertRaises(core.SsoDenied):
            await self.core.session_state(client_id="wrong", binding_id="b" * 32)
        self.repo.session = replace(self.repo.session, user_id="subject-2")
        self.assertFalse((await self.core.session_state(client_id=core.CLIENT_ID,
                                                       binding_id="b" * 32))["active"])

    async def test_optional_old_role_and_invalid_role(self):
        self.repo.user = replace(self.repo.user, role=None)
        legacy = await self.core.session_state(client_id=core.CLIENT_ID, binding_id="b" * 32)
        self.assertNotIn("role", legacy)
        self.assertIsNone(schema.ChatSsoStateResponse(**legacy).role)
        self.repo.user = replace(self.repo.user, role="root")
        rejected = await self.core.session_state(client_id=core.CLIENT_ID, binding_id="b" * 32)
        self.assertFalse(rejected["active"])
        with self.assertRaises(ValueError):
            schema.ChatSsoStateResponse(**{**legacy, "role": "root"})


class ChangeWriterTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_redeemed_token_receives_proof(self):
        # Compile the real production method in isolation, avoiding its settings
        # and side-effect imports. Its SQL, mutation order and payload are real.
        tree = ast.parse((ROOT / "app/services/auth.py").read_text(encoding="utf8"))
        method = next(node for cls in tree.body if isinstance(cls, ast.ClassDef)
                      for node in cls.body if isinstance(node, ast.AsyncFunctionDef)
                      and node.name == "confirm_email_change")
        method.returns = None
        for argument in method.args.args:
            argument.annotation = None
        code = compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])),
                       "auth-confirm-email-change", "exec")
        scope = dict(json=json, User=User, select=select, EMAIL_CHANGE_PURPOSE="email_change",
                     ValidationError=RuntimeError, ConflictError=RuntimeError)
        exec(code, scope)
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            now = datetime(2026, 1, 1)
            user = User(id="subject-1", status="active", username="unchanged",
                        email="old@example.invalid")
            actual = UserSecurityToken(user_id=user.id, purpose="email_change",
                        email="new@example.invalid", payload=json.dumps({"new_email": "new@example.invalid"}),
                        consumed_at=now, expires_at=now + timedelta(minutes=10))
            invalidated = UserSecurityToken(user_id=user.id, purpose="email_change",
                        email="unused@example.invalid", payload=json.dumps({"new_email": "unused@example.invalid"}),
                        expires_at=now + timedelta(minutes=10))
            session.add_all((user, actual, invalidated))
            session.commit()
            async def consume(*args, **kwargs):
                return actual
            async def invalidate(*args):
                invalidated.consumed_at = now
            context = types.SimpleNamespace(session=AsyncSessionAdapter(session),
                        _consume_security_token=consume, _consume_open_security_tokens=invalidate)
            result = await scope["confirm_email_change"](context, types.SimpleNamespace(token="synthetic"))
            self.assertEqual(result.email, "new@example.invalid")
            self.assertEqual((result.id, result.username), ("subject-1", "unchanged"))
            self.assertTrue(json.loads(actual.payload)["email_verified"])
            self.assertNotIn("email_verified", json.loads(invalidated.payload))
        engine.dispose()


if __name__ == "__main__":
    unittest.main(verbosity=2)
