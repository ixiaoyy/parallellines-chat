# 可选匿名流量入口

适用当前锁定 Open WebUI `v0.12.5`，上游提交 `f7ac7ee10962ddec0017f5c29b6740d9b9c5faca`。这是独立、默认关闭的部署覆盖层，不改既有前端构建/缓存配方、业务补丁、身份桥、品牌或聊天数据。

## 接线与性能边界

原生 `src/app.html` 已以 `defer` 引用 `/static/loader.js`；已核本地前端构建的 `build/static/loader.js` 为空。启动时原生 `config.py` 将 `build/static` 复制到后端静态目录。本覆盖层只填这一空槽；槽已有其他定制就失败，不覆盖或拼接。

仅正式 `chat.pingxingxian.space` 执行。等待浏览器空闲（`requestIdleCallback` 最长等待 2 秒；不支持时延后 1 秒）再异步加载主站 `/traffic-collector.v1.js`，设置 `data-site=chat` 和固定采集地址。加载使用 `no-referrer`，不读取登录、聊天路径、标题、文件或用户输入；一次失败不重试，不参与登录、模型或对话调用。

共享 collector 负责 SPA 规范路由、匿名标识、最多 20 条的小批量、30 秒/离开提交、请求时限与失败丢弃。此覆盖层不复制那套采集逻辑。Chat 原生分析页面及明细保持原样；模型计费仍以网关为准，不能把两边 Token 相加。

## 准备与显式启用（已收口至统一构建入口）

先交付主站 collector 与公开写入接口，确认其隐私、批量和失败边界；再检查目标代理的 CSP（内容安全策略）允许精确主站脚本及采集地址。不能全局放宽 CSP。这里未连接服务器，也未确认线上代理或镜像。

依据全站防臃肿红线（停止复制散装 `Dockerfile.*`），本能力已收口至统一前端构建入口 [build-chat-frontend.mjs](../../deploy/build-chat-frontend.mjs) 与 [Dockerfile.chat-release](../Dockerfile.chat-release)，不再维护单独的 `Dockerfile.traffic-analytics`：

1. 构建公开前端产物时显式传入 `--traffic-analytics`（默认关闭，未传入时 `static/loader.js` 保持原生空槽且 `frontend-build-manifest.json` 结构不变）：

```sh
node deploy/build-chat-frontend.mjs <SOURCE> <OUTPUT> \
  --extend frontend-chat-error-guidance,frontend-model-capabilities \
  --traffic-analytics
```

2. 校验产物时同步传 `--traffic-analytics`，使 `frontend-build-manifest.json` 完整覆盖已写入的 `static/loader.js` 与 `traffic_loader_sha256`：

```sh
node deploy/build-chat-frontend.mjs --verify-public <OUTPUT> --traffic-analytics
```

3. 使用唯一统一镜像入口 `openwebui-profile-overlay/Dockerfile.chat-release` 从当前已审核完整 Chat 镜像派生，保留个人连接、统一登录、授权恢复与 Pyodide：

```sh
docker build -f openwebui-profile-overlay/Dockerfile.chat-release \
  --build-arg CHAT_BASE_IMAGE=<reviewed-current-image@sha256:digest> \
  -t <new-reviewed-chat-release-image> <OUTPUT>
```

发布时将 `CHAT_TRAFFIC_IMAGE` 指向审核后的统一派生镜像，仅向现有 Compose 项目合入 `deploy/traffic-analytics-chat.overlay.yaml`；不得替换真实密钥或数据卷。关闭可恢复启用前镜像，或在不带 `--traffic-analytics` 参数下重新打包；`apply.py` 仅保留作离线槽位单测与紧急清理参考工具。主站接收开关关闭也应停止接受流量。

## 本地定向验证

```sh
python -m unittest discover -s openwebui-profile-overlay/traffic-analytics -p test_apply.py
node --test openwebui-profile-overlay/traffic-analytics/test_loader.mjs
```

这些检查覆盖空槽、开关、重复安装、保留他人定制、固定域名、延迟加载与无私有数据读取；不证明目标镜像构建、CSP、真实脚本加载或服务器采集已生效。获得目标发布授权后，还须验证原 Chat 登录/对话和采集失败时的正常使用。

2026-11-17 联合消费核对：从真实 `.build/frontend-i18n-20261117/release-v2/frontend-build` 仅隔离复制 `index.html` 与空 `static/loader.js` 后，既有安装器启用、关闭均成功，原中英产物未修改。中英的 `frontend-build-manifest.json` 记录的是覆盖前的空 loader；派生后应保留为中英输入回执，不能声称它逐文件描述已开启采集的最终镜像。当前没有读取该回执验证运行镜像的消费者；若后续增加最终文件完整性校验，应另记采集派生层及唯一 loader 覆盖，不能改写原回执或放宽校验。这次没有执行 Docker 构建。
