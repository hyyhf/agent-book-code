<div align="center">

<img src="assets/header.png" alt="FunHarness Logo" width="100%" />

# 『从0开始构建 AI 智能体』

**一本带你亲手打造类 “Claw” AI Agent 的实战书**

[![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/) [![uv](https://img.shields.io/badge/uv-package%20manager-7C3AED?style=for-the-badge&logo=astral&logoColor=white)](https://github.com/astral-sh/uv) [![OpenAI Compatible](https://img.shields.io/badge/LLM-OpenAI%20Compatible-10a37f?style=for-the-badge&logo=openai&logoColor=white)](https://openai.com) [![License](https://img.shields.io/badge/License-MIT-F59E0B?style=for-the-badge)](LICENSE) [![GitHub stars](https://img.shields.io/github/stars/hyyhf/agent-book-code?style=for-the-badge&color=DDB88B&logo=github&logoColor=white&v=1)](https://github.com/hyyhf/agent-book-code)

*从第一个 LLM API 调用开始，一层一层搭出真正能工作的 AI 智能体*

</div>

---

<div align="center">

</div>

## 封面

<div align="center">
  <img src="assets/ebook_cover.png" alt="Book Cover" width="680" />
</div>

> 真正理解 Agent 的方式，不是只使用 Agent，而是亲手把它构建出来。


## 目录预览

<div align="center">
  <img src="assets/toc1.png" width="32%" />
  <img src="assets/toc2.png" width="32%" />
  <img src="assets/toc3.png" width="32%" />
  <br/>
  <img src="assets/toc4.png" width="32%" />
  <img src="assets/toc5.png" width="32%" />
  <img src="assets/toc6.png" width="32%" />
</div>

<div align="center"><img src="assets/chinese-divider-transparent-cropped.png" width="80%" /></div>

## 关于本书

本书是一本**从零开始构建 AI 智能体**的实战教材。不依赖任何高层 Agent 框架，完全基于原始 LLM API，带领读者一步一步手写出一个具备工具调用、上下文管理、权限控制、可观测性的 AI 编程助手**FunHarness**。

它不是一本只展示 API 调用片段的教程，也不是一本只讲概念的 Agent 读物。本书更像一条可运行、可拆解、可复现的工程路线：先从一次最小 LLM 调用开始，理解消息、流式输出和函数调用；再逐步加入 Agent Loop、工具系统、上下文管理、记忆、权限、Hooks、多 Agent 协作与可观测性；最后把这些能力沉淀进一个完整的 mini 产品。

这也是本书与普通示例代码最大的区别：读者不是“看完一个项目”，而是跟着项目的演进过程，亲手把一个类 **“Claw” AI Agent** 搭出来。每一章都有对应的中间版本，每一次新增能力都能被运行、对比和修改。到最后，FunHarness 不只是代码仓库里的产物，而是读者理解 Agent 工程化之后的第一个实战作品。

> [!tip]
> 本书的核心信念：**理解 > 使用**。
> 真正掌握 Agent 原理的最佳方式，是亲手把每一层抽象从零实现一遍。


<div align="center">

`──────  从原理到产品，从示例到自己的 Agent  ──────`

</div>

### 目标读者

- 想深入理解 LLM Agent 工作原理，而不只是调用现成框架的开发者
- 正在构建自己的 AI 工具链、编程助手或自动化 Agent 的工程师
- 对 Claude Code、Cursor、OpenDevin、类 “Claw” AI Agent 等 AI 编程工具感兴趣，想了解其内部机制的技术人员
- 希望通过一个可运行 mini 产品练习 Agent 工程化设计的读者

### 本书特色

| 特色              | 说明                                                                                  |
| ----------------- | ------------------------------------------------------------------------------------- |
| **从零构建**      | 每一章都从最基础的概念出发，不跳步骤                                                  |
| **迭代演进**      | FunHarness 从 `v01` 到 `v08` 逐版本进化，读者可以看到每个特性如何被加入、为什么要加入 |
| **可运行代码**    | 每章均有独立可运行的示例，以及对应版本的 FunHarness                                   |
| **mini 产品实战** | 最终产物 FunHarness 是一个真实可用、配置简单、便于教学和试验的 AI 编程助手            |
| **读者可复刻**    | 所有读者都可以在本仓库基础上继续改造，打造属于自己的类 “Claw” AI Agent                |
| **原理优先**      | 在引入第三方库之前，先手写核心机制，确保读者真正理解底层                              |

<div align="center"><img src="assets/chinese-divider-transparent-cropped.png" width="80%" /></div>

## 内容结构与章节代码

本仓库共 **11 章**代码，每章包含若干独立示例脚本和对应版本的 FunHarness 主体。

| 章节           | 主题                                      | 关键脚本                                                                                                     | 运行方式                                 |
| -------------- | ----------------------------------------- | ------------------------------------------------------------------------------------------------------------ | ---------------------------------------- |
| **Chapter 01** | LLM 基础：API 调用与流式输出              | `first_chat.py` `streaming_demo.py` `function_calling.py` `multi_turn_chat.py` `sampling_params.py`          | `uv run chapter01/first_chat.py`         |
| **Chapter 02** | Agent Loop：从无到有的智能体循环          | `minimal_agent_loop.py` `streaming_agent.py` `message_lifecycle.py` `error_retry.py` **`funharness_v01.py`** | `uv run chapter02/minimal_agent_loop.py` |
| **Chapter 03** | Harness 框架：裸 Agent vs. 有框架 Agent   | `bare_vs_harnessed.py` `harness_anatomy.py` `prompt_context_harness.py`                                      | `uv run chapter03/bare_vs_harnessed.py`  |
| **Chapter 04** | 工具系统：注册、调用到生命周期            | `tool_registry.py` `tool_lifecycle.py` `tool_design_principles.py` `core_tools.py` **`funharness_v02.py`**   | `uv run chapter04/tool_registry.py`      |
| **Chapter 05** | 上下文管理：发现、压缩与系统提示          | `context_discovery.py` `context_compaction.py` `system_prompt_builder.py` **`funharness_v03.py`**            | `uv run chapter05/context_discovery.py`  |
| **Chapter 06** | 记忆、技能与会话：跨轮次的状态持久化      | `persistent_memory.py` `skill_loader.py` `session_manager.py` **`funharness_v04.py`**                        | `uv run chapter06/persistent_memory.py`  |
| **Chapter 07** | 权限系统：auto / suggest / approve 三模式 | `permission_manager.py` `approval_flow.py` **`funharness_v05.py`**                                           | `uv run chapter07/permission_manager.py` |
| **Chapter 08** | Hooks、Middleware 与插件架构              | `hooks.py` `middleware.py` `plugin_system.py` **`funharness_v06.py`**                                        | `uv run chapter08/hooks.py`              |
| **Chapter 09** | 多任务与多智能体协作                      | `task_manager.py` `multi_agent.py` `build_verify_fix.py` **`funharness_v07.py`**                             | `uv run chapter09/task_manager.py`       |
| **Chapter 10** | 可观测性与评估：追踪、日志与成本看板      | `observability.py` `evaluation.py` **`funharness_v08.py`**                                                   | `uv run chapter10/observability.py`      |
| **Chapter 12** | OpenAI Agents SDK：框架对比与实战         | `01_basic_agent.py` `02_handoff.py` `03_guardrails.py` `04_tracing.py` `05_study_group.py`                   | `uv run chapter12/01_basic_agent.py`     |

> [!tip]
> **说明**：每章中加粗的 `funharness_vXX.py` 是该章对应版本的 FunHarness 完整实现，可独立运行。最终的完整版本即本仓库的 [`funharness/`](./funharness/) 目录。

### 环境准备

1. **安装包管理器 uv**

本项目使用 [uv](https://github.com/astral-sh/uv) 作为包和环境管理器。如果你尚未安装，可以使用以下命令：

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

> 更多安装方式请参考 [uv 官方安装文档](https://docs.astral.sh/uv/getting-started/installation/)。

2. **克隆仓库与安装依赖**

```bash
# 克隆仓库
git clone https://github.com/your-org/agent-book-code.git
cd agent-book-code

# 安装依赖（使用 uv）
uv sync

# 配置环境变量
cp .env.example .env
# 编辑 .env，填入 OPENAI_API_KEY 和 OPENAI_MODEL_NAME
```

`.env` 最小配置：

```bash
OPENAI_API_KEY=your_api_key_here
OPENAI_MODEL_NAME=deepseek-v4-flash # 可选：使用自定义 API base（兼容 OpenAI 格式的任意服务商）
OPENAI_BASE_URL=https://api.deepseek.com/v1
```

运行任意章节示例：

```bash
uv run chapter01/first_chat.py
uv run chapter04/tool_registry.py
uv run chapter09/multi_agent.py
```


## 本书出版计划

本书目前正在计划出版中。等正式出版后，会将电子版完整书籍上传到本仓库并开放分享，方便大家结合章节代码一起阅读、运行和实践。

如果你正在关注 AI Agent、编程助手或类 “Claw” Agent 的实现方式，可以先从本仓库的代码和 FunHarness 项目开始体验，也欢迎持续关注后续正式书稿。

<div align="center"><img src="assets/chinese-divider-transparent-cropped.png" width="80%" /></div>

---

## FunHarness —— 本书的配套项目

<div align="center">

</div>

<div align="center">
<img src="assets/funharness_logo.png" alt="FunHarness Logo" width="350" />

[![Version](https://img.shields.io/badge/FunHarness-v1.0-D4A017?style=for-the-badge&logoColor=white)](#) [![TUI](https://img.shields.io/badge/Interface-Textual%20TUI-14532d?style=for-the-badge&logo=gnometerminal&logoColor=white)](https://github.com/Textualize/textual) [![Feishu](https://img.shields.io/badge/Channel-Feishu%20Bot-00B4AB?style=for-the-badge&logoColor=white)](./docs/feishu_channel.zh.md) [![Tools](https://img.shields.io/badge/Built--in%20Tools-30%2B-6366F1?style=for-the-badge)](#工具系统) [![Modes](https://img.shields.io/badge/Permission%20Modes-auto%20%7C%20suggest%20%7C%20approve-EF4444?style=for-the-badge)](#权限系统)

</div>

FunHarness 是本书的**核心实战项目**，也是贯穿全书的主线。它是一个用于教学、试验和二次改造的 **minimal AI Agent 产品**：足够小，小到读者可以读完关键代码；足够完整，完整到它真的能在本地运行、调用工具、管理上下文、保存记忆、追踪任务，并通过 TUI 或机器人通道与用户交互。

它对标的是 Claude Code、OpenHarness 这一类 AI 编程助手的核心思想，但不是把复杂系统直接搬给读者。FunHarness 把一个 AI Agent 产品拆成可学习的工程组件：Agent Loop 负责推理与推进，ToolRegistry 负责能力边界，Context 管理负责长对话，Permission 负责安全约束，Hooks/Middleware 负责扩展，可观测性负责调试和复盘。读者能清楚看到一个“会做事”的 Agent 是怎样从几十行代码长出来的。

更重要的是，FunHarness 是本书内容的**升华和进一步实战**。前面的章节讲原理、拆机制、写小例子；FunHarness 则把这些知识重新装配成一个可用产品。它既可以作为课程演示项目，也可以作为个人试验台，还可以作为读者打造自己类 **“Claw” AI Agent** 的起点。

> [!tip]
> **FunHarness = 一个你自己能读懂、改得动的 Claude Code**
> - 一个面向学习和实验的 mini Claude Code / OpenClaw 风格 AI Agent。
> - 它不追求功能最全，而是追求每一行代码都能讲清楚、每一个能力都能被复刻。这正是“从零构建”的意义。
> - 它有 TUI，有工具调用，有权限，有上下文压缩，有记忆，有任务系统，有可观测性，还有飞书机器人通道。更重要的是，这些能力不是黑盒塞给你的，而是沿着本书章节逐步演化出来的。读者可以跟着书一起从 `funharness_v01.py` 写到完整的 `funharness/`，最终打造出属于自己的类 “Claw” AI Agent。

### 为什么说它适合教学和试验

| 特点             | 说明                                                                                                                 |
| ---------------- | -------------------------------------------------------------------------------------------------------------------- |
| **教学优先**     | 每个模块都尽量直接、清晰、可读，适合学习 agent 基础设施                                                              |
| **配置简单**     | 只需要 Python、uv 和一个兼容 OpenAI 格式的模型配置，本地即可启动，不需要复杂服务编排                                 |
| **结构克制**     | 不引入庞大的 Agent 框架，核心模块边界清楚，适合逐文件阅读                                                            |
| **版本可对照**   | `funharness_v01` 到 `funharness_v08` 展示了从最小 Loop 到完整产品的演进路径,每个版本都对应书中一个阶段，方便对比学习 |
| **能力可拆装**   | 工具、记忆、技能、权限、Hooks、任务、可观测性都可以单独理解和替换                                                    |
| **真实可运行**   | 不是伪代码或玩具 demo，而是一个能处理本地文件、命令、任务和会话的 mini 产品                                          |
| **类 Claw 体验** | 支持本地工具执行、文件修改、命令运行、外部聊天入口                                                                   |
| **适合再创造**   | 读者可以保留核心框架，替换工具、提示词、权限策略和交互界面，做出自己的 Agent                                         |

### 与本书的关系

FunHarness 在全书中**分 8 个版本逐步演化**：

```
Chapter 02  funharness_v01  最小 Agent Loop（无工具）
Chapter 04  funharness_v02  + 工具注册与调用
Chapter 05  funharness_v03  + 上下文管理与系统提示
Chapter 06  funharness_v04  + 记忆、技能、会话持久化
Chapter 07  funharness_v05  + 权限系统（三模式）
Chapter 08  funharness_v06  + Hooks / Middleware / 插件
Chapter 09  funharness_v07  + 任务规划与多智能体
Chapter 10  funharness_v08  + 可观测性、追踪、成本看板
              |
              v
funharness/   完整生产版本（TUI + 飞书通道）
```

读者可以将每章的 `funharness_vXX.py` 与下一章的版本对比，精确看到每个特性被加入的那一刻。

本书前半部分是在拆解 Agent 的“零件”，FunHarness 则是在带读者完成一次整机装配。当你理解了每个版本为什么变化，就不只是会运行 FunHarness，而是具备了继续扩展它、裁剪它、重写它的能力。

---

### 功能更新

> [!NOTE] 
> - **2026-04-29 —— 补充Agent Teams：**
>   - 本次更新补齐了 `FunHarness` 的 **Agent Teams / SubAgent、持久任务系统、后台运行时与定时调度** 能力，让它从单一对话助手升级为可以分工、排期、后台执行的 mini Agent 工作台。

| 更新方向         | 新增能力                                                                                     | 常用入口                                            |
| ---------------- | -------------------------------------------------------------------------------------------- | --------------------------------------------------- |
| **Agent Teams**  | 创建长期队友、维护队友名册、写入 inbox、异步委派任务，适合把审查、实现、验证拆给不同角色处理 | `/team`、`/team create`、`/delegate`、`tool_team_*` |
| **SubAgent**     | 一次性隔离运行子 Agent，适合独立分析、交叉检查、生成候选方案，不污染主会话上下文             | `tool_subagent_run`                                 |
| **持久任务图**   | 任务支持 `owner`、依赖关系、ready 判断、状态流转、备注与进度落盘，完成任务后自动解锁后续任务 | `/plan`、`/task`、`/tasks`、`/next`、`/done`        |
| **后台运行时**   | 慢命令可以进入后台槽位运行，完整输出保存到 `.funharness/runtime/`，完成后通过事件回到主循环  | `/bg run`、`/bg output`、`tool_runtime_*`           |
| **定时调度**     | 支持未来时间、`in 10m` 和简化 cron，将 prompt 安排到指定时间触发                             | `/schedule create`、`/schedule`、`tool_schedule_*`  |
| **TUI 状态展示** | 底部状态栏同步展示 Team、后台任务、调度数量；并修复并行同名工具调用的预览串线问题            | TUI 状态栏、工具流式预览                            |
| **安全与稳定性** | 修复工作区路径逃逸、POSIX 进程组超时清理、跨会话任务状态串扰、JSON 预览失效等问题            | 权限系统、Sandbox、TUI                              |

新增功能保持 mini 项目的实现风格：核心状态都落在 `.funharness/` 下，命令行、TUI 与工具调用共享同一套能力，方便学习、调试和继续裁剪扩展。

---

### 核心功能

| 功能模块                  | 说明                                                                                                                      |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| **Agent Loop**            | 流式响应、工具调用、迭代循环、中断支持，最多 100 轮自动推进                                                               |
| **工具系统**              | 装饰器注册，自动从 docstring 生成 OpenAI Function Calling schema                                                          |
| **内置工具 (30+)**        | 文件读/写/替换、Shell 命令、目录列表、正则搜索、网页抓取、网页搜索、记忆读写、任务管理、后台运行、定时调度、多 Agent 协作 |
| **上下文管理**            | Token 估算、自动 compact、工具结果截断，防止 context 爆炸                                                                 |
| **持久记忆**              | `.funharness/MEMORY.md` 跨会话记忆，支持关键词搜索与追加                                                                  |
| **技能系统**              | `skills/<name>/SKILL.md` 自动发现，注入系统提示，`/skills` 查看                                                           |
| **会话管理**              | 自动保存/恢复对话，`.funharness/sessions/` 存储历史                                                                       |
| **权限系统**              | `auto` / `suggest` / `approve` 三档，读写操作分类，`Shift+Tab` 实时切换                                                   |
| **审批流**                | `approve` 模式下弹出审批 UI，Allow / Deny 交互按钮                                                                        |
| **持久任务图**            | `/plan <需求>` 生成任务图，支持依赖、owner、ready 判断、完成后自动解锁后续任务，并写入 `PROGRESS.md`                      |
| **后台运行时**            | `tool_runtime_run` / `/bg run` 将慢命令放入后台执行槽位，完整输出落盘，摘要通过事件队列回到主循环                         |
| **定时调度**              | `tool_schedule_create` / `/schedule create` 记录未来触发的 prompt，支持 `in 10m`、ISO 时间和简化 cron                     |
| **SubAgent / Agent Team** | `tool_subagent_run` 适合一次性隔离分析；`tool_team_create` / `/delegate` 支持长期队友、inbox 和异步委派                   |
| **Hooks / Middleware**    | Pre/Post Tool 钩子、中间件链，无侵入地扩展 agent 行为                                                                     |
| **可观测性**              | 结构化日志、Span 追踪、成本看板、失败模式分析、一键导出                                                                   |
| **TUI 界面**              | 基于 Textual 的终端 UI，流式渲染、Markdown 展示、工具块边框                                                               |
| **飞书机器人**            | 长连接模式，无需公网地址，私聊/群聊均可触发                                                                               |


---

### 目录结构

```
funharness/
└── src/
    ├── agent.py             # 核心 Agent 类（FunHarnessAgent）
    ├── __main__.py          # 入口：fh 命令 / fh feishu 命令
    ├── core/
    │   ├── tools.py         # ToolRegistry + 30+ 个内置工具
    │   ├── llm.py           # LLM 调用、流式处理、重试
    │   ├── context.py       # Token 估算、compact、截断
    │   ├── memory.py        # 持久记忆读写
    │   ├── skills.py        # Skill 自动发现与摘要
    │   ├── session.py       # 会话序列化与管理
    │   ├── permissions.py   # 三模式权限 + 审批流 + Sandbox
    │   ├── hooks.py         # Pre/Post Tool 钩子系统
    │   ├── tasks.py         # 持久任务图、依赖、owner、进度追踪、Git 提交
    │   ├── runtime.py       # 后台运行时任务、通知队列、输出落盘
    │   ├── schedule.py      # 定时调度记录、未来触发、调度通知
    │   ├── team.py          # SubAgent、持久队友、名册、inbox、委派
    │   ├── observability.py # 追踪、日志、成本看板、失败分析
    │   └── system_prompt.py # 分层系统提示构建
    ├── tui/
    │   ├── app.py           # Textual TUI 主应用
    │   ├── banner.py        # ASCII art 金色渐变 banner
    │   └── theme.py         # 颜色主题
    └── channels/
        └── feishu.py        # 飞书长连接机器人通道

defaultspace/                # Agent 默认工作区（fh 启动后 chdir 至此）
└── .funharness/
    ├── MEMORY.md            # 持久记忆文件
    ├── tasks.json           # 持久任务图
    ├── runtime/             # 后台运行时任务状态与完整输出
    ├── schedules.json       # 定时调度记录
    ├── team/                # 队友名册、inbox 与执行历史
    ├── sessions/            # 历史会话存储
    └── skills/              # 技能目录（每个子目录一个 skill）
        ├── karpathy-guidelines/SKILL.md
        └── ppt-skill/SKILL.md
```

---

### 安装与启动

**前置条件**：Python 3.12+、[uv](https://github.com/astral-sh/uv)

```bash
# 1. 克隆并安装
git clone https://github.com/your-org/agent-book-code.git
cd agent-book-code
uv sync

# 2. 配置环境变量
# 编辑 .env，至少填入 OPENAI_API_KEY 和 OPENAI_MODEL_NAME

# 3. 启动 TUI 界面
uv run fh
```

启动后进入全交互式 TUI。

#### 权限模式

| 模式               | 读文件 / 搜索 | 写文件 / Shell | 适用场景             |
| ------------------ | :-----------: | :------------: | -------------------- |
| `auto`             |   自动执行    |    自动执行    | 完全信任的本地工作区 |
| `suggest` *(默认)* |   自动执行    |   **需审批**   | 日常开发推荐         |
| `approve`          |  **需审批**   |   **需审批**   | 敏感或共享环境       |

TUI 内按 `Shift+Tab` 可循环切换权限模式，当前模式持续显示在输入框下方。

---

### 运行截图

<div align="center">

**TUI 主界面**

<img src="assets/Funharness_UI.png" alt="FunHarness TUI Main" width="780" />

</div>

<br/>

<div align="center">

|                          工具调用过程                          |                          任务执行状态                          |
| :------------------------------------------------------------: | :------------------------------------------------------------: |
| <img src="assets/funharness_runtime_page_1.png" width="450" /> | <img src="assets/funharness_runtime_page_2.png" width="450" /> |

</div>

---

### 斜杠命令参考

在 TUI 输入框中输入以下命令可控制 agent 行为：

| 命令                                                        | 说明                                             |
| ----------------------------------------------------------- | ------------------------------------------------ |
| `/help`                                                     | 显示所有可用命令                                 |
| `/new`                                                      | 开始新会话（自动保存当前会话）                   |
| `/cost`                                                     | 显示当前 Token 用量与估算费用                    |
| `/context`                                                  | 显示上下文窗口状态（tokens / 消息数）            |
| `/save`                                                     | 手动保存当前会话                                 |
| `/memory`                                                   | 查看持久记忆内容                                 |
| `/mode [auto\|suggest\|approve]`                            | 查看或切换权限模式                               |
| `/perms`                                                    | 查看路径权限配置                                 |
| `/skills`                                                   | 列出已发现的 Skills 及摘要                       |
| `/hooks`                                                    | 列出已注册的 Hooks                               |
| `/middleware`                                               | 列出中间件链                                     |
| `/plan <需求描述>`                                          | 生成 JSON 任务列表并保存                         |
| `/task create <标题>`                                       | 手动创建一条持久任务                             |
| `/task get <T1>`                                            | 查看单条任务的完整 JSON 状态                     |
| `/task update <T1> status=<状态> owner=<成员> notes=<备注>` | 更新任务状态、负责人或备注                       |
| `/tasks`                                                    | 查看当前任务列表与完成进度                       |
| `/next`                                                     | 获取下一个待执行任务详情                         |
| `/done <T1> [files]`                                        | 标记任务完成并记录产出文件                       |
| `/progress`                                                 | 查看进度追踪文件                                 |
| `/team`                                                     | 查看当前 Agent Team 名册                         |
| `/team create <name> <role> [instructions]`                 | 创建或更新一个长期队友                           |
| `/team inbox <name>`                                        | 查看队友 inbox                                   |
| `/delegate <name> <任务描述>`                               | 将工作异步委派给某个队友                         |
| `/bg`                                                       | 查看后台 runtime task 状态                       |
| `/bg run <command>`                                         | 在后台运行慢命令，主循环可继续工作               |
| `/bg output <runtime_id>`                                   | 查看后台任务完整输出                             |
| `/schedule`                                                 | 查看定时调度列表                                 |
| `/schedule create <name> <when> <prompt>`                   | 创建定时 prompt，例如 `in 10m` 或 `*/5 * * * *`  |
| `/schedule delete <job_id>`                                 | 删除调度记录                                     |
| `/events`                                                   | 手动拉取 runtime / schedule 事件                 |
| `/trace`                                                    | 显示 Span 追踪时间线与汇总                       |
| `/logs`                                                     | 显示最近 15 条结构化日志                         |
| `/dashboard`                                                | 显示成本与 Token 看板                            |
| `/failures`                                                 | 分析失败模式（循环/错误/重复调用）               |
| `/export`                                                   | 导出追踪、日志和成本数据到 `.funharness/traces/` |
| `clear` or `Ctrl+L`                                         | 清空对话（保存当前会话后重置）                   |
| `Ctrl+Z`                                                    | 中断当前会话                                     |
| `quit` or `Ctrl+C`                                          | 退出 FunHarness                                  |

#### 示例：用 Agent Team 完成一个小练习

**给学生演示如何做一个待办清单 CLI 小项目**。你可以把下面几行直接输入到 FunHarness 的 TUI 里，观察任务如何被拆分、委派、后台执行和定时提醒。

```text
# 第 1 步：让主 Agent 先把练习拆成可执行任务
/plan 做一个 todo.py 命令行工具，支持添加待办、列出待办、标记完成，并补充简单测试

# 第 2 步：查看任务板。读者可以看到每个任务的编号、状态和负责人
/tasks

# 第 3 步：创建一个“代码助教”，专门检查代码是否容易讲清楚
/team create teacher 教学助教 关注代码是否适合初学者理解，指出命名、结构和注释是否清楚

# 第 4 步：把审查工作交给这个助教。主 Agent 仍然可以继续和你对话
/delegate teacher 检查当前 todo.py 练习方案是否适合课堂讲解，并给出改进建议

# 第 5 步：查看后台任务。委派给队友的工作会显示在这里
/bg

# 第 6 步：手动拉取完成事件，看看队友是否已经给出反馈
/events

# 第 7 步：如果项目里有测试，可以把测试放到后台跑
/bg run pytest -q

# 第 8 步：再次查看后台列表。复制其中显示的运行编号，再看完整输出
/bg
# 下面的 run_ab12cd34 是示例编号；实际使用时换成 /bg 里显示的编号
/bg output run_ab12cd34

# 第 9 步：安排一个提醒。10 分钟后，FunHarness 会提醒你回头检查测试和队友反馈
/schedule create 课后检查 in 10m 检查 todo.py 的测试结果和教学助教反馈
/schedule
```

这组命令背后的含义可以这样理解：

- **`/plan` 和 `/tasks`**：把一个模糊目标变成任务板，适合教学时展示“Agent 如何拆任务”。
- **`/team create` 和 `/delegate`**：创建一个有明确职责的队友，再把某件事交给它异步处理。
- **`/bg run` 和 `/bg output`**：把测试、构建这类耗时命令放到后台，不阻塞当前对话。
- **`/schedule create`**：给未来某个时间点安排提醒或复查，适合课堂演示“Agent 可以持续跟进任务”。

只需要先记住一个顺序：**先 `/plan` 拆任务，再 `/team create` 建队友，然后 `/delegate` 分工，最后用 `/bg` 和 `/schedule` 跟踪执行情况**。

<div align="center"><img src="assets/chinese-divider-transparent-cropped.png" width="90%" /></div>

## 飞书机器人通道

[![Feishu](https://img.shields.io/badge/飞书-长连接模式-00B4AB?style=for-the-badge&logo=lark&logoColor=white)](./docs/feishu_channel.zh.md)  [![Permission](https://img.shields.io/badge/权限模式-suggest%20推荐%20%7Cauto%20%7Capprove-orange?style=for-the-badge)](#)

FunHarness 也支持以**飞书机器人**作为输入通道：用户在飞书里给机器人发消息，本地 FunHarness 负责运行 Agent、调用工具，并把结果回发到飞书。

这一部分主要展示 FunHarness 的通道扩展能力：同一个 Agent Core 不只可以接 TUI，也可以接 IM 机器人。飞书通道采用长连接模式，本地程序主动连接飞书开放平台接收事件，**不需要公网地址**，适合教学演示和内网试验。

> 详细配置、事件订阅和常见问题见：[点击查看详细配置](./docs/feishu_channel.zh.md)

```
飞书消息 -> 长连接事件 -> 本地 FunHarness -> Agent Loop + Tools -> 回复飞书
```

### 快速启动

```bash
# 1. 在飞书开放平台创建自建应用，开启机器人能力，添加 im.message.receive_v1 事件
# 2. 配置 .env
```

`.env` 飞书相关配置：

```bash
OPENAI_API_KEY=your_api_key
OPENAI_MODEL_NAME=deepseek-v3

FEISHU_APP_ID=cli_xxx               # 飞书应用 App ID
FEISHU_APP_SECRET=xxx               # 飞书应用 App Secret
FEISHU_EVENT_MODE=ws                # 长连接模式（不需要公网）
FEISHU_PERMISSION_MODE=suggest      # 推荐先用 suggest 模式
```

| 配置项                   | 是否必填 | 说明                                          |
| ------------------------ | :------: | --------------------------------------------- |
| `FEISHU_APP_ID`          |   必填   | 飞书应用的 App ID，通常以 `cli_` 开头         |
| `FEISHU_APP_SECRET`      |   必填   | 飞书应用的 App Secret                         |
| `FEISHU_EVENT_MODE`      |   推荐   | `ws` 表示长连接，不需要公网地址               |
| `FEISHU_PERMISSION_MODE` |   推荐   | 建议先用 `suggest`，更安全                    |
| `OPENAI_API_KEY`         |   必填   | FunHarness 调用模型需要                       |
| `OPENAI_MODEL_NAME`      |   推荐   | 例如 `deepseek-v4-flash` 或 `deepseek-v4-pro` |

```bash
# 3. 启动通道
uv run fh feishu
```

常用消息：

```
帮我查看当前项目结构
@FunHarness 帮我阅读 README 并总结
/help        查看所有命令
/interrupt   中断当前任务
```

### 飞书运行截图

<div align="center">

|                   消息触发与工具调用                   |                   Agent 回复与结果                    |
| :----------------------------------------------------: | :---------------------------------------------------: |
| <img src="assets/feishu_run_page_1.jpg" width="200" /> | <img src="assets/feishu_runpage_2.jpg" width="200" /> |

</div>

> **当前限制**：飞书通道目前只处理文本消息；`suggest` 模式下高风险操作会被拒绝（暂不支持远程交互式 approval）；更多说明见 [docs/feishu_channel.zh.md](./docs/feishu_channel.zh.md)。

<div align="center"><img src="assets/chinese-divider-transparent-cropped.png" width="80%" /></div>

## 依赖说明

| 依赖              | 版本     | 用途                                       |
| ----------------- | -------- | ------------------------------------------ |
| `openai`          | >=2.32.0 | LLM API 调用（兼容任意 OpenAI 格式服务商） |
| `textual[syntax]` | >=8.2.4  | TUI 界面框架                               |
| `lark-oapi`       | >=1.4.23 | 飞书机器人 SDK                             |
| `python-dotenv`   | >=1.2.2  | `.env` 配置加载                            |


所有依赖通过 `uv sync` 自动安装，无需手动 pip install。

---

## 许可证

本项目采用 [MIT License](./LICENSE) 开源协议。

---

## 致谢

本书与 FunHarness 项目在设计上参考了以下优秀开源项目，特此致谢：

- [learn-claude-code](https://github.com/shareAI-lab/learn-claude-code) — Claude Code 学习资源，启发了本书的工具系统设计
- [OpenHarness](https://github.com/HKUDS/OpenHarness) — FunHarness 的命名与架构灵感来源
- [hermes agent](https://github.com/nousresearch/hermes-agent) — 多 Agent 协作模式的参考实现
- [Pi coding-agent](https://github.com/badlogic/pi-mono/tree/main/packages/coding-agent) —  agent 工程实现的重要参考来源

---

<div align="center">

**如果这本书或这个项目对你有帮助，欢迎点个 Star**

<a href="https://github.com/hyyhf/agent-book-code"><img src="https://img.shields.io/github/stars/hyyhf/agent-book-code?style=for-the-badge&color=DDB88B&logo=github" alt="GitHub stars"></a>

*Made with love for everyone who wants to understand AI agents from the ground up.*

</div>
