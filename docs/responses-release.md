# 三个模型的原生 Responses 修复

## 行为与边界

批准的模型为 `gpt-6-luna`、`gpt-6.1-terra`、`gpt-6.1-sol`，共用现有
`https://api.pingxingxian.space/oauth-relay/v1` 连接。将该连接的 `api_type`
设为 `responses`，由 Open WebUI 原生入口转换消息、工具、结果及流式事件。
不在 New API 启用 Chat-to-Responses 转换，不改变现有计费保护。

`apply_responses.py` 只修改固定版本的原生转换函数，原函数不匹配时停止：

- 将 `reasoning_effort` 搬到 `reasoning.effort`，不强制关闭或降低推理。
- 若已经提供原生 `reasoning.effort`，以该值为准，保留其他 reasoning 字段。
- 未显式选择 `none` 时，保留模型推理默认值，移除不兼容的 `temperature`、
  `top_p`、`top_logprobs` 和对应 logprobs include；删除 Chat 专属的顶层 `logprobs`。
- 上述参数调整只针对三个精确模型 ID，其他模型沿用原生转换行为。

模型 ID、默认模型、各模型参数记录、登录、OAuth 用户身份、权限、余额、
计费和后台任务的模型归属均不修改。图片输入和工具回传继续使用原生转换。
无新生产依赖或数据库结构迁移。

## 部署

先提交源文件，再将该 Git 提交的归档放在服务器 `/data` 下的专属发布目录。
`python3 deploy/deploy-responses.py /data/.../scope.json` 复用已有的
`deploy-profile-sync.py` 与 `profile-deploy-utils.py`，仅部署 `chat` 阶段。

scope 顶层沿用 `source`、`receipts`、`chat`。`chat` 沿用当前容器、项目、服务、
Compose 文件列表、工作目录、env 文件路径、当前镜像引用与 ID、当前源文件摘要、
`source_root` 和 `interpreter`，另需：

- `new_image`：未使用的新镜像标签。
- `source_commit`：本次完整 Git 提交 ID。
- `config_backup`：目标容器挂载数据目录内的全新私有 JSON 路径，必须位于
  `/app/backend/data` 下；不得使用旧发布的快照路径。

`desired_source` 在部署所需的增量镜像构建期间生成，无需预先运行离线转换或测试。
镜像以已确认的当前运行镜像为基底，保留其全部既有补丁与前端产物；构建只应用
Responses 转换补丁，不执行语法检查、测试、lint 或前端重建。原生授权恢复补丁
是否上线，仍须按其独立发布记录确认，不能由此次增量发布推定。

仅重建目标 Chat 容器，不重启 New API、issuer、主站或旧 LibreChat。新镜像健康后，
`configure-responses-storage.py` 在授权维护进程中使用原生 Config 模型和相同存储类型，
条件更新 `openai.api_configs` 这一行，仅增加/修改目标连接的 `api_type`。
不读取管理员密码或令牌，不创建管理员，不导出配置内容。若有合法管理员会话，
也可单独使用 `configure-model-access.mjs` 的 `responses-only` 模式走原生管理 API。

## 读回与恢复

部署回执记录新镜像、提交、运行源文件摘要、三个模型、配置读回和备份路径。
快照为 mode 0600，父目录为 mode 0700，留在挂载数据目录，不进入 Git 或日志。
写入前检测同一配置行是否被并发修改，冲突即停止；不写其他配置行或用户记录。

镜像激活失败时沿用现有局部 Compose 恢复。配置激活失败时先用同一维护脚本恢复
配置，再按目标服务的条件更新恢复旧镜像。若配置已被其他维护操作改变，恢复拒绝
覆盖，并在回执中明确 `configuration_restored: false`。此时保留已健康的新镜像，
不继续自动切回旧镜像，由部署负责人先按私有快照确认配置状态。
旧镜像、Compose 私有备份、配置快照均保留，不恢复整库、不删除用户数据。

后续手工恢复连接时，将已提交的 `configure-responses-storage.py` 通过标准输入交给
目标容器的 `python - rollback <config_backup>`；确认输出 `restored: true` 后，
再通过同一局部 Compose 帮助器恢复本次备份中的 Chat 服务块并重建该服务。
不得把完整 Compose 备份覆盖回正在维护的文件。

本次用户明确要求不做离线验证。服务健康、运行源码与配置读回只能证明部署生效；
真实重新登录、刷新、工具调用、实际聊天及用户扣费仍是独立验收，不自动发起付费请求。

依据：[OpenAI 三个模型的接口兼容说明](https://developers.openai.com/api/docs/guides/latest-model#gpt-6-astra-update-api-and-model-parameters)。
