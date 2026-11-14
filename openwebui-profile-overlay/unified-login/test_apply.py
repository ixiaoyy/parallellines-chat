"""将正式 overlay 应用到显式的 silent 基线副本，检查真实消费者与防漂移。"""
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location('unified_overlay', HERE/'apply.py')
overlay = importlib.util.module_from_spec(spec); spec.loader.exec_module(overlay)


class ApplyContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = os.environ.get('OPENWEBUI_SILENT_BASELINE')
        if not root:
            raise unittest.SkipTest('OPENWEBUI_SILENT_BASELINE must name the explicit prepared local source')
        cls.baseline = Path(root).resolve(strict=True)

    def fixture(self):
        temporary = tempfile.TemporaryDirectory(prefix='pl-unified-overlay-'); self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for relative in overlay.PATCHERS:
            target = root/relative; target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((self.baseline/relative).read_bytes())
        return root

    def test_check_is_read_only_final_manifest_and_consumer_wiring(self):
        root = self.fixture()
        before = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*') if path.is_file()}
        overlay.apply(root, check=True)
        self.assertEqual(before, {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*') if path.is_file()})
        output = overlay.apply(root)
        manifest = json.loads((HERE/'manifest.json').read_text(encoding='utf-8'))
        for relative, content in output.items():
            self.assertEqual(hashlib.sha256(content).hexdigest(), manifest['files'][relative]['after_sha256'])
            if relative.endswith('.py'): ast.parse(content.decode())
        auth = (root/'backend/open_webui/utils/auth.py').read_text(encoding='utf-8')
        self.assertIn('await verify_web_session(user, data, request)', auth)
        self.assertIn('await verify_web_session(user, decoded)', auth)
        self.assertIn('oauth_session_id=oauth_session_id, provider=provider', auth)
        self.assertIn("extra_claims = {'pl_unified_recovery': True}", auth)
        self.assertIn('oauth_session_id=challenge.oauth_session_id', (root/'backend/open_webui/routers/mfa.py').read_text(encoding='utf-8'))
        app = (root/'src/routes/(app)/+layout.svelte').read_text(encoding='utf-8')
        self.assertIn('if ($config?.features?.main_unified_login) { loaded = true; return; }', app)
        self.assertIn('{#if $user || !$config?.features?.main_unified_login}', app)
        self.assertIn('<GuestChatShell siteName={$WEBUI_NAME} />', app)
        root_layout = (root/'src/routes/+layout.svelte').read_text(encoding='utf-8')
        self.assertIn('unifiedIdentityReset = true; bootstrapRequests.cancel();', root_layout)
        self.assertIn('if (value && unifiedIdentityReset) { user.set(null); return; }', root_layout)
        page = (root/'src/routes/auth/+page.svelte').read_text(encoding='utf-8')
        self.assertIn("!$page.url.searchParams.has('mfa')", page)
        self.assertIn('!$config?.features?.main_unified_login && $config?.oauth?.auto_redirect', page)
        router = ast.parse((root/'backend/open_webui/routers/auths.py').read_text(encoding='utf-8'))
        for name in ['signin', 'signup', 'ldap_auth']:
            method = next(value for value in router.body if isinstance(value, ast.AsyncFunctionDef) and value.name == name)
            self.assertIsInstance(method.body[1], ast.If)
            self.assertIn("main_unified_login_required", ast.unparse(method.body[1]))
        # 不引入按账户踢所有设备；保留原 API key 入口、剪枝和公开分享路由。
        self.assertNotIn('revoke_user_tokens', (root/'backend/open_webui/utils/main_unified_login.py').read_text(encoding='utf-8'))
        self.assertFalse(any('share/' in relative for relative in output))
        original = (self.baseline/'backend/open_webui/utils/auth.py').read_text(encoding='utf-8')
        start = '    # auth by api key\n'
        end = '    # auth by jwt token\n'
        self.assertEqual(auth[auth.index(start):auth.index(end)], original[original.index(start):original.index(end)])

    def test_changed_baseline_and_existing_new_file_stop_before_any_write(self):
        root = self.fixture(); first = next(iter(overlay.PATCHERS))
        (root/first).write_text('concurrent change')
        with self.assertRaisesRegex(ValueError, 'baseline_changed'): overlay.apply(root)
        self.assertFalse((root/next(iter(overlay.NEW_FILES))).exists())
        root = self.fixture(); new = root/next(iter(overlay.NEW_FILES)); new.parent.mkdir(parents=True, exist_ok=True); new.write_text('other owner')
        with self.assertRaisesRegex(ValueError, 'new_file_collision'): overlay.apply(root)
        self.assertEqual(new.read_text(encoding='utf-8'), 'other owner')

    def test_backend_only_recipe_never_requires_or_writes_frontend(self):
        root = self.fixture()
        output = overlay.apply(root, backend_only=True)
        self.assertTrue(all(relative.startswith('backend/') for relative in output))
        self.assertEqual((root/'src/routes/+layout.svelte').read_bytes(), (self.baseline/'src/routes/+layout.svelte').read_bytes())

    def test_guest_copy_is_normal_controls_not_implementation_instructions(self):
        guest = (HERE/'GuestChatShell.svelte').read_text(encoding='utf-8')
        for rejected in ['先写下你的想法', '只保留在本标签页', '不会自动发送', '登录后查看你的历史对话',
                         '主站账号登录', '登录后选择模型', '本地游客验收草稿']:
            self.assertNotIn(rejected, guest)
        self.assertIn('placeholder="输入消息…"', guest)
        self.assertIn('class="guest-model" on:click={login}>选择模型</button>', guest)
        self.assertIn('onMount(() => { draft = readGuestDraft(); });', guest)
        self.assertIn('on:input={remember}', guest)


if __name__ == '__main__': unittest.main()
