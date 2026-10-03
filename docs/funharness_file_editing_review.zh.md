# FunHarness 文件编辑工具审查与升级建议

审查日期：2026-09-29。本文记录升级前的源码审查、本机临时文件复现和设计建议。后续升级已落地，实际行为、性能与边界见[文件编辑工具说明](funharness_file_editing.zh.md)。

结论：保留 `tool_replace_in_file` 的名字和单文件批量替换能力，建立 read/write/edit 共用的文件编辑底层。优先修复误改、并发覆盖、换行破坏和结果传递，再用模型评测决定是否增加 patch 格式。

## 审查依据

外部实现固定到提交，避免默认分支变化使结论失效：

- Pi：`badlogic/pi-mono` 当前重定向到 `earendil-works/pi`，审查提交 `11894012dd461232eb075bc890538b6866860a10`。
  - [edit.ts](https://github.com/earendil-works/pi/blob/11894012dd461232eb075bc890538b6866860a10/packages/coding-agent/src/core/tools/edit.ts)：批量接口、取消边界、BOM、结果元数据。
  - [edit-diff.ts](https://github.com/earendil-works/pi/blob/11894012dd461232eb075bc890538b6866860a10/packages/coding-agent/src/core/tools/edit-diff.ts)：匹配、重叠校验、换行及有限归一化匹配。
  - [file-mutation-queue.ts](https://github.com/earendil-works/pi/blob/11894012dd461232eb075bc890538b6866860a10/packages/coding-agent/src/core/tools/file-mutation-queue.ts)：按真实路径串行化本进程修改。
  - [read.ts](https://github.com/earendil-works/pi/blob/11894012dd461232eb075bc890538b6866860a10/packages/coding-agent/src/core/tools/read.ts)：按行窗口读取与图片读取共用入口。
- DeepSeek Harness：`deepseek-ai/deepseek-harness`，审查提交 `4878cdabd87d4041bdaff61d04c966883b9fd07a`。
  - [tool-fs/edit.ts](https://github.com/deepseek-ai/deepseek-harness/blob/4878cdabd87d4041bdaff61d04c966883b9fd07a/packages/fs/tool-fs/src/edit.ts)：唯一匹配默认值、显式 replace_all、结构化结果。
  - [fs-observation-policy](https://github.com/deepseek-ai/deepseek-harness/blob/4878cdabd87d4041bdaff61d04c966883b9fd07a/packages/fs/fs-observation-policy/src/index.ts)：按会话保存观察到的版本与缺失状态。
  - [fs-local/index.ts](https://github.com/deepseek-ai/deepseek-harness/blob/4878cdabd87d4041bdaff61d04c966883b9fd07a/packages/fs/fs-local/src/index.ts)：文件锁、版本检查和底层读写。
  - [fsio.ts](https://github.com/deepseek-ai/deepseek-harness/blob/4878cdabd87d4041bdaff61d04c966883b9fd07a/packages/fs/fs-local/src/fsio.ts)：临时文件、原子发布、换行处理。
  - [win32.ts](https://github.com/deepseek-ai/deepseek-harness/blob/4878cdabd87d4041bdaff61d04c966883b9fd07a/packages/fs/fs-local/src/win32.ts)：Windows ReplaceFileW 和权限保留。
  - [base 工具选择说明](https://github.com/deepseek-ai/deepseek-harness/blob/4878cdabd87d4041bdaff61d04c966883b9fd07a/.agents/notes/implemented/simplification/2026-09-05-base-default-file-editor.md)：共享默认配置选 read/write/edit；str_replace_editor 是可选接口，两者不能混为默认实现。

外部项目未安装依赖、未运行其完整测试；以下外部行为来自所列源码。本地复现直接调用当前 FunHarness 的工具。

## 当前实现的问题

涉及本地文件：`funharness/src/core/tools.py`、`funharness/src/agent.py`、`funGUI/backend/service.py`。

| 优先级 | 发现 | 证据及影响 |
| --- | --- | --- |
| P1 | 默认修改所有匹配，完成后才警告 | `tools.py:290` 遍历所有非重叠匹配。临时文件 `x x` 以 x→y 编辑，实际得到 `y y`。这是当前明确的行为，但对局部代码修改容易造成误改。 |
| P1 | 遗漏 new_text 会成为删除 | `tools.py:257` 将缺失字段默认成空字符串；单次接口也有空字符串默认值。复现批量条目仅有 old_text 时，该文本被删除。Agent 只验证顶层 required，没有验证数组条目的必填字段。 |
| P1 | Windows 换行会发生非目标修改 | `tools.py:283,320` 使用默认换行转换。原文件 `first\nsecond\n`，first→first 仍变成 CRLF；new_text 含 CRLF 时，复现生成了 `\r\r\n`。 |
| P1 | 读后写覆盖并发修改 | 没有共同锁、读取版本或提交前冲突检查。用故障注入模拟工具读完文件后编辑器保存另一区域，最后的工具写入覆盖了那份修改。 |
| P1 | 直接截断原文件再写入 | `Path.write_text` 未使用临时文件替换。写入中断或 I/O 失败可能留下不完整文件；此项由代码路径确认，未模拟真实磁盘故障。 |
| P2 | GUI 批量编辑 diff 路径错误 | `service.py:2985` 从成功文案解析路径，只去掉 warning。批量结果的 ` across 2 replacement(s) ...` 被当成路径的一部分。运行源文件中原始解析方法，确认错误路径。 |
| P2 | 缺少按行读取与可恢复诊断 | read_file 仅有字符前缀截断；匹配失败仅给有限文字提示。编辑远处代码需要额外搜索/重读，容易增加模型往返与上下文。 |
| P2 | GUI 重复读取、传输和存储全文 | 编辑前后另外读取文件，并将 old_content/new_content 放入事件和变更记录。成本随文件大小和编辑次数增加；尚未做端到端性能剖析。 |

已有优点应当保留：所有条目基于同一份原文匹配；先校验再写入；拒绝重叠替换；批量改动只写一次；用片段拼接构造结果。没有必要为了换工具名称重新实现这些能力。

本机合成测量：1,008,000 字节文本、12 个唯一替换块、5 次独立执行，工具耗时分别为 9.66、14.79、22.55、17.85、17.12 毫秒。包含文件读写，不含模型、GUI 和网络。它不代表所有工作负载，但没有支持“普通源文件的字符串查找已经是主要瓶颈”的判断。短文本大量命中等极端情况仍需资源上限。

## 值得借鉴的设计与边界

Pi 的批量编辑很适合 FunHarness：一次调用提交互不重叠的块，旧文本在原文件中唯一，模型只输出足够定位的最小上下文。它还把显示 diff 放在 details 中，与模型确认文本分开，并通过共享队列协调 edit/write。

Pi 的自动归一化匹配包括 Unicode NFKC、引号、横线、特殊空格及行尾空白。FunHarness 不宜直接照搬为默认行为：这些字符在源码字符串、Markdown 和数据文件中可能有实际意义。可以借鉴诊断和候选定位，但应优先保留精确内容语义。

DeepSeek Harness 更值得借鉴的是底层分工：工具负责参数和模型反馈；会话观察策略负责“依据哪个版本修改”；文件系统负责锁、版本检查、匹配、写入。默认 edit 唯一匹配，只有显式 replace_all 才全局替换。观察策略启用后，未读文件和版本过期分别给出稳定错误与重读提示。

这些实现也不是所有问题的现成答案：Pi 默认写入路径仍直接调用 writeFile；DeepSeek 的共享锁协调的是该文件系统实例，不能阻止任意外部编辑器。原子替换解决半写文件，不自动等于对外部进程的原子版本比较与写入。FunHarness 的设计与测试必须明确这一区别。

## 推荐方案

### 1. 保留一个常用编辑入口，规范参数

继续使用 `tool_replace_in_file`。模型可见 schema 以 replacements 为主，避免同时暴露多个等价编辑工具；旧版 old_text/new_text 在兼容适配层转换。

建议的调用形态：

```json
{
  "path": "src/example.py",
  "replacements": [
    {"old_text": "timeout = 300", "new_text": "timeout = 900"},
    {"old_text": "enabled = False", "new_text": "enabled = True"}
  ]
}
```

规则：默认每项必须唯一匹配；全局替换需要显式 replace_all，可额外指定 expected_count 防止命中范围变化。new_text 必须显式提供，空字符串表示删除；混用互相冲突的参数时报错；全部条目基于同一快照，失败不落盘。运行时也校验嵌套参数，不能仅依靠提供给模型的 schema。

默认全局替换改成默认唯一匹配属于有意的行为变更。保留调用名称并不意味着旧调用仍会默认改全部；现有测试、提示词和直接调用者应一并迁移。

### 2. read/write/edit 共用 FileEditService

建议提取 `core/file_editing.py`，职责包括规范路径、读取原始字节、识别编码与换行、匹配、冲突检查、写入及生成编辑结果。纯匹配规划与有副作用的提交分开，预览与正式执行使用同一算法。

- 保留 UTF-8 BOM、末尾换行和未修改区域；混合换行不能无提示全文件改写。非 UTF-8 与二进制明确拒绝文本编辑。
- 不变内容返回 changed=false，避免写入、触发 watcher 和制造 diff。
- 按规范化的真实目标串行化写入，write/replace、主 Agent、子 Agent、群组 Agent 使用同一协调机制。处理 Windows 大小写、路径别名、符号链接；硬链接语义应单独明确。
- 同目录临时文件完整写入并关闭，再发布；Windows 保留文件权限，文件占用只做有上限的重试。禁止删除原文件后重试这种退化路径。
- 取消在提交前生效；一旦提交成功，后续 diff 生成、临时文件清理或 UI 错误不能把结果误报成“没修改”。等待锁和资源操作需要有界、可取消。

### 3. 版本观察留在 Harness 内部

每个 Agent/会话记录自己确实读取的目标版本，成功编辑后更新；不能用全局 path→最新版本替代，否则另一个 Agent 的读取会错误地授权当前 Agent 的旧上下文。

版本应与读出的字节快照一致，结合内容摘要和文件身份/元数据检测变更。写入前核对读取基线，并在准备提交时再次检查；发现变化返回 FILE_CHANGED，提供有限的重读位置。不要自动覆盖或反复重放旧编辑。

针对同进程写入可提供确定的串行保护；跨后端进程需要额外共享锁或统一写入服务。外部编辑器不会遵守 Harness 锁，仍需清楚说明剩余竞争窗口，不能把“hash 检查 + rename”宣传成通用操作系统 CAS。

状态恢复时不能把历史摘要中的文件内容当成最新版本。未恢复到可靠快照的目标要求重新读取。通过 shell 读取或写入的文件也不能仅凭工具成功文案推断版本；后续编辑需要取得真实文件快照。

### 4. 减少上下文与重复工作

给 tool_read_file 的纯文本分支增加 start_line/limit，返回可直接复制的原文窗口、总行数、截断位置和下一段范围。保持现有 300000 默认字符上限与图片读取能力；PDF 等提取文本不能被当成原文件字节来授权文本替换。

编辑成功默认反馈修改数量、位置及少量必要上下文；匹配失败反馈条目序号、匹配数量、候选行范围和明确恢复动作。相似文本可帮助定位，不能自动挑一个写入。提供 AMBIGUOUS_MATCH、TEXT_NOT_FOUND、FILE_CHANGED、INVALID_ARGUMENT、WRITE_FAILED 等稳定错误码。

限制文件大小、条目数、命中数和结果大小，避免极短 old_text 在巨大文件中生成大量匹配对象。全文与完整 diff 按需读取，避免每次工具调用都重复灌入上下文。

### 5. 结构化结果贯穿 Agent 与 GUI

复用现有 ToolResult 的 content/display 分离机制，增加明确的机器状态字段供 Agent、hooks 和日志使用。content 给模型短摘要；display 给 GUI 路径、变更块、行数、提交前后版本和必要的变更引用。

GUI 直接消费编辑器提交时生成的结果，不再解析英语文案、也不再在提交后重新读文件重建本次 diff。这样显示的是本次修改，不会混入紧随其后的另一个编辑。

ToolResult 当前只有 content/display；增加状态时还需升级 `_execute_tool` 的成功判定和 hooks。只把 status 塞入 UI 元数据而仍靠文本中是否出现 error/failed 判断成败，不足以完成改造。

### 6. Patch 作为后续可测量的选择

先把共享编辑底层做稳。大范围重构或多文件任务可能受益于 patch，但不能未经 DeepSeek 模型评测就认定它一定比批量替换更省 token、更准确。若增加 patch，应复用同一权限、锁、版本和结果体系。

多文件“预校验全部通过”不等于整个文件系统事务。若支持部分提交、失败回滚或撤销，必须记录实际成功的文件；撤销也须检查版本，避免覆盖用户后续编辑。

## 落地顺序与验证

1. 修复默认匹配、必填参数、换行和 no-op；建立共享提交路径，并覆盖并发、取消与写入失败。
2. 接入按行读取和每 Agent 的版本观察；更新群组工具包装、权限及提示词，明确直接调用与会话调用的策略。
3. 接通结构化编辑结果、GUI diff、hooks 和日志；覆盖旧历史记录展示。
4. 对真实模型做编辑任务集评测，再决定受控匹配或 patch 是否值得增加。

最低回归范围：单次/批量/删除、重复/缺失/重叠匹配、原文非级联、LF/CRLF/混合换行/BOM/无末尾换行、中文路径、无效 UTF-8、空文件、同文件竞争、外部修改、新建文件冲突、符号链接、只读/占用/写入失败、提交前取消/提交后报告、子 Agent 与群组调用、GUI 批量 diff、模型和显示元数据隔离。

模型评测指标应包括最终编辑正确率、误改率、失败后恢复率、调用轮数、输入/输出 token、墙钟时间和原格式保留率。CPU 微基准与这些指标分开报告。
