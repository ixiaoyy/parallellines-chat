"""个人连接凭据的版本化静态加密；不查询数据库、不读取环境、不记录秘密。"""
from __future__ import annotations

import base64
from copy import deepcopy
import json

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


PREFIX = 'plpc:v1:'
DOMAIN = b'open-webui-personal-connection-keys-v1'


class PersonalConnectionCredentialError(RuntimeError):
    """仅携带稳定错误码；调用边界据此返回 400/503，不附带密钥或密文。"""

    def __init__(self, code='personal_connection_credentials_unavailable', status_code=503):
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def _keys(settings, *, writing=False):
    """定位原生 Key 数组；缺失/null 沿用旧语义，拒绝带非字符串的凭据。"""
    if not isinstance(settings, dict):
        return None
    ui = settings.get('ui')
    if not isinstance(ui, dict):
        return None
    connection = ui.get('directConnections')
    if not isinstance(connection, dict):
        return None
    keys = connection.get('OPENAI_API_KEYS')
    if keys is None:
        return None
    if not isinstance(keys, list) or any(not isinstance(value, str) for value in keys):
        raise PersonalConnectionCredentialError(
            'personal_connection_credentials_invalid' if writing else 'personal_connection_credentials_unavailable',
            400 if writing else 503,
        )
    return keys


def _cipher(secret_key: str | None) -> Fernet:
    """复用原生 WEBUI_SECRET_KEY + HKDF 形式，使用个人连接专属派生域。"""
    if not isinstance(secret_key, str) or not secret_key:
        raise PersonalConnectionCredentialError('personal_connection_encryption_unavailable')
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=DOMAIN).derive(secret_key.encode())
    return Fernet(base64.urlsafe_b64encode(key))


def _open(value: str, user_id: str, cipher: Fernet) -> str:
    """验证版本及密文所属账号；未知版本、损坏、换密钥或跨账号复制均明确失败。"""
    if not value.startswith(PREFIX):
        raise PersonalConnectionCredentialError()
    try:
        payload = json.loads(cipher.decrypt(value[len(PREFIX):].encode()).decode())
    except (InvalidToken, ValueError, UnicodeError):
        raise PersonalConnectionCredentialError() from None
    if (
        not isinstance(payload, dict)
        or payload.get('version') != 1
        or not isinstance(user_id, str)
        or not user_id
        or payload.get('user_id') != user_id
        or not isinstance(payload.get('key'), str)
    ):
        raise PersonalConnectionCredentialError()
    return payload['key']


def encrypt_personal_connection_settings(settings, user_id: str, secret_key: str | None):
    """加密显式更新中的个人 Key；不修改输入，其余设置及缺失/null/空 Key 保持原样。"""
    result = deepcopy(settings)
    keys = _keys(result, writing=True)
    if not keys or not any(keys):
        return result
    if not isinstance(user_id, str) or not user_id:
        raise PersonalConnectionCredentialError()
    cipher = _cipher(secret_key)
    for index, value in enumerate(keys):
        if not value:
            continue
        if value.startswith('plpc:'):
            # 内部重用已封存值时只验签/归属，不套第二层密文。
            _open(value, user_id, cipher)
            continue
        payload = json.dumps({'version': 1, 'user_id': user_id, 'key': value}, ensure_ascii=False).encode()
        keys[index] = PREFIX + cipher.encrypt(payload).decode()
    return result


def decrypt_personal_connection_settings(settings, user_id: str, secret_key: str | None):
    """仅供已认证当前用户的专用设置接口解密；旧明文兼容只读，不在读取时迁移。"""
    result = deepcopy(settings)
    keys = _keys(result)
    if not keys:
        return result
    cipher = None
    for index, value in enumerate(keys):
        if not value.startswith('plpc:'):
            continue
        if cipher is None:
            cipher = _cipher(secret_key)
        keys[index] = _open(value, user_id, cipher)
    return result


def redact_personal_connection_settings(settings):
    """普通用户对象序列化不包含个人 Key/密文，其他设置和个人连接元数据不变。"""
    result = deepcopy(settings)
    if isinstance(result, dict) and isinstance(result.get('ui'), dict):
        connection = result['ui'].get('directConnections')
        if isinstance(connection, dict):
            connection.pop('OPENAI_API_KEYS', None)
    return result
