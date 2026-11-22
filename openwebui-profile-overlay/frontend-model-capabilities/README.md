# 输入区按钮与当前模型能力对齐

在已核 Chat 前端基线之上，为 `MessageInput.svelte` 接入统一模型能力归一化逻辑，确保联网搜索、视觉/图片、附件上传与服务端内置能力按钮与当前选中模型保持一致（思考/推理控制已由上游 `ModelControls.svelte` 严格按 `model_controls` 元数据按需显示）。

## 归一化规则

1. **显式配置最高优先**：管理端在 `model.info.meta.capabilities` 中声明的布尔值始终生效。
2. **识别上游真实模态元数据**：读取 `model.capabilities`、`model.openai.capabilities` 与 `architecture.input_modalities` / `output_modalities`，纯文本模型自动关闭视觉/生图标记。
3. **个人直连保守收口**：`direct: true` 的个人 API 连接在未显式声明时，默认关闭依赖服务端编排的能力（`web_search`、`image_generation`、`code_interpreter`、`terminal`、`builtin_tools`），避免点开后报错。
4. **修复空选边界**：未选中任何模型时不再因 `0 === 0` 误显示搜索或附件按钮。

## 统一构建入口

```powershell
node deploy/build-chat-frontend.mjs <ISOLATED_SOURCE> <NEW_OUTPUT> --from-feature frontend-chat-error-guidance --extend frontend-chat-error-guidance,frontend-model-capabilities
```

本地单元测试：

```powershell
node --test openwebui-profile-overlay/frontend-model-capabilities/test_model_capabilities.mjs
```
