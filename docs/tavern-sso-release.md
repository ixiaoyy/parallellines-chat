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
3. 从本次已审提交构建身份桥候选，保留旧镜像和源文件恢复版本；只替换现有单实例身份桥，保持其数据库和密钥挂载。不能拿历史 `deploy-identity-refresh.py` 的旧源码摘要锁当成本次新发布配方，也不为省事重建 Chat 或 New API。
4. 核身份桥健康与 OIDC discovery，再由娱乐模块固定发布入口更新 Synapse OIDC 和 Element 自动跳转配置。先确保新客户端已实际注册，再关闭 Tavern 原生密码登录/邀请码注册，避免先切客户端造成登录中断。
5. 仅核本次允许的低负载健康与登录路径；不调用模型，不消耗余额，不创建其他系统业务数据。登录业务与浏览器操作另按当前授权执行。

Tavern 配置及固定运维消费者由 `parallellines-entertainment` 与主站娱乐运维入口维护；不将这里的 Compose 参考模板套到现网已有身份桥，也不复制主站 Actions Secrets。

Chat 的 [聊天频道入口](../openwebui-profile-overlay/frontend-chat-rooms/README.md) 是单独启用的前端增量。主站登录生效后再发布链接，避免先让用户进入仍要求独立登录的旧页面。

## 验收与现有账号

- 主站已登录时，打开 Tavern 无额外账号输入；主站未登录时仅出现主站登录，并可正确返回。
- 无已验证邮箱的有效主站用户可完成新客户端授权；其 `sub` 和 token audience 正确，不能拿 Tavern token 调用仅接受 Chat audience 的模型代理。
- 回调精确匹配，错误 verifier、缺失/非 S256 PKCE、交换别的客户端授权码和重放均拒绝；失败页只能返回固定 Tavern 入口。
- 现有 Chat/New API 授权、UserInfo 和 Chat 刷新令牌保持原行为；Tavern 不获得刷新令牌、同步邮箱或角色。
- 保留已有 Matrix 身份域、原生账号、房间、消息、密钥和媒体；不按同名、邮箱自动绑定已有 Matrix 账号。需要保留旧账号身份时，须先由账号所有者证明归属并另行确认原生映射方案，不删除或合并历史。
- 原生设备验证与恢复仍需人工验证。本次只有静态检查，未运行这些真实场景，不能宣称已实现线上免重复登录。

此接入仅统一新登录。Synapse 签发的原生会话不因主站退出自动失效，切换主站账号也不会自动切换 Tavern 已有会话的账号；本次没有增加 OIDC back-channel logout 或主站统一注销，也不把短期 OIDC token 生命周期冒充 Matrix 会话生命周期。

## 恢复

在变更前分别保存身份桥旧镜像/服务定义、Tavern 旧公开与私有配置及既有恢复入口，保留数据库和所有 signing key。
若新登录失败，先按娱乐模块恢复合同恢复 Element/Synapse 原配置与原生登录能力，再恢复现有身份桥的已记录旧镜像和环境前像。
只移除本次新增的新客户端配置，不删除旧账号、消息、数据库、设备密钥或其他客户端的 grant，也不重建主站、Chat、New API 或共享网关。
本次没有自定义 schema 或数据迁移；恢复镜像仍须按实际版本核兼容性，不自动回灌旧数据库覆盖新消息。
