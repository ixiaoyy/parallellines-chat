# 统一登录：服务器增量交付

实际服务器操作归本次 root 操作者。此页不是已激活回执。不要运行 `.build/unified-integration-*` 的隔离测试上下文，也不要上传合成凭据、真实 `.env`、key、data 或 `frontend-debug`。

## 已核基线与提交范围

root 的只读盘点 `43622360404` 确认：Chat 当前为 `parallellines-chat:silent-recovery-20261112`，镜像 `sha256:e672d829c3a9a1306f55727c7cf624fade235e4ec65fb00ecdfdf5ae6a0fc42b`；项目 `pl-official-candidate-20261030`、服务 `openwebui`、Compose `/opt/pl-official-candidate-20261030/compose.yaml`、loopback 18080。发行方当前为 `parallellines-identity:account-sync-20261102`，镜像 `sha256:6837ffcfbef78fe4316155807d17148bd53848c78979743ac08f3970994c7b35`；项目 `parallellines-chat`、服务 `chat-oidc`、Compose `/data/deployment/parallellines-identity/compose.yaml`。执行前仍须让服务级 CAS 和镜像/源条件复核，不能仅沿用本文。

统一登录的原 16 个生产文件及新增 `deploy/deploy-unified-login.py` 进入本次提交。为保留已在线 silent/auth-recovery 的对应源，还需要这 14 个前置文件；这不授权重发旧功能或重置配置：

```text
openwebui-profile-overlay/Dockerfile.current-recovery
openwebui-profile-overlay/Dockerfile.silent-recovery
openwebui-profile-overlay/apply_model_auth.py
openwebui-profile-overlay/main_model_auth.py
openwebui-profile-overlay/main_model_silent_recovery.py
openwebui-profile-overlay/frontend-auth-recovery/apply.py
openwebui-profile-overlay/frontend-auth-recovery/frontend-auth-recovery.patch
openwebui-profile-overlay/frontend-auth-recovery/main-oauth-auth-recovery.mjs
openwebui-profile-overlay/frontend-auth-recovery/manifest.json
openwebui-profile-overlay/frontend-silent-recovery/apply.py
openwebui-profile-overlay/frontend-silent-recovery/apply_backend.py
openwebui-profile-overlay/frontend-silent-recovery/frontend-silent-recovery.patch
openwebui-profile-overlay/frontend-silent-recovery/main-model-silent-recovery.mjs
openwebui-profile-overlay/frontend-silent-recovery/manifest.json
```

`deploy/build-performance-frontend.mjs`、`deploy/frontend-build-cache.mjs` 及上一轮独立 deployer/test/docs 改动不是新 `build-unified-frontend` 的直接依赖，不因已 staged 就混入本次。当前统一登录的 5 个测试文件、其 README、本页与 `.gitignore` 的 `.tmp/` 行属于本次提交；不提交整个 `.tmp`/`.build`。

## 发布阻断修正与前端复用

Open WebUI 的 `token.userinfo` 可来自 ID token，email/username 已齐时原生不会读取 `/me`。可信统一标记只由发行方 UserInfo 提供。因此正式 overlay 仅在 `MAIN_UNIFIED_LOGIN_ENABLED=true` 且 provider 为 `oidc` 时进入既有 `client.userinfo(token)` 分支，保留原 claims 回填；旧模式和其他 provider 原逻辑不变。

本次只修后端 OAuth callback，不重构建已验前端。公开 manifest 的 `feature_manifests` 保留真实原编译输入；新增 `backend_overlay_manifest_sha256` 指向修正后正式 overlay，`frontend_reuse` 明确 `rebuilt:false`、原 manifest、原编译 unified 摘要及 7 个未变前端源摘要。公开 4810 个文件及文件字节不变。私有 source-map manifest 只同步公开 manifest 的绑定，不进入发布包。服务器 helper 同时核原前置清单、当前后端清单、前端源复用证明和全部公开文件，不能把更新 metadata 当作重构建。

## 最小正式上下文

Chat 上下文只有：

```text
Dockerfile.unified-login
.dockerignore
frontend-build-manifest.json
frontend-build/                 # 4810 个已验公开文件，不含 map/Pyodide
unified-login/apply.py
unified-login/manifest.json
unified-login/main_unified_login.py
unified-login/start.sh
```

正式 Dockerfile 只从上述当前完整 Chat 镜像 ID 增量构建，`apply.py /app --backend-only` 拒绝错误 before、重复或缺消费者；不从 stock 镜像补起，不重新安装 Python 包。新 manifest 复制到既有 `/app/frontend-performance-manifest.json`，沿用完整公开清单读回入口。Pyodide、原生数据、模型连接和费用保持原样。

发行方 helper 从当前完整发行方镜像 ID 叠加 `config.mjs`、`identity-provider.mjs`、`main-bridge.mjs`、`sqlite-adapter.mjs`、`rp-session.mjs` 五个文件，先核现有 package/lock，不安装 npm 包；不复制或改写 SQLite、签名 key、RP secret。只能替换现有一个 issuer 进程。

## 服务器 scope 与调用

复用 `deploy-frontend-performance.py` → `deploy-profile-sync.py` → `profile-deploy-utils.py` 的服务级 CAS、私有备份、健康和条件恢复。不要直接调用旧 profile bridge build（它不含 rp-session/sqlite adapter），也不要用旧恢复 deployer 交付统一登录。

scope 顶层为 `source`、`receipts` 两个 `/data` 绝对路径，以及 `bridge`/`chat` 对象。各对象沿旧 Release 的字段：`container,project,service,new_image,expected_image_id,expected_compose_image,target_compose,working_dir,env_files,compose_files,source_root,interpreter,expected_source`。路径和原 Compose 文件/插值文件集合必须由 root 当前盘点填入，不能用新模板覆盖。`source_root=/app`；bridge `interpreter=node`；chat `interpreter=python`，另给 `frontend_context` 和 `frontend_manifest_sha256`。`expected_source` 仅列真实已核或将由服务器条件核验的旧文件；新增文件不能拿 `null` 当既有摘要。被日志遮蔽的字段仍为未知，不换通道恢复。

Chat 的 `expected_source` 必须包括正式 overlay 全部 6 个非空 backend before 值。发行方必须包括原 package/lock；其他受保护旧源按 root 盘点保留。可用发布包中 `release-input-manifest.json` 的 `expected_backend_before`/`desired_backend_after` 与公开 manifest 填值，但这些是发布预期，不冒充已观察到被遮蔽的实际值。

```sh
python SOURCE/deploy/deploy-unified-login.py ROOT_REVIEWED_SCOPE.json bridge
python SOURCE/deploy/deploy-unified-login.py ROOT_REVIEWED_SCOPE.json chat
```

命令包含实际激活，不是 preview。先让 Main Cookie/审批/状态 frame 与公共代理恢复路径拒绝规则就绪，再激活 bridge，确认后再 Chat。helper 只改目标 image/build 和非敏感开关：bridge `UNIFIED_AUTH_ENABLED=true`；Chat `MAIN_UNIFIED_LOGIN_ENABLED=true`、`WEBUI_AUTH=true`、`OAUTH_AUTO_REDIRECT=false`、恢复模式默认 false。不改 env_file、凭据、挂载、网络、原生模型配置。若 Chat 已有自定义 command/entrypoint，先停止并由 root 审核，不能绕过 `--no-proxy-headers`。

激活失败只在服务仍匹配本次写入时恢复本服务原定义、旧镜像与健康，私有回执记录恢复结果；不整文件覆盖 Compose，不回灌数据库。跨 Main/bridge/Chat/New API 的总顺序和整组恢复仍归 root。保留上述原镜像和独立 SSH/健康路线。

## 验收边界

已保留真实 issuer 锁依赖 37 pass、0 fail、1 Windows/POSIX skip，及已接受的游客 UI、原定向单测和完整前端构建证据。后续 UserInfo 强制获取与本服务级发布封装按用户指示不新增本地测试/重构建；必须由 root 在服务器候选完成真实 OIDC callback、单浏览器退出/换号、其他设备保留、匿名限制、公开分享和恢复隔离。健康与文件读回不是这些业务验收。不要调用模型或把本次登录验收描述成模型/费用验证。FableSpace 不变。
