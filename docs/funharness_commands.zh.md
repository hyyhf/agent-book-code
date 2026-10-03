# FunHarness 命令执行与调度

`tool_run_command` 使用托管运行任务。命令需要运行多久，与一次工具调用需要等待多久，分别由不同参数控制。

## 调用方式

```python
# 普通构建：默认先等 1 秒，最多运行 900 秒。
tool_run_command(command="npm run build", timeout=900)

# 未完成时返回 runtime_id；继续等待同一个任务，绝不重跑命令。
tool_runtime_wait(runtime_id="command_...", yield_time_ms=30000)
tool_runtime_output(runtime_id="command_...")
tool_runtime_status(runtime_id="command_...")

# 显式常驻服务：立即返回，不设置执行期限。
tool_run_command(command="npm run dev", background=True, timeout=0)

# 请求终止进程树，再查询/等待确认 cancelled。
tool_runtime_cancel(runtime_id="command_...")
tool_runtime_wait(runtime_id="command_...", yield_time_ms=3000)
```

| 参数 | 默认值 | 含义 |
| --- | --- | --- |
| `timeout` | 300 秒 | 命令的总执行期限，最大 86400 秒；仅显式后台任务允许 0，表示无期限 |
| `yield_time_ms` | 1000 毫秒 | 首次调用最多等待多久，范围 0～10000；到期返回任务编号，命令继续由运行管理器管理 |
| `background` | false | 显式声明后台任务；true 时立即返回 |
| `shell` | `default` | 选择执行语法：`cmd`、`powershell`（Windows 5.1）、`pwsh`（7+）、`sh` 或 `bash`；默认保持 Windows CMD / POSIX sh |

`tool_runtime_wait` 每次最多等待 30 秒。查询与等待不会重置执行期限，也不会重新执行命令。`tool_runtime_run` 作为显式后台入口保留，与上述入口共用执行器和生命周期。

工具返回 `status=running` 表示仍在运行，不能当作执行成功。依赖其结果的后续操作必须等待完成。主 Agent 尝试结束回答时，如果自己还有未完成的普通命令，会继续等待并把结果送回模型；显式后台服务不阻止本轮结束。子 Agent 和群组 Agent 使用同样的等待机制。

## 进程所有权与取消

- 普通命令即使已经返回任务编号，也仍属于当前 Agent；GUI 的停止操作会请求取消这些命令。
- 显式后台服务不随普通对话停止而退出，可通过 `tool_runtime_cancel` 或 GUI 的运行任务取消入口停止；后端关闭时清理。它们不保证跨后端重启继续运行。
- 子 Agent 和群组的运行范围结束时清理它们的命令，取消信号和子 Agent 总时间预算也会传入执行器。
- Windows 在命令开始执行前将其加入 Job Object，设置关闭即终止，并隐藏控制台；结束时终止该 Job 内的后代，等待其释放资源。POSIX 使用独立会话和进程组终止。
- 命令最外层进程退出时，运行管理器会清理其剩余后代。启动服务器时直接执行服务器命令，不要使用 `start`、`nohup`、后台后缀 `&`、`Start-Process`、`Start-Job` 或 daemonize 另行脱离管理。PowerShell 在引号路径前使用的调用运算符 `&` 是正常的前台调用。
- 每个运行管理器最多接纳 8 个同时活动的命令，超出时返回错误，要求先等待或取消已有任务。
- 保存的 PID 仅供观察，取消操作使用仍然持有的运行任务对象。切换 GUI 会话后，同一个后端仍可将取消请求转交给实际拥有该任务的管理器，不会根据旧 PID 盲目杀进程。

旧记录或重启后失去所有者的活动任务标记为 `lost`，不会永久显示为正在运行，也不会自动重跑可能产生副作用的命令。记录同时保存所有者与进程启动标识，以降低 PID 重用造成的误判；应先检查文件或其他产物，再决定是否重试。

## 输出与状态

输出使用无缓冲、非阻塞管道读取；每次读取有字节上限，持续刷屏也不会饿死超时和取消检查。取消和超时后仍保留已经读到的输出。标准输出和错误输出分别保留首尾，避免大段日志覆盖最后的报错。

运行中每约 0.5 秒更新有界输出快照与任务记录，提供 `pid`、`elapsed_seconds`、`timeout`、`last_output_at`、`output_bytes` 和 `exit_code`。快照约限制为 50000 字符，超长内容明确显示截断提示；它不是完整日志归档。如果需要完整构建日志，让命令写入指定文件，再按需读取。

状态包括 `queued`、`running`、`cancelling`、`done`、`failed`、`cancelled`、`timed_out`、`lost`。`cancelling` 仅表示取消请求已提交，`cancelled` 表示执行器已完成终止和清理。

输入流关闭，工具用于非交互命令，安装/登录等命令应使用适当的非交互参数。

## PowerShell 支持

`tool_run_command` 和 `tool_runtime_run` 均支持 `shell` 参数。默认保留 Windows CMD / POSIX sh；PowerShell 脚本应明确选择版本：

```python
tool_run_command(
    shell="powershell",  # 系统内置 Windows PowerShell 5.1
    command="Get-ChildItem -LiteralPath . | Select-Object Name,Length | Format-Table",
)

tool_run_command(
    shell="pwsh",  # 已安装的 PowerShell 7
    command="""
$items = Get-ChildItem -LiteralPath . -File
$items | Where-Object { $_.Extension -eq '.py' } | Select-Object Name
""",
)

tool_runtime_run(shell="pwsh", command="npm.cmd run dev", timeout=0)
```

- PowerShell 直接启动，不经过 CMD 解析。用户脚本保存在独立临时文件中，通过 UTF-16LE 编码的短引导命令读取和执行，避免中文、多行、嵌套引号及 Windows 命令行长度限制问题；正常退出、失败、超时和取消后清理临时文件。
- 使用 `-NoProfile -NonInteractive`，不加载用户 profile，不等待交互输入；控制台和管道文本编码设为 UTF-8。外部程序若强制使用其他编码，可在脚本中明确配置相应编码。
- Cmdlet 默认采用 `$ErrorActionPreference='Stop'`，错误返回失败和可读诊断。保留最后一个外部程序的退出码；预期的非零码需要脚本显式处理，必要时 `exit 0`。不根据输出是否包含单词 Error 判断进程成功。
- PowerShell 5.1 和 7 按各自原生语法运行：例如 `&&` / `||` 需要 PowerShell 7。未安装所选 shell 时明确报错，不会静默改用其他 shell。
- Shell 变量和 `Set-Location` 仅在本次命令中有效。独立 `.ps1` 文件的执行仍受系统执行策略约束；调用 Windows npm/npx 时可明确使用 `npm.cmd` / `npx.cmd`。
- 实际 shell 名称和可执行文件路径保存在任务记录中；Agent 系统提示会列出当前可用 shell，并引导模型显式选择。两种 PowerShell 共用已实现的限时、后台任务、非阻塞输出、取消和进程树清理机制。
- 危险命令匹配保留对磁盘 `format` 工具的阻止，同时允许 `Format-Table`、`Format-List` 等 PowerShell 输出命令。

参数和编码依据微软的 [`about_PowerShell_exe`](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_powershell_exe?view=powershell-5.1) 与 [PowerShell 首选项文档](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_preference_variables?view=powershell-7.5)。

## 修复的原有问题

原执行器在后台线程里阻塞读取 stdout/stderr，然后由执行线程关闭这些管道。即使外层已经判定超时，关闭操作仍可能等待读取锁；当后代进程持有管道时，读取线程也可能一直等不到 EOF。旧实现还有同步树清理、Windows 直接 Python 分支未统一设置启动选项、群组命令包装器不接收 timeout 等问题。

新的执行器不再创建阻塞读取线程，也不依赖管道 EOF 判断工具是否应该返回。模型等待、进程期限、进程所有权和日志读取分别有明确的边界。进程终止后允许短暂的有界资源清理时间，因此实际返回可比设定期限稍晚，不能把 `timeout` 理解成实时系统的精确截止时刻。

实现依据：Python 3.12 起 Windows 管道支持 [`os.set_blocking`](https://docs.python.org/3.12/library/os.html#os.set_blocking)；Windows 的子进程归属与关闭清理使用 [Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)。这不是恶意程序安全沙箱；POSIX 主动脱离进程组的程序、外部服务管理器接管的任务，不在普通进程组清理的保证范围内。

更新后需要重启 GUI 后端。旧执行器已经卡住的线程不会通过前端刷新自动替换。

## 本次验证（2026-09-28）

- 核心测试 174 项通过；GUI 的停止、附件、计划、团队相关回归 29 项通过。
- 新增真实进程覆盖超时、取消、运行中输出、海量 stdout/stderr、关闭 stdin、管道和孙进程、父进程提前退出、后端被强制结束、无控制台窗口、跨会话查询/取消、状态文件并发读写，以及子 Agent 时间预算。实际进程验证环境为 Windows / Python 3.12；POSIX 分支未在本次环境实测。
- GUI 全量测试运行 419 项，3 项失败不在本次命令模块：`test_legacy_course_route_is_not_automatically_rechecked` 清理临时 SQLite 数据库时文件仍被占用；`test_topic_loop_links_question_experiment_feedback_and_review_material` 事件排序断言失败（单独重跑通过）；`test_switch_replaces_service_and_keeps_event_bus` 的 `FakeService` 缺少 `stream_mind_garden_agent`。这些模块未在本次修改中修复。

## PowerShell 增强验证（2026-09-29）

Windows PowerShell 5.1.26100.9444 与 PowerShell 7.6.5 的 30 项专项测试通过；增加主 Agent 权限与执行链路验证后，核心全量 205 项通过。覆盖中文目录和参数、引号、变量、多行 here-string、对象管道、Format-Table、UTF-8 文件往返、超过 Windows 命令行长度的脚本、退出码、解析错误、非交互输入失败、超时、取消子进程、运行任务 shell 记录和等待接口。POSIX 分支仍未在本次 Windows 环境实测。
