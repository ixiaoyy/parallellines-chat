# Chat 侧栏 · 聊天频道

已登录的 Chat 在左侧导航显示 `聊天频道`（英文 `Chat Channels`）。桌面收起侧栏时使用同一入口的图标和提示，手机打开侧栏后可见。链接在新标签页打开 `https://tavern.pingxingxian.space/`，当前对话和草稿保持原状。链接不携带 Chat 凭据或页面来源；目标站自己接续主站登录，不调用 Chat 的登录或模型接口。访客壳、主站导航和 Open WebUI 原生频道不变。

## 构建和切换

固定 Open WebUI `v0.12.5`。在符合个人连接清单的隔离源码副本上，沿用现有锁定依赖与前端构建入口：

```powershell
node deploy/build-i18n-frontend.mjs ISOLATED_PERSONAL_SOURCE NEW_ARTIFACT --chat-rooms
```

该选项先应用已核中英界面，再追加两个侧栏接点和中英文案。省略选项时，原中英构建和既有候选不变。若组合补丁已完整应用、只需重试构建，传新输出目录，并加 `--chat-rooms --prepared`。整套前后像与清单不一致时停止，不覆盖旧候选或已经准备好的产物。

获准交付时，准备以下公开镜像上下文；只取本次构建的公开目录和清单，私有 source map 目录 `frontend-debug/` 留在构建端：

```text
.dockerignore                   # 构建入口产出
Dockerfile.frontend-i18n        # 从同一已审 Chat 源码复制
frontend-build-manifest.json    # 记录本次完整增量及公开文件
frontend-build/
```

主站娱乐运维工作流的 `publish-tavern-frontend` 只接受已登记的构建产物和已核 Tavern 单点登录验收。它通过 `deploy/tavern-release-inputs.py` 验证构建来源、文件清单和字节；从当前 Chat 镜像派生，仅替换前端资源，不替换后端、Pyodide、数据或授权配置。

`rollback-tavern-frontend` 只恢复已保存的 Chat 前一镜像与服务定义，保留运行数据及原有恢复、个人连接和其他功能，不回退 Tavern 或身份桥。接线参数、服务器目录和回退顺序见 [Tavern 固定运维动作](../../docs/tavern-sso-release.md#固定动作的输入)。

本地浏览器夹具可验证两种侧栏、跳转和草稿；真实主站登录、设备验证及生产入口生效仍需获准发布后验收。
