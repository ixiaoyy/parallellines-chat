# 模型授权后台恢复

基线为已发布 `auth-recovery-20261111`：前端已包含 performance、bootstrap 和手动授权提示，后端保留原 Responses/免费模型补丁。旧 `frontend-auth-recovery/manifest.json` 是历史交付，不能改写为本功能的结果。

## 行为和安全边界

- `/api/models` 的确切 `403 main_oauth_authorization_required` 同步启动后台流程，原请求仍立即失败，不把 20 秒恢复加入原 12 秒启动组。
- 同页/同失败阶段共用一个隐藏 iframe；成功事件只让 App 布局重新读一次模型目录。只在真实目录成功后允许后续新失效再次恢复；不重发聊天、不离开页面、不保存或替换草稿/附件。
- 失败保留用户操作。账号不一致仅显示确认原账号后重新检查授权的按钮，该按钮仍走受保护恢复入口，不能直接切换 Chat 登录。普通网络/超时/iframe 被阻止不能当成主站会话过期。
- 新 `POST /oauth/oidc/model-authorization` 要求原生已验证真人 JWT、显式 Bearer、固定 Chat Origin，且 Bearer 用户与仍有效的 Chat Cookie 用户一致。
- Authlib 原生 state 元数据只绑定 Chat 用户 ID、关联 ID 和 120 秒创建时间；不信任前端主体、不在 URL/新浏览器存储中保存 JWT。
- 恢复使用逐请求浅拷贝客户端和独立 `pl_model_oidc` state namespace，原 `oidc` 客户端/全局 manager 不变。旧后端回滚后找不到恢复 state，不能交换该授权码或进入普通登录；并行普通登录 state 不被恢复 prepare 清除。
- 原固定 OIDC 回调先完成 Authlib state/token/UserInfo 校验，取得可信主体后立即核对当前仍有效的 Chat Cookie 与既有 OIDC 主体。只为同一用户保存 OAuth session；不进入资料、角色、开户、MFA、Chat JWT 签发链。
- prepare 总限时 5 秒、callback 总限时 15 秒、后台总限时 20 秒。保持原 issuer、scope、`prompt=consent`、PKCE、token 寿命和主站实时复核；不改 Main/Bridge 的 framing 或错误页策略。
- 完成页仅向固定 Chat origin 发送固定类型、关联 ID 和结果；父页同时检查 origin、iframe window 和关联 ID。完成页带 `no-store`、`no-referrer`、`nosniff` 与 nonce CSP。

## 可重现源码应用

从上轮完整源码建立新的隔离目录，不复制 `node_modules`、`build`、`.svelte-kit`、`target`。复用同锁版本依赖 Junction，不安装依赖。

1. `python frontend-silent-recovery/apply.py SOURCE`。
2. `python frontend-silent-recovery/apply_backend.py SOURCE/backend/open_webui`。
3. 将 `openwebui-profile-overlay/main_model_silent_recovery.py` 复制为 `SOURCE/backend/open_webui/utils/main_model_silent_recovery.py`。
4. 按本目录 `manifest.json` 验证三份前端和三份后端文件。构建/部署使用对应新入口，不复用旧 stock recovery 镜像。

## 本地定向检查

设置 `MODEL_RECOVERY_BASELINES` 指向上轮源码的 `index.ts`、`app-layout.svelte`、`main.py`、`oauth.py` 精确副本，设置 `OPENWEBUI_SILENT_INDEX` 指向本轮已补丁源码的 `src/lib/apis/index.ts`：

```powershell
python -m unittest discover -s openwebui-profile-overlay/frontend-silent-recovery -p 'test_*.py' -v
node --test openwebui-profile-overlay/frontend-silent-recovery/test_silent_recovery.mjs
```

这些检查使用替身存储、身份服务和传输，执行实际 helper、补丁后 callback、`getModels` 与未改动的 bootstrap。实际 Authlib 1.7.2 的纯内存 namespace 往返与旧客户端拒绝证据另存本次任务记录；两者均不证明浏览器 Cookie/framing、生产同账号 SSO、真实模型可用或计费结果。真实浏览器验收必须另完成，不通过制造生产故障补测。
