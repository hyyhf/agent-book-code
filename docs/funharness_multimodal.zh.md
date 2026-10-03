# FunHarness 多模态输入与工具观察

## 使用

文件统一通过 `tool_read_file` 读取，自动根据文件类型返回文本或图片内容。
已有图片（包括 Agent 自己写程序生成的截图）可以直接读取：

```python
tool_read_file(path="screenshots/page.png")
tool_read_file(path="screenshots/page.png", detail="high")
tool_read_file(path="src/app.py", max_chars=300000)
```

图片作为视觉内容送入下一轮模型调用，没有独立的图片查看工具或截图工具。
命令工具仍返回命令输出；生成图片后，Agent 再调用 `tool_read_file`。
仅在命令输出里打印路径、Markdown 图片链接或 base64，不会自动变成视觉输入。

`tool_read_file(path, max_chars=300000, detail="auto")` 默认返回最多 30 万字符的文档提取结果，
可显式提高 `max_chars`。附件读取和群组文档读取使用相同默认值。
该参数只限制文本，不裁切图片数据。旧工具调用 `tool_read_file(path)` 保持兼容。
`detail` 仅作用于图片，支持 `auto`、`low`、`high`、`original`，读取普通文档时忽略。

附件仍可通过 `tool_read_attachment(attachment_id, detail="high")` 按 ID 读取；
群组使用 `group_read_workspace(path, detail="high")` 保持目录隔离。
这些原有入口与 `tool_read_file` 共用底层文件识别和多模态读取实现。

DeepSeek 示例配置（也可以在已有模型配置界面选择）：

```dotenv
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL_NAME=deepseek-flash
OPENAI_API_KEY=<your-key>
```

本次没有替换用户已有模型配置；其他模型是否支持视觉输入取决于提供商。

## 内部结构

`core/content.py` 定义 `Content = str | list[TextBlock | ImageBlock]`，
`ToolResult.content` 使用这个类型，`ToolResult.display` 继续只用于界面。
`core/tools.py` 仍导出 `ToolResult`，兼容现有导入。

内部图片块示例（省略实际数据）：

```json
{
  "type": "image",
  "mime_type": "image/png",
  "data": "<base64 bytes>",
  "width": 1920,
  "height": 1080,
  "detail": "high",
  "source_path": "/workspace/screenshots/page.png"
}
```

图片数据是读取时的快照。会话保存、恢复和分支不需要重新打开原图；原图被覆盖或删除，
保留的观察仍然可重放。采用内联快照是为了保持现有 JSON 会话存储自包含，避免引入
额外的媒体索引、资产迁移和垃圾回收协议；代价是会话文件会包含 base64 数据。

模型上下文接收原始结构；hooks、工具历史、日志、GUI 回调、会话标题和摘要使用
`content_text()` 生成文本投影，不将 base64 当作字符串展示或总结。
主 Agent、SubAgent（含使用它的团队/Swarm worker）和 GroupAgentRunner 均保留结构化工具结果。
主 Agent 的 `run()` 也接受直接传入的内容块列表。

## Chat Completions 适配

项目保留现有 Chat Completions 客户端。根据
[DeepSeek 官方视觉文档](https://api-docs.deepseek.com/zh-cn/guides/vision/)，
该接口的图片输入使用 `user.content` 中的 `image_url` 块。

内部会话仍然记录真实的工具结果与 `tool_call_id`：

```text
assistant: calls A, B
tool A: text + image
tool B: text
```

`sanitize_messages_for_api()` 发送前转换为：

```text
assistant: calls A, B
tool A: text projection
tool B: text
user: tool observation A + text + image_url(data URL)
```

观察中标注工具调用 ID 和来源，说明它不是新的用户指令。
图片消息必须放在整批工具结果之后，不能插入 A 和 B 之间。
被中断的调用先补齐工具结果，再附加视觉观察。转换不修改原始会话，
不混入 UI 的 display 元数据，并保留 DeepSeek 的 `reasoning_content`。
`original` 在兼容接口中映射为 `high`。

## 资源与上下文策略

- 按实际字节识别 PNG、JPEG、GIF、WebP；无扩展名或错误扩展名的截图也可读取。
- Pillow 验证并解码图片，拒绝损坏文件、不支持的格式、超过 32 MiB 或 4000 万像素的图片。
- 原始图片字节保留，不静默缩小截图；大图或微小文字可由 Agent 自己生成裁剪图后读取。
- 动画仅保证首帧可理解；需要检查时序时，先提取各帧。这不等于已支持视频理解。
- token 估算按尺寸近似计算，实际用量以 API 返回的 usage 为准，不使用 base64 长度估算 token。
- 老工具结果只裁切文本块；超过最近 8 条工具结果的旧图可从活动上下文移除，并保留明确提示和原路径。
- 总图片预算优先保留新图，通常控制在约 32 MiB 原始字节对应的 base64 大小内。
  常规清理保护最近一批工具图和最新用户消息；超限紧急恢复可以明确标注并移除过大的工具图，提示按需逐张重读。
  新输入本身过大时，发送层拒绝超过 48 MiB 的请求，运行时尝试压缩并重试；当前用户输入本身无法容纳时明确报错。
- 完整摘要保留最近的 assistant/tool 分组；旧图只提供文字元数据和已有视觉结论，
  不向摘要模型伪装提供像素。摘要调用失败时使用本地有限长度摘录降级，避免持续发送超大上下文。
- 主会话完整压缩前保存活动上下文快照；常规旧图淘汰不会单独生成快照，需要重新检查时重新读取文件。
- 上下文预算、超限恢复、输出预留及会话持久化详见 [上下文管理说明](funharness_context.zh.md)。

图片读取使用现有路径权限。群组图片入口限制在群组目录；附件 ID 只能解析到本会话的上传目录。
同时补齐了 `tool_find_files` 的只读风险分类和路径检查。

## 后续音视频扩展

当前仅实现 text/image。接入音频或视频时，需要增加有明确字段的内容块、对应验证/预算策略，
以及提供商转换分支；工具循环、JSON 会话和 UI 的文本投影无需改回纯字符串。
未支持的内容块当前会明确报错，不会静默变成普通文本，也不会假装模型已经理解它们。

## 验证

离线回归：

```powershell
.venv/Scripts/python.exe -m unittest discover -s funharness/src/core/tests -q
```

真实 API 验证（主动运行才产生请求和费用）：

```powershell
# 从运行环境提供 DEEPSEEK_API_KEY；不要将密钥写进脚本。
.venv/Scripts/python.exe -m funharness.scripts.verify_vision
```

脚本固定使用 `deepseek-flash` 和官方 API 地址。随机码仅绘制在测试图片中，
不写入模型提示、文件名或文档预览。断言读取正确的随机码和矩形颜色，覆盖主 Agent
文件读图（含显式高细节模式）、附件读取、删除最终答案后的会话重放、SubAgent，
以及一批工具返回两张图片。
图片、会话和日志均在临时目录，脚本不保存 API 密钥。

2026-09-25 统一文件读取入口后的真实调用：上述 6 项通过，Core 全部 127 项测试通过。离线测试还覆盖群组权限与恢复、并行工具调用、
中断边界、图片资源限制、上下文压缩，以及命令工具运行程序后再读取新生成的图片。

GUI 兼容改动位于 `fungui/backend/service.py`，回归测试位于
`fungui/backend/tests/test_attachments_api.py`。整个 `funGUI/` 目录目前被根目录的
`.gitignore` 忽略，提交或打包时需要单独留意这两个本地文件。
额外执行的工作区切换测试发现 `FakeService` 缺少 `stream_mind_garden_agent` 方法；
该测试桩与工作区切换代码未在本次修改中调整。
