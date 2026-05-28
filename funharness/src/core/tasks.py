"""
FunHarness - Durable Task Graph

Tasks describe work goals: what should be done, who owns it, and which
dependencies must finish first. Runtime execution and teammates live elsewhere.
"""
import json
import subprocess
from datetime import datetime
from enum import Enum
from pathlib import Path

from .llm import client, MODEL


class TaskStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class Task:
    def __init__(
        self,
        task_id: str,
        title: str,
        description: str = "",
        verify: str = "",
        depends_on: list[str] | None = None,
        owner: str = "",
    ):
        self.task_id = task_id
        self.title = title
        self.description = description
        self.verify = verify
        self.depends_on = depends_on or []
        self.blocks: list[str] = []
        self.owner = owner
        self.status = TaskStatus.PENDING
        self.artifacts: list[str] = []
        self.notes = ""
        self.error = ""
        self.created_at = datetime.now().isoformat(timespec="seconds")
        self.started_at = ""
        self.finished_at = ""

    @property
    def is_ready(self) -> bool:
        return self.status == TaskStatus.PENDING and not self.depends_on

    def start(self, owner: str = ""):
        self.status = TaskStatus.IN_PROGRESS
        if owner:
            self.owner = owner
        self.started_at = datetime.now().isoformat(timespec="seconds")

    def complete(self, artifacts: list[str] | None = None, notes: str = ""):
        self.status = TaskStatus.DONE
        self.finished_at = datetime.now().isoformat(timespec="seconds")
        if artifacts:
            self.artifacts.extend(a for a in artifacts if a not in self.artifacts)
        if notes:
            self.notes = notes

    def fail(self, error: str):
        self.status = TaskStatus.FAILED
        self.finished_at = datetime.now().isoformat(timespec="seconds")
        self.error = error

    def skip(self, reason: str = ""):
        self.status = TaskStatus.SKIPPED
        self.finished_at = datetime.now().isoformat(timespec="seconds")
        self.error = reason

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "description": self.description,
            "verify": self.verify,
            "depends_on": self.depends_on,
            "blocks": self.blocks,
            "owner": self.owner,
            "status": self.status.value,
            "artifacts": self.artifacts,
            "notes": self.notes,
            "error": self.error,
            "created_at": self.created_at,
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
            owner=data.get("owner", ""),
        )
        task.blocks = data.get("blocks", [])
        task.status = TaskStatus(data.get("status", "pending"))
        task.artifacts = data.get("artifacts", [])
        task.notes = data.get("notes", "")
        task.error = data.get("error", "")
        task.created_at = data.get("created_at", task.created_at)
        task.started_at = data.get("started_at", "")
        task.finished_at = data.get("finished_at", "")
        return task

    def __repr__(self):
        icon = {
            "pending": "[ ]",
            "in_progress": "[~]",
            "done": "[x]",
            "failed": "[!]",
            "skipped": "[-]",
        }[self.status.value]
        owner = f" @{self.owner}" if self.owner else ""
        deps = f" <- {','.join(self.depends_on)}" if self.depends_on else ""
        return f"{icon} {self.task_id}: {self.title}{owner}{deps}"


class TaskList:
    def __init__(self, project_name: str = ""):
        self.project_name = project_name
        self.tasks: list[Task] = []
        self.created_at = datetime.now().isoformat(timespec="seconds")

    def add(self, task: Task):
        existing = self.get(task.task_id)
        if existing:
            raise ValueError(f"Task already exists: {task.task_id}")
        self.tasks.append(task)
        self._rebuild_blocks()

    def get(self, task_id: str) -> Task | None:
        for task in self.tasks:
            if task.task_id == task_id:
                return task
        return None

    def next_id(self) -> str:
        nums = []
        for task in self.tasks:
            if task.task_id.upper().startswith("T"):
                try:
                    nums.append(int(task.task_id[1:]))
                except ValueError:
                    pass
        return f"T{(max(nums) if nums else 0) + 1}"

    def ready(self, owner: str = "") -> list[Task]:
        done = {t.task_id for t in self.tasks if t.status in (TaskStatus.DONE, TaskStatus.SKIPPED)}
        ready = []
        for task in self.tasks:
            if task.status != TaskStatus.PENDING:
                continue
            if owner and task.owner not in ("", owner):
                continue
            if all(dep in done for dep in task.depends_on):
                ready.append(task)
        return ready

    def next_pending(self, owner: str = "") -> Task | None:
        ready = self.ready(owner=owner)
        return ready[0] if ready else None

    def update(
        self,
        task_id: str,
        status: str = "",
        owner: str = "",
        notes: str = "",
        artifacts: list[str] | None = None,
        error: str = "",
    ) -> Task:
        task = self.get(task_id)
        if task is None:
            raise KeyError(task_id)
        if owner:
            task.owner = owner
        if notes:
            task.notes = notes
        if status:
            new_status = TaskStatus(status)
            if new_status == TaskStatus.IN_PROGRESS:
                task.start(owner=owner)
            elif new_status == TaskStatus.DONE:
                task.complete(artifacts=artifacts, notes=notes)
                self._unlock(task.task_id)
            elif new_status == TaskStatus.FAILED:
                task.fail(error or notes)
            elif new_status == TaskStatus.SKIPPED:
                task.skip(error or notes)
                self._unlock(task.task_id)
            else:
                task.status = new_status
        elif artifacts:
            task.artifacts.extend(a for a in artifacts if a not in task.artifacts)
        self._rebuild_blocks()
        return task

    def _unlock(self, finished_task_id: str):
        for task in self.tasks:
            if finished_task_id in task.depends_on:
                task.depends_on = [dep for dep in task.depends_on if dep != finished_task_id]

    def _rebuild_blocks(self):
        for task in self.tasks:
            task.blocks = []
        by_id = {task.task_id: task for task in self.tasks}
        for task in self.tasks:
            for dep in task.depends_on:
                if dep in by_id and task.task_id not in by_id[dep].blocks:
                    by_id[dep].blocks.append(task.task_id)

    @property
    def progress(self):
        done = sum(1 for t in self.tasks if t.status in (TaskStatus.DONE, TaskStatus.SKIPPED))
        return done, len(self.tasks)

    @property
    def progress_pct(self):
        done, total = self.progress
        return (done / total * 100) if total > 0 else 0.0

    def summary(self) -> str:
        done, total = self.progress
        lines = [f"Project: {self.project_name} ({done}/{total} tasks done, {len(self.ready())} ready)"]
        for task in self.tasks:
            lines.append(f"  {task}")
        return "\n".join(lines)

    def save(self, path):
        self._rebuild_blocks()
        data = {
            "project_name": self.project_name,
            "created_at": self.created_at,
            "tasks": [task.to_dict() for task in self.tasks],
        }
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        task_list = cls(project_name=data.get("project_name", ""))
        task_list.created_at = data.get("created_at", "")
        for td in data.get("tasks", []):
            task_list.tasks.append(Task.from_dict(td))
        task_list._rebuild_blocks()
        return task_list


class ProgressTracker:
    def __init__(self, project_dir="."):
        self.project_dir = Path(project_dir)
        self.progress_file = self.project_dir / "PROGRESS.md"

    def update(self, task_list: TaskList):
        done, total = task_list.progress
        lines = [
            f"# {task_list.project_name} - Progress",
            "",
            f"Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"Progress: {done}/{total} ({task_list.progress_pct:.0f}%)",
            "",
        ]
        groups = [
            (TaskStatus.IN_PROGRESS, "In Progress"),
            (TaskStatus.PENDING, "Pending"),
            (TaskStatus.FAILED, "Failed"),
            (TaskStatus.DONE, "Completed"),
            (TaskStatus.SKIPPED, "Skipped"),
        ]
        for status, label in groups:
            tasks = [t for t in task_list.tasks if t.status == status]
            if not tasks:
                continue
            lines.append(f"## {label}")
            for task in tasks:
                owner = f" @{task.owner}" if task.owner else ""
                deps = f" (blocked by: {', '.join(task.depends_on)})" if task.depends_on else ""
                if status == TaskStatus.DONE:
                    files = ", ".join(task.artifacts) if task.artifacts else "no files"
                    lines.append(f"- [x] {task.task_id}: {task.title}{owner} ({files})")
                elif status == TaskStatus.FAILED:
                    lines.append(f"- [!] {task.task_id}: {task.title}{owner} - Error: {task.error}")
                elif status == TaskStatus.IN_PROGRESS:
                    lines.append(f"- [~] {task.task_id}: {task.title}{owner}")
                elif status == TaskStatus.SKIPPED:
                    lines.append(f"- [-] {task.task_id}: {task.title}{owner} - {task.error}")
                else:
                    lines.append(f"- [ ] {task.task_id}: {task.title}{owner}{deps}")
            lines.append("")
        self.progress_file.write_text("\n".join(lines), encoding="utf-8")

    def read(self):
        if self.progress_file.exists():
            return self.progress_file.read_text(encoding="utf-8")
        return "(no progress file yet)"


class GitTracker:
    def __init__(self, repo_dir="."):
        self.repo_dir = Path(repo_dir)

    def _run_git(self, *args):
        try:
            result = subprocess.run(
                ["git"] + list(args), cwd=str(self.repo_dir),
                capture_output=True, text=True, timeout=15)
            return (result.returncode == 0, result.stdout.strip() or result.stderr.strip())
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False, "git unavailable"

    def is_git_repo(self):
        ok, _ = self._run_git("rev-parse", "--is-inside-work-tree")
        return ok

    def commit_task(self, task: Task):
        if not self.is_git_repo():
            return ""
        files = list(task.artifacts)
        if (self.repo_dir / "PROGRESS.md").exists():
            files.append("PROGRESS.md")
        if not files:
            return ""
        for file in files:
            self._run_git("add", str(file))
        ok, status = self._run_git("diff", "--cached", "--stat")
        if ok and not status:
            return ""
        ok, out = self._run_git("commit", "-m", f"[task:{task.task_id}] {task.title}")
        return out if ok else ""


def _parse_list(value: str | list[str] | None) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [part.strip() for part in str(value).split(",") if part.strip()]


def plan_tasks(user_requirement, model=MODEL, on_token=None,
               on_reasoning_token=None, on_reasoning_done=None,
               llm_client=None):
    prompt = f"""\
Break this requirement into small, atomic tasks. For each, provide:
task_id (T1, T2...), title, description, verify, depends_on (list of task_ids), owner (optional role/name).
Return ONLY a JSON array. No markdown.

Requirement: {user_requirement}"""

    messages = [
        {"role": "system", "content": "You decompose requirements into a durable task graph. Return valid JSON only."},
        {"role": "user", "content": prompt},
    ]
    active_client = llm_client or client
    if on_token:
        raw_parts = []
        response = active_client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.2,
            stream=True,
            reasoning_effort="high",
            extra_body={"thinking": {"type": "enabled"}},
        )
        reasoning_open = False
        reasoning_done_sent = False

        def finish_reasoning() -> None:
            nonlocal reasoning_open, reasoning_done_sent
            if reasoning_open and not reasoning_done_sent:
                if on_reasoning_done:
                    on_reasoning_done()
                reasoning_done_sent = True
            reasoning_open = False

        for chunk in response:
            if not getattr(chunk, "choices", None):
                continue
            delta = chunk.choices[0].delta
            reasoning_text = getattr(delta, "reasoning_content", None) or ""
            if reasoning_text:
                reasoning_open = True
                reasoning_done_sent = False
                if on_reasoning_token:
                    on_reasoning_token(reasoning_text)
            text = getattr(delta, "content", None) or ""
            if text:
                finish_reasoning()
                on_token(text)
                raw_parts.append(text)
        finish_reasoning()
        raw = "".join(raw_parts).strip()
    else:
        response = active_client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.2,
        )
        raw = (response.choices[0].message.content or "[]").strip()
    if raw.startswith("```"):
        raw = "\n".join(raw.split("\n")[1:-1])
    try:
        tasks_data = json.loads(raw)
    except json.JSONDecodeError:
        tasks_data = [{"task_id": "T1", "title": user_requirement[:60],
                       "description": user_requirement, "verify": "manual review",
                       "depends_on": [], "owner": ""}]

    task_list = TaskList(project_name=user_requirement[:40])
    for td in tasks_data:
        task_list.add(Task(
            td.get("task_id", f"T{len(task_list.tasks)+1}"),
            td.get("title", ""),
            td.get("description", ""),
            td.get("verify", ""),
            _parse_list(td.get("depends_on", [])),
            td.get("owner", ""),
        ))
    return task_list


def pick_next_task(task_list, owner: str = ""):
    return task_list.next_pending(owner=owner)


def format_task_for_agent(task):
    parts = [f"## Current Task: {task.task_id} - {task.title}", "",
             f"**Description:** {task.description or task.title}"]
    if task.owner:
        parts.append(f"**Owner:** {task.owner}")
    if task.verify:
        parts.append(f"**Verification:** {task.verify}")
    if task.depends_on:
        parts.append(f"**Depends on:** {', '.join(task.depends_on)}")
    parts.append("\nComplete this task, then report what files you created or modified.")
    return "\n".join(parts)
