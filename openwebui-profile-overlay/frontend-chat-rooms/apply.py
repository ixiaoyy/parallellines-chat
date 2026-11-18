"""在已核中英界面上追加聊天频道入口，不改访客、账户或模型流程。"""
import argparse
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).parent
SIDEBAR = 'src/lib/components/layout/Sidebar.svelte'
LINK = 'src/lib/components/layout/Sidebar/ChatRoomsLink.svelte'


def prepare(root):
    """生成展开和收起侧栏的同一链接，并补中英文案。"""
    sidebar = (root / SIDEBAR).read_text(encoding='utf-8')
    # 两个接点分别对应桌面收起栏，以及桌面和手机共用的展开栏。
    for anchor, addition in (
        ("\timport UserMenu from './Sidebar/UserMenu.svelte';\n", "\timport ChatRoomsLink from './Sidebar/ChatRoomsLink.svelte';\n"),
        ('\n\t\t\t\t\t{#each pinnedItems as itemId (itemId)}', '\n\t\t\t\t\t<ChatRoomsLink compact />\n'),
        ('\n\t\t\t\t\t\t<div id="pinned-menu-items-list">', '\n\t\t\t\t\t\t<ChatRoomsLink />\n'),
    ):
        if sidebar.count(anchor) != 1:
            raise ValueError('chat_rooms_sidebar_anchor_changed')
        sidebar = sidebar.replace(anchor, addition + anchor, 1)
    result = {SIDEBAR: sidebar, LINK: (HERE / 'ChatRoomsLink.svelte').read_text(encoding='utf-8')}
    for locale, label in {'en-US': 'Chat Channels', 'zh-CN': '聊天频道'}.items():
        name = f'src/lib/i18n/locales/{locale}/translation.json'
        values = json.loads((root / name).read_text(encoding='utf-8'))
        if 'Chat Channels' in values:
            raise ValueError('chat_rooms_translation_key_already_exists:' + locale)
        values['Chat Channels'] = label
        result[name] = json.dumps(values, ensure_ascii=False, indent='\t') + '\n'
    return result


def apply(root, check=False):
    """先核整套预期前后像及路径，check 模式不写源码。"""
    root = Path(root).resolve(strict=True)
    files = json.loads((HERE / 'manifest.json').read_text(encoding='utf-8'))['files']
    for name, hashes in files.items():
        target = root / name
        if target.is_symlink() or not target.resolve().is_relative_to(root):
            raise ValueError('chat_rooms_source_path_invalid:' + name)
        before = hashlib.sha256(target.read_bytes()).hexdigest() if target.exists() else None
        if before != hashes['before_sha256']:
            raise ValueError('chat_rooms_source_baseline_changed:' + name)
    result = prepare(root)
    if result.keys() != files.keys():
        raise ValueError('chat_rooms_source_inventory_changed')
    for name, source in result.items():
        if hashlib.sha256(source.encode()).hexdigest() != files[name]['after_sha256']:
            raise ValueError('chat_rooms_source_result_changed:' + name)
    if not check:
        for name, source in result.items():
            (root / name).write_text(source, encoding='utf-8', newline='\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('root')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    apply(args.root, args.check)
