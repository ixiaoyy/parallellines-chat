"""个人连接专用设置正文不进入审计日志；保持既有审计开关、元数据和其他路径。"""
import ast
from pathlib import Path


ORIGINAL = "        capture_body = not (request.url.path.startswith('/api/v1/auths') or request.url.path.startswith('/oauth/'))\n"
REPLACEMENT = """        # 专用用户设置会收发个人 API Key；在截断/采集前跳过正文，不能只依赖 password 脱敏。
        # 仅匹配两个设置入口及正常尾斜杠，审计事件、身份和响应状态仍沿用原生逻辑。
        personal_settings_path = request.url.path.rstrip('/') in (
            '/api/v1/users/user/settings',
            '/api/v1/users/user/settings/update',
        )
        capture_body = not (
            request.url.path.startswith('/api/v1/auths')
            or request.url.path.startswith('/oauth/')
            or personal_settings_path
        )
"""


def patch_file(source):
    """仅替换唯一原生正文采集判断；版本漂移或重复应用时拒绝，不修改日志配置。"""
    if source.count(ORIGINAL) != 1:
        raise ValueError('personal_audit_source_anchor_changed')
    result = source.replace(ORIGINAL, REPLACEMENT, 1)
    ast.parse(result)
    return result


def prepare(root):
    """只读 backend/open_webui，返回审计源码候选，由统一应用器核清单后写入。"""
    root = Path(root)
    relative = 'utils/audit.py'
    return {relative: patch_file((root / relative).read_text(encoding='utf-8'))}
