# 对话发送阶段报错分类与恢复引导

在已核 8 层基线（截至 `frontend-chat-rooms`）之上，为 `Chat.svelte` 的 `handleOpenAIError` 接入独立的报错分类与下一步引导模块，不改动 Open WebUI 布局、消息存储或登录契约。

## 分类与动作

| 类型 | 平台模型（`system_oauth`） | 个人连接模型（`isPersonalModel`） |
| --- | --- | --- |
| **授权失效** | 提示重新授权，提供「重新授权」按钮（`/oauth/oidc/login`） | 提示个人 Key 鉴权失败，提供「检查个人连接」按钮（打开设置 `connections` 页） |
| **余额 / 额度不足** | 提示平台额度不足，提供「查看平台额度」入口（新标签页） | 提示个人连接余额不足，提供「检查个人连接」入口 |
| **限流（429）** | 提示请求过于频繁，提供「稍后重试」动作，不诱导重新登录 | 同左 |
| **网络 / 上游波动（5xx / 超时）** | 提示服务暂时不可用且对话已保留，提供「立即重试」动作 | 同左 |

## 统一构建入口

无需新建 `Dockerfile.*` 或 `build-*.mjs`，直接通过统一入口追加构建：

```powershell
node deploy/build-chat-frontend.mjs <ISOLATED_SOURCE> <NEW_OUTPUT> --from-feature frontend-chat-error-guidance --extend frontend-chat-error-guidance
```

本地单元测试：

```powershell
node --test openwebui-profile-overlay/frontend-chat-error-guidance/test_chat_error_guidance.mjs
```
