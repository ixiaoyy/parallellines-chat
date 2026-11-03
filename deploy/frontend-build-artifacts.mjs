import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';

const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');

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
