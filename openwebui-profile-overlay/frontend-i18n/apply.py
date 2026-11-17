"""接续个人连接的中英界面增量；所有源码先核对清单再统一写入。"""
import argparse
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).parent


def once(source, before, after):
    """只替换已核对的唯一接点，拒绝未知版本或重复应用。"""
    if source.count(before) != 1:
        raise ValueError('i18n_source_anchor_changed:' + before[:80])
    return source.replace(before, after, 1)


def add_translations(source, additions):
    """仅补本增量词条，保留原语言包和其他语种。"""
    values = json.loads(source)
    values.update(additions)
    return json.dumps(values, ensure_ascii=False, indent='\t') + '\n'


def prepare(root):
    """从已核个人连接源码准备新文本；不修改后端或访问运行数据。"""
    root = Path(root)
    read = lambda name: (root / name).read_text(encoding='utf-8')
    result = {
        'src/lib/components/layout/GuestChatShell.svelte': (HERE / 'GuestChatShell.svelte').read_text(encoding='utf-8'),
        'src/lib/i18n/main-ui.ts': (HERE / 'main-ui.ts').read_text(encoding='utf-8'),
    }
    translations = json.loads((HERE / 'translations.json').read_text(encoding='utf-8'))
    for language, entries in translations.items():
        name = 'src/lib/i18n/locales/' + language + '/translation.json'
        result[name] = add_translations(read(name), entries)

    name = 'src/lib/i18n/index.ts'
    source = read(name)
    source = once(source, "detector.addDetector({ name: 'defaultLocale', lookup: () => defaultLocale });", """detector.addDetector({ name: 'defaultLocale', lookup: () => defaultLocale });
	// 首次只选实际存在的浏览器语言包；手动选择仍由原生 locale 存储优先。
	detector.addDetector({ name: 'supportedBrowser', lookup: () => {
		if (typeof navigator === 'undefined') return undefined;
		return (navigator.languages?.length ? navigator.languages : [navigator.language])
			.map(toBundleCode).find((code) => languageCodes.includes(code));
	} });""")
    source = once(source, "order: ['querystring', 'localStorage', 'defaultLocale', 'navigator']", "order: ['querystring', 'localStorage', 'supportedBrowser', 'defaultLocale']")
    result[name] = source

    # 提示模块读取当前原生语言；不改变原异常、重试动作或身份判断。
    for name, replacements in {
        'src/lib/apis/main-oauth-auth-recovery.mjs': {
            'toast.error(AUTHORIZATION_MESSAGE,': 'toast.error(translateMainUi(AUTHORIZATION_MESSAGE),',
            "label: '重新授权'": "label: translateMainUi('重新授权')",
        },
        'src/lib/apis/main-model-silent-recovery.mjs': {
            "frame.title = '模型授权恢复';": "frame.title = translateMainUi('模型授权恢复');",
            "toast.error('登录账号不一致，已停止自动恢复。请确认主站与当前 Chat 使用原来的同一账号，再重新检查授权。',": "toast.error(translateMainUi('登录账号不一致，已停止自动恢复。请确认主站与当前 Chat 使用原来的同一账号，再重新检查授权。'),",
            "label: '重新检查授权'": "label: translateMainUi('重新检查授权')",
            "toast.error('后台恢复模型授权未完成，请手动重新授权后重试。',": "toast.error(translateMainUi('后台恢复模型授权未完成，请手动重新授权后重试。'),",
            "label: '重新授权'": "label: translateMainUi('重新授权')",
        },
        'src/lib/apis/index.ts': {
            "toast.error('平台模型需要重新授权，个人连接仍可使用。');": "toast.error(translateMainUi('平台模型需要重新授权，个人连接仍可使用。'));",
        },
        'src/lib/apis/personal-connections-runtime.mjs': {
            "const name = config.name?.trim() || '个人连接';": "const name = config.name?.trim() || translateMainUi('个人连接');",
            'if (acknowledged) emit({ error: { message } });': 'if (acknowledged) emit({ error: { message: translateMainUi(message) } });',
            'else cb({ error: message });': 'else cb({ error: translateMainUi(message) });',
        },
        'src/routes/+layout.svelte': {
            "toast.error('登录信息无法更新，请清除本站数据后重试。');": "toast.error(translateMainUi('登录信息无法更新，请清除本站数据后重试。'));",
        },
        'src/routes/(app)/+layout.svelte': {
            "toast.error('部分个人模型列表读取失败，可在外部连接中重试或手动填写模型 ID。');": "toast.error(translateMainUi('部分个人模型列表读取失败，可在外部连接中重试或手动填写模型 ID。'));",
        },
        'src/routes/auth/+page.svelte': {
            'toast.error(error);': 'toast.error(translateMainUi(error));',
        },
    }.items():
        source = read(name)
        statement = "import { translateMainUi } from '$lib/i18n/main-ui';\n"
        if name.endswith('.svelte'):
            marker = '<script lang="ts">' if '<script lang="ts">' in source else '<script>'
            source = once(source, marker, marker + '\n\t' + statement)
        else:
            source = statement + source
        for before, after in replacements.items():
            source = once(source, before, after)
        result[name] = source
    return result


def apply(root, check=False):
    """核对完整输入/结果和受限路径；check 模式完全不写候选源码。"""
    root = Path(root).resolve(strict=True)
    manifest = json.loads((HERE / 'manifest.json').read_text(encoding='utf-8'))
    for name, expected in manifest['files'].items():
        target = root / name
        if target.is_symlink() or not target.resolve().is_relative_to(root):
            raise ValueError('i18n_source_path_invalid:' + name)
        actual = hashlib.sha256(target.read_bytes()).hexdigest() if target.exists() else None
        if actual != expected['before_sha256']:
            raise ValueError('i18n_source_baseline_changed:' + name)
    result = prepare(root)
    if set(result) != set(manifest['files']):
        raise ValueError('i18n_source_inventory_changed')
    for name, source in result.items():
        if hashlib.sha256(source.encode()).hexdigest() != manifest['files'][name]['after_sha256']:
            raise ValueError('i18n_source_result_changed:' + name)
    if not check:
        for name, source in result.items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source, encoding='utf-8', newline='\n')
    return list(result)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('root')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    print(json.dumps(apply(args.root, args.check)))
