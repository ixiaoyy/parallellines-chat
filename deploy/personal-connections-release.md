# Chat 个人连接：固定增量发布

此入口只发布现有 Chat 服务，不发布主站、圈子、身份桥或模型网关，不修改真实环境文件、原生配置、数据、挂载和网络。`ENABLE_DIRECT_CONNECTIONS` 即使关闭也只报告，不自动开启。

## 已确认的目标与通道

- Main 仓库新增 `.github/workflows/chat-ops.yml` 与 `.github/scripts/chat_ops.py`，复用现有 `DEPLOY_*` Secrets 和固定主机指纹，不复制或读取 Secrets 值。
- 固定 Compose project `pl-official-candidate-20261030`、service `openwebui`、container `/pl-official-candidate-20261030-openwebui-1`。执行时重新核唯一服务及健康，使用实际 Compose 文件链/工作目录/env_file 元数据；不能仅依据此说明直接重建。
- 私有发布根：`/data/deployment/parallellines-chat-personal/<Chat 完整提交 SHA>`。镜像 ID、源文件摘要和服务定义仅在服务器内部捕获、比较和保存，控制台不输出被 GitHub 遮蔽的值。
- `inventory` 与 `preflight` 使用独立读取并发组；`prepare`、`deploy` 进入现有 `deploy-production` 互斥组。没有任意 shell、脚本或下载地址参数。

## 固定公开包

标签：`personal-connections-<Chat40hex>`。

资产名：`parallellines-chat-personal-connections-<Chat40hex>.tar.gz`。

仅从 `https://github.com/ixiaoyy/parallellines-chat/releases/download/标签/资产名` 下载。操作输入必须提供人工核对的完整 SHA256；官方 GitHub 资产 HTTPS 重定向允许，其他主机拒绝。

```text
release-input-manifest.json
source/                         本次 Chat Git commit 的 git archive 内容
context/
  Dockerfile.personal-connections
  .dockerignore
  frontend-build-manifest.json
  frontend-build/                已验公开资产，不含 source map/Pyodide
  personal-connections/          仅 Dockerfile COPY 的生产 Python 和 manifest
```

根清单 schema 为 `1`，字段为 `chat_revision`、`frontend_manifest_sha256`、`backend_overlay_manifest_sha256`。两个清单摘要分别绑定公开构建和本次后端增量。包中不放 `.git`、真实 `.env`、密钥、数据库、`node_modules`、私有映射或诊断数据。

归档先校验全部条目再展开：最多 12000 项，压缩包最多 256 MiB，展开合计最多 512 MiB，单文件最多 128 MiB；拒绝绝对路径、父级逃逸、反斜杠、重复路径、链接、设备文件和私有文件。准备完成后固化包内每个文件与私有 scope 的摘要，后续预检/部署拒绝漂移。

## 操作与边界

1. `inventory`：只读当前单 Chat 镜像标签、服务身份和健康，不输出镜像 ID、环境或用户数据。
2. `prepare`：下载固定公开包，校验完整哈希及安全展开；以 feature manifest 的 backend before 值核当前运行源；服务器内部生成私有 scope。检查 SQLite、持久化挂载及稳定密钥，不构建/重启服务。
3. `preflight`：独立读取健康；准备后的 scope 还核 before 镜像、源、Compose、密钥和数据库。它是激活前条件检查，成功部署后 before 已变化时会拒绝；`inventory` 仍可独立检查现场健康。
4. `deploy`：再次核准备包与实际前像，离线叠加当前完整镜像；空临时数据、无网络镜像探针检查真实新模块。使用 SQLite 原生 `.backup()` 生成私有一致备份，再仅 CAS 更新 Chat 的 image/build 并重建该服务。

本次没有数据库迁移或配置写入。SQLite 源使用只读连接；备份为本次私有目录内 `webui-before.db`，权限 0600，不自动恢复数据库。非 SQLite 或未知持久挂载直接停止。

密钥连续性在容器内验证：运行密钥必须有保留的 Compose/env_file 来源，或位于持久挂载中的匹配密钥文件；不生成/替换密钥。一次性加密 canary 仅留操作进程内存，新容器只返回匹配布尔值，原始 Key 及其哈希均不输出。

健康后读回本次 `/app/frontend-personal-connections-manifest.json`、全部公开资产和后端 after 值，确认无公开 source map、密钥连续；不会调用生成模型。现有原生 Direct 开关只读报告，关闭时功能仍不对用户开放。

## 失败恢复

继承 `deploy-profile-sync.py` / `profile-deploy-utils.py` 的目标服务 CAS、私有定义备份、健康和条件恢复，保留原镜像。不修改其他服务，不整文件覆盖 Compose，不回灌旧数据库。

候选尚未启动时恢复原服务定义。`compose up` 已尝试但实际仍是预期旧镜像时，在同一激活定义 CAS 下直接恢复旧定义并核健康，不停止仍健康的原容器；若原容器已停才按原定义重起。陌生第三镜像仍拒绝接管。

实际已运行候选时，恢复回调先在既有服务 CAS 锁内确认仍是本次激活定义和候选镜像，捕获具体容器 ID，仅停止并确认该候选已排空，再只读检查 `plpc:v1:` 新密文；只有零条时才在同一 CAS 写回旧定义并启动旧镜像，避免查询后仍接受新 Key 写入的竞态。

若已有新密文或无法只读确认，不写旧定义；以同一激活前像的 no-op CAS 按候选定义重起，并记录其健康与向前修复依赖。若他人已接管目标服务，CAS 会在 stop 前拒绝，或在候选重起前拒绝，不停止/覆盖对方服务。私有 `receipt.json` 记录排空、密文阻断、恢复/候选重起和健康错误；不恢复数据库，独立读取通道保持可用。

## 验证层级

本地定向测试只验证固定操作、下载/解包边界、公开输入/离线构建命令、配置保留、真实临时 SQLite 一致备份、密钥 canary、恢复条件和无数据回灌，不代表已部署。

```powershell
python -m unittest discover -s deploy -p test_deploy_personal_connections.py -v
python -m unittest discover -s ../ParallelLines/.github/scripts -p test_chat_ops.py -v
```

Main CI 当前把 `.github/scripts/**` 纳入前端/pipeline 检查，但部署范围来自独立 api/web/nginx/compose 过滤。仅本次 Chat 运维文件 push 的四个部署标志均为 false；发布者仍须读取实际 push 的 deploy-plan，不能手动触发 Main 全栈 Deploy。

实际执行由本次 root 操作者完成：先提交、推送两个仓库各自的本次文件，发布明确 Chat commit 对应的包，再依次 dispatch inventory → prepare → preflight → deploy。需要真实登录/供应商验收时另按已授权范围处理，不把文件读回和健康替代模型或计费验收。
