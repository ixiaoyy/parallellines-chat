"""授权服务器维护：只开放一个免费模型的原生 ACL/连接过滤，不读取管理员凭据。

默认 plan 只读。apply 必须先写私有恢复点；rollback 仅条件撤回本次新增差异。
不导入应用启动模块、不执行迁移、不改变默认模型、任务、用户、余额或其他模型。
"""
import argparse
import asyncio
import copy
import json
import math
import os
from pathlib import Path
import stat
import sys
import time
import uuid


MODEL = 'openrouter/free'
RELAY = 'https://api.pingxingxian.space/oauth-relay/v1'
CONFIG_KEY = 'openai.api_configs'
DATA_ROOT = Path('/app/backend/data')
PUBLIC_READ = {'principal_type': 'user', 'principal_id': '*', 'permission': 'read'}
CONFIG_ROWS = ('openai.enable', 'openai.api_base_urls', 'openai.api_keys', CONFIG_KEY, 'models.base_models_cache')


class MaintenanceFailure(RuntimeError):
    """只携带稳定步骤名，避免数据库或配置异常泄露到维护输出。"""


def require(condition, step):
    """条件不满足时停止本次维护；step 只能使用脚本内固定标识。"""
    if not condition:
        raise MaintenanceFailure(step)


def grant_key(grant):
    """返回原生 ACL 的语义键，用于比较权限而不重新生成既有授权记录。"""
    return tuple(grant.get(key) for key in ('principal_type', 'principal_id', 'permission'))


def public_grant(grants):
    """读取该模型的唯一登录用户只读授权，返回原生记录或 None。"""
    matches = [grant for grant in grants if grant_key(grant) == grant_key(PUBLIC_READ)]
    require(len(matches) <= 1, 'duplicate_public_read_grant')
    return matches[0] if matches else None


def normalized_grants(grants):
    """按原生记录 ID 和语义规范化本模型授权，保留并发变化检测能力。"""
    return sorted((grant.get('id'), *grant_key(grant)) for grant in grants)


def plan_from_state(state):
    """检查已有连接、模型和缓存合同，返回仅含本次差异的内存计划。"""
    rows = state['rows']
    require(all(key in rows for key in CONFIG_ROWS), 'required_native_config_missing')
    require(rows['openai.enable'] is True, 'openai_connection_disabled')
    urls, keys, configs = rows['openai.api_base_urls'], rows['openai.api_keys'], rows[CONFIG_KEY]
    require(isinstance(urls, list) and all(isinstance(url, str) for url in urls)
            and isinstance(keys, list) and len(keys) == len(urls) and isinstance(configs, dict), 'connection_schema')
    valid_keys = {str(index) for index in range(len(urls))}
    require(all(key in valid_keys for key in configs), 'indexed_connection_config_required')
    indexes = [index for index, url in enumerate(urls) if url == RELAY]
    require(len(indexes) == 1, 'single_trusted_connection_required')
    index = indexes[0]
    target = configs.get(str(index))
    require(isinstance(target, dict) and target.get('enable') is True
            and target.get('auth_type') == 'system_oauth' and target.get('api_type') == 'responses', 'trusted_oauth_responses_required')
    require(keys[index] == '' and target.get('forward_cookies') is False
            and target.get('headers') == {} and not target.get('prefix_id') and not target.get('azure')
            and target.get('provider') in (None, '', 'openai'), 'connection_credentials_or_options_changed')
    model_ids = target.get('model_ids')
    require('model_ids' not in target or isinstance(model_ids, list) and all(isinstance(item, str) for item in model_ids), 'model_filter_schema')
    require(rows['models.base_models_cache'] is False, 'base_model_cache_must_already_be_disabled')
    ttl = state['cache_ttl_seconds']
    require(isinstance(ttl, (int, float)) and not isinstance(ttl, bool) and math.isfinite(ttl) and ttl > 0, 'finite_native_model_cache_required')
    model = state['model']
    if model is not None:
        require(model.get('id') == MODEL and model.get('base_model_id') is None and model.get('is_active') is True, 'native_free_model_changed')
    grants = state['grants']
    require(isinstance(grants, list) and all(isinstance(grant, dict)
            and isinstance(grant.get('id'), str) and grant.get('principal_type') in ('user', 'group')
            and isinstance(grant.get('principal_id'), str) and grant.get('permission') in ('read', 'write') for grant in grants), 'grant_schema')
    require(not any(grant.get('principal_type') == 'user' and grant.get('principal_id') == '*'
                    and grant.get('permission') != 'read' for grant in grants), 'public_write_not_allowed')
    require(model is not None or not grants, 'orphan_model_grants')
    next_configs = copy.deepcopy(configs)
    filter_changed = bool(model_ids) and MODEL not in model_ids
    if filter_changed:
        next_configs[str(index)]['model_ids'] = [*model_ids, MODEL]
    return {'index': index, 'access_changed': public_grant(grants) is None,
            'filter_changed': filter_changed, 'next_configs': next_configs,
            'before_filter_present': 'model_ids' in target, 'before_filter': copy.deepcopy(model_ids),
            'after_filter': copy.deepcopy(next_configs[str(index)].get('model_ids'))}


class PrivateJournal:
    """仅在现有挂载数据目录写入本操作的 0600 最小恢复记录，不含令牌或连接密钥。"""
    def __init__(self, path):
        self.path = Path(path) if path is not None else None

    def validate(self, exists=False):
        require(self.path is not None and self.path.is_absolute()
                and self.path.resolve(strict=False).is_relative_to(DATA_ROOT.resolve(strict=False)), 'private_data_backup_required')
        require(not any(part.is_symlink() for part in (self.path, *self.path.parents)), 'backup_symlink_rejected')
        require(self.path.parent.is_dir() and stat.S_IMODE(self.path.parent.stat().st_mode) & 0o077 == 0, 'unsafe_backup_directory')
        if exists:
            require(self.path.is_file() and stat.S_IMODE(self.path.stat().st_mode) & 0o077 == 0, 'unsafe_backup_file')

    def create(self, value):
        """排他创建恢复点；目录须由授权操作者预先准备，不覆盖已有文件。"""
        self.validate()
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        self.sync_parent()

    def sync_parent(self):
        """让恢复点目录项在数据库写入前落盘；仅用于已确认的 Linux 容器数据目录。"""
        fd = os.open(self.path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def read(self):
        """读取并核对私有恢复记录，不返回到标准输出。"""
        self.validate(exists=True)
        saved = json.loads(self.path.read_text(encoding='utf-8'))
        require(isinstance(saved, dict) and saved.get('version') == 1 and saved.get('model') == MODEL, 'unexpected_backup')
        return saved

    def update(self, value):
        """仅原子更新同一操作的恢复日志；提交后日志失败必须报告不确定，不能重试数据库写入。"""
        require(self.read().get('operation_id') == value.get('operation_id'), 'backup_operation_changed')
        temporary = self.path.with_name(self.path.name + '.tmp-' + uuid.uuid4().hex)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)
        self.sync_parent()


def load_native_runtime():
    """在独立容器维护进程加载既有 ORM/会话，不导入应用启动、迁移或管理员认证模块。"""
    require(not any(name in sys.modules for name in ('open_webui.main', 'open_webui.config', 'open_webui.env', 'open_webui.internal.db')), 'standalone_maintenance_process_required')
    require(os.environ.get('ENABLE_PERSISTENT_CONFIG', 'True').lower() == 'true', 'native_persistent_config_required')
    os.environ['ENABLE_DB_MIGRATIONS'] = 'false'
    from sqlalchemy import Text, cast, select, update
    from open_webui.internal.db import async_engine, get_async_db
    from open_webui.models.config import Config
    from open_webui.models.models import Model, ModelForm, ModelMeta, ModelParams, Models
    from open_webui.models.access_grants import AccessGrants
    from open_webui.models.users import User
    from open_webui.env import MODELS_CACHE_TTL

    class NativeRuntime:
        async def read_state(self):
            """仅读必要配置、目标模型公开结构和 ACL；不读取密码、管理员令牌或模型私有参数。"""
            async with get_async_db() as db:
                rows = {}
                for key in CONFIG_ROWS:
                    row = await db.get(Config, key)
                    if row is not None:
                        rows[key] = copy.deepcopy(row.value)
                columns = (Model.id, Model.name, Model.base_model_id, Model.is_active, Model.user_id, Model.version_id)
                row = (await db.execute(select(*columns).where(Model.id == MODEL))).mappings().first()
                model = dict(row) if row is not None else None
            grants = await AccessGrants.get_grants_by_resource('model', MODEL)
            values = [{'id': grant.id, 'principal_type': grant.principal_type, 'principal_id': grant.principal_id, 'permission': grant.permission} for grant in grants]
            return {'rows': rows, 'model': model, 'grants': values, 'cache_ttl_seconds': MODELS_CACHE_TTL}

        async def check_owner(self, user_id):
            """只核对显式指定的既有管理员 ID/角色，不创建用户或读取认证凭据。"""
            async with get_async_db() as db:
                row = (await db.execute(select(User.id, User.role).where(User.id == user_id))).first()
                return row is not None and row.role == 'admin'

        async def create_model(self, user_id):
            """复用原生 Models 创建最小模型及版本记录，初始无授权，不改其他模型。"""
            result = await Models.insert_new_model(ModelForm(id=MODEL, name='OpenRouter 免费', meta=ModelMeta(), params=ModelParams(), access_grants=[]), user_id)
            require(result is not None, 'model_create_failed')

        async def add_grant(self):
            """复用原生单条 ACL 添加登录用户只读授权，保留全部原授权及其 ID。"""
            grant = await AccessGrants.grant_access('model', MODEL, 'user', '*', 'read')
            require(grant is not None, 'grant_create_failed')
            return grant.id

        async def remove_grant(self):
            """仅撤回本次增加的登录用户只读授权，不全量替换 ACL 或删除模型记录。"""
            require(await AccessGrants.revoke_access('model', MODEL, 'user', '*', 'read'), 'grant_remove_failed')

        async def cas_configs(self, before, after):
            """沿用成熟维护入口的 Config 单行条件更新，检测并发而不覆盖配置。"""
            async with get_async_db() as db:
                # PostgreSQL 原生 JSON 没有等号运算；比较同一存储返回的文本，不猜测序列化格式。
                current = (await db.execute(select(Config.value, cast(Config.value, Text)).where(Config.key == CONFIG_KEY))).first()
                require(current is not None and current[0] == before, 'configuration_changed_concurrently')
                result = await db.execute(update(Config).where(Config.key == CONFIG_KEY, cast(Config.value, Text) == current[1]).values(value=after, updated_at=int(time.time())))
                require(result.rowcount == 1, 'configuration_changed_concurrently')
                await db.commit()

        async def close(self):
            """仅释放本维护进程的连接池，不影响运行中的 Chat 进程。"""
            await async_engine.dispose()

    return NativeRuntime()


def same_state(left, right):
    """比较写入前的必要配置/模型/ACL，任一并发变化均停止当前操作。"""
    return left['rows'] == right['rows'] and left['model'] == right['model'] and normalized_grants(left['grants']) == normalized_grants(right['grants'])


def summary(plan, state, action, progress):
    """只输出可公开维护结果，不输出配置、ACL 主体、owner 或凭据值。"""
    return {'ok': True, 'action': action, 'model': MODEL, 'model_missing': state['model'] is None,
            'access_changed': plan['access_changed'], 'connection_filter_changed': plan['filter_changed'],
            'completed_steps': list(progress['completed']), 'model_requests': 0, 'cache_ttl_seconds': state['cache_ttl_seconds'],
            'native_directory_refresh_required': True}


async def configure(action, runtime, journal, owner_user_id, progress):
    """计划/应用/恢复这一个模型；每步写前重读，失败不自动重试或回滚其他维护者的数据。"""
    state = await runtime.read_state()
    plan = plan_from_state(state)
    if action == 'plan':
        return summary(plan, state, action, progress)
    if action == 'rollback':
        return await rollback(runtime, journal, state, progress)
    require(action == 'apply', 'unexpected_action')
    if not plan['access_changed'] and not plan['filter_changed']:
        return summary(plan, state, action, progress)
    if state['model'] is None:
        require(isinstance(owner_user_id, str) and owner_user_id and await runtime.check_owner(owner_user_id), 'existing_admin_owner_required')
    saved = {'version': 1, 'operation_id': uuid.uuid4().hex, 'model': MODEL,
             'before_model': copy.deepcopy(state['model']), 'before_grants': copy.deepcopy(state['grants']),
             'connection_index': plan['index'], 'before_filter_present': plan['before_filter_present'],
             'before_filter': plan['before_filter'], 'after_filter': plan['after_filter'],
             'filter_changed': plan['filter_changed'], 'access_changed': plan['access_changed'],
             'added_grant_id': None, 'expected_model': copy.deepcopy(state['model']),
             'expected_grants': copy.deepcopy(state['grants']), 'completed': []}
    journal.create(saved)
    require(same_state(await runtime.read_state(), state), 'state_changed_before_apply')
    if state['model'] is None:
        progress['write_attempted'] = True
        await runtime.create_model(owner_user_id)
        progress['completed'].append('model.created')
        actual = await runtime.read_state()
        require(actual['model'] is not None and actual['rows'] == state['rows'] and not actual['grants'], 'model_create_readback_failed')
        saved['expected_model'] = copy.deepcopy(actual['model'])
        saved['completed'] = list(progress['completed'])
        journal.update(saved)
        state = actual
    if plan['access_changed']:
        require(same_state(await runtime.read_state(), state), 'state_changed_before_grant')
        progress['write_attempted'] = True
        added_id = await runtime.add_grant()
        progress['completed'].append('model.read_grant')
        saved['added_grant_id'] = added_id
        saved['expected_grants'] = [*copy.deepcopy(state['grants']), {'id': added_id, **PUBLIC_READ}]
        saved['completed'] = list(progress['completed'])
        journal.update(saved)
        actual = await runtime.read_state()
        require(actual['rows'] == state['rows'] and actual['model'] == state['model']
                and normalized_grants(actual['grants']) == normalized_grants(saved['expected_grants']), 'grant_readback_or_concurrent_change')
        state = actual
    if plan['filter_changed']:
        require(same_state(await runtime.read_state(), state), 'state_changed_before_filter')
        progress['write_attempted'] = True
        await runtime.cas_configs(state['rows'][CONFIG_KEY], plan['next_configs'])
        progress['completed'].append('connection.filter')
        saved['completed'] = list(progress['completed'])
        journal.update(saved)
        actual = await runtime.read_state()
        expected_rows = {**state['rows'], CONFIG_KEY: plan['next_configs']}
        require(actual['rows'] == expected_rows and actual['model'] == state['model']
                and normalized_grants(actual['grants']) == normalized_grants(state['grants']), 'filter_readback_or_concurrent_change')
        state = actual
    return summary(plan, state, action, progress)


async def rollback(runtime, journal, state, progress):
    """只在本次模型及过滤项仍匹配私有日志时恢复；不删除新增模型或其他并发数据。"""
    saved = journal.read()
    require(saved.get('model') == MODEL and saved.get('version') == 1, 'unexpected_backup')
    index = saved['connection_index']
    require(plan_from_state(state)['index'] == index, 'connection_index_changed')
    target = state['rows'][CONFIG_KEY][str(index)]
    before_filter_matches = ('model_ids' in target) == saved['before_filter_present'] and target.get('model_ids') == saved['before_filter']
    current_public = public_grant(state['grants'])
    grant_restored = not saved['access_changed'] or current_public is None
    if before_filter_matches and grant_restored:
        return {'ok': True, 'action': 'rollback', 'changed': False, 'restored': True, 'model_requests': 0}
    require(state['model'] == saved['expected_model'], 'model_changed_since_apply')
    require(normalized_grants(state['grants']) == normalized_grants(saved['expected_grants']), 'grants_changed_since_apply')
    if saved['access_changed']:
        require(current_public is not None and current_public['id'] == saved['added_grant_id'], 'grant_identity_changed')
    if saved['filter_changed']:
        require(before_filter_matches or target.get('model_ids') == saved['after_filter'], 'filter_changed_since_apply')
    require(same_state(await runtime.read_state(), state), 'state_changed_before_rollback')
    if saved['filter_changed'] and not before_filter_matches:
        configs = copy.deepcopy(state['rows'][CONFIG_KEY])
        if saved['before_filter_present']:
            configs[str(index)]['model_ids'] = saved['before_filter']
        else:
            configs[str(index)].pop('model_ids', None)
        progress['write_attempted'] = True
        await runtime.cas_configs(state['rows'][CONFIG_KEY], configs)
        progress['completed'].append('connection.filter_restored')
        actual = await runtime.read_state()
        expected = copy.deepcopy(state)
        expected['rows'][CONFIG_KEY] = configs
        require(same_state(actual, expected), 'rollback_filter_readback_failed')
        state = actual
    if saved['access_changed'] and current_public is not None:
        require(same_state(await runtime.read_state(), state), 'state_changed_before_grant_restore')
        progress['write_attempted'] = True
        await runtime.remove_grant()
        progress['completed'].append('model.read_grant_removed')
        actual = await runtime.read_state()
        require(normalized_grants(actual['grants']) == normalized_grants(saved['before_grants'])
                and actual['rows'] == state['rows'] and actual['model'] == state['model'], 'rollback_grant_readback_failed')
    return {'ok': True, 'action': 'rollback', 'changed': True, 'restored': True,
            'completed_steps': list(progress['completed']), 'model_requests': 0, 'native_directory_refresh_required': True}


async def main(args, progress):
    """独立维护进程的唯一运行入口，始终释放自己的会话池。"""
    runtime = load_native_runtime()
    try:
        return await configure(args.action, runtime, PrivateJournal(args.backup), args.owner_user_id, progress)
    finally:
        await runtime.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', choices=('plan', 'apply', 'rollback'), default='plan')
    parser.add_argument('backup', nargs='?', type=Path)
    parser.add_argument('--owner-user-id')
    args = parser.parse_args()
    os.umask(0o077)
    progress = {'write_attempted': False, 'completed': []}
    try:
        print(json.dumps(asyncio.run(main(args, progress))))
    except Exception as error:
        print(json.dumps({'ok': False, 'action': args.action,
                          'failed_step': str(error) if isinstance(error, MaintenanceFailure) else 'native_operation_failed',
                          'failure_type': type(error).__name__, 'completed_steps': progress['completed'],
                          'partial_or_uncertain': progress['write_attempted'], 'model_requests': 0}))
        raise SystemExit(1) from None
