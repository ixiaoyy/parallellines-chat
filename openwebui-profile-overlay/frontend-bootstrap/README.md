# Chat 第二批：启动恢复与设置按需加载

本增量只修改固定 Open WebUI `v0.12.5` 的前端源码。它依次建立在已核对的
`frontend-performance` 增量上，不改写旧补丁或已发布清单，不触碰身份桥、后端、
模型授权、价格、默认模型或全局模型目录缓存。完成本地交付不构成发布授权。

## 行为与保留合同

- 首次配置读取、登录后的第二次配置读取各有 12 秒网络截止；第二次读取仍必须完成，
  不能在失败后用匿名配置展示账号页面。原 forward-auth 重定向探测使用同一取消信号。
- 会话恢复保留已有 12 秒截止及 HTTP 状态语义。只有原会话验证的真实 401 清除无效令牌；
  网络、超时和临时服务错误保留令牌并提供显式重试。
- 登录后的设置、模型、横幅和工具启动读取共用一个 12 秒截止，设置仍先于模型读取。
  超时取消原生请求并进入重试状态；销毁页面时取消本轮读取，迟到结果不得回写。
  非取消模型/权限错误继续沿原处理方式，不擅自退出账号或扩大模型可见范围。
- 上述是网络阶段截止，不是承诺整个网站在 12 秒内完成脚本下载和国际化资源加载。
  重试由用户点击触发整页重新启动，避免重复安装监听器或复用未核实的账号状态。
- `SettingsModal` 首次打开才动态导入。加载成功后保留组件实例和双向 `show` 绑定；
  布尔、字符串、对象请求、快捷键、`?settings=`、普通用户的管理标签回退均保留。
- 浏览器会缓存失败的动态导入。模块失败后不能仅再次调用同 URL 的 `import()`；
  用户点击重试才重载页面，再一次性恢复设置定位，不自动重载或循环重试。

## 一次性设置恢复记录

`sessionStorage` 的 `parallellines.settings-retry.v1` 仅在用户点击模块加载重试时写入。
已检查的对象请求唯一调用方是 `ModelItemMenu`，形状为
`{ tab: 'admin:models', state: { id: model.id } }`。只允许布尔打开、标签字符串，
或 `tab` 与 `state.id` 定位；不序列化设置草稿、API Key 或任意状态字段。

记录绑定已验证的当前账号，最长五分钟；取回时先消费，账号不匹配、过期或结构非法均不恢复。
存储不可用或出现不支持的状态时保持明确失败提示，由用户刷新后重新进入，不假装定位已保留。
这条记录不保存令牌、不提供授权，也不改变主站会话或模型目录缓存。

## 本地可复现入口

在固定上游的全新准备目录先应用原性能补丁，再应用本增量：

```sh
python openwebui-profile-overlay/frontend-performance/apply.py PREPARED_SOURCE
python openwebui-profile-overlay/frontend-bootstrap/apply.py PREPARED_SOURCE
node deploy/build-performance-frontend.mjs PREPARED_SOURCE NEW_OUTPUT bootstrap
```

已应用原性能补丁的全新准备目录只执行后两步。`apply.py` 核对所有前置/输入摘要，
拒绝漂移或重复应用；构建入口必须显式选择 `bootstrap`，核对第二批全部输出摘要，
并将补丁顺序、两批来源及新清单摘要写入公开和私有产物清单。默认旧入口不接受第二批源码。
保留原锁文件和现有依赖，继续使用第一批的隐藏 source map、私有调试目录与 Docker 排除规则。

定向检查：

```sh
node --test openwebui-profile-overlay/frontend-bootstrap/bootstrap.test.mjs
node openwebui-profile-overlay/frontend-bootstrap/browser-smoke.mjs PUBLIC_FRONTEND_DIRECTORY EVIDENCE_DIRECTORY
node openwebui-profile-overlay/frontend-bootstrap/browser-smoke.mjs PUBLIC_FRONTEND_DIRECTORY EXTRA_EVIDENCE_DIRECTORY --extra
```

测试默认使用本地第二批准备源码；其他目录可设置 `OPENWEBUI_BOOTSTRAP_SOURCE_ROOT`。
浏览器脚本使用已有 Playwright 工具，可通过 `PLAYWRIGHT_PACKAGE_JSON` 指定其现有 `package.json`，
不会自动安装或升级依赖。静态服务仅绑定随机本机端口，所有业务接口均为隔离替身，
外部网络被阻断；未发起真实模型请求。真人手机体验、实际 OAuth 和线上性能均须另验。

## 恢复

未部署时保留旧候选和本轮源码差异即可。将来获得发布授权后，仍按目标服务更新和旧镜像恢复；
不通过恢复数据库、令牌或整份 Compose 来撤回本前端增量。

## 2026-11-03 本地交付证据

最终匹配产物位于 `.build/frontend-bootstrap-20261103/release-final/`，构建日志为同目录父级
`build-final.log`。固定上游、原锁依赖和 6 GB 堆完成真实生产构建；公开目录 4,808 文件，
95,160,967 B，私有映射 838 个，81,962,849 B。没有部署或调用真实模型。

使用同一隔离普通账号、相同模型/权限配置、桌面浏览器与冷页面条件，登录后首页在打开设置前的 JS/CSS：

| 指标 | 第一批产物 | 第二批最终产物 |
| --- | ---: | ---: |
| JS/CSS 去重资源数 | 206 | 198 |
| 原始字节 | 7,736,427 B | 6,562,284 B |
| 同一 gzip 算法计算 | 2,271,287 B | 1,974,302 B |

资源数按浏览器实际请求过的唯一 JS/CSS 路径计算；gzip 是这些文件逐个压缩后的合计估算，不是实网传输计量。
这是把设置依赖延后的收益：原始字节减少约 15.2%，同口径 gzip 估算减少约 13.1%。
不是整份产物减小，也不是实网提速百分比；加入恢复能力后，总公开静态产物较第一批增加 11,253 B。
原始文件清单分别保存在 `baseline-browser/initial-resources.json` 与
`final-normal-browser/initial-resources.json`（均在本轮 `.build/frontend-bootstrap-20261103/` 下）。

8 项定向测试通过；实际浏览器证据包括真实 12 秒配置截止后重试、设置/模型等待恢复、
503 保留令牌与 401 清除无效令牌、模型 403 不退出账号、手机尺寸普通/管理员深链接、
真实模块 503 后显式重载及一次性定位恢复、关闭重开，以及原生模型菜单编辑对象的 `state.id` 恢复。
设置/模型等待用例加速计时器以避免重复等待；配置用例实际等待约 12.8 秒到错误界面。

`final-browser/results.json` 保存功能候选的 10 项浏览器结果；随后只合入取消日志去重，
未变业务路径沿用这些证据。最终产物的正常/设置路径见 `final-normal-browser/results.json`，
原生对象定位恢复见 `final-object-browser/results.json`；第二次配置等待检查见
`extra-browser/results.json` 中已完成的独立结果。相关桌面和手机尺寸截图已实际查看。

源码对组取消仅保留统一启动边界；上游既有 `esbuild.pure` 会移除生产 `console.error`。
生产浏览器的单条错误日志假设因此不成立，实际为零；本批保持原构建策略，不改全局日志或另加 warn，
不宣称已经在生产浏览器观测到统一日志。错误仍明确显示为可重试状态，不伪装成功。

隔离接口不能证明真实 OAuth、外部模型、线上缓存或真人手机体验。第一批 Docker/BuildKit 的
实际冷/热缓存与镜像运行验证仍未补做；本批不擅自启动 Docker，也不新增这些操作的授权。
