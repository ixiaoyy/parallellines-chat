# 个人 API 连接：Cherry 交互参考增量

## 范围与状态

固定 Open WebUI `v0.12.5` / `f7ac7ee10962ddec0017f5c29b6740d9b9c5faca`，接续 performance → bootstrap → auth-recovery → silent-recovery → unified-login。后端兼容现有免费模型隔离补丁。

本目录说明补丁合同与验证，不构成线上发布回执。发布须使用另行批准的 Chat 专用运维入口，保留现有服务定义、配置、数据和密钥；本地测试不连接真实数据库或模型供应商，不安装新依赖。

交互参考 [Cherry Studio 服务商设置](https://github.com/CherryHQ/cherry-studio/blob/9a55e8ecc0bd451b524f605ad088e941eb1500e2/docs/website/content/pre-basic/providers/zi-ding-yi-fu-wu-shang.md) 及该快照的官方截图：按连接名称 → Key → API 地址 → 模型列表/手填 ID 配置，保留 Open WebUI 原生页面、弹窗、组件、品牌和配色；不复制 Cherry 代码或新建另一套设置界面。

## 行为与保持项

- 正常登录用户可在原生个人外部连接中添加多个连接，每个连接一个 Key；平台模型和个人模型同时保留。
- 新个人连接只支持 HTTPS、OpenAI 兼容 `/chat/completions` 与 Bearer Key。界面显示实际 `/models` 和聊天路径；不自动补版本、不猜协议。
- 获取模型列表只发 GET，不发生成测试。成功、空列表、失败有独立反馈，读取可取消、最多 15 秒；失败仍可手填模型 ID。启动时第三方自动发现放在后台，不阻塞原平台/身份的 12 秒启动组；旧账号/Key 的迟到结果不回填。
- 新连接以 `pl-personal-<32 位小写 hex>` 作为稳定内部前缀，名称可编辑。目录显示 `连接名称 / 模型`，真实上游模型 ID 不变，不能覆盖同名平台模型。原生回复 `modelName` 保存当时显示来源。
- 编辑旧连接保持既有前缀（含空）、认证类型、空 Key 和旧地址合同，不静默改历史模型 ID。旧配置不会自动转为本次命名空间。
- 保存失败保留表单与候选；成功保存后的目录刷新失败单独提示，不诱发重复添加。整组连接写入使用单 pending 门禁，防止多连接并发保存覆盖配置。删除/禁用连接或更换账号、地址、Key 会终止受影响的个人请求，不切其他 Key 或平台模型。
- 原生停止生成事件按聊天/消息 ID 取消对应个人上游请求，即使用户已切换聊天也生效，不停止其他会话。
- 平台模型仍走原有 `system_oauth → New API → 该用户原生计费`；平台付费/免费默认和权限不变。平台模型授权缺失可单独恢复，不阻断已配置的个人目录；真实 Chat 401 和其他启动失败不放宽。
- 个人 direct 会话的标题、标签、后续问题、查询和自动摘要不继承平台任务默认模型；管线改投其他模型时明确拒绝。独立历史手动压缩缺浏览器上下文，本期明确关闭，不偷用平台摘要模型。
- 浏览器连接最多等待 30 秒，收到响应后最多 10 分钟；服务端新个人流队列空闲最多 90 秒、总计最多 660 秒。断流/无结束标记明确失败并清理队列，不重试生成请求。

不新增多 Key 轮询、自动服务商降级、收费试聊、个人 Anthropic/Gemini/Responses、服务器任意地址代理、用量管理页或助手预设系统。

## 已确认的凭据保存方式

用户选择随账号跨设备同步、服务端加密保存：

- 复用既有 `user.settings.ui.directConnections.OPENAI_API_KEYS` 数组，不新增表或字段、没有迁移。
- 仅显式保存个人连接时，Key 用版本化 Fernet 密文存储，沿原生 `WEBUI_SECRET_KEY + HKDF` 方式派生个人连接专属域；密文绑定用户 ID。
- 当前用户的专用设置 GET（raw/merged）和 POST 响应才解密，供浏览器原生直连使用。普通用户对象、状态/列表和事件序列化去掉 Key/密文；不把完整 Key 放入模型目录或聊天记录。
- 两个专用设置入口不采集请求/响应正文审计，避免明文 Key 或截断正文进入日志；现有审计级别、元数据、身份和响应状态保持不变。
- 浏览器只在内存持有读取到的 Key，本增量不写 localStorage。对话仍进入 Chat 后端处理和保存，不能宣传成本机独占聊天。
- 旧明文设置只读兼容；用户显式保存连接时升级。没有自动批量转换、清理或读取真实旧数据。普通偏好修改保持原密文字节，不重新加密。
- 密文损坏、加密配置缺失或跨账号复制明确失败；不降级为明文或空 Key。新建连接拒绝 URL 内的账号密码、查询参数和片段；不提供额外鉴权请求头入口。

**运维前提**：必须保留稳定的 `WEBUI_SECRET_KEY`。数据库备份与对应密钥均是恢复依赖，应分别受限保存；更换/丢失密钥会导致旧个人凭据无法解密，不会自动删除或重置。此增量不修改真实环境文件或密钥。

## 文件职责

| 文件 | 用途 |
| --- | --- |
| `apply.py`、`manifest.json` | 固定前后清单，全部核对通过后统一写入；拒绝漂移/重复应用 |
| `frontend.py`、`personal-connection-ui.mjs` | 原生三个组件的个人连接流程和中文文案 |
| `runtime.py`、`personal-connections-runtime.mjs` | 模型来源、目录失败边界、稳定连接解析、有界 Socket 请求 |
| `backend.py` | 任务选择、发送前费用保护、会话归属和流式队列清理 |
| `credentials.py`、`main_personal_connections.py` | 用户设置落库加密及专用设置读取、普通用户返回脱敏 |
| `audit.py` | 在正文截断/采集前保护专用设置接口，保留其他原生审计路径 |
| `../../deploy/build-personal-connections-frontend.mjs` | 复用已有依赖构建并分离公开资源/私有 source map |

## 应用与构建

在既有统一登录完整源码的独立副本上操作；保留其他任务的展开源、配置、依赖和旧候选。SOURCE 必须是尚未应用本增量且满足清单的源码根。

```powershell
python openwebui-profile-overlay/personal-connections/apply.py SOURCE --check
python openwebui-profile-overlay/personal-connections/apply.py SOURCE
node deploy/build-personal-connections-frontend.mjs SOURCE NEW_ARTIFACT
```

不安装依赖或下载 Pyodide。公开产物在 `frontend-build/`，私有 source map 在 `frontend-debug/`，后者不能上传或进入镜像。

`Dockerfile.personal-connections` 只能使用满足前置清单的统一登录完整基础镜像，保留它的启动参数、后端依赖和 Pyodide。其 backend-only 应用不会写数据库；本次未执行 Docker 构建。

## 验证与后续验收

已完成的隔离检查覆盖原生任务/路由函数、真实 Fernet/Pydantic、表单辅助逻辑、组件编译、统一清单应用、模型目录及保存接线。完整生产前端构建通过，仍有继承的上游 Svelte 自闭合标签/可访问性等警告，未作为本次无关清理范围。

最终浏览器夹具在 1440/390 宽检查原生设置、添加、模型读取成功/空列表/失败、手填模型 ID、保存失败重试及第二设备设置重载；另覆盖编辑保留 ID、Key 显隐、启停、模型来源选择器和删除。两种宽度均无页面异常或横向溢出，已实际检查最终截图。夹具阻止外联，不含真实 Key。

发布审查修复了停止生成未取消上游、审计正文泄漏 Key、并发保存覆盖、慢个人目录阻断启动四项问题，并检查后台发现的个人模型保留 `direct:true`。相关定向回归通过；旧输入的有效证据保留，不为提交重复全量测试。

最终候选：`D:\work\pingx\parallellines-chat\.build\personal-connections-review-20261117\release-final`；截图与结果：同级 `browser-evidence/`。前一候选及失败选择器诊断仍保留，不能用它们替代最终候选证据。

定向检查入口：

```powershell
# 测试使用未应用本增量的固定源码作为原生基线，不指向运行数据库。
$env:PERSONAL_CONNECTIONS_SOURCE = '明确的基线源码绝对路径'
python -m unittest discover -s openwebui-profile-overlay/personal-connections -p 'test_*.py'
node --test openwebui-profile-overlay/personal-connections/test_frontend.mjs openwebui-profile-overlay/personal-connections/test_runtime.mjs
# 接线测试读取已应用的候选源，需单独设置该路径。
$env:PERSONAL_CONNECTIONS_SOURCE = '明确的候选源码绝对路径'
node --test openwebui-profile-overlay/personal-connections/test_integration.mjs
node --test openwebui-profile-overlay/personal-connections/test_review_runtime.mjs
node openwebui-profile-overlay/personal-connections/browser-smoke.mjs ARTIFACT/frontend-build NEW_EVIDENCE
```

这些不等于真实供应商、完整 FastAPI/数据库/Socket 服务或计费验收。目标环境另获授权后，需核对基础镜像、稳定密钥、原生 `ENABLE_DIRECT_CONNECTIONS` 和用户设置权限，验证真实账号的加密落库、第二设备读取、退出/禁用取消、平台零余额与个人 Key 独立使用。生成验证可能收费，必须另行明确授权；不将模板或开关状态当作运行事实。

## 恢复

没有配置变更、数据迁移或批量写入需要本次撤回。尚未启用前可保留原镜像和候选。

一旦用户保存了 `plpc:v1:` 密文，不能直接退回不认识该格式的旧凭据读取代码。故障时保留加解密层并关闭原生个人连接入口，或向前修复；不要回灌旧数据库丢弃后续写入，也不要删除用户凭据。若必须使用旧版，需单独确认凭据恢复和数据处理方案。
