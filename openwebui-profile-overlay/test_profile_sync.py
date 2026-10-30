import ast
import asyncio
import hashlib
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch as mock_patch

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from main_profile_sync import enabled, sync_profile
from apply import patch, OAUTH_SHA256, USERS_SHA256


class ProfileTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.user = SimpleNamespace(id='stable-id', name='Old name', email='old@example.invalid',
            role='admin', oauth={'oidc': {'sub': 'stable-sub'}}, password='unchanged')
        self.config = SimpleNamespace(OAUTH_UPDATE_NAME_ON_LOGIN=True, OAUTH_UPDATE_EMAIL_ON_LOGIN=True,
            OAUTH_USERNAME_CLAIM='preferred_username', OAUTH_EMAIL_CLAIM='email')
        self.claims = {'preferred_username': 'Main username', 'email': 'new@example.invalid', 'email_verified': True}
        self.users = SimpleNamespace(get_user_by_email=AsyncMock(return_value=None),
            update_user_by_id=AsyncMock(return_value=self.user))

    async def test_one_atomic_profile_only_write(self):
        user, fields = await sync_profile(self.user, self.claims, self.config, self.users)
        self.users.update_user_by_id.assert_awaited_once_with('stable-id',
            {'name': 'Main username', 'email': 'new@example.invalid'}, db=None)
        self.assertEqual(fields, ['name', 'email'])
        self.assertEqual(user.id, 'stable-id')
        self.assertEqual(user.role, 'admin')
        self.assertEqual(user.oauth, {'oidc': {'sub': 'stable-sub'}})

    async def test_collision_rejects_before_any_name_or_email_write(self):
        self.users.get_user_by_email.return_value = SimpleNamespace(id='other-id')
        with self.assertRaises(HTTPException) as caught:
            await sync_profile(self.user, self.claims, self.config, self.users)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertNotIn(self.claims['email'], caught.exception.detail)
        self.users.update_user_by_id.assert_not_awaited()

    async def test_unverified_email_never_updates_even_name(self):
        for value in [False, 'true', 1, None]:
            with self.assertRaises(HTTPException):
                await sync_profile(self.user, {**self.claims, 'email_verified': value}, self.config, self.users)
        self.users.update_user_by_id.assert_not_awaited()

    async def test_racing_conflict_rolls_back_and_redacts_database_values(self):
        self.users.update_user_by_id.side_effect = IntegrityError('email secret@example.invalid', {}, Exception('sensitive'))
        db = SimpleNamespace(rollback=AsyncMock())
        with self.assertRaises(HTTPException) as caught:
            await sync_profile(self.user, self.claims, self.config, self.users, db=db)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertNotIn('@', caught.exception.detail)
        db.rollback.assert_awaited_once()

    async def test_failure_cannot_silently_login_with_stale_profile(self):
        for failure in [None, RuntimeError('private database details')]:
            self.users.update_user_by_id.return_value = failure
            self.users.update_user_by_id.side_effect = failure if isinstance(failure, Exception) else None
            with self.assertRaises(HTTPException) as caught:
                await sync_profile(self.user, self.claims, self.config, self.users)
            self.assertEqual(caught.exception.status_code, 409)
            self.assertNotIn('private', caught.exception.detail)

    async def test_unchanged_profile_does_not_write(self):
        claims = {**self.claims, 'preferred_username': self.user.name, 'email': self.user.email.upper()}
        _, fields = await sync_profile(self.user, claims, self.config, self.users)
        self.assertEqual(fields, [])
        self.users.update_user_by_id.assert_not_awaited()

    def test_feature_only_selected_oidc_provider(self):
        with mock_patch.dict(os.environ, {'MAIN_OAUTH_PROFILE_SYNC_ENABLED': 'true'}):
            self.assertTrue(enabled('oidc'))
            self.assertFalse(enabled('github'))
        with mock_patch.dict(os.environ, {'MAIN_OAUTH_PROFILE_SYNC_ENABLED': 'false'}):
            self.assertFalse(enabled('oidc'))

    def test_exact_upstream_patch_compiles_and_keeps_merge_and_role_code(self):
        source_root = Path(__file__).parents[1] / '.build/openwebui-profile-baseline'
        source = (source_root / 'oauth.py').read_bytes()
        users = (source_root / 'users.py').read_bytes()
        self.assertEqual(hashlib.sha256(source).hexdigest(), OAUTH_SHA256)
        self.assertEqual(hashlib.sha256(users).hexdigest(), USERS_SHA256)
        updated = patch(source.decode('utf-8'))
        ast.parse(updated)
        self.assertIn('main_profile_bound = user is not None and main_profile_sync_enabled(provider)', updated)
        self.assertEqual(updated.count('user = await Users.get_user_by_oauth_sub(provider, sub, db=db)'), 1)
        self.assertEqual(updated.count('if auth_config.OAUTH_MERGE_ACCOUNTS_BY_EMAIL:'), 1)
        tree = ast.parse(users.decode('utf-8'))
        table = next(x for x in tree.body if isinstance(x, ast.ClassDef) and x.name == 'UsersTable')
        method = next(x for x in table.body if isinstance(x, ast.AsyncFunctionDef) and x.name == 'update_user_by_id')
        text = ast.get_source_segment(users.decode('utf-8'), method)
        self.assertEqual(text.count('await session.commit()'), 1)
        self.assertIn("update(Auth).where(Auth.id == id).values(email=updated['email'])", text)


if __name__ == '__main__':
    unittest.main()
