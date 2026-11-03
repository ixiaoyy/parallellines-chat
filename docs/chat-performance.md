# Chat 首页、弱网登录与同机调用优化

## 本次范围

公开地址仍是 `https://chat.pingxingxian.space`。三个模型、模型价格、用户身份、
JWT 有效期、OAuth issuer/回调、服务端鉴权和计费逻辑均不修改。
没有新增生产依赖、数据库字段或数据迁移，不创建账号或发起模型请求。

### CDN 与浏览器缓存

`deploy/nginx-chat.conf` 仅为 `/_app/immutable/` 的带哈希资源提供一年
`public / immutable` 缓存。该位置不向源站转发 Cookie/Authorization，也不返回
Set-Cookie；错误状态不会获得这条长期缓存头。其他页面、登录、接口和流式对话
明确 `no-store`，保留原有不缓冲的流式转发。

`deploy/deploy-chat-cdn.py` 只修改已存在的 Chat 标记区段，锁定并保留被 bind mount
的配置 inode；其他域名保持原字节。私有区段快照留在 `/data`，检查 Nginx 配置后
热重载，失败只恢复本次区段，不重建应用或覆盖其他虚拟主机。

复用已代理的 Cloudflare，不更换 DNS/CDN 服务或启用付费功能。Cloudflare 的默认
规则可直接使用源站缓存头；若账号另有覆盖规则，必须另行核对。旧边缘条目在原 TTL
到期后使用新策略；需要立即全量清理时，应使用有权限的 Cloudflare 管理入口，
不扫描或读取服务器凭据。依据：[Cloudflare 默认缓存行为](https://developers.cloudflare.com/cache/concepts/default-cache-behavior/)。

### 容器之间走内网，公开身份保持不变

排查确认，原来的 Chat → New API/身份桥、身份桥 → 主站 API 仍将域名解析到
Cloudflare 公网地址，只有 Nginx → 应用是 Docker 内网。
同机部署现在可选择 `host-gateway` 解析：

| 容器 | 仅在容器内映射的域名 |
| --- | --- |
| Open WebUI | `pingxingxian.space`、`api.pingxingxian.space` |
| New API | `pingxingxian.space` |
| 身份桥 | `pingxingxian.space` |

经宿主机发布的 443 端口进入原可信 Nginx。URL、HTTPS 主机名、SNI、证书校验、
Host、issuer、受众和回调都不变，不改成明文 HTTP，不绕过用户授权或改用共享 API key。
浏览器和外部客户端的公共 DNS/CDN 行为不变；OpenAI 上游仍按原有外部请求路径访问。

`internal-routing.*.overlay.yaml` 是可选同机配置，不得用于 Nginx 位于其他主机的部署。
应用前必须确认宿主机网关、证书、目标公开路径以及现有 proxy 环境；若设置了出站
HTTP 代理，需要先确认它不会绕过这些内网解析。本次目标容器均未设置出站代理。

### 按需加载

`frontend-performance` 源码增量把格式化复制需要的完整高亮、Markdown 扩展和数学
格式依赖移到动态模块；普通复制和已提供 HTML 的复制不触发该模块下载。
保持原来的语言覆盖、复制样式、纯文本回退以及布尔返回值。
`ClipboardItem` 接收 HTML Blob Promise，并在用户手势内立即调用 clipboard.write，
不先等待模块下载再申请手机剪贴板权限。
依据：[WebKit ClipboardItem Promise 与用户手势规则](https://webkit.org/blog/10855/async-clipboard-api/)。

本次正常构建的首屏 JS/CSS 请求仍是 56 个；原始字节从 1,286,173 降为 1,246,722，
按同一 gzip 方法计算从 348,594 降为 336,656 字节，约减少 12 KB（3.4%）。
这是小幅减重，不能当作手机首页总耗时已同比改善；主要缓存收益是重复访问避免重下
这些文件。生产清单中格式化复制模块为 dynamic entry，不在首屏资源清单。

### 弱网不误退出

旧 `getSessionUser` 的调用合同保留：现有非首页调用方仍按原来的 null/detail 规则处理。
首页使用独立的严格恢复入口，最多等待 12 秒，并区分 HTTP 401 与临时网络/服务错误。
真实 401 仍清除无效登录信息；临时失败保留本机令牌，显示重试，不渲染未经服务端
确认的账号页面，也不放宽任何后端鉴权。真实过期、撤销或主站授权到期仍必须重新登录。

## 构建与分阶段部署

1. 取固定上游提交 `f7ac7ee10962ddec0017f5c29b6740d9b9c5faca`，保留锁文件与许可证。
2. 对新源码目录运行 `frontend-performance/apply.py`，使用已记录的前后文件清单。
   不自动叠加尚未上线的授权提示补丁，不覆盖其他工作。
3. 在准备好的源码目录按现有锁文件安装构建依赖；下载缓存放在源码目录外，跨准备目录复用：

   ```sh
   npm ci --force --ignore-scripts --prefer-offline --cache "$HOME/.cache/parallellines-chat/npm" --no-audit --no-fund
   ```

   `npm ci` 仍以原锁文件校验和安装，不把另一版本的 `node_modules` 当作有效命中；不改锁文件。
   已有匹配的准备目录可直接复用依赖；然后从本仓库执行
   `node deploy/build-performance-frontend.mjs PREPARED_SOURCE NEW_OUTPUT`。
   它执行交付必需的正常生产构建，不跑测试、lint 或独立验证构建，不重下 Pyodide。
   可通过 `FRONTEND_BUILD_HEAP_MB` 显式限制堆内存；前次构建 3 GB 上限不足，调整至 6 GB 后构建成功。
4. 获得当次提交、推送和发布授权后，只上传已审核源码归档、`frontend-build/`、
   `frontend-build-manifest.json`、Dockerfile 及 `.dockerignore`；保留旧镜像。
   私有 `frontend-debug/` 不上传、不作为镜像内容或公开静态资源。
5. 本次纯前端发布使用 `deploy/deploy-frontend-performance.py scope.json`。
   它复用已有目标服务 CAS、私有备份、就绪检查和恢复，只替换 Chat 的 `image/build`，
   不合并 `extra_hosts`、环境、身份、免费模型开关或其他原生配置。
   `frontend_context` 仅含公开目录、公开清单、`.dockerignore` 与已提交的 `Dockerfile.performance`；
   从实际 live 镜像 ID 建立唯一基线别名，叠加前端后验证完整清单与编译映射移除。
   `source_root=/app`、`interpreter=python`；`expected_source` 保留实际旧首页及后端/Pyodide 摘要，
   激活后仅更新首页预期值，其他基线继续核对。先从正常只读 preflight 取得实际服务与版本，不套旧值。
6. 仅在另行批准内网解析阶段时，才使用原 `deploy-performance.py scope.json bridge|newapi|chat`。
   该旧入口会合并解析配置，不能无条件代替上述纯前端发布器。
   scope 顶层沿用现有 `source`、`receipts`，各阶段沿用当前项目、Compose 文件列表、
   env 文件路径、工作目录、服务、容器、镜像和目标服务文件。
   `routing_hosts` 为批准域名到免费检查路径的映射，`expected_private_address` 必须来自实际宿主机网关。
   网络阶段使用原镜像；Chat 阶段另需 `frontend_context`、`frontend_manifest_sha256`、
   当前 `build/index.html` 摘要与新的镜像标签。

每阶段复用已有服务级条件更新、私有备份、健康检查和恢复，失败即停止后续阶段。
只有本阶段目标服务未被其他维护改动时才恢复；不覆盖其他服务，不回灌数据库，
不改环境中的账号凭据。成功阶段之间向后兼容，不因后续失败盲目重启已经成功的组件。
Chat 增量镜像保留当前后端补丁及 Pyodide，并逐文件读回整个前端清单，包含懒加载资源。

### 私有调试产物与 Docker 缓存

两个前端构建入口均用 Vite 的隐藏 source map：映射仍生成，但公开 JS 不声明
`sourceMappingURL`。共用打包器将 `.map` 放入 `frontend-debug/`，普通资源按原字节写入
`frontend-build/`；公开清单只列公开文件，私有 `source-map-manifest.json` 保存映射摘要、
上游/补丁来源及对应公开清单摘要。两种产物一起保留供排障，已有产物拒绝覆盖。
私有目录使用受限文件权限；Windows 上仍须放在仅当前用户可访问的目录，不放入共享文件夹。

生成的性能构建上下文与 overlay 的 `.dockerignore` 都排除 `frontend-debug/`。
镜像只复制公开目录，并删除继承基线中 `/_app/` 下的旧 `.map`，保留旧哈希 JS/CSS 与完整 Pyodide。
旧映射所在的基础镜像层不会因此缩小；收益是本次新静态层与后续传输不再重复包含映射，
并且新运行镜像不再公开这些映射，不能把映射原始大小当作首屏下载收益。

身份桥完整构建要求 BuildKit：依赖文件先复制，`npm ci` 的下载缓存使用独立 cache mount，
业务源码变化不使依赖安装层失效；锁文件变化时仍执行安装，但可复用已下载包，缓存不进入镜像。
Chat 的性能/后端增量继续基于已确认镜像离线构建，不重装后端或前端依赖。
免费 Chat 发布器的 `--no-cache` 保留，它需要补丁 RUN 输出的五个文件摘要作为回执；
不能单独删除该参数使缓存命中时缺失核验依据。本地构建缓存状态未代表服务器实际命中情况。

本地定向检查入口：`node --test deploy/frontend-build-artifacts.test.mjs`。
它检查公开/私有拆分、摘要关联、旧产物保护、Pyodide 保留及构建配置；
不能替代 Docker 镜像构建、线上资源检查或真实用户手机验收。

上线验证仅检查必要的 CDN 缓存/动态绕过、运行文件和私网 HTTPS 路径。
服务器内耗时、桌面公网耗时不等同于用户手机运营商链路，手机实际体验及真实账号
弱网恢复仍需使用场景验收；不自动发送收费模型请求。

## 旧版清理边界

性能发布本身不删除旧 LibreChat。后续按[独立退役流程](legacy-retirement.md)归位共享
身份桥的 Compose 和现有配置输入后，才删除旧版专属资源。身份桥的数据和签名文件
继续使用原物理路径，不把共享 Main SSO 协议或新站一起删除。
