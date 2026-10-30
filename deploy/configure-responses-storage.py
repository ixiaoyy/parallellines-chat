"""通过原生 Config 模型维护已启用的单一 OAuth 连接；只供授权服务器维护使用。

inspect 不写入；apply 将单个配置行的恢复快照保存在指定私有路径；rollback
仅在该行仍等于本次写入结果时恢复。不会读取/生成管理员凭据或调用模型。
"""
import argparse
import asyncio
import copy
import json
import os
from pathlib import Path
import time

from sqlalchemy import update
from open_webui.internal.db import async_engine, get_async_db
from open_webui.models.config import Config


KEY = 'openai.api_configs'
RELAY = 'https://api.pingxingxian.space/oauth-relay/v1'


def require(value, message):
    if not value:
        raise RuntimeError(message)


def desired(value):
    """保留现有配置键和全部字段，只设置已批准连接的 api_type。"""
    require(isinstance(value, dict) and len(value) == 1, 'single_existing_connection_required')
    key = next(iter(value))
    require(key in ('0', RELAY), 'unexpected_connection_key')
    config = value[key]
    require(isinstance(config, dict) and config.get('enable') is True, 'enabled_connection_required')
    require(config.get('auth_type') == 'system_oauth', 'system_oauth_required')
    require(not config.get('prefix_id') and not config.get('azure')
            and config.get('provider') in (None, '', 'openai'), 'unexpected_provider')
    result = copy.deepcopy(value)
    result[key]['api_type'] = 'responses'
    return result


async def compare_and_swap(db, before, after):
    """条件更新同一配置行；并发变更时停止，不覆盖其他维护者的设置。"""
    result = await db.execute(update(Config).where(Config.key == KEY, Config.value == before)
                              .values(value=after, updated_at=int(time.time())))
    require(result.rowcount == 1, 'configuration_changed_concurrently')
    await db.commit()


async def configure(action, backup):
    """按动作检查、启用或恢复 Responses；输出不含完整配置和身份数据。"""
    async with get_async_db() as db:
        enabled = await db.get(Config, 'openai.enable')
        urls = await db.get(Config, 'openai.api_base_urls')
        row = await db.get(Config, KEY)
        require(enabled is not None and enabled.value is True, 'openai_connection_disabled')
        require(urls is not None and urls.value == [RELAY], 'unexpected_connection_urls')
        require(row is not None, 'existing_configuration_required')
        before = copy.deepcopy(row.value)
        after = desired(before)
        if action == 'inspect':
            return {'ok': True, 'api_type': next(iter(before.values())).get('api_type'), 'model_requests': 0}
        if action == 'rollback':
            if not backup.exists():
                return {'ok': True, 'changed': False, 'backup_exists': False}
            saved = json.loads(backup.read_text())
            require(saved.get('key') == KEY, 'unexpected_backup_key')
            if before == saved['before']:
                return {'ok': True, 'changed': False, 'restored': True}
            require(before == saved['after'], 'configuration_changed_since_release')
            await compare_and_swap(db, before, saved['before'])
            return {'ok': True, 'changed': True, 'restored': True}
        if before == after:
            return {'ok': True, 'changed': False, 'api_type': 'responses', 'model_requests': 0}

        # 快照只包含本次目标配置行，留在挂载的数据目录内，不导出到日志或 Git。
        require(backup.is_absolute() and backup.is_relative_to('/app/backend/data'), 'private_data_backup_required')
        require(not any(p.is_symlink() for p in (backup, *backup.parents)), 'backup_symlink_rejected')
        backup.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        require(backup.parent.stat().st_mode & 0o077 == 0, 'unsafe_backup_directory')
        with backup.open('x', encoding='utf-8') as stream:
            json.dump({'key': KEY, 'before': before, 'after': after}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        await compare_and_swap(db, before, after)
    actual = await Config.get(KEY)
    require(actual == after, 'configuration_readback_failed')
    return {'ok': True, 'changed': True, 'api_type': 'responses', 'model_requests': 0}


async def main(action, backup):
    """独立维护进程结束时释放本进程连接池，不影响服务进程。"""
    try:
        return await configure(action, backup)
    finally:
        await async_engine.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['inspect', 'apply', 'rollback'])
    parser.add_argument('backup', type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        print(json.dumps(asyncio.run(main(args.action, args.backup))))
    except Exception as error:
        print(json.dumps({'ok': False, 'failure_type': type(error).__name__, 'action': args.action}))
        raise SystemExit(1) from None
