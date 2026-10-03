# FunHarness 上下文预算与超限恢复

原来的压缩条件是固定 240 万字符，而且始终完整保留最近几条消息。
单次巨大工具返回、图片或很长的 reasoning/tool arguments 都可能绕过这个限制。
服务端返回上下文超限后没有恢复分支，后续“继续”会再次发送相同的大历史。

## 当前行为

主 Agent、SubAgent 和 GroupAgentRunner 都在每次推理前使用 `ContextManager`：

1. 估算消息、中文文本、reasoning、工具参数、工具 schema 和图片的 token 成本。
   图片按视觉输入估算，base64 不作为文本 token；估算偏小时，使用返回的 prompt usage 校准后续预算。
2. 为模型输出预留空间，再保留 20% 的安全余量。显式发送与预留一致的 `max_tokens`。
3. 超预算时压缩历史。保留系统/开发者指令和最新真实用户请求，后台上下文与中间件反馈不冒充用户请求。
4. 摘要请求有独立输入/输出上限及 20 秒超时；失败或返回空摘要时，使用本地有限长度摘录继续。
   取消操作仍然中断，不会被误认为摘要失败而继续执行。
5. 最近工具返回同样受预算约束，文字裁切保留首尾。保留完整 assistant/tool 批次和调用 ID。
   参数或 reasoning 本身过大时，把整批调用改写为已经发生的工具观察，避免截断 JSON 或重复执行工具。
6. 识别上下文超限和请求体过大错误，压缩后最多重试 3 次。主 Agent 和群组 Agent 的重试同时覆盖
   流式请求建立和流读取阶段，失败的流会关闭。认证、普通参数错误和网络错误不进入上下文恢复分支。

正常图像清理优先保留最新观察；紧急压缩无法容纳整批图片时，会明确标注移除的图片和来源，
让模型按需逐张重新读取。不会截断 base64、伪造视觉内容或隐瞒图片已经移除。

## 会话恢复

- 上下文原地替换，主 Agent 和 Session 始终引用相同的消息列表。
- 主 Agent 在替换前保存上下文快照到 `.funharness/sessions/context_archives/<session_id>/`，
  替换后立即保存活动会话。快照是此次完整压缩之前的活动上下文；之前常规淘汰的内容不会被重建。
- 学到的服务端窗口、缩减预算和估算系数放在 `context_state` 中，主会话、分支和群组会话支持序列化。
  GUI 加载会话也保留这些状态。因此保存、重载和发送“继续”后仍使用修正后的预算。
- 切换模型、API 地址或预算环境配置会重置学习结果，避免把一个端点的限制套用到另一个端点。
- 子 Agent 在其生命周期内保留预算状态；后台提供的 context 可压缩，当前 task 保持完整。

快照不会自动作为模型输入；需要追溯原始内容时可以检查对应 JSON。

## 配置与检查

`/context` 显示包含工具定义的估算输入 token、输入预算、模型窗口以及输出预留空间。

| 环境变量 | 含义 |
| --- | --- |
| `FUNHARNESS_CONTEXT_WINDOW` | 当前端点实际支持的总窗口，单位 token；必须为正整数 |
| `FUNHARNESS_MAX_OUTPUT_TOKENS` | 输出预留及请求的输出上限；最多占窗口的四分之一 |

默认登记 DeepSeek Flash/V4 对应的 1,048,576 token 窗口，其他模型保守使用 128,000。
DeepSeek 的窗口计入输入和输出，官方模型元数据提供 `context_window` 与 `max_output_tokens`。
参见 [DeepSeek Models API](https://api-docs.deepseek.com/api/list-models/)。
当前 harness 对已登记 DeepSeek 模型默认预留 65,536 输出 token，其他模型默认 8,192；
这是本项目的请求策略，不表示所有兼容端点都支持相同限制。
较小的代理端点应设置实际窗口；服务端超限错误提供具体窗口时也会自动下调。
未提供具体窗口的超限错误还会把过大的输出预留降至 8,192。

token 估算不是厂商 tokenizer 的精确值，因此仍保留服务端超限后的恢复机制。
如果系统指令、工具定义或当前用户输入本身已超过预算，压缩旧历史无法解决；
此时保留原输入并明确提示减少本次输入/附件、分批读取或修正窗口配置，不会无限重试。

## 验证

```powershell
.venv/Scripts/python.exe -m unittest discover -s funharness/src/core/tests -q
.venv/Scripts/python.exe -m unittest funGUI.backend.tests.test_attachments_api funGUI.backend.tests.test_groups_api funGUI.backend.tests.test_interrupt funGUI.backend.tests.test_team_api -q

# 主动运行才产生真实 API 请求；从环境提供密钥，不写入文件。
.venv/Scripts/python.exe -m funharness.scripts.verify_context
```

真实验证脚本默认读取 `DEEPSEEK_API_KEY`，也支持 `--key-stdin`。
测试中的超限错误在本地注入，不发送巨大历史制造真实限额错误；
压缩摘要、恢复后的推理和会话重载后的继续请求使用真实 `deepseek-flash` API。
2026-09-25 三个场景全部通过，原任务中的随机项目编号在压缩和重载后保留。
同日最终离线回归：Core 149 项通过，GUI 相关 25 项通过。

离线测试覆盖超大新工具批次、中文/schema/reasoning 预算、摘要失败与取消、流读取超限、
恢复时不重复执行工具、图像请求体裁减、有限重试、端点窗口学习、会话与分支持久化，以及三个 Agent 循环。

GUI 加载修改及回归测试位于当前被 `.gitignore` 忽略的 `funGUI/` 目录，提交这些本地改动时需要单独包含。
