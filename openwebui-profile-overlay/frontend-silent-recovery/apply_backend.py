"""在已发布手动恢复基线上接入同用户 iframe 授权恢复，保留普通登录原文。"""
import hashlib
from pathlib import Path
import sys

BASELINES = {
    'main.py': 'c40fa80418d08771783c0158b65481793cc31fe8c92220027a669bdf5cf1b428',
    'utils/oauth.py': '9fe24392ccc3f88741801a198e9bcbeab5f72f814978c0e0d39ec9fdac14d5df',
}


def replace_once(source, old, new):
    """只接受唯一原文，拒绝重复补丁或其他版本漂移。"""
    if source.count(old) != 1:
        raise ValueError('silent_recovery_source_marker_changed')
    return source.replace(old, new, 1)


def patch_main(source):
    """新增受认证初始化入口与恢复回调总时限，普通 OAuth 路由继续原样调用。"""
    marker = "@app.get('/oauth/{provider}/login')\n"
    source = replace_once(source, marker,
        "# 仅真人 Chat 会话可准备模型授权恢复；不接受浏览器提供用户主体。\n"
        "@app.post('/oauth/oidc/model-authorization')\n"
        "async def model_authorization_recovery(request: Request, user=Depends(get_verified_user)):\n"
        "    from open_webui.utils.main_model_silent_recovery import prepare_model_recovery\n"
        "    return await prepare_model_recovery(request, user, oauth_manager)\n\n\n" + marker)
    marker = '    return await oauth_manager.handle_callback(request, provider, response, db=db)\n'
    return replace_once(source, marker,
        '    # 恢复流程只在隐藏 frame 内结束；普通登录不受专用时限和返回页影响。\n'
        '    from open_webui.utils.main_model_silent_recovery import is_model_recovery_callback, run_model_recovery_callback\n'
        '    if is_model_recovery_callback(request, provider):\n'
        '        return await run_model_recovery_callback(request, provider, response, oauth_manager, db=db)\n' + marker)


def patch_oauth(source):
    """可信主体得到后立即返回专用恢复分支，任何用户变更之前拒绝账号不一致。"""
    start = source.index('    async def handle_callback(self, request, provider, response, db=None):\n')
    end = source.index('    async def handle_backchannel_logout(', start)
    method = source[start:end]
    method = replace_once(method,
        '    async def handle_callback(self, request, provider, response, db=None):\n',
        '    async def handle_callback(self, request, provider, response, db=None, *, model_recovery_client=None):\n')
    marker = '        error_message = None\n'
    method = replace_once(method, marker,
        "        recovery = None\n"
        "        if provider == 'oidc' and request.query_params.get('state', '').startswith('plmr_'):\n"
        '            from open_webui.utils.main_model_silent_recovery import load_model_recovery_context, finish_model_recovery, recovery_result\n'
        '            recovery = await load_model_recovery_context(request, model_recovery_client or self.get_client(provider))\n'
        "            if not recovery['valid']:\n"
        "                return recovery_result(recovery['request_id'], 'failed')\n"
        + marker)
    method = replace_once(method, '            client = self.get_client(provider)\n',
                          '            client = model_recovery_client or self.get_client(provider)\n')
    # 恢复专用错误仅回固定结果；普通登录继续原日志，不展开 Token/UserInfo 异常原文。
    method = replace_once(method,
        '                except Exception as retry_exc:\n                    detailed_error = _build_oauth_callback_error_message(retry_exc)\n',
        '                except Exception as retry_exc:\n'
        "                    if recovery is not None:\n"
        "                        return recovery_result(recovery['request_id'], 'failed')\n"
        '                    detailed_error = _build_oauth_callback_error_message(retry_exc)\n')
    method = replace_once(method,
        '            except Exception as e:\n                detailed_error = _build_oauth_callback_error_message(e)\n',
        '            except Exception as e:\n'
        '                if recovery is not None:\n'
        "                    return recovery_result(recovery['request_id'], 'failed')\n"
        '                detailed_error = _build_oauth_callback_error_message(e)\n')
    method = replace_once(method,
        "        except Exception as e:\n            log.error(f'Error during OAuth process: {e}')\n",
        '        except Exception as e:\n'
        '            if recovery is not None:\n'
        "                return recovery_result(recovery['request_id'], 'failed')\n"
        "            log.error(f'Error during OAuth process: {e}')\n")
    marker = '            sub = str(sub)\n'
    method = replace_once(method,
        "            if not sub:\n                log.warning(f'OAuth callback failed, sub is missing: {user_data}')\n",
        "            if not sub:\n"
        "                if recovery is not None:\n"
        "                    return recovery_result(recovery['request_id'], 'failed')\n"
        "                log.warning(f'OAuth callback failed, sub is missing: {user_data}')\n")
    method = replace_once(method, marker, marker +
        '            # 必须先核对原 Chat 用户；恢复不进入资料、角色、开户或 JWT 签发链。\n'
        '            if recovery is not None:\n'
        '                return await finish_model_recovery(request, recovery, sub, token, db=db)\n')
    marker = "        webui_url = await Config.get('webui.url')\n"
    method = replace_once(method, marker,
        '        if recovery is not None:\n'
        "            return recovery_result(recovery['request_id'], 'failed')\n\n" + marker)
    return source[:start] + method + source[end:]


def apply(root, check=False):
    """root 为隔离 open_webui 目录；全部基线核对通过后才写两个补丁文件。"""
    root = Path(root)
    prepared = {}
    for relative, expected in BASELINES.items():
        content = (root / relative).read_bytes()
        if hashlib.sha256(content).hexdigest() != expected:
            raise ValueError('silent_recovery_backend_baseline_changed:' + relative)
        prepared[relative] = (patch_main if relative == 'main.py' else patch_oauth)(content.decode('utf-8'))
    if not check:
        for relative, source in prepared.items():
            (root / relative).write_text(source, encoding='utf-8', newline='\n')


if __name__ == '__main__':
    apply(Path(sys.argv[1]))
