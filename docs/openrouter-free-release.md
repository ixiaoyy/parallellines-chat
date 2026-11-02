# Chat 的 OpenRouter 免费入口：本地交付与发布边界

本文记录本地交付和授权发布合同。**实际镜像、部署、配置写入及用户验收状态以当次私有发布回执为准**，
本地模拟检查不能证明已经上线或普通用户登录验收通过。
现有 `gpt-6-luna`、`gpt-6.1-terra`、`gpt-6.1-sol`、默认模型及其计费规则保持不动。
不新增 Chat 共享 API Key，不接入被上游限制的 OpenCode 免费模型。

## 数据流与实施范围

沿用 `Chat → https://api.pingxingxian.space/oauth-relay/v1 → OpenRouter`：

- 新模型精确 ID 为 `openrouter/free`，仍用当前用户的 `system_oauth` 和现有 Responses 连接。
  密钥只在 AI 网关；不切换三个付费模型的接口协议，不增加第二条共享密钥连接。
- 固定 Open WebUI `v0.12.5`，提交 `f7ac7ee10962ddec0017f5c29b6740d9b9c5faca`。
  `apply_openrouter_free.py` 只在五个后端文件中的六个已核对原生函数内追加免费分支。
  逐函数原文不匹配、重复应用或标记变化均拒绝；其他身份、Responses、前端及性能补丁保留。
- `get_task_model_id` 先检查当前会话的原始精确模型 ID。免费会话的标题、标签、后续问题、
  查询、自动补全、表情和工具前置任务继续选 `openrouter/free`，不继承全局
  `TASK_MODEL_EXTERNAL=gpt-6-luna`。付费会话仍执行原选择规则。
- 任务经过管线后若从免费模型改成其他模型，发送前返回 `free_task_model_changed`，
  不替换模型后继续发送，也不降级到付费模型。MOA 同样检查；不会开启图像生成等既有关停功能。
- 主聊天以原生入口保存在 `request.state.model_id` 的原始模型为准。主管线/inlet 先改模后触发
  搜索、检索等任务时，发送分发仍拒绝付费模型；直接任务/原生入口没有该来源时只捕获当前入站模型。
  最终 OpenAI 路由在别名、参数和前缀处理后再次核对免费模型及可信 `system_oauth` Responses
  连接，不把免费请求改投 arena、直接连接或其他后端。此限制不建立粘性全局标记，不改变付费父请求。
- 自动和手动长会话压缩共用的摘要函数对原始 `openrouter/free` 也使用免费模型，不继承独立的
  `chat.context_compaction.model` 付费默认值。其他会话仍沿用原压缩模型和参数。
- 普通用户可见授权通过独立原生配置工具增加。旧的 `configure-model-access.mjs` 不改，
  不重写默认模型、全局后台任务配置、角色、用户余额或三个付费模型的记录。

依赖 New API 的 **`v0.13.2-pkce.8` 免费 Chat 增量**，以 pkce.7 账号同步配方为构建基线。
部署前核对服务器当前实际版本，不从本地配方推断线上状态。
必须同时遵守该版本的身份前置和原有开关，不能以本次免费模型接入自动启用账号同步。
网关独立开关 `CHAT_OPENROUTER_FREE_ENABLED` 默认关闭；只有其模型权限、零价、渠道和最终
请求检查都通过时，才允许该免费 Chat 请求免余额预扣。Chat 本身不绕过任何网关权限或余额检查。
不关闭全局免费预扣开关。按本次明确批准的边界，仅已核验 Chat 的 `openrouter/free` 免附加违规费；
违规请求仍拒绝并记录日志。普通 API、原三个模型和其他模型的违规费用/保护保持原样，不能顺手
关闭全局收费策略。

## 本地检查

本批检查仅执行固定源码函数及模拟 HTTP/管理员 API，不导入应用启动、不连接数据库或真实模型。

```powershell
cd openwebui-profile-overlay
python -m unittest test_openrouter_free -v
cd ..
node --test deploy/configure-openrouter-free.test.mjs
python -m unittest discover -s deploy -p test_configure_openrouter_free_storage.py -v
```

Python 默认读取已有 `.build/performance-build/open-webui-f7ac7ee10962ddec0017f5c29b6740d9b9c5faca/backend/open_webui`。
其他准备位置可通过非敏感路径变量 `OPENWEBUI_SOURCE_ROOT` 指定。缺少固定源码时会明确跳过，
不能把跳过当成功；测试不会自动下载源码或安装依赖。

覆盖：所有任务端点选择免费模型、主管线/inlet 改模后触发查询仍拒绝付费、任务管线改写拒绝、
原生别名/连接后改模拒绝、免费自动/手动压缩及原付费/本地模型选择不变；原生 Responses
转换、非流式 HTTP 接线、SSE 透传和清理；原生权限/过滤配置的幂等、保留、并发变化及失败脱敏。
这些检查不代表实际 Chat 登录、真实上游 Responses、流式长连接或零余额线上验收已通过。

## 获得发布授权后的准备顺序

以下步骤仅在当次明确授权的目标、提交和维护范围内执行；工具本身不自动部署。

1. 按 New API pkce.8 的完整配方准备并验证前置版本，保持免费 Chat 开关关闭。
   保留原镜像、服务定义及私有配置恢复点，不恢复旧整库、不重发或恢复已撤销内部凭据。
2. 以已经确认的当前 Chat 镜像（含现有身份、Responses 和前端补丁）增量构建：

   ```sh
   docker build --build-arg CHAT_BASE_IMAGE="$APPROVED_CURRENT_CHAT_IMAGE" \
     -f openwebui-profile-overlay/Dockerfile.openrouter-free \
     -t "$NEW_UNUSED_CHAT_IMAGE" openwebui-profile-overlay
   ```

   镜像引用和摘要来自当次私有部署回执，不用未核对的官方原始镜像覆盖现有补丁。
   构建只应用后端补丁，不重建前端。记录输出的五个 `PL_FREE_TASK_SOURCE_SHA256`。
3. 复用已有局部 Compose 更新/恢复方式，只替换 Chat 服务镜像，保留全部挂载、环境和其他服务。
   先核对服务健康、运行源码及上述五个文件，不能用健康检查替代真实聊天验收。

   `deploy/deploy-openrouter-free.py SCOPE.json` 是复用已有发布器的薄入口：scope 沿用原
   `Release` 的 `source/receipts/chat` 结构，另要求 `chat.source_commit`。其中
   `expected_source` 必须从当前运行镜像的五个实际文件取得，不能套用纯上游副本的摘要；
   本次构建从 RUN 输出取得 `desired_source`。它只更新目标 Chat 的镜像及构建来源，
   不调用旧发布器会修改全套 `CHAT_ENV` 的默认实现，不修改网关开关或模型授权。
4. 优先使用下述已授权服务器维护入口；存在正常管理员会话时也可使用后面的原生管理 API 工具。

   **服务器维护入口：**`deploy/configure-openrouter-free-storage.py` 复用既有原生 Config、Models、ACL
   和运行环境会话，不创建独立数据库连接配置，不读取/生成管理员密码、令牌或新管理员。
   不导入应用启动模块，加载原生模型前强制关闭本维护进程迁移；不执行 DDL 或启动 migrations。
   它只检查目标可信连接的空 key，其他连接配置不输出或改写，也不提取其密钥供其他用途。

   默认或显式 `plan` 只读：

   ```sh
   docker exec -i "$CHAT_CONTAINER" python - plan < deploy/configure-openrouter-free-storage.py
   ```

   仅在已授权应用配置后执行：

   ```sh
   docker exec -i "$CHAT_CONTAINER" python - apply \
     /app/backend/data/private-free-model/before.json --owner-user-id "$EXISTING_CHAT_ADMIN_ID" \
     < deploy/configure-openrouter-free-storage.py
   ```

   容器、数据挂载和既有管理员 ID 从当次核对结果指定；如目标模型已存在，可省略 owner 参数。
   只有模型记录缺失时才核该显式用户的 ID/角色并复用原生 Models 创建最小记录；不创建用户或提权。
   操作者须预先在既有挂载数据目录准备 `0700` 的私有备份目录；恢复点排他创建为 `0600`，不覆盖旧文件。
   写入只添加一条登录用户 `read` ACL（保留旧 grant ID），必要时 CAS 更新一个 Config 过滤项。
   私有小日志记录实际新增 grant ID、前后过滤项及完成步骤，不含管理员凭据或完整连接配置。
   每步写前重读；失败输出 `partial_or_uncertain` 和已完成步骤，不自动重试或覆盖并发变更。

   独立维护进程不能清除运行中服务的进程内缓存。为保持已有缓存合同，脚本要求持久
   `models.base_models_cache=false`，且 `MODELS_CACHE_TTL` 为有限正数；不符合即停止，不修改全局设置。
   维护完成后等待返回的 `cache_ttl_seconds`，再经正常用户入口刷新目录并验收；不声称即时推送缓存刷新。

   **原生管理 API 入口：**在已有正常 Chat 管理员会话下准备私有标准输入文件，运行 Node 工具的只读计划，
   再经确认执行写入。它与服务器维护脚本的备份格式不同，不能互换。
   管理员凭据不放命令行、环境、源码、聊天或日志；不读取/生成新凭据来绕过正常登录。

   ```sh
   node deploy/configure-openrouter-free.mjs < /data/private/free-model-plan.json
   ```

   输入字段：`adminBearer` 为现有正常管理员会话；`apply` 默认为 `false`。
   要写入时明确设置 `apply: true` 和全新 `backupFile: /data/.../before-openrouter-free.json`。
   目录应由操作者预先建立并限制为 `0700`；备份排他创建为 `0600`，不覆盖旧文件。
   工具只访问四个固定原生配置端点，不发模型请求：
   `GET /openai/config`、`POST /openai/config/update`、
   `GET /api/v1/models/model?id=openrouter%2Ffree`、`POST /api/v1/models/model/access/update`。

   - 可信连接必须已经启用、`system_oauth`、`responses`、空 API Key、空自定义头、不转发 Cookie。
   - 空 `model_ids` 保持动态目录；已有显式列表才在末尾追加免费模型，保留原项及其他连接。
   - 原生更新会丢弃非有效数字索引的配置并归一化密钥数组。本工具在任何写入前拒绝 URL-key、
     越界/非规范索引或数量不匹配的密钥数组，不自动迁移这些旧配置；如遇到应先人工核对。
   - 模型只增加所有已登录用户的 `read` 授权，保留已有私有授权与模型名称、参数、元数据。
     不给匿名用户授权、不增加写权限。已有别名、禁用记录或全体用户写权限会使计划失败。
   - 新增的最小模型记录名称为 `OpenRouter 免费`。没有实际网关模型时，单独授权记录不会产生模型能力。
5. 完成必要配置和静态核对后，才按网关配方开启 `CHAT_OPENROUTER_FREE_ENABLED`。
   真实验收只调用免费模型，核对普通用户列表、现有凭据、零余额、非流式/流式实际回答与后台任务模型。
   观察原三个付费模型的配置/协议保持原样，不用付费请求做本次验收。

## 恢复与失败处理

本次没有数据库结构迁移。配置工具备份只含本次模型的前后权限及连接过滤项，不含令牌或任何
连接密钥；发生部分写入时打印已完成步骤和 `partialOrUncertain`，不会伪装成功或自动重试写入。
服务器维护入口对应字段为 `partial_or_uncertain`；已有原生 Models 为新模型生成的版本记录
属于正常数据写入，不属于 DDL。

1. **先关闭网关的免费 Chat 开关，停止接收新免费会话请求并等待在途请求结束**，不影响原三模型
   或普通 API 的 OpenRouter 渠道。旧 Chat 镜像没有免费后台任务隔离，不能先回退 Chat。
2. 若本次使用 Node 管理 API 工具，用正常管理员入口读取模型权限和目标连接过滤项，与私有备份的 `expectedAfter*` 核对。
   权限比较使用 `principal_type/principal_id/permission`，不依赖服务器重新生成的授权记录 ID。
   若存在其他管理员改动，停止恢复，不能把旧完整配置覆盖回去。
   若本次通过服务器维护入口应用，改用同一份原生维护脚本及其私有日志：

   ```sh
   docker exec -i "$CHAT_CONTAINER" python - rollback \
     /app/backend/data/private-free-model/before.json < deploy/configure-openrouter-free-storage.py
   ```

   脚本先检查原模型身份、授权记录及本次实际新增 grant ID、目标过滤项；只有匹配才撤回本次差异。
   Config 恢复使用当前行 CAS，保留其他连接当前字段；旧 ACL 不重新生成、不删除模型记录。
   发生并发变化或提交/日志间不确定状态时停止，保留数据和日志交由发布负责人核对，不盲目重试。
3. Node 管理 API 路径确认无冲突后，经原生模型权限接口恢复 `beforeAccessGrants`；只对目标连接恢复
   `beforeModelIds`（原来无该字段则删除本次新增字段），其他当前配置原样保留，再读回。
   本次新建的最小模型记录可保留为空权限；不要为清理它而删除既有模型或用户数据。
4. 最后按现有局部 Compose 恢复方式切回本次之前的已确认 Chat 镜像，保留数据和其他服务。
   不回退 New API 到缺少内部凭据/计费保护的旧版本，不恢复整库、余额或身份快照。

OpenRouter 免费池共享上游账号限额，自动选择的模型会变化。接口限流、免费模型撤下或上游失败
必须明确返回错误，不能自动换成付费模型。新 Chat 与上游真实 Responses 链路仍需发布授权后验收。
