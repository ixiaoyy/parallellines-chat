# Tavern 主站登录接入

## 范围与状态

本次为既有 OIDC 身份桥新增原生 Synapse 客户端，不重写 Element、Synapse 或主站登录。
本地代码准备不代表已经发布；本次不连接服务器、不生成或读取真实密钥、不重启服务，也不执行本地应用、构建或业务测试。

用户在 Tavern 尚无会话时打开 `https://tavern.pingxingxian.space/`，Element 原生 SSO 自动转 Synapse，再进入既有主站 `/auth/chat` 审批链路。
已有主站会话直接完成审批；无会话时只在主站登录，然后返回原审批流程。用户不再另设 Tavern 密码或使用邀请码注册。
原生设备验证、恢复密钥和端到端加密保留，不能把设备验证当作重复账号登录予以关闭。

## 固定合同

| 项目 | 约定 |
| --- | --- |
| 发行方 | `https://pingxingxian.space/auth/oidc` |
| 新客户端 | `parallellines-tavern` |
| 唯一回调 | `https://matrix.pingxingxian.space/_synapse/client/oidc/callback` |
| 客户端认证 | `client_secret_basic` |
| 授权码保护 | PKCE S256，所有客户端继续强制启用 |
| Scope | `openid profile`，不请求 `email` 或 `offline_access` |
| 身份来源 | 主站稳定 `sub`；姓名、用户名和邮箱不作为合并键 |
| 刷新令牌 | 不签发；不改变 Chat 原有刷新机制 |
| 失败重试 | 仅 `https://tavern.pingxingxian.space/`，不接受调用方任意跳转 |

仅在身份桥已有服务器私有环境文件显式配置 `TAVERN_OIDC_CLIENT_SECRET` 时注册新客户端；省略该变量保持现有两客户端注册不变。
新密钥至少 32 字符且不得与 Chat 或 New API 的客户端密钥相同。示例文件中的注释值不能用于运行。

Tavern 不继承 `OIDC_MAIN_PROFILE_SYNC_ENABLED`，没有已验证邮箱这一新增前置条件，也不接收该功能的邮箱或角色声明。
主站 `sub`、审批、会话 binding、一次性码及有效期继续由既有受信链路提供；主站内部协议 `client_id=parallellines-chat` 不改名。
现有 Chat/New API 的回调、密钥、资料同步开关、原生角色和额度合同不变。

## 获得生产授权后的发布顺序

以下动作均需另行获得本次生产目标授权；本文不授权执行。使用现有固定发布通道，不启动第二个发行方，不复用另一应用的数据或密钥。

1. 核实并保留当前身份桥容器、镜像、唯一 SQLite 数据目录、signing JWKS、Cookie keys 和全部现有环境文件顺序，记录可独立使用的健康检查与恢复入口。共享身份桥重建会短暂影响 Chat/New API 新授权，须在确认的窗口执行。
2. 仅为新客户端生成独立随机密钥，私下写入身份桥原有受限环境文件的 `TAVERN_OIDC_CLIENT_SECRET`，并将同一新密钥写到 Tavern 自己的受限 Synapse secret 文件，供原生 `client_secret_path` 读取。不要在公开 Compose、命令参数或 Actions 日志中传输/展开密钥，不更换旧密钥。具体私有路径和所有者以已核实的生产配置及 Tavern 发布合同为准。
3. 从主站娱乐运维工作流执行 `publish-tavern-bridge`。用已审 Chat 源码，仅在当前桥镜像上叠加 `config.mjs`、`error-page.mjs` 和 `identity-provider.mjs`；保留旧镜像和服务定义，不重装依赖、不改变环境文件和 SQLite/密钥挂载，只替换原单实例。历史 `deploy-identity-refresh.py` 不用于本次发布。
4. 核身份桥健康、OIDC 发现文档及新客户端已实际注册，再执行 `enable-main-login` 更新 Synapse OIDC 和 Element 自动跳转配置。先有新客户端，再关闭 Tavern 原生密码登录和邀请码注册。
5. 用 `main-login-status` 核健康和登录方式；部署负责人另行确认下列真实登录场景后，才执行 `publish-tavern-frontend`。状态探针不证明浏览器授权往返成功。验收不调用模型、不消耗余额；真实登录和浏览器操作另按当前授权执行。

Tavern 配置及固定运维消费者由 `parallellines-entertainment` 与主站娱乐运维入口维护；不将这里的 Compose 参考模板套到现网已有身份桥，也不复制主站 Actions Secrets。

Chat 的 [聊天频道入口](../openwebui-profile-overlay/frontend-chat-rooms/README.md) 是单独启用的前端增量。主站登录生效后再发布链接，避免先让用户进入仍要求独立登录的旧页面。

### 固定动作的输入

在主站 `Entertainment module operations` 工作流选择动作。各动作的 `chat_revision` 都用包含本发布输入核验脚本的完整、已审 Chat `main` 提交 ID；发布时重新从官方仓库读取该提交的固定文件。

| 动作 | 额外输入与前置 |
| --- | --- |
| `tavern-release-status` | 无。只读返回两项服务当前镜像 ID、容器健康、此提交的操作状态和已备妥的恢复入口。 |
| `publish-tavern-bridge` | `expected_image_id` 填状态动作中桥的完整镜像 ID；部署负责人先在现有私有配置中准备同一套独立 Tavern 客户端密钥。候选切换前按原配置链核两侧密钥一致及既有客户端仍在，只返回成功或失败，不启动第二个发行方。旧桥被覆盖的三个源文件须与已核中英发布前像一致。 |
| `publish-tavern-frontend` | `expected_image_id` 填当前 Chat 镜像 ID；`frontend_manifest_sha256` 填已审公开构建清单的 SHA-256；两种真实登录场景通过才勾选 `tavern_login_verified`。当前实例须是已核中英前端基线。 |
| `rollback-tavern-frontend` / `rollback-tavern-bridge` | 仅输入原发布使用的 `chat_revision`。桥回退额外核 Tavern 仍为原配置或已恢复，状态不明、正在切换或仍使用主站登录时停止。 |

先按[侧栏构建说明](../openwebui-profile-overlay/frontend-chat-rooms/README.md)准备或复用已验的公开产物。获准上传后，由部署负责人将 `frontend-build/`、`frontend-build-manifest.json`、`.dockerignore` 以及同一已审提交的 `openwebui-profile-overlay/Dockerfile.frontend-i18n`（保留文件名）放进服务器私有目录 `/data/deployment/parallellines-tavern-login/<chat_revision>/frontend-input/`。目录由 root 管理、权限 `0700`；不要传 `frontend-debug/`、source map、Pyodide、环境文件或密钥。动作校验公开文件和构建来源，从运行镜像派生，不在服务器重编前端，也不覆盖后端、启动或模型配置。

镜像或前像有意变更时应重新审核基线，不跳过守卫。激活中断或未通过健康检查时，状态保留为 `switching`，先按原提交执行回退；不会自动回退或继续下一组件。成功回退后可按同一提交和已审输入重试。

## 验收与现有账号

- 主站已登录时，打开 Tavern 无额外账号输入；主站未登录时仅出现主站登录，并可正确返回。
- 无已验证邮箱的有效主站用户可完成新客户端授权；其 `sub` 和 token audience 正确，不能拿 Tavern token 调用仅接受 Chat audience 的模型代理。
- 回调精确匹配，错误 verifier、缺失/非 S256 PKCE、交换别的客户端授权码和重放均拒绝；失败页只能返回固定 Tavern 入口。
- 现有 Chat/New API 授权、UserInfo 和 Chat 刷新令牌保持原行为；Tavern 不获得刷新令牌、同步邮箱或角色。
- 旧 Matrix 账号（含旧管理员）无需继续以原身份登录，不做绑定、迁移或同名、同邮箱接管。保留原账号、房间、消息、媒体和密钥；首次以主站账号登录产生的是新的 Matrix 身份，不继承旧账号的历史访问权限或管理员权限。
- 如后续仍需 Matrix 管理员，由部署负责人在另获授权后，使用私有原生管理渠道为指定主站用户对应的新 Matrix 身份明确授权。不依据主站角色自动提升，不将私有管理员 API 暴露到公网。
- 原生设备验证与恢复仍需人工验证。本次只有静态检查，未运行这些真实场景，不能宣称已实现线上免重复登录。

此接入仅统一新登录。Synapse 签发的原生会话不因主站退出自动失效，切换主站账号也不会自动切换 Tavern 已有会话的账号；本次没有增加 OIDC back-channel logout 或主站统一注销，也不把短期 OIDC token 生命周期冒充 Matrix 会话生命周期。

## 恢复

在变更前分别保存身份桥和 Chat 旧镜像/服务定义、Tavern 旧公开与私有配置及独立恢复入口，保留数据库和所有 signing key。

仅侧栏异常，执行 `rollback-tavern-frontend`，只切回 Chat 前一镜像，Tavern 继续使用主站登录。

如整套接入需应急回退，先撤回已发布的 Chat 前端入口，再执行娱乐模块 `retract` 撤下 Tavern 公网入口、`restore-main-login` 恢复原配置，最后执行 `rollback-tavern-bridge`。这样应急回退不会重新对外开放旧密码/邀请码入口。不要先回退仍被使用的共享身份桥。

切换前会保存独立恢复程序。如果 GitHub 工作流不可用，获准的服务器管理员将 `CHAT_REVISION` 设为原发布的完整提交 ID，可在服务器检查状态或回退。以下以只回退 Chat 前端为例；身份桥把路径和动作中的 `frontend` 改为 `bridge`：

```sh
operator="/data/deployment/parallellines-tavern-login/$CHAT_REVISION/frontend/operator.py"
sudo /usr/bin/python3 -B "$operator" tavern-release-status "$CHAT_REVISION"
sudo /usr/bin/python3 -B "$operator" rollback-tavern-frontend "$CHAT_REVISION"
```

回退不需要 GitHub、候选构建目录或浏览器登录。原镜像名称须仍指向所记录的镜像 ID；已有更新、文件变动或中断写导致前后像无法对应时停止，由部署负责人用私有回执和备份处理，不整份覆盖 Compose。

身份桥回退只恢复已记录的镜像和本次修改的服务定义，不改写原有环境文件。私有 Tavern 密钥不由发布/回退动作删除；不删除旧账号、消息、数据库、设备密钥或其他客户端的授权，也不重建主站、New API 或共享网关。
本次没有自定义 schema 或数据迁移；恢复镜像仍须按实际版本核兼容性，不自动回灌旧数据库覆盖新消息。
