"""个人连接凭据落库边界补丁；只生成新源码，由统一入口决定写入。"""
from __future__ import annotations

import ast
from pathlib import Path


HERE = Path(__file__).parent


def _once(source, before, after):
    """唯一接点核对；源码漂移或重复应用时停止，不覆盖其他工作。"""
    if source.count(before) != 1:
        raise ValueError('personal_credentials_source_anchor_changed:' + before[:70])
    return source.replace(before, after, 1)


MODEL_IMPORT = '''from open_webui.utils.main_personal_connections import (
    encrypt_personal_connection_settings,
    redact_personal_connection_settings,
)
'''

ROUTE_IMPORT = '''from open_webui.utils.main_personal_connections import (
    PersonalConnectionCredentialError,
    decrypt_personal_connection_settings,
)
'''

RESPONSE_HELPER = '''def _personal_connection_settings_for_user(user):
    """只有当前用户的专用设置响应解密 Key；损坏/换密钥不降级成空凭据。"""
    stored = user.settings.model_dump() if isinstance(user.settings, UserSettings) else user.settings
    try:
        settings = decrypt_personal_connection_settings(stored, user.id, WEBUI_SECRET_KEY)
    except PersonalConnectionCredentialError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={'code': exc.code, 'message': '个人连接凭据暂不可用，请联系管理员检查加密配置。'},
        ) from None
    return UserSettings.model_validate(settings) if settings is not None else None


'''


def patch_models(source):
    """模型层仅加密显式连接更新，并在普通 UserModel 输出中去掉凭据字段。"""
    source = _once(
        source,
        'from open_webui.env import DATABASE_USER_ACTIVE_STATUS_UPDATE_INTERVAL',
        'from open_webui.env import DATABASE_USER_ACTIVE_STATUS_UPDATE_INTERVAL, WEBUI_SECRET_KEY',
    )
    source = _once(source, 'from open_webui.utils.misc import throttle\n', MODEL_IMPORT + 'from open_webui.utils.misc import throttle\n')
    source = _once(source, '    field_validator,\n', '    field_serializer,\n    field_validator,\n')
    source = _once(source, "    @field_validator('variables', mode='before')", '''    @field_serializer('settings')
    def serialize_settings(self, value):
        """普通用户返回/事件不能携带个人凭据；专用设置路由单独读取解密。"""
        stored = value.model_dump() if isinstance(value, UserSettings) else value
        return redact_personal_connection_settings(stored)

    @field_validator('variables', mode='before')''')
    source = _once(source, '''            user_settings = dict(user.settings or {})
            updated = dict(updated)
            ui_settings = updated.pop('ui', None)''', '''            user_settings = dict(user.settings or {})
            # 只处理显式提交的个人连接；普通偏好/通知更新保持已有密文字节和旧行为。
            updated = encrypt_personal_connection_settings(dict(updated), id, WEBUI_SECRET_KEY)
            ui_settings = updated.pop('ui', None)''')
    ast.parse(source)
    return source


def patch_routes(source):
    """保留权限/事件和 raw/merged 语义，只在当前用户设置入口解密。"""
    source = _once(source, 'from open_webui.internal.db import get_async_session\n', ROUTE_IMPORT + 'from open_webui.internal.db import get_async_session\n')
    source = _once(source, 'from open_webui.env import ENABLE_PROFILE_IMAGE_URL_FORWARDING, PROFILE_IMAGE_ALLOWED_MIME_TYPES, STATIC_DIR',
                   'from open_webui.env import ENABLE_PROFILE_IMAGE_URL_FORWARDING, PROFILE_IMAGE_ALLOWED_MIME_TYPES, STATIC_DIR, WEBUI_SECRET_KEY')
    source = _once(source, "@router.get('/user/settings', response_model=UserSettings | None)", RESPONSE_HELPER + "@router.get('/user/settings', response_model=UserSettings | None)")
    source = _once(source, '''    # user already fetched by get_verified_user — no need to refetch
    if raw:
        return user.settings

    default_interface_settings = await Config.get('ui.default_interface_settings')
    if not isinstance(default_interface_settings, dict) or not default_interface_settings:
        return user.settings

    user_settings = user.settings.model_dump() if isinstance(user.settings, UserSettings) else dict(user.settings or {})''', '''    # user already fetched by get_verified_user — no need to refetch
    settings = _personal_connection_settings_for_user(user)
    if raw:
        return settings

    default_interface_settings = await Config.get('ui.default_interface_settings')
    if not isinstance(default_interface_settings, dict) or not default_interface_settings:
        return settings

    user_settings = settings.model_dump() if isinstance(settings, UserSettings) else dict(settings or {})''')
    source = _once(source, '''    user = await Users.update_user_settings_by_id(user.id, updated_user_settings, db=db)
    if user:
        await publish_event(''', '''    # 未改连接时先验证将返回的既有凭据，避免偏好已写入后才报告解密失败。
    # 显式替换/删除连接可修复旧凭据；不读取、迁移或重封装被替换的秘密。
    if not isinstance(ui_settings, dict) or 'directConnections' not in ui_settings:
        _personal_connection_settings_for_user(user)
    try:
        user = await Users.update_user_settings_by_id(user.id, updated_user_settings, db=db)
    except PersonalConnectionCredentialError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={'code': exc.code, 'message': '个人连接凭据无法安全保存，请检查连接或联系管理员。'},
        ) from None
    if user:
        await publish_event(''')
    source = _once(source, '''            EVENTS.USER_SETTINGS_UPDATED,
            actor=user,
            subject_id=user.id,
        )
        return user.settings''', '''            EVENTS.USER_SETTINGS_UPDATED,
            actor=user,
            subject_id=user.id,
        )
        return _personal_connection_settings_for_user(user)''')
    ast.parse(source)
    return source


def prepare(root):
    """只读 backend/open_webui，返回原生相对路径的新源码；不读取运行配置/数据库。"""
    root = Path(root)
    return {
        'models/users.py': patch_models((root / 'models/users.py').read_text(encoding='utf-8')),
        'routers/users.py': patch_routes((root / 'routers/users.py').read_text(encoding='utf-8')),
        'utils/main_personal_connections.py': (HERE / 'main_personal_connections.py').read_text(encoding='utf-8'),
    }
