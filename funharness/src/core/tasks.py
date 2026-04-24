"""
FunHarness - Task Management

Task decomposition, progress tracking, and git integration.
"""
import json
import os
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
    def __init__(self, task_id, title, description="", verify="", depends_on=None):
        self.task_id = task_id
        self.title = title
        self.description = description
        self.verify = verify
        self.depends_on = depends_on or []
        self.status = TaskStatus.PENDING
        self.artifacts: list[str] = []
        self.error = ""
        self.started_at = ""
        self.finished_at = ""

    def start(self):
        self.status = TaskStatus.IN_PROGRESS
        self.started_at = datetime.now().isoformat(timespec="seconds")

    def complete(self, artifacts=None):
        self.status = TaskStatus.DONE
        self.finished_at = datetime.now().isoformat(timespec="seconds")
        if artifacts:
            self.artifacts.extend(artifacts)

    def fail(self, error):
        self.status = TaskStatus.FAILED
        self.finished_at = datetime.now().isoformat(timespec="seconds")
        self.error = error

    def to_dict(self):
        return {
            "task_id": self.task_id, "title": self.title,
            "description": self.description, "verify": self.verify,
            "depends_on": self.depends_on, "status": self.status.value,
            "artifacts": self.artifacts, "error": self.error,
            "started_at": self.started_at, "finished_at": self.finished_at,
        }

    @classmethod
    def from_dict(cls, data):
        t = cls(data["task_id"], data["title"], data.get("description", ""),
                data.get("verify", ""), data.get("depends_on", []))
        t.status = TaskStatus(data.get("status", "pending"))
        t.artifacts = data.get("artifacts", [])
        t.error = data.get("error", "")
        t.started_at = data.get("started_at", "")
        t.finished_at = data.get("finished_at", "")
        return t

    def __repr__(self):
        icon = {"pending": "[ ]", "in_progress": "[~]", "done": "[x]",
                "failed": "[!]", "skipped": "[-]"}[self.status.value]
        return f"{icon} {self.task_id}: {self.title}"


class TaskList:
    def __init__(self, project_name=""):
        self.project_name = project_name
        self.tasks: list[Task] = []
        self.created_at = datetime.now().isoformat(timespec="seconds")

    def add(self, task):
        self.tasks.append(task)

    def get(self, task_id):
        for t in self.tasks:
            if t.task_id == task_id:
                return t
        return None

    def next_pending(self):
        done_ids = {t.task_id for t in self.tasks if t.status == TaskStatus.DONE}
        for t in self.tasks:
            if t.status == TaskStatus.PENDING and all(d in done_ids for d in t.depends_on):
                return t
        return None

    @property
    def progress(self):
        done = sum(1 for t in self.tasks if t.status in (TaskStatus.DONE, TaskStatus.SKIPPED))
        return done, len(self.tasks)

    @property
    def progress_pct(self):
        done, total = self.progress
        return (done / total * 100) if total > 0 else 0.0

    def summary(self):
        done, total = self.progress
        lines = [f"Project: {self.project_name} ({done}/{total} tasks done)"]
        for t in self.tasks:
            lines.append(f"  {t}")
        return "\n".join(lines)

    def save(self, path):
        data = {"project_name": self.project_name, "created_at": self.created_at,
                "tasks": [t.to_dict() for t in self.tasks]}
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        tl = cls(project_name=data.get("project_name", ""))
        tl.created_at = data.get("created_at", "")
        for td in data.get("tasks", []):
            tl.tasks.append(Task.from_dict(td))
        return tl


class ProgressTracker:
    def __init__(self, project_dir="."):
        self.project_dir = Path(project_dir)
        self.progress_file = self.project_dir / "PROGRESS.md"

    def update(self, task_list):
        done, total = task_list.progress
        lines = [f"# {task_list.project_name} - Progress", "",
                 f"Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                 f"Progress: {done}/{total} ({task_list.progress_pct:.0f}%)", ""]
        for status, label in [(TaskStatus.DONE, "Completed"), (TaskStatus.IN_PROGRESS, "In Progress"),
                              (TaskStatus.FAILED, "Failed"), (TaskStatus.PENDING, "Pending")]:
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
                else:
                    lines.append(f"- [ ] {t.task_id}: {t.title}")
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

    def commit_task(self, task):
        if not self.is_git_repo():
            return ""
        files = list(task.artifacts)
        if (self.repo_dir / "PROGRESS.md").exists():
            files.append("PROGRESS.md")
        if not files:
            return ""
        for f in files:
            self._run_git("add", str(f))
        ok, status = self._run_git("diff", "--cached", "--stat")
        if ok and not status:
            return ""
        ok, out = self._run_git("commit", "-m", f"[task:{task.task_id}] {task.title}")
        return out if ok else ""


def plan_tasks(user_requirement, model=MODEL):
    prompt = f"""\
Break this requirement into small, atomic tasks. For each, provide:
task_id (T1, T2...), title, description, verify, depends_on (list of task_ids).
Return ONLY a JSON array. No markdown.

Requirement: {user_requirement}"""

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "You decompose requirements into task lists. Return valid JSON only."},
            {"role": "user", "content": prompt},
        ], temperature=0.2)

    raw = (response.choices[0].message.content or "[]").strip()
    if raw.startswith("```"):
        raw = "\n".join(raw.split("\n")[1:-1])
    try:
        tasks_data = json.loads(raw)
    except json.JSONDecodeError:
        tasks_data = [{"task_id": "T1", "title": user_requirement[:60],
                       "description": user_requirement, "verify": "manual review", "depends_on": []}]

    tl = TaskList(project_name=user_requirement[:40])
    for td in tasks_data:
        tl.add(Task(
            td.get("task_id", f"T{len(tl.tasks)+1}"), td.get("title", ""),
            td.get("description", ""), td.get("verify", ""), td.get("depends_on", [])))
    return tl


def pick_next_task(task_list):
    return task_list.next_pending()


def format_task_for_agent(task):
    parts = [f"## Current Task: {task.task_id} - {task.title}", "",
             f"**Description:** {task.description}"]
    if task.verify:
        parts.append(f"**Verification:** {task.verify}")
    if task.depends_on:
        parts.append(f"**Depends on:** {', '.join(task.depends_on)}")
    parts.append("\nComplete this task, then report what files you created or modified.")
    return "\n".join(parts)
