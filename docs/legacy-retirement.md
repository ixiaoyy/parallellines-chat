# 旧 LibreChat 退役

用户要求主站 `/chat` 转到新 Chat，不再保留旧服务和专属代码。

## 保留与移除

- `/chat`、`/chat/` 及旧子路径使用 303 跳转到 `https://chat.pingxingxian.space/`。
  不转发旧 query、会话 ID、OAuth code/state 或请求正文；不把整个旧 URL 拼到新站。
- 移除旧 LibreChat 应用、管理面板、MongoDB 三个容器及其专属部署目录、工作流和无用网络。
  只有未被其他容器使用的旧镜像才清理；不做全局 Docker prune。
- 旧 MongoDB 和附件等卷在停止写入后形成私有冷备份，数据卷离线保留，不删除数据库。
- 保留新 Chat、New API、主站的用户/角色/余额以及 `/auth/chat`、`/auth/oidc` 共享身份路径。
  共享 issuer 的数据库、Cookie/JWK 签名材料和现有新客户端凭据均不重建或轮换。

## 共享身份桥归位

桥仍以现有项目 `parallellines-chat`、服务 `chat-oidc` 运行，DNS alias 不变。
独立 Compose 放到 `/data/deployment/parallellines-identity/compose.yaml`，不再引用
旧 `deploy/librechat` 目录。现有配置输入在服务器内不解析、不打印地原样迁移到
私有 secrets 目录；仅原 service 中批准的环境变量进入进程，旧专属变量不再使用。
数据及 JWK bind mount 保持原来的 `/opt/parallellines/deploy/chat-sso` 物理路径。

仅注册 `parallellines-openwebui`、`parallellines-newapi` 两个 RP，移除旧回调/密钥要求、
旧专属刷新豁免及恢复链接。SQLite adapter 的 Client.find 同时限制已注册客户端集合，
避免 OP 从历史持久记录重新载入旧 RP；旧记录不删除，表、TTL 和事务规则不改变。
`main-bridge.mjs` 中内部 Main SSO 协议 CID `parallellines-chat` 必须原样保留。

## 发布、保护和恢复

`retire-legacy-chat.py scope.json` 仅用于已盘点的现有主机。scope 指定 `/data` 发布目录、
已提交源码、新桥镜像、原镜像 ID、三个旧容器 ID 和五份公共源码的前后摘要。
脚本依次备份旧代码/配置、迁移独立桥、热更新旧入口、停旧实例、冷备数据、删除旧实例
及专属代码。新桥只复制五个模块，沿用当前 Node 依赖；没有测试、lint、依赖安装或
收费模型请求。不能沿用旧 profile 发布仅复制四文件的清单。

身份桥或跳转激活失败时恢复原桥配置和 Nginx。数据与旧代码删除只在新站和身份桥
健康后开始。若后段归档/清理失败，保持新站与跳转，不恢复旧数据库或自动复活旧应用；
按私有备份和当前检查点处理。不能为重试删除已有数据卷或回灌 issuer 的旧令牌记录。

服务器旧部署工作区曾有未提交运行修复，整目录先形成 mode 0600 的私有恢复包再删除。
主站本地游戏分支和改动不在本次提交范围。主站远端应移除旧目录及手动部署工作流，
并保留新站/API 的当前代理配置，避免其他主站部署恢复旧代理或覆盖已运行的新站。

验收仅核跳转目标、查询参数未外带、旧实例/旧代码不存在、共享桥文件与健康和新站/API
可达；不声称已经代用户完成真实 OAuth 登录、刷新、聊天或扣费验收。
