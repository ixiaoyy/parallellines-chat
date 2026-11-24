"""向已审核 Chat 镜像的原生空 loader 槽安装可选流量入口，不改前端包或后端业务。"""
import argparse
from pathlib import Path


def apply(application: Path, enabled: str = 'false') -> bool:
    """显式启用时填空槽，关闭仅清本覆盖层；返回是否写入，保留其他定制。"""
    if enabled not in {'true', 'false'}:
        raise ValueError('traffic_enabled_must_be_true_or_false')
    target = application / 'build/static/loader.js'
    content = Path(__file__).with_name('loader.js').read_bytes()
    if enabled == 'false':
        # 从已启用的派生镜像再次派生时也能关闭；只清除逐字匹配的本覆盖层。
        if target.is_file() and target.read_bytes() == content:
            target.write_bytes(b'')
            return True
        return False

    index = application / 'build/index.html'
    if not index.is_file() or index.read_text(encoding='utf-8').count('/static/loader.js') != 1:
        raise ValueError('native_deferred_loader_entry_missing_or_changed')
    if not target.is_file():
        raise ValueError('native_loader_slot_missing')
    previous = target.read_bytes()
    if previous == content:
        return False
    # 原生槽已被其他功能占用时停止，不覆盖现有定制，也不拼接未经审核的脚本。
    if previous:
        raise ValueError('native_loader_slot_not_empty_preserve_existing_customization')
    target.write_bytes(content)
    return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('application', type=Path)
    parser.add_argument('--enabled', choices=('true', 'false'), default='false')
    args = parser.parse_args()
    print('traffic_loader_updated' if apply(args.application, args.enabled) else 'traffic_loader_unchanged')
