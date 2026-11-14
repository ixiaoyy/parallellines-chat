# 主站统一登录与聊天游客入口

此目录维护 Open WebUI `v0.12.5`、提交 `f7ac7ee10962ddec0017f5c29b6740d9b9c5faca` 的统一登录增量，接在 performance → bootstrap → auth-recovery → silent-recovery 后。源提交、发行方协议测试和部署健康均不能替代真实跨站登录验收，也不构成线上激活回执。

## 行为

- 公开聊天入口和 `/auth` 显示保留 Open WebUI 品牌的游客界面，不挂载私有 Chat、Sidebar、模型目录、历史、工具、上传或个人 Socket。已公开的 anyone-read 分享路径不改。
- 游客可写文本；发送、选择模型、上传、历史操作进入原生 OIDC，真实权限仍由后端验证。草稿沿独立 sessionStorage key 返回空的新对话，不自动发送，不覆盖明确 URL prompt；不在页面常驻展示实现说明。
- Main 是唯一普通凭据入口。统一模式在认证/开户/LDAP 连接前拒绝本地 signin/signup/ldap；普通用户的密码修改入口隐藏并拒绝，主站资料、稳定 `sub`、角色、模型连接和计费合同不变。
- 新 Chat Web JWT 签入可信 Main binding、主体、有效期和具体 OAuthSession ID；同一上下文仍放在原生加密 token JSON。原生 OAuth/MFA 完成后才签发，不以邮箱合并或前端消息建立身份。
- 私有 Web 请求和原生 Socket token 验证实时向发行方复核 Main。OAuth 行存在时还检查所属用户和元数据；只有原生合法剪枝造成的缺行可继续依赖签名上下文与 Main 实时状态。数据库/解密/authority 故障返回 503，不伪装退出。API key、automation/subagent 原路径不改，不调用账户级撤销。
- 旧 bearer 审批 binding 没有 `pl_unified_browser:true`，不会自动变为统一登录；旧 Main OAuth Web JWT 需重新接续一次。当前浏览器 Main 退出/换号清旧私有 UI 和 host Cookie，未撤其他设备的 sid。
- 用户退出先由本 RP Basic 凭据申请固定 Main 单次票据，再由 Main 撤原 sid。Main 不可用不伪报全站退出。出票竞争只再读一次 liveness，不重试出票。

## 接口和开关

| 位置 | 开关或入口 | 限制 |
| --- | --- | --- |
| Main | `UNIFIED_AUTH_ENABLED` | 中央 Cookie、`/auth/unified/chat-approve` 和状态 iframe 先就绪 |
| issuer | `UNIFIED_AUTH_ENABLED` | 默认关闭；开启 RP 后台两口及 UserInfo `pl_unified_browser` |
| Chat | `MAIN_UNIFIED_LOGIN_ENABLED` | 默认关闭；不使用 `WEBUI_AUTH=false` |
| 恢复 | `MAIN_UNIFIED_RECOVERY_ENABLED`、`MAIN_UNIFIED_RECOVERY_ADMIN_IDS` | 默认关闭；稳定本地 admin ID JSON 白名单，不根据有无 OIDC 绑定猜账号 |

发行方使用两个 RP 各自原有 Basic 凭据，主站内部桥 client_id 仍是 `parallellines-chat`；bridge secret 不下放。

- `POST /auth/oidc/main-session-state` `{binding_id,subject}`：客户端必须拥有实际存活 Grant；可信统一绑定返回 `active/sub/binding_id/auth_expires_at/unified_browser`，真实无效返回 200 `active:false`，Main 故障 503。
- `POST /auth/oidc/unified-logout-ticket` 同 proof：仅返回登记 RP 根地址对应的 `https://pingxingxian.space/auth/unified/logout?ticket=...`、60 秒；不接受调用方 return URL。
- Chat `GET /api/v1/auths/unified/session`：原生真人依赖已复核后只给非敏感 subject/recovery 状态。
- Chat `POST /api/v1/auths/unified/local-signout`：固定 Chat Origin，只清当前签名 JWT 所属 OAuth 行和 host Cookie，无需旧 Main 仍活跃。

状态 frame 固定 Main SPA `/auth/unified/state?client=chat&request_id=<32lowerhex>`。父页严格核 Main origin、iframe window、nonce 和消息版本。只有 `available:true` 的匿名状态可当真实退出；不可用保留身份，不恢复成错误账号。frame 不传 token/email，focus/主站通知触发有限更新，不轮询。

## 应用与构建

从已核 silent-recovery 完整源码建立新的隔离目录，保留既有 Responses、免费模型、资料同步等材料；不要修改其他任务的展开源。不要复制真实 `.env`、数据、私钥或运行日志。`manifest.json` 的 before/after 和唯一锚点同时拒绝版本漂移、碰撞和半应用。

```powershell
python openwebui-profile-overlay/unified-login/apply.py SOURCE --check
python openwebui-profile-overlay/unified-login/apply.py SOURCE
node deploy/build-unified-frontend.mjs SOURCE NEW_ARTIFACT
```

构建入口本身不安装依赖；使用对应 lock 的既有依赖。本轮经明确授权，仅在隔离 issuer 副本执行现有锁的 `npm ci --ignore-scripts --no-audit --no-fund`，安装 42 个包，不更新版本或运行生命周期脚本。构建复用 `frontend-build-artifacts.mjs` 分离公开产物与私有 source map，保留原镜像 Pyodide；不上传 `frontend-debug`。

Chat 镜像只基于已核完整 silent-recovery 镜像构建：在新上下文放 `Dockerfile.unified-login`、本目录为 `unified-login/`、构建公开目录为 `frontend-build/`。`CHAT_BASE_IMAGE` 必须明确指定；`apply.py /app --backend-only` 校验实际镜像前置，不以 stock 上游替代。发行方 Dockerfile 已包含新的 `rp-session.mjs`。本次未运行 Docker 构建。

实际 root 服务器发布使用[最小增量封装与 CAS 调用](../../deploy/unified-login-release.md)。已验公开前端允许明确记录的后端-only UserInfo 修正复用：保留原编译输入，单独记录当前后端清单与未变前端源，不冒充重构建。该修正只在 unified+oidc 读取实时 UserInfo，避免带齐 email/username 的 ID token 缺少可信统一标记而拒绝登录。

`deploy/unified-login-chat.overlay.yaml`、`deploy/unified-login-issuer.overlay.yaml` 分别合入各自现有 Compose 项目；不要由一个模板新建第二个 issuer，保留现有 SQLite、签名密钥和服务凭据。开关默认关闭，后续启用需该目标环境另获授权，并先验 Main Cookie/审批/frame/代理，再 issuer，再 Chat/New API，最后启用各消费者，不能单独打开 Chat。

## 隔离管理员恢复

唯一恢复入口为 `POST /api/v1/auths/unified/recovery/signin`，仍验证原生密码、限频和 MFA。要求真实 loopback 直连、显式开关和稳定 local admin ID 入列。该入口不出现在公开页面。

新镜像通过 `unified-login/start.sh` 沿用原生启动，只增加 `--no-proxy-headers` 并保留 worker/压缩参数，防止转发头把公网来访伪装成 loopback。公开 Main/Chat 代理必须拒绝恢复路径，Chat 不监听公网；恢复应在容器内直连或受控本地隧道使用。不要覆盖该启动参数，不要把真实密码写入命令历史或文档。完成恢复后关闭开关；关闭后恢复 JWT 也不能继续访问。

## 检查与未验收项

```powershell
$env:OPENWEBUI_SILENT_BASELINE='明确的原始 silent 源码绝对路径'
python -m unittest discover -s openwebui-profile-overlay/unified-login -p 'test_*.py' -v
node --test openwebui-profile-overlay/unified-login/test_browser.mjs
node --test identity-bridge/test/rp-session.test.mjs
```

本地通过：8 项后端替身合同、4 项真实 overlay 应用/消费者检查、5 项浏览器模块、2 项 RP 本地 HTTP；完整前端构建通过。实际完整构建游客页已检查 1440/390/320 宽和 `/auth`：新访客空输入、无密码/上传表单；选择模型、发送、上传、历史只到本地捕获的 OIDC，草稿不自动发送。预览 CSP 禁止 Main frame 外联，未查询生产或调用模型。

经后续隔离验证，真实锁依赖的 provider/multiclient/sqlite-adapter 全套已通过：37 pass、0 fail、1 skip（Windows 不执行 POSIX 0700/0600 权限专项）。包含真实 OIDC 的统一标记布尔值、固定 audience、客户端 Grant 所有权、PKCE、刷新和持久化；只将 Main 权威服务替换为本地夹具，所有 SQLite 和签名 key 均由测试生成。这不能当成真实 Main/Chat 跨站验收。

尚未验收：aiohttp 在后端隔离单测中仍用桩，不证明真实 issuer 连接；未运行完整 OWUI/FastAPI/数据库/MFA/Socket 入口。还需在获授权隔离环境完成真实 Main Cookie/OIDC、两浏览器单 sid 退出/换号、旧绑定迁移、私有 HTTP/Socket、恢复隔离与公开分享验证。当前验收不调用模型；模型及计费不从本次登录测试推断。FableSpace 不在范围内。

恢复必须整组退回上一套已核 Main/issuer/Chat/New API 镜像和开关配置，保留数据/密钥；不能只退 Chat 前端或用 stock 上游重置数据。回滚不恢复已撤销 sid，原浏览器需重新登录。不要用关闭统一开关临时开放普通密码绕过故障。
