# 第一阶段：中英界面

固定 Open WebUI `v0.12.5`，接在完整 `personal-connections` 补丁后。保留原生全部语言、Open WebUI 品牌和既有页面布局；翻译只影响界面，不翻译对话内容、用户资料或模型 ID。

## 行为与边界

- 游客首页提供原生语言列表。选择保存在 Chat 原有 `locale` 本地存储中；查询参数 `lang` 与既有手选优先，首次匹配浏览器已有语言包，不匹配时仍使用原生 `DEFAULT_LOCALE` / 英文回退。不同站点不共享语言偏好。
- 中英词条覆盖自定义游客页、模型授权/恢复、统一登录缓存失败、个人模型目录失败、登录资料同步提示，以及个人连接新增文案。其他语种仍使用原语言包和英文回退。
- 登录、身份、额度、计费、状态码和抛错对象均不变；服务端原提示保持，浏览器只翻译已知文案。
- 共享身份桥错误页在 `identity-bridge/` 单独维护。首次中英识别取 `Accept-Language`，手选记为 host-only、HttpOnly、SameSite=Lax 的语言 Cookie；无跨站同步。语言链接只打开 `GET /auth/oidc/login-failure` 的固定展示页，仍返回 403，不重交审批码、不交换身份。重试目标为固定枚举，CSP 仍禁止脚本和外部资源。

## 准备与构建

使用已满足 `personal-connections/manifest.json` 的**独立可写源码副本**，不要传正在维护的旧候选或服务目录。复用对应锁的既有依赖，不安装依赖、不下载 Pyodide。

```powershell
# 正式入口先核对并应用本增量，再核对整套补丁和锁文件、构建并分离公开资源/私有映射。
node deploy/build-i18n-frontend.mjs ISOLATED_PERSONAL_SOURCE NEW_ARTIFACT
```

应用器支持只读检查：`python openwebui-profile-overlay/frontend-i18n/apply.py ISOLATED_PERSONAL_SOURCE --check`。清单拒绝重复应用、未知版本、输入漂移和半套补丁。

若准备已成功但构建失败，保留源码和失败产物；修复已明确的构建环境问题后，传新的输出目录并加 `--prepared`，只在整套最终清单匹配时重建，不重复应用补丁或清理旧产物。

`Dockerfile.frontend-i18n` 只能基于已核完整个人连接镜像；只复制 `frontend-build/` 与公开清单，不复制 `frontend-debug/`。身份桥镜像按已有 `identity-bridge/Dockerfile` 生成，不能启动第二个实例共写数据库。构建/测试不等于部署授权，本次不连接真实模型、数据库或服务器。

## 验证与恢复

定向检查：`python -m unittest discover -s openwebui-profile-overlay/frontend-i18n -p 'test_*.py'`；`node --test identity-bridge/test/error-page.test.mjs`；浏览器夹具 `node openwebui-profile-overlay/frontend-i18n/browser-smoke.mjs ARTIFACT/frontend-build NEW_EVIDENCE`。

Python 应用检查需将 `CHAT_I18N_BASELINE` 指向未应用本增量的个人连接源码；运行时检查需将 `CHAT_I18N_SOURCE` 指向已应用候选，再执行 `node --test openwebui-profile-overlay/frontend-i18n/test_runtime.mjs`。身份桥的 `test/i18n-handler.test.mjs` 使用现有锁定依赖运行，不接真实权威服务。

本地最终候选为 `D:\work\pingx\parallellines-chat\.build\frontend-i18n-20261117\release-v2`。完整生产构建通过，继承的上游 Svelte 警告未作为本次清理范围。3 项应用/清单测试、6 项错误页安全/语言测试、1 项真实身份桥 handler 测试和 1 项个人运行时测试通过；运行时验证 401/403/429/500 的英文提示及清理事件，不发送模型生成请求。

1440/390 宽已检查真实构建的中英游客页、首访英文浏览器识别、手选刷新、草稿保持、登录错误、身份桥切换/Cookie 记忆和英文个人连接表单/目录失败反馈；均无页面异常或横向溢出，截图已实际查看。证据位于同级 `browser-evidence-v2/`。这只证明本地夹具路径，没有执行 Docker 构建、真实跨站登录、真实供应商或计费、上传或部署。

没有数据库迁移或真实环境配置修改。未上线时直接保留旧候选；上线后的界面回退用原完整个人连接镜像，保留个人凭据加密层、数据库和密钥，不能退回不认识个人密文的 stock 上游。身份桥使用此前已核镜像恢复，语言 Cookie 可保留，不影响认证。实际登录、模型/计费和线上页面验收需要目标环境另行授权。
