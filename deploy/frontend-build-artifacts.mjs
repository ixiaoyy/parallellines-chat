import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { isDeepStrictEqual } from 'node:util';

const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const MAX_FILES = 12_000;
const MAX_PUBLIC_BYTES = 512 * 1024 * 1024;
const MAX_ASSET_BYTES = 128 * 1024 * 1024;
const MAX_MANIFEST_BYTES = 2 * 1024 * 1024;
const isRecord = value => value !== null && typeof value === 'object' && !Array.isArray(value);

function validPublicPath(relative) {
  // 保留许可证、字体及 Wasm 等公开资源，只排除不安全路径和不应随制品交付的文件。
  const parts = relative.toLowerCase().split('/');
  return !/[\\:\u0000-\u001f\u007f]/u.test(relative) &&
    parts.every(part => part && !part.startsWith('.') && !/[ .]$/.test(part) &&
      !['pyodide', 'frontend-debug', 'backend', 'node_modules', 'secrets', 'data', 'logs'].includes(part)) &&
    !/(?:\.map(?:\.(?:gz|br))?|\.(?:env|pem|key|sqlite\w*|db|py)|\.jwks\.json)$/i.test(relative);
}

function readPublicFile(file, limit) {
  const info = fs.lstatSync(file);
  if (!info.isFile() || info.nlink !== 1 || info.size > limit) throw new Error('frontend_public_file_invalid');
  return fs.readFileSync(file);
}

/** 用调用方提供的叠加层来源只读验证公开清单和资源树，不读取私有调试输出或复制文件。 */
export function verifyPublicFrontend({ publicDirectory, manifestPath, metadata }) {
  const manifest = JSON.parse(readPublicFile(manifestPath, MAX_MANIFEST_BYTES).toString('utf8'));
  if (!isRecord(manifest) || !isRecord(manifest.files)) throw new Error('frontend_public_manifest_invalid');
  const { files, ...actualMetadata } = manifest;
  if (!isDeepStrictEqual(actualMetadata, metadata)) throw new Error('frontend_public_manifest_source_changed');
  const names = Object.keys(files);
  if (!Object.hasOwn(files, 'index.html') || names.length > MAX_FILES ||
      !fs.lstatSync(publicDirectory).isDirectory()) {
    throw new Error('frontend_public_inventory_invalid');
  }

  const directories = new Set();
  const foldedNames = new Set();
  for (const name of names) {
    const folded = name.toLowerCase();
    if (!validPublicPath(name) || foldedNames.has(folded) ||
        typeof files[name] !== 'string' || !/^[a-f0-9]{64}$/.test(files[name])) {
      throw new Error('frontend_public_path_or_hash_invalid');
    }
    foldedNames.add(folded);
    for (let parent = path.posix.dirname(name); parent !== '.' && !directories.has(parent);
      parent = path.posix.dirname(parent)) directories.add(parent);
  }

  let fileCount = 0;
  let publicBytes = 0;
  function inspect(relative = '') {
    for (const entry of fs.readdirSync(path.join(publicDirectory, relative), { withFileTypes: true })) {
      const child = relative ? `${relative}/${entry.name}` : entry.name;
      if (entry.isDirectory() && directories.has(child)) {
        inspect(child);
      } else if (entry.isFile() && Object.hasOwn(files, child)) {
        const bytes = readPublicFile(path.join(publicDirectory, child), MAX_ASSET_BYTES);
        if (sha(bytes) !== files[child] || (child === 'index.html' && bytes.length === 0)) {
          throw new Error('frontend_public_asset_changed');
        }
        publicBytes += bytes.length;
        if (publicBytes > MAX_PUBLIC_BYTES) throw new Error('frontend_public_bytes_exceeded');
        fileCount += 1;
      } else {
        throw new Error('unexpected_frontend_public_asset');
      }
    }
  }
  inspect();
  if (fileCount !== names.length) throw new Error('frontend_public_asset_missing');
  return { fileCount, publicBytes };
}

/**
 * 将 built 中的生成前端拆成 publicDirectory 与私有 debugDirectory，不改生成文件字节。
 * manifestPath/metadata 沿用调用方的公开清单位置与来源字段，已有产物拒绝覆盖，Pyodide 不复制。
 * 返回公开 manifest、私有 debugFiles 摘要表及两类原始字节数，供交付核对和构建日志使用。
 */
export function packageFrontendBuild({ built, publicDirectory, debugDirectory, manifestPath, metadata }) {
  for (const target of [publicDirectory, debugDirectory, manifestPath]) {
    if (fs.existsSync(target)) throw new Error('output_exists_preserve_existing_artifact:' + target);
  }
  if (!fs.statSync(path.join(built, 'index.html')).isFile()) throw new Error('built_index_missing');
  const files = {};
  const debugFiles = {};
  const bytes = { public: 0, debug: 0 };
  fs.mkdirSync(debugDirectory, { recursive: true, mode: 0o700 });

  function copy(relative = '') {
    for (const entry of fs.readdirSync(path.join(built, relative), { withFileTypes: true })) {
      const child = relative ? `${relative}/${entry.name}` : entry.name;
      // 继承原镜像的完整 Pyodide；source map 仅供私有调试，不能进入公开目录或发布清单。
      if (child === 'pyodide') continue;
      if (entry.isSymbolicLink()) throw new Error('build_symlink_rejected');
      if (entry.isDirectory()) copy(child);
      else if (entry.isFile()) {
        const content = fs.readFileSync(path.join(built, child));
        const debug = child.endsWith('.map');
        const destination = path.join(debug ? debugDirectory : publicDirectory, child);
        fs.mkdirSync(path.dirname(destination), { recursive: true, mode: debug ? 0o700 : 0o755 });
        fs.writeFileSync(destination, content, { flag: 'wx', mode: debug ? 0o600 : 0o644 });
        (debug ? debugFiles : files)[child] = sha(content);
        bytes[debug ? 'debug' : 'public'] += content.length;
      } else throw new Error('unexpected_build_entry');
    }
  }

  copy();
  const manifest = { ...metadata, files };
  const serialized = JSON.stringify(manifest, null, 2) + '\n';
  fs.writeFileSync(manifestPath, serialized, { flag: 'wx' });
  // 私有清单绑定公开清单摘要，排障时可核对来源；该目录不属于 Docker 上下文或上传范围。
  fs.writeFileSync(path.join(debugDirectory, 'source-map-manifest.json'), JSON.stringify({
    ...metadata, production_manifest_sha256: sha(serialized), files: debugFiles,
  }, null, 2) + '\n', { flag: 'wx', mode: 0o600 });
  return { manifest, debugFiles, bytes };
}

export const privateDebugDockerIgnore = 'frontend-debug/\n';
