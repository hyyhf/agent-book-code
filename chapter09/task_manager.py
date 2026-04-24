"""
9.1 - 长时运行 Agent 的 Harness 策略

实现增量式任务管理:
- TaskStatus / Task: 任务状态与数据结构
- TaskList: 特性清单的加载、保存、查询
- ProgressTracker: 进度文件(PROGRESS.md)的生成与更新
- GitTracker: 任务完成后自动 Git 提交
- plan_tasks: 初始化 Agent 负责拆解需求, 生成特性清单
- pick_next_task: 编码 Agent 从清单中领取下一个任务

运行方式:
    uv run python chapter09/task_manager.py
"""
import json
import os
import subprocess
import time
from datetime import datetime
from enum import Enum
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

client = OpenAI()
MODEL = os.getenv("OPENAI_MODEL_NAME", "deepseek-v4-flash")


# =============================================================
#  9.1.1 任务状态与数据结构
# =============================================================

class TaskStatus(Enum):
    """任务在生命周期中的状态。"""
    PENDING = "pending"          # 等待执行
    IN_PROGRESS = "in_progress"  # 正在执行
    DONE = "done"                # 已完成
    FAILED = "failed"            # 执行失败
    SKIPPED = "skipped"          # 被跳过


class Task:
    """一个可被 Agent 执行的原子任务。

    每个任务是特性清单中的一项, 包含标题、描述、验证标准,
    以及执行过程中产生的工件(修改的文件列表)和日志。
    """

    def __init__(
        self,
        task_id: str,
        title: str,
        description: str = "",
        verify: str = "",
        depends_on: list[str] | None = None,
    ):
        self.task_id = task_id
        self.title = title
        self.description = description
        self.verify = verify                    # 验证标准
        self.depends_on = depends_on or []      # 依赖的前置任务 ID
        self.status = TaskStatus.PENDING
        self.artifacts: list[str] = []          # 产出的文件路径
        self.error: str = ""                    # 失败原因
        self.started_at: str = ""
        self.finished_at: str = ""

    def start(self):
        self.status = TaskStatus.IN_PROGRESS
        self.started_at = datetime.now().isoformat(timespec="seconds")

    def complete(self, artifacts: list[str] | None = None):
        self.status = TaskStatus.DONE
        self.finished_at = datetime.now().isoformat(timespec="seconds")
        if artifacts:
            self.artifacts.extend(artifacts)

    def fail(self, error: str):
        self.status = TaskStatus.FAILED
        self.finished_at = datetime.now().isoformat(timespec="seconds")
        self.error = error

    def skip(self, reason: str = ""):
        self.status = TaskStatus.SKIPPED
        self.error = reason

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "description": self.description,
            "verify": self.verify,
            "depends_on": self.depends_on,
            "status": self.status.value,
            "artifacts": self.artifacts,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Task":
        task = cls(
            task_id=data["task_id"],
            title=data["title"],
            description=data.get("description", ""),
            verify=data.get("verify", ""),
            depends_on=data.get("depends_on", []),
        )
        task.status = TaskStatus(data.get("status", "pending"))
        task.artifacts = data.get("artifacts", [])
        task.error = data.get("error", "")
        task.started_at = data.get("started_at", "")
        task.finished_at = data.get("finished_at", "")
        return task

    def __repr__(self):
        icon = {
            TaskStatus.PENDING: "[ ]",
            TaskStatus.IN_PROGRESS: "[~]",
            TaskStatus.DONE: "[x]",
            TaskStatus.FAILED: "[!]",
            TaskStatus.SKIPPED: "[-]",
        }[self.status]
        return f"{icon} {self.task_id}: {self.title}"


# =============================================================
#  9.1.2 特性清单(TaskList)
# =============================================================

class TaskList:
    """特性清单: 管理一组有序的任务。

    任务清单是长时运行 Agent 的核心驱动机制。
    初始化 Agent 分析需求后生成清单, 编码 Agent 按序逐条执行。
    清单支持 JSON 序列化, 可在多次会话间持久化。
    """

    def __init__(self, project_name: str = ""):
        self.project_name = project_name
        self.tasks: list[Task] = []
        self.created_at = datetime.now().isoformat(timespec="seconds")

    def add(self, task: Task):
        """添加一个任务到清单末尾。"""
        self.tasks.append(task)

    def get(self, task_id: str) -> Task | None:
        """根据 ID 查找任务。"""
        for t in self.tasks:
            if t.task_id == task_id:
                return t
        return None

    def next_pending(self) -> Task | None:
        """获取下一个可执行的任务。

        跳过依赖尚未完成的任务。
        """
        done_ids = {t.task_id for t in self.tasks if t.status == TaskStatus.DONE}
        for t in self.tasks:
            if t.status != TaskStatus.PENDING:
                continue
            # 检查依赖是否全部完成
            if all(dep in done_ids for dep in t.depends_on):
                return t
        return None

    @property
    def progress(self) -> tuple[int, int]:
        """返回 (已完成数, 总数)。"""
        done = sum(1 for t in self.tasks
                   if t.status in (TaskStatus.DONE, TaskStatus.SKIPPED))
        return done, len(self.tasks)

    @property
    def progress_pct(self) -> float:
        done, total = self.progress
        return (done / total * 100) if total > 0 else 0.0

    def summary(self) -> str:
        """生成人类可读的清单摘要。"""
        done, total = self.progress
        lines = [f"Project: {self.project_name} ({done}/{total} tasks done)"]
        for t in self.tasks:
            lines.append(f"  {t}")
        return "\n".join(lines)

    def save(self, path: str | Path):
        """将清单保存为 JSON 文件。"""
        data = {
            "project_name": self.project_name,
            "created_at": self.created_at,
            "tasks": [t.to_dict() for t in self.tasks],
        }
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "TaskList":
        """从 JSON 文件加载清单。"""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        tl = cls(project_name=data.get("project_name", ""))
        tl.created_at = data.get("created_at", "")
        for td in data.get("tasks", []):
            tl.tasks.append(Task.from_dict(td))
        return tl


# =============================================================
#  9.1.3 进度文件(PROGRESS.md)
# =============================================================

class ProgressTracker:
    """自动生成和更新 PROGRESS.md 进度文件。

    进度文件是长时运行 Agent 的"记忆锚点":
    当上下文窗口被压缩后, Agent 可以读取 PROGRESS.md
    快速恢复对当前工作状态的认知。
    """

    def __init__(self, project_dir: str | Path = "."):
        self.project_dir = Path(project_dir)
        self.progress_file = self.project_dir / "PROGRESS.md"

    def update(self, task_list: TaskList):
        """根据任务清单更新 PROGRESS.md。"""
        done, total = task_list.progress
        lines = [
            f"# {task_list.project_name} - Progress",
            f"",
            f"Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"Progress: {done}/{total} ({task_list.progress_pct:.0f}%)",
            f"",
        ]

        # 按状态分组展示
        for status, label in [
            (TaskStatus.DONE, "Completed"),
            (TaskStatus.IN_PROGRESS, "In Progress"),
            (TaskStatus.FAILED, "Failed"),
            (TaskStatus.PENDING, "Pending"),
            (TaskStatus.SKIPPED, "Skipped"),
        ]:
            group = [t for t in task_list.tasks if t.status == status]
            if not group:
                continue
            lines.append(f"## {label}")
            for t in group:
                if status == TaskStatus.DONE:
                    files = ", ".join(t.artifacts) if t.artifacts else "no files"
                    lines.append(f"- [x] {t.task_id}: {t.title} ({files})")
                elif status == TaskStatus.FAILED:
                    lines.append(f"- [!] {t.task_id}: {t.title} - Error: {t.error}")
                elif status == TaskStatus.IN_PROGRESS:
                    lines.append(f"- [~] {t.task_id}: {t.title}")
                else:
                    lines.append(f"- [ ] {t.task_id}: {t.title}")
            lines.append("")

        self.progress_file.write_text("\n".join(lines), encoding="utf-8")
        return str(self.progress_file)

    def read(self) -> str:
        """读取当前进度文件内容。"""
        if self.progress_file.exists():
            return self.progress_file.read_text(encoding="utf-8")
        return "(no progress file yet)"


# =============================================================
#  9.1.4 Git 集成
# =============================================================

class GitTracker:
    """在任务完成后自动创建 Git 提交。

    每完成一个任务, 自动 git add + commit, 形成细粒度的版本历史。
    这样即使 Agent 后续出错, 也能方便地回退到某个任务完成后的状态。
    """

    def __init__(self, repo_dir: str | Path = "."):
        self.repo_dir = Path(repo_dir)

    def _run_git(self, *args) -> tuple[bool, str]:
        """执行 git 命令, 返回 (是否成功, 输出)。"""
        try:
            result = subprocess.run(
                ["git"] + list(args),
                cwd=str(self.repo_dir),
                capture_output=True,
                text=True,
                timeout=15,
            )
            output = result.stdout.strip()
            if result.returncode != 0:
                return False, result.stderr.strip()
            return True, output
        except FileNotFoundError:
            return False, "git not found"
        except subprocess.TimeoutExpired:
            return False, "git command timed out"

    def is_git_repo(self) -> bool:
        """检查当前目录是否是 Git 仓库。"""
        ok, _ = self._run_git("rev-parse", "--is-inside-work-tree")
        return ok

    def init_if_needed(self) -> str:
        """如果不是 Git 仓库, 初始化一个。"""
        if self.is_git_repo():
            return "Already a git repo"
        ok, out = self._run_git("init")
        return out if ok else f"Git init failed: {out}"

    def commit_task(self, task: Task) -> str:
        """为一个已完成的任务创建 Git 提交。

        只添加任务产出的文件 + PROGRESS.md。
        """
        if not self.is_git_repo():
            return "Not a git repo, skipping commit"

        # 添加任务产出的文件
        files_to_add = list(task.artifacts)
        progress_file = self.repo_dir / "PROGRESS.md"
        if progress_file.exists():
            files_to_add.append("PROGRESS.md")

        if not files_to_add:
            return "No files to commit"

        for f in files_to_add:
            self._run_git("add", str(f))

        # 检查是否有变更
        ok, status = self._run_git("diff", "--cached", "--stat")
        if ok and not status:
            return "No changes to commit"

        msg = f"[task:{task.task_id}] {task.title}"
        ok, out = self._run_git("commit", "-m", msg)
        return out if ok else f"Commit failed: {out}"

    def get_log(self, n: int = 5) -> str:
        """获取最近 n 条提交记录。"""
        ok, out = self._run_git("log", f"-{n}", "--oneline")
        return out if ok else "(no git history)"


# =============================================================
#  9.1.2 初始化 Agent: 分析需求, 生成特性清单
# =============================================================

def plan_tasks(
    user_requirement: str,
    project_context: str = "",
    model: str = MODEL,
) -> TaskList:
    """初始化 Agent: 将用户需求拆解为有序的任务清单。

    这是"初始化 Agent 与编码 Agent 分工"模式的前半部分。
    初始化 Agent 的职责是理解需求、分析依赖、生成可执行的任务清单,
    但不执行任何具体编码。

    Args:
        user_requirement: 用户的原始需求描述
        project_context: 已有的项目上下文(目录结构、已有文件等)
        model: 使用的模型名称
    """
    prompt = f"""\
You are a senior engineer planning a coding task. Break the following requirement
into a numbered list of small, atomic tasks that can be executed sequentially.

For each task, provide:
- task_id: a short ID like "T1", "T2", etc.
- title: a one-line summary
- description: what exactly needs to be done (1-2 sentences)
- verify: how to verify this task is complete (e.g., "run tests", "file exists")
- depends_on: list of task_ids this task depends on (empty list if none)

Return ONLY a JSON array of task objects. No markdown, no explanation.

User requirement:
{user_requirement}
"""
    if project_context:
        prompt += f"\nExisting project context:\n{project_context}\n"

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "You decompose requirements into task lists. Return valid JSON only."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
    )

    raw = response.choices[0].message.content or "[]"
    # 提取 JSON (处理模型可能包裹在 ```json ``` 中的情况)
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:-1])

    try:
        tasks_data = json.loads(raw)
    except json.JSONDecodeError:
        # 兜底: 创建单个任务
        tasks_data = [{
            "task_id": "T1",
            "title": user_requirement[:60],
            "description": user_requirement,
            "verify": "manual review",
            "depends_on": [],
        }]

    task_list = TaskList(project_name=user_requirement[:40])
    for td in tasks_data:
        task_list.add(Task(
            task_id=td.get("task_id", f"T{len(task_list.tasks)+1}"),
            title=td.get("title", ""),
            description=td.get("description", ""),
            verify=td.get("verify", ""),
            depends_on=td.get("depends_on", []),
        ))

    return task_list


def pick_next_task(task_list: TaskList) -> Task | None:
    """编码 Agent 从清单中领取下一个可执行的任务。

    这是"初始化 Agent 与编码 Agent 分工"模式的后半部分。
    编码 Agent 不需要理解全局需求, 只需要拿到当前任务的描述和验证标准。
    """
    return task_list.next_pending()


def format_task_for_agent(task: Task) -> str:
    """将任务格式化为 Agent 可理解的指令。"""
    parts = [
        f"## Current Task: {task.task_id} - {task.title}",
        f"",
        f"**Description:** {task.description}",
    ]
    if task.verify:
        parts.append(f"**Verification:** {task.verify}")
    if task.depends_on:
        parts.append(f"**Depends on:** {', '.join(task.depends_on)}")
    parts.append(f"\nComplete this task, then report what files you created or modified.")
    return "\n".join(parts)


# =============================================================
#  演示
# =============================================================

if __name__ == "__main__":
    import tempfile

    print("=== 增量式任务管理演示 ===\n")

    # 1. 手动创建任务清单
    print("--- 手动创建任务清单 ---")
    tl = TaskList(project_name="Todo App")
    tl.add(Task("T1", "Initialize project structure",
                description="Create package.json and src/ directory",
                verify="package.json exists"))
    tl.add(Task("T2", "Implement data model",
                description="Create Todo class with CRUD operations",
                verify="run unit tests",
                depends_on=["T1"]))
    tl.add(Task("T3", "Build CLI interface",
                description="Create command-line interface for the app",
                verify="run: python todo.py --help",
                depends_on=["T2"]))
    tl.add(Task("T4", "Add persistence",
                description="Save todos to a JSON file",
                verify="create and reload todos",
                depends_on=["T2"]))

    print(tl.summary())

    # 2. 模拟执行流程
    print("\n--- 模拟执行流程 ---")
    # 领取第一个任务
    task = pick_next_task(tl)
    print(f"  Next task: {task}")
    print(f"  Agent instruction:\n{format_task_for_agent(task)}\n")

    # 模拟完成 T1
    task.start()
    print(f"  Started: {task}")
    task.complete(artifacts=["package.json", "src/"])
    print(f"  Completed: {task}")

    # 领取下一个(T2, 因为 T1 已完成, T2 的依赖满足)
    task = pick_next_task(tl)
    print(f"  Next task: {task}")
    task.start()
    task.complete(artifacts=["src/todo.py", "tests/test_todo.py"])

    # T3 和 T4 都可以执行了(都只依赖 T2)
    task = pick_next_task(tl)
    print(f"  Next task: {task}")
    task.start()
    task.fail("CLI framework not installed")
    print(f"  Failed: {task}")

    print(f"\n  Progress: {tl.progress[0]}/{tl.progress[1]} ({tl.progress_pct:.0f}%)")

    # 3. 保存和加载
    print("\n--- 保存和加载 ---")
    with tempfile.TemporaryDirectory() as tmpdir:
        save_path = os.path.join(tmpdir, "tasks.json")
        tl.save(save_path)
        print(f"  Saved to {save_path}")

        loaded = TaskList.load(save_path)
        print(f"  Loaded: {loaded.project_name}, {len(loaded.tasks)} tasks")
        print(f"  Progress: {loaded.progress[0]}/{loaded.progress[1]}")

        # 4. 进度文件
        print("\n--- 进度文件 ---")
        tracker = ProgressTracker(tmpdir)
        tracker.update(loaded)
        print(tracker.read())

    # 5. Git 集成(仅展示 API, 不实际执行)
    print("--- Git 集成 ---")
    git = GitTracker(".")
    print(f"  Is git repo: {git.is_git_repo()}")
    if git.is_git_repo():
        print(f"  Recent commits:\n{git.get_log(3)}")

    # 6. 用模型生成任务清单(需要 API)
    print("\n--- 模型生成任务清单 ---")
    planned = plan_tasks(
        "Build a simple calculator CLI that supports add, subtract, multiply, divide"
    )
    print(planned.summary())
