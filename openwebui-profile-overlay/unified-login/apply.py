"""接续已核 silent-recovery 源码的统一登录补丁；正式维护此 overlay，不直接维护构建树。"""
import argparse
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).parent


def once(source, before, after):
    """只替换唯一已知接点，已有并发变更/版本漂移明确停止。"""
    if source.count(before) != 1:
        raise ValueError('unified_source_anchor_changed:' + before[:70])
    return source.replace(before, after, 1)


def patch_app(source):
    """游客只渲染薄外壳，不启动旧私有页或打断已登录用户的原生初始化。"""
    source = once(source, "\timport Sidebar from '$lib/components/layout/Sidebar.svelte';",
                  "\timport GuestChatShell from '$lib/components/layout/GuestChatShell.svelte';\n\timport { WEBUI_NAME } from '$lib/stores';\n\timport Sidebar from '$lib/components/layout/Sidebar.svelte';")
    source = once(source, "\t\tif ($user === undefined || $user === null) {\n\t\t\tawait gotoAuth();",
                  "\t\tif ($user === undefined || $user === null) {\n\t\t\tif ($config?.features?.main_unified_login) { loaded = true; return; }\n\t\t\tawait gotoAuth();")
    source = once(source, "\t$: if (loaded && ($user === undefined || $user === null)) {",
                  "\t$: if (loaded && !$config?.features?.main_unified_login && ($user === undefined || $user === null)) {")
    source = once(source, '</script>', "\t// 统一模式游客不能打开私有设置弹窗。\n\t$: if (!$user && $config?.features?.main_unified_login) { $showSettings = false; $showChangelog = false; }\n</script>")
    source = once(source, '<SettingsModal bind:show={$showSettings} />\n<ChangelogModal bind:show={$showChangelog} />', '{#if $user || !$config?.features?.main_unified_login}\n<SettingsModal bind:show={$showSettings} />\n<ChangelogModal bind:show={$showChangelog} />\n{/if}')
    marker = '</div>\n{/if}\n\n<style>'
    return once(source, marker, '</div>\n{:else if $config?.features?.main_unified_login}\n\t<GuestChatShell siteName={$WEBUI_NAME} />\n{/if}\n\n<style>')


def patch_root(source):
    """根布局只在验证后的用户连接 Socket；主站非敏感 frame 负责有限身份接续。"""
    setup = r"""
    let unifiedSocketPending = false;
    let unifiedIdentityReset = false;
    onMount(() => {
        let stopState;
        // 中央身份变化先卸载旧私有页面和 Socket，再清缓存/接续新账号。
        const resetPrivate = () => {
            // 取消旧启动读取；已在途的旧回调也不能在顶层接续前重新挂载旧账号。
            unifiedIdentityReset = true; bootstrapRequests.cancel();
            loaded = true; sessionRestoreError = false; bootstrapError = false;
            user.set(null); $socket?.disconnect(); socket.set(null);
            models.set([]); settings.set({}); tags.set([]); channels.set([]);
            channelId.set(null); channelRequestQueues.set({}); chatId.set('');
            toolServers.set([]); terminalServers.set([]); connectedUserTerminals.set(new Map());
            resetChatListState();
        };
        window.addEventListener('pl:unified-private-reset', resetPrivate);
        const stopConfig = config.subscribe((value) => {
            if (value?.features?.main_unified_login && !stopState) {
                stopState = watchUnifiedSession((state) => {
                    void syncUnifiedSession(state).then((result) => {
                        if (result?.storage_unavailable) toast.error('登录信息无法更新，请清除本站数据后重试。');
                    }).catch(() => console.warn('main_unified_state_unavailable'));
                });
            }
        });
        const stopUser = user.subscribe((value) => {
            if (!$config?.features?.main_unified_login) return;
            if (value && unifiedIdentityReset) { user.set(null); return; }
            if (!value) { $socket?.disconnect(); socket.set(null); return; }
            if (!$socket && !unifiedSocketPending) {
                unifiedSocketPending = true;
                void setupSocket($config.features?.enable_websocket ?? true).finally(() => { unifiedSocketPending = false; });
            }
        });
        return () => { stopConfig(); stopUser(); stopState?.(); window.removeEventListener('pl:unified-private-reset', resetPrivate); };
    });
"""
    source = once(source, "\tlet loaded = false;", "\tlet loaded = false;\n" + setup)
    source = once(source, '<script>', "<script>\n\timport { watchUnifiedSession, syncUnifiedSession } from '$lib/apis/main-unified-browser.mjs';")
    source = once(source, "import { refreshChatList } from '$lib/stores/chatList';", "import { refreshChatList, resetChatListState } from '$lib/stores/chatList';")
    source = once(source, "\t\t\t\tawait setupSocket($config.features?.enable_websocket ?? true);",
                  "\t\t\t\tif (!$config.features?.main_unified_login) await setupSocket($config.features?.enable_websocket ?? true);")
    source = once(source, '{:else if $isApp}', '{:else if $isApp && ($user || !$config?.features?.main_unified_login)}')
    return once(source, '{#if $config?.features.enable_community_sharing && !sessionRestoreError && !bootstrapError}',
                '{#if $config?.features.enable_community_sharing && ($user || !$config?.features?.main_unified_login) && !sessionRestoreError && !bootstrapError}')


def patch_auth_page(source):
    """保留原生 OAuth/MFA 回调，普通游客不显示第二套密码表单。"""
    source = once(source, '<script lang="ts">', '<script lang="ts">\n\timport GuestChatShell from \'$lib/components/layout/GuestChatShell.svelte\';')
    source = once(source, "$socket.emit('user-join',", "$socket?.emit('user-join',")
    source = once(source, 'if ($config?.oauth?.auto_redirect && !logout && !form && !error)', 'if (!$config?.features?.main_unified_login && $config?.oauth?.auto_redirect && !logout && !form && !error)')
    source = once(source, 'if (($config?.features?.auth_trusted_header ?? false) || $config?.features?.auth === false)', 'if (!$config?.features?.main_unified_login && (($config?.features?.auth_trusted_header ?? false) || $config?.features?.auth === false))')
    # MFA 仍显示上游原生第二因素界面；不能被游客壳遮掉，也不绕过原挑战。
    source = once(source, '</svelte:head>', '</svelte:head>\n\n{#if $config?.features?.main_unified_login && !mfaChallenge && !pendingApproval && !$page.url.searchParams.has(\'mfa\')}\n\t<GuestChatShell siteName={$WEBUI_NAME} />\n{:else}')
    return source + '\n{/if}\n'


def patch_auth_api(source):
    """Main 单次退出票据不改写为 OP logout URL，旧原生退出逻辑保持。"""
    source = "import { isMainLogout } from '../main-unified-browser.mjs';\n" + source
    return once(source, 'export const getLogoutRedirectUrl = (redirectUrl?: string | null) => {',
                "export const getLogoutRedirectUrl = (redirectUrl?: string | null) => {\n\tif (isMainLogout(redirectUrl)) return redirectUrl;")


def patch_chat(source):
    """空的新对话只回填游客草稿；从不调用 submitPrompt 或自动发送。"""
    source = once(source, '<script lang="ts">', '<script lang="ts">\n\timport { consumeGuestDraft } from \'$lib/apis/main-unified-browser.mjs\';')
    # initNewChat 最终确定输入之后再回填，避免覆盖明确 URL 提示词或已有对话。
    start = source.index('\tconst initNewChat = async () => {')
    end = source.index('\n\tconst loadChat', start)
    method = source[start:end]
    # 使用独立 sessionStorage key，原 clearChatInputStorage 不会删除游客刚写的内容。
    method = method.replace('\n\t};', "\n\t\tif ($config?.features?.main_unified_login && !prompt) { const guestDraft = consumeGuestDraft(); if (guestDraft) prompt = guestDraft; }\n\t};", 1)
    return source[:start] + method + source[end:]


def patch_backend_auth(source):
    """保留原签名/MFA/角色检查，额外约束统一 Web JWT；API key 分支不动。"""
    source = once(source, "            user = await Users.get_user_by_id(data['id'])\n", "            user = await Users.get_user_by_id(data['id'])\n            if user is not None:\n                from open_webui.utils.main_unified_login import verify_web_session\n                await verify_web_session(user, data, request)\n")
    source = once(source, '    except Exception as e:\n        # Delete the token cookie', "    except Exception as e:\n        # 仅新 authority 不可用保留 Cookie，真实失效仍走原清理。\n        if isinstance(e, HTTPException) and e.status_code == 503 and isinstance(e.detail, dict) and e.detail.get('code') == 'main_unified_authority_unavailable':\n            raise\n        # Delete the token cookie")
    marker = '    return user\n\n\nasync def get_verified_user_by_id('
    source = once(source, marker, '    from open_webui.utils.main_unified_login import verify_web_session\n    await verify_web_session(user, decoded)\n    return user\n\n\nasync def get_verified_user_by_id(')
    source = once(source, "        request, user, db, response, set_cookie=set_cookie, source=source, auth=current\n", "        request, user, db, response, set_cookie=set_cookie, source=source, auth=current,\n        oauth_session_id=oauth_session_id, provider=provider\n")
    # 在原生 MFA 开始前拒绝普通独立登录；显式恢复仍完整保留密码/MFA 流程。
    source = once(source, '    auth = auth or await get_auth(user.id)\n', "    from open_webui.utils.main_unified_login import enabled as unified_enabled, recovery_allowed, denied\n    if unified_enabled() and source != 'oauth' and not recovery_allowed(user, request):\n        raise denied('main_unified_login_required', 403)\n    auth = auth or await get_auth(user.id)\n")
    source = once(source, '    mfa_verified=False,\n    auth_time=None,\n):', '    mfa_verified=False,\n    auth_time=None,\n    oauth_session_id=None,\n    provider=None,\n):')
    marker = "    token = create_token(\n        {\n            'id': user.id,"
    replacement = "    from open_webui.utils.main_unified_login import enabled as unified_enabled, session_claims, recovery_allowed, denied\n    extra_claims = {}\n    if unified_enabled():\n        if source == 'oauth':\n            extra_claims = await session_claims(user, oauth_session_id)\n        elif recovery_allowed(user, request):\n            extra_claims = {'pl_unified_recovery': True}\n        else:\n            raise denied('main_unified_login_required')\n    token = create_token(\n        {\n            **extra_claims,\n            'id': user.id,"
    return once(source, marker, replacement)


def patch_oauth(source):
    """所有新 Main 回调在既有验证后记录统一证明；不改普通 state/PKCE/角色和已有恢复。"""
    # Authlib 的 token.userinfo 可只是 ID-token claims；统一模式必须取发行方实时 UserInfo 的可信标记。
    userinfo = '            if (\n                (not user_data)\n'
    source = once(source, userinfo,
                  "            from open_webui.utils.main_unified_login import enabled as unified_enabled\n"
                  "            if (\n                (provider == 'oidc' and unified_enabled())\n"
                  "                or (not user_data)\n")
    marker = '            sub = str(sub)\n'
    return once(source, marker, marker + "            # 只从已验证的原生 OIDC UserInfo 取得中央会话证明。\n            from open_webui.utils.main_unified_login import attach_login_binding\n            await attach_login_binding(provider, token, user_data)\n")


def patch_oauth_model(source):
    """原生 token 刷新保留同一服务端 Main 绑定，不改 Session 剪枝和其它设备策略。"""
    marker = '        """Update OAuth session tokens"""\n'
    source = once(source, marker, marker + "        from open_webui.utils.main_unified_login import preserve_binding\n        previous = await self.get_session_by_id(session_id, db=db)\n        if previous is not None:\n            token = preserve_binding(previous.token, token)\n")
    marker = '    async def get_session_by_id_and_user_id(\n'
    strict = '''    async def get_session_by_id_strict(self, session_id: str) -> Optional[OAuthSessionModel]:
        """统一 Web 核验专用：只有真实缺行返回 None，数据库/解密故障交调用边界转不可用。"""
        async with get_async_db_context() as db:
            result = await db.execute(select(OAuthSession).filter_by(id=session_id))
            row = result.scalars().first()
            if row is None:
                return None
            return OAuthSessionModel(id=row.id, user_id=row.user_id, provider=row.provider,
                                     token=self._decrypt_token(row.token), expires_at=row.expires_at,
                                     created_at=row.created_at, updated_at=row.updated_at)

'''
    return once(source, marker, strict + marker)


def patch_mfa(source):
    """原生第二因素完成后继续使用此前已绑定的 OAuthSession，不绕过 MFA。"""
    return once(source, '        source=challenge.auth_method,\n', '        source=challenge.auth_method,\n        oauth_session_id=challenge.oauth_session_id,\n        provider=challenge.provider,\n')


def patch_router(source):
    """新增本地会话复核/清理与隔离恢复入口，用户主动退出接一次 Main 票据。"""
    marker = "@router.post('/signout')\nasync def signout(request: Request, response: Response, db: AsyncSession = Depends(get_async_session)):\n"
    source = once(source, marker, marker + "    from open_webui.utils.main_unified_login import enabled, unified_signout\n    if enabled():\n        result = await unified_signout(request)\n        if result is not None:\n            return result\n")
    # 独立口在任何认证、自动开户或 LDAP 连接之前明确拒绝；旧部署开关关闭时原样运行。
    for signature in [
        "async def signin(\n    request: Request,\n    response: Response,\n    form_data: SigninForm,\n    db: AsyncSession = Depends(get_async_session),\n):\n",
        "async def signup(\n    request: Request,\n    response: Response,\n    form_data: SignupForm,\n    db: AsyncSession = Depends(get_async_session),\n):\n",
        "async def ldap_auth(\n    request: Request,\n    response: Response,\n    form_data: LdapForm,\n    db: AsyncSession = Depends(get_async_session),\n):\n",
    ]:
        source = once(source, signature, signature + "    # 普通用户唯一凭据入口在主站；此处不接受第二套密码/注册/LDAP。\n    from open_webui.utils.main_unified_login import enabled, denied\n    if enabled():\n        raise denied('main_unified_login_required', 403)\n")
    password = "async def update_password(\n    request: Request,\n    form_data: UpdatePasswordForm,\n    session_user=Depends(get_current_user),\n    db: AsyncSession = Depends(get_async_session),\n):\n"
    source = once(source, password, password + "    # 普通账号密码由主站维护；只给已明确入列的本地恢复管理员保留原能力。\n    from open_webui.utils.main_unified_login import enabled, recovery_allowed, denied\n    if enabled() and not recovery_allowed(session_user, request):\n        raise denied('main_unified_login_required', 403)\n")
    extra = "\n\n# 新 Web 私有访问已由 get_human_user 完成 Main 绑定复核，浏览器只得到非敏感主体。\n@router.get('/unified/session')\nasync def main_unified_session(request: Request, user=Depends(get_human_user)):\n    from open_webui.utils.main_unified_login import JWT_KEY, recovery_allowed\n    binding = request.state.claims.get(JWT_KEY)\n    if isinstance(binding, dict):\n        return {'unified': True, 'subject': binding['subject'], 'recovery': False}\n    if recovery_allowed(user, request):\n        return {'unified': False, 'subject': None, 'recovery': True}\n    raise HTTPException(401, detail={'code': 'main_unified_reconnect_required'})\n\n\n@router.post('/unified/local-signout')\nasync def main_unified_local_signout(request: Request):\n    from open_webui.utils.main_unified_login import local_signout\n    return await local_signout(request)\n"
    recovery = '''

@router.post('/unified/recovery/signin', response_model=SigninResult)
async def main_unified_recovery_signin(request: Request, response: Response, form_data: SigninForm,
                                     db: AsyncSession = Depends(get_async_session)):
    """显式本地管理员恢复：稳定 ID 白名单、真实 loopback、原生密码和 MFA，不公开第二个登录站。"""
    from open_webui.utils.main_unified_login import enabled, recovery_transport_allowed, recovery_allowed, denied
    if not enabled() or not recovery_transport_allowed(request) or not ENABLE_PASSWORD_AUTH:
        raise denied('main_unified_recovery_denied', 403)
    if await signin_rate_limiter.is_limited(request.app.state.redis, form_data.email.lower()):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail=ERROR_MESSAGES.RATE_LIMIT_EXCEEDED)
    existing = await Users.get_user_by_email(form_data.email.lower(), db=db)
    if not recovery_allowed(existing, request):
        raise denied('main_unified_recovery_denied', 403)
    if (await get_mfa_config()).ENABLE_MFA:
        await limit_account(existing.id, 'password')
    authenticated = await Auths.authenticate_user(form_data.email.lower(),
                                                 lambda pw: verify_password(form_data.password, pw), db=db)
    user, auth = authenticated if authenticated else (None, None)
    if not recovery_allowed(user, request):
        raise HTTPException(400, detail=ERROR_MESSAGES.INVALID_CRED)
    return await create_signin_response(request, user, db, response, set_cookie=True, source='password', auth=auth)
'''
    return source + extra + recovery


def patch_main(source):
    """公开配置只增加展示开关，不输出任何 RP 密钥、Main token 或绑定值。"""
    source = once(source, "            'auth': WEBUI_AUTH,\n", "            'auth': WEBUI_AUTH,\n            'main_unified_login': os.environ.get('MAIN_UNIFIED_LOGIN_ENABLED') == 'true',\n")
    # 只改变统一模式公开的入口能力，避免已有数据库配置重新显示独立凭据表单。
    for field, key in [('enable_ldap', 'ldap.enable'), ('enable_signup', 'ui.enable_signup'),
                       ('enable_login_form', 'ui.enable_login_form'), ('enable_password_change_form', 'ui.enable_password_change_form')]:
        source = once(source, f"'{field}': config.get('{key}'),", f"'{field}': False if os.environ.get('MAIN_UNIFIED_LOGIN_ENABLED') == 'true' else config.get('{key}'),")
    return source


PATCHERS = {
    'src/routes/(app)/+layout.svelte': patch_app,
    'src/routes/+layout.svelte': patch_root,
    'src/routes/auth/+page.svelte': patch_auth_page,
    'src/lib/apis/auths/index.ts': patch_auth_api,
    'src/lib/components/chat/Chat.svelte': patch_chat,
    'backend/open_webui/utils/auth.py': patch_backend_auth,
    'backend/open_webui/utils/oauth.py': patch_oauth,
    'backend/open_webui/models/oauth_sessions.py': patch_oauth_model,
    'backend/open_webui/routers/mfa.py': patch_mfa,
    'backend/open_webui/routers/auths.py': patch_router,
    'backend/open_webui/main.py': patch_main,
}
NEW_FILES = {'src/lib/apis/main-unified-browser.mjs': 'main-unified-browser.mjs',
             'src/lib/components/layout/GuestChatShell.svelte': 'GuestChatShell.svelte',
             'backend/open_webui/utils/main_unified_login.py': 'main_unified_login.py'}


def prepare(root, backend_only=False, *, frontend_only=False):
    """按选定范围准备并核对补丁字节；两个单侧模式互斥。"""
    if backend_only and frontend_only:
        raise ValueError('unified_overlay_modes_conflict')
    manifest = json.loads((HERE / 'manifest.json').read_text())
    result = {}
    for relative, patch in PATCHERS.items():
        # 独立前端构建只核 Web 前像；部署所需的后端版本仍由原清单声明。
        is_backend = relative.startswith('backend/')
        if (backend_only and not is_backend) or (frontend_only and is_backend):
            continue
        content = (root / relative).read_bytes()
        if hashlib.sha256(content).hexdigest() != manifest['files'][relative]['before_sha256']:
            raise ValueError('unified_source_baseline_changed:' + relative)
        result[relative] = patch(content.decode()).encode()
    for relative, own in NEW_FILES.items():
        is_backend = relative.startswith('backend/')
        if (backend_only and not is_backend) or (frontend_only and is_backend):
            continue
        if (root / relative).exists():
            raise ValueError('unified_new_file_collision:' + relative)
        result[relative] = (HERE / own).read_bytes()
    # 应用脚本和正式源均与同一配方锁定，不能只更新草稿而交付旧 manifest。
    for relative, content in result.items():
        if hashlib.sha256(content).hexdigest() != manifest['files'][relative]['after_sha256']:
            raise ValueError('unified_output_manifest_changed:' + relative)
    return result


def apply(root, check=False, backend_only=False, *, frontend_only=False):
    """返回隔离源码的补丁字节；check 不写，frontend_only 不读写后端前像。"""
    prepared = prepare(root, backend_only, frontend_only=frontend_only)
    if not check:
        for relative, content in prepared.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    return prepared


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--check', action='store_true')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--backend-only', action='store_true')
    modes.add_argument('--frontend-only', action='store_true')
    args = parser.parse_args()
    apply(args.root, args.check, args.backend_only, frontend_only=args.frontend_only)
