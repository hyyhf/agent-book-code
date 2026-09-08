# FunHarness Studio v0.9.0

FunHarness Studio v0.9.0 重磅推出**概念桌面 (Concept Desk)** 功能，将概念工坊 (Concept Workshop) 升级为 macOS 空间桌面风格的沉淀与工作区交互体验。同时优化了多任务 Stage Manager 调度、实时 Agent 推演脉络展示以及跨模块成果回执通知。

## 主要改进

### 概念桌面 (Concept Desk) 与空间工作区

- **空间桌面交互**：新增“概念桌面”原生工作区，包含竹韵纸纹主视口、动态 Agent 运行状态、桌面快捷文件夹与空间 Dock 导航。
- **Stage Manager 任务轨道**：右侧集成 Stage Manager 任务调度面板，聚合概念生长 (Growth)、认知冲刺 (Sprint)、概念产物 (Concept HTML)、互动沙盘 (Sandbox)、概念进化 (Evolution)、概念宇宙 (Universe) 与概念档案 (Archive) 7 大子功能模块，实现多维空间高效调度。
- **实时推演脉络与信息流转**：主视口集中聚焦当前任务，实时展示 Agent 的核心阶段、事件轨道（Event Rail）、流式输出预览及信息流（Information Handoff）流向。
- **成果回执 (Completion Toast)**：后台任务与模块演进完成后，自动弹出成果回执通知卡片，显示耗时、关键步骤与回执状态，支持一键无缝跳转至目标成果。
- **多空间视图自由切换**：支持在“概念桌面概览”、“学习空间”、“探索空间”、“归档空间”及“概念库”之间自由切换，提供沉浸式认知与沉淀体验。

### 性能与构建打包

- **资源集成**：优化概念桌面静态资源（如背景纹理与图标）的集成与路径解析。
- **打包稳定性**：更新打包构建脚本，确保 Windows 便携版及安装包内各模块运行稳定。

## 下载

- `FunHarness Studio Setup 0.9.0.exe`：Windows x64 安装版。
- `FunHarness Studio v0.9.0 win-unpacked.zip`：Windows x64 免安装版（如 Release 同时提供）。

升级前请先退出正在运行的 FunHarness Studio。安装版和免安装版会读取相同的用户配置与工作区数据，请勿同时运行两个版本。

## 说明

- 概念桌面保持与现有 Agent Core 及 `.funharness/` 状态存储完全兼容，历史会话与概念档案均可无缝加载。
- 更多概念工坊与概念桌面的使用细节，请参阅相关说明文档。
