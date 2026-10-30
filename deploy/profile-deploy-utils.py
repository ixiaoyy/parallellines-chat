"""Scoped Compose edits with target-service compare-and-swap and private backups.

No Docker invocation, environment inspection, credential loading or logging.
Default locking requires Linux flock. All cooperating writers must use the same
Compose inode lock; writes intentionally retain that inode for bind mounts.
"""

from contextlib import contextmanager
import copy
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat
import sys
import uuid

import yaml


@dataclass
class TaggedValue:
    tag: str
    value: object


class ComposeLoader(yaml.SafeLoader):
    pass


class ComposeDumper(yaml.SafeDumper):
    def choose_scalar_style(self):
        # Explicit Compose tags otherwise make PyYAML quote typed scalars such
        # as null/true. Keep their underlying type on a load/dump round trip;
        # string values are explicitly quoted by _dump_tag below.
        if self.event.tag in ('!reset', '!override') and self.event.style is None:
            if self.analysis is None:
                self.analysis = self.analyze_scalar(self.event.value)
            allowed = self.analysis.allow_flow_plain if self.flow_level else self.analysis.allow_block_plain
            if allowed and not (self.simple_key_context and (self.analysis.empty or self.analysis.multiline)):
                return ''
        return super().choose_scalar_style()


def _compose_tag(loader, node):
    if isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node, deep=True)
    elif isinstance(node, yaml.MappingNode):
        value = loader.construct_mapping(node, deep=True)
    else:
        tag = loader.resolve(yaml.ScalarNode, node.value, (node.style is None, False))
        value = loader.construct_object(yaml.ScalarNode(tag, node.value, style=node.style), deep=True)
    return TaggedValue(node.tag, value)


def _dump_tag(dumper, value):
    _require(value.tag in ('!reset', '!override'), "Unknown Compose override tag")
    node = dumper.represent_data(value.value)
    node.tag = value.tag
    if isinstance(value.value, str) and node.style is None:
        node.style = "'"
    return node


for _tag in ('!reset', '!override'):
    ComposeLoader.add_constructor(_tag, _compose_tag)
ComposeDumper.add_representer(TaggedValue, _dump_tag)


def unwrap(value):
    return value.value if isinstance(value, TaggedValue) else value


class ServicePatchError(RuntimeError):
    """Messages intentionally exclude YAML/configuration values."""


def digest(data):
    return hashlib.sha256(data).hexdigest()


def _require(condition, message):
    if not condition:
        raise ServicePatchError(message)


def _parse(text):
    try:
        _require(isinstance(text, str), "Compose text must be UTF-8 text")
        tokens = list(yaml.scan(text))
        _require(not any(isinstance(token, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken))
                         for token in tokens), "Compose aliases and anchors are unsupported")
        root = yaml.compose(text)
        def check_node(node):
            if isinstance(node, yaml.MappingNode):
                keys = []
                for key, value in node.value:
                    _require(isinstance(key, yaml.ScalarNode) and key.tag == 'tag:yaml.org,2002:str',
                             "Compose mapping keys must be strings")
                    _require(key.value not in keys, "Duplicate Compose mapping key")
                    keys.append(key.value)
                    check_node(value)
            elif isinstance(node, yaml.SequenceNode):
                for value in node.value:
                    check_node(value)
        check_node(root)
        document = yaml.load(text, Loader=ComposeLoader)
        _require(isinstance(root, yaml.MappingNode) and not root.flow_style and
                 isinstance(document, dict), "Compose root must be a block mapping")
        return root, document
    except ServicePatchError:
        raise
    except Exception:
        raise ServicePatchError("Compose YAML could not be parsed safely") from None


def load_compose(text):
    """Safe parsed document, preserving only Compose !reset/!override tags."""
    return _parse(text)[1]


load_yaml = load_compose


def _content_end(node):
    """Ignore block-container lookahead marks, which include next-service comments."""
    if isinstance(node, yaml.ScalarNode) or getattr(node, 'flow_style', False):
        return node.end_mark.index
    if isinstance(node, yaml.MappingNode):
        return max((_content_end(item) for pair in node.value for item in pair),
                   default=node.start_mark.index)
    return max((_content_end(item) for item in node.value), default=node.start_mark.index)


def _service(text, service):
    _require(isinstance(service, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', service),
             "Invalid service name")
    root, document = _parse(text)
    matches = [value for key, value in root.value if key.value == 'services']
    _require(len(matches) == 1 and isinstance(matches[0], yaml.MappingNode) and
             not matches[0].flow_style, "Compose services must be a block mapping")
    matches = [(key, value) for key, value in matches[0].value if key.value == service]
    _require(len(matches) == 1, "Target service missing")
    key, node = matches[0]
    _require(isinstance(node, yaml.MappingNode) and not node.flow_style and node.value and
             key.start_mark.column == 2 and node.start_mark.column == 4 and
             node.start_mark.line > key.start_mark.line,
             "Target service must use ordinary block-style YAML")
    lines = text.splitlines(keepends=True)
    start = sum(map(len, lines[:node.start_mark.line]))
    leaf_end = _content_end(node)
    # Include the final target field's complete line, but retain following blank
    # lines and comments verbatim, including comments introducing another service.
    end = leaf_end if leaf_end and text[leaf_end - 1] == '\n' else text.find('\n', leaf_end)
    if end < 0:
        end = len(text)
    elif end != leaf_end:
        end += 1
    _require(start < end <= len(text), "Target service span is invalid")
    return document, start, end


def service_hash(text, service):
    """Hash the exact UTF-8 target body, independent of other services' edits."""
    _, start, end = _service(text, service)
    return digest(text[start:end].encode('utf-8'))


def patch_service(text, service, update):
    """Mutate a copy of one service mapping; preserve every outside byte verbatim."""
    document, start, end = _service(text, service)
    expected = copy.deepcopy(document)
    try:
        update(expected['services'][service])
        replacement = yaml.dump(expected['services'][service], Dumper=ComposeDumper, sort_keys=False,
                                allow_unicode=True, default_flow_style=False)
    except Exception:
        raise ServicePatchError("Target service update failed") from None
    _require(isinstance(expected['services'][service], dict) and expected['services'][service],
             "Target service must remain a nonempty mapping")
    replacement = ''.join('    ' + line if line.strip() else line
                          for line in replacement.splitlines(keepends=True))
    old = text[start:end]
    if '\r\n' in old:
        replacement = replacement.replace('\n', '\r\n')
    if not old.endswith('\n'):
        replacement = replacement.rstrip('\r\n')
    result = text[:start] + replacement + text[end:]
    _, actual = _parse(result)
    _require(actual == expected, "Compose change exceeded intended service")
    return result


@contextmanager
def _linux_lock(stream):
    _require(sys.platform.startswith('linux'), "Production Compose locking requires Linux")
    import fcntl
    fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
    try:
        yield
    finally:
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _safe_path(path):
    path = Path(path).absolute()
    _require('..' not in path.parts and all(not part.is_symlink() for part in (path, *path.parents)),
             "Symlink or nonnormalized path rejected")
    return path


def _private_directory(path):
    path = _safe_path(path)
    if not path.exists():
        _require(path.parent.is_dir(), "Backup parent directory missing")
        path.mkdir(mode=0o700)
    _require(path.is_dir(), "Backup directory is invalid")
    if os.name == 'posix':
        _require(stat.S_IMODE(path.stat().st_mode) == 0o700, "Backup directory must be mode 0700")
    return path


def _same_inode(path, stream):
    opened = os.fstat(stream.fileno())
    current = path.stat(follow_symlinks=False)
    _require(stat.S_ISREG(opened.st_mode) and stat.S_ISREG(current.st_mode) and
             (opened.st_dev, opened.st_ino) == (current.st_dev, current.st_ino),
             "Compose path inode changed")


def cas_service_update(path, service, expected_service_hash, update, backupdir, *, lock_factory=None):
    """CAS one service while preserving concurrent changes to other services.

    update receives a mutable service dict. Returns paths/hashes only. For rollback
    call again with the returned after_service_sha256 and an update which restores
    ONLY the old service mapping loaded from backup_path. Never copy the whole
    backup onto a live Compose file. A different target hash refuses rollback.

    lock_factory is an explicit test injection taking the open binary stream and
    returning a context manager. Omit it in production to enforce Linux flock.
    """
    _require(isinstance(expected_service_hash, str) and
             re.fullmatch(r'[a-f0-9]{64}', expected_service_hash), "Expected service hash invalid")
    path = _safe_path(path)
    backupdir = _private_directory(backupdir)
    _require(path.is_file(), "Compose file missing")
    lock = lock_factory or _linux_lock
    flags = os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
    with os.fdopen(os.open(path, flags), 'r+b') as stream:
        with lock(stream):
            _same_inode(path, stream)
            stream.seek(0)
            original = stream.read()
            try:
                text = original.decode('utf-8')
            except UnicodeError:
                raise ServicePatchError("Compose must contain valid UTF-8") from None
            before_service = service_hash(text, service)
            _require(before_service == expected_service_hash, "Target service changed; CAS refused")
            replacement = patch_service(text, service, update).encode('utf-8')
            after_service = service_hash(replacement.decode('utf-8'), service)
            receipt = {'before_file_sha256': digest(original), 'after_file_sha256': digest(replacement),
                       'before_service_sha256': before_service, 'after_service_sha256': after_service,
                       'changed': replacement != original, 'backup_path': None}
            if replacement == original:
                return receipt
            backup = backupdir / ('compose-' + uuid.uuid4().hex + '.yaml')
            descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0), 0o600)
            with os.fdopen(descriptor, 'wb') as saved:
                saved.write(original)
                saved.flush()
                os.fsync(saved.fileno())
            if os.name == 'posix':
                _require(stat.S_IMODE(backup.stat().st_mode) == 0o600, "Backup must be mode 0600")
            receipt['backup_path'] = str(backup)
            # Detect uncooperative replacement/writes before mutation. flock only
            # coordinates writers that honor it; no file API can police others.
            _same_inode(path, stream)
            stream.seek(0)
            _require(stream.read() == original, "Compose changed during update; CAS refused")
            try:
                stream.seek(0)
                stream.write(replacement)
                stream.truncate()
                stream.flush()
                os.fsync(stream.fileno())
                _same_inode(path, stream)
                stream.seek(0)
                _require(stream.read() == replacement, "Compose write verification failed")
            except Exception:
                # A partial write has an uncertain outcome. Do not overwrite
                # another writer through a blind whole-file rollback.
                raise ServicePatchError("Compose write outcome uncertain; inspect private backup and current state") from None
            return receipt
