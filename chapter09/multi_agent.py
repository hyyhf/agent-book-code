"""
9.3 - 多智能体协作

实现多 Agent 协作模式:
- AgentMessage: Agent 间通信的消息格式
- SubAgent: 子 Agent 的生成与任务委派
- Orchestrator: 编排多个子 Agent 的协调器
- BackgroundTask: 后台任务的生命周期管理

运行方式:
    uv run python chapter09/multi_agent.py
"""
import json
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, Future
from enum import Enum
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

client = OpenAI()
MODEL = os.getenv("OPENAI_MODEL_NAME", "deepseek-chat")


# =============================================================
#  9.3.3 Agent 间通信
# =============================================================

class AgentMessage:
    """Agent 间通信的消息。

    多 Agent 协作中, Agent 之间需要传递信息。
    AgentMessage 定义了一个标准化的消息格式,
    包含发送者、接收者、消息类型和负载。
    """

    class Type(Enum):
        TASK = "task"            # 任务委派
        RESULT = "result"        # 任务结果
        STATUS = "status"        # 状态更新
        ERROR = "error"          # 错误报告

    def __init__(
        self,
        sender: str,
        receiver: str,
        msg_type: "AgentMessage.Type",
        payload: dict,
    ):
        self.id = uuid.uuid4().hex[:8]
        self.sender = sender
        self.receiver = receiver
        self.msg_type = msg_type
        self.payload = payload
        self.timestamp = time.time()

    def to_context(self) -> str:
        """将消息转换为可注入 Agent 上下文的文本。"""
        return (
            f"[Message from {self.sender}] "
            f"Type: {self.msg_type.value}\n"
            f"{json.dumps(self.payload, ensure_ascii=False, indent=2)}"
        )

    def __repr__(self):
        return (
            f"AgentMessage({self.sender}->{self.receiver}, "
            f"type={self.msg_type.value})"
        )


class MessageBus:
    """简易消息总线: Agent 间通信的中转站。

    每个 Agent 有一个消息队列, 其他 Agent 可以向其发送消息。
    Orchestrator 通过消息总线协调所有子 Agent。
    """

    def __init__(self):
        self._queues: dict[str, list[AgentMessage]] = {}
        self._lock = threading.Lock()

    def register(self, agent_id: str):
        """注册一个 Agent 的消息队列。"""
        with self._lock:
            if agent_id not in self._queues:
                self._queues[agent_id] = []

    def send(self, message: AgentMessage):
        """发送消息到目标 Agent 的队列。"""
        with self._lock:
            if message.receiver not in self._queues:
                self._queues[message.receiver] = []
            self._queues[message.receiver].append(message)

    def receive(self, agent_id: str) -> list[AgentMessage]:
        """接收并清空指定 Agent 的所有待处理消息。"""
        with self._lock:
            messages = self._queues.get(agent_id, [])
            self._queues[agent_id] = []
            return messages


# =============================================================
#  9.3.1 子 Agent 的生成与任务委派
# =============================================================

class SubAgent:
    """子 Agent: 在隔离的上下文中执行子任务。

    核心设计原则:
    1. 上下文隔离: 子 Agent 有自己独立的消息历史, 不继承父 Agent 的完整上下文
    2. 职责单一: 每个子 Agent 聚焦于一个具体子任务
    3. 结果回传: 执行完成后将结果通过消息总线返回给 Orchestrator
    """

    def __init__(
        self,
        agent_id: str,
        role: str,
        system_prompt: str = "",
        model: str = MODEL,
    ):
        self.agent_id = agent_id
        self.role = role
        self.model = model
        self.system_prompt = system_prompt or self._default_prompt()
        self.messages: list[dict] = [
            {"role": "system", "content": self.system_prompt}
        ]
        self.status = "idle"  # idle, running, done, failed
        self.result: str = ""

    def _default_prompt(self) -> str:
        return (
            f"You are a sub-agent with role: {self.role}. "
            f"Your ID is {self.agent_id}. "
            f"Execute the assigned task concisely and report results. "
            f"Do not ask questions. Focus on delivering actionable output."
        )

    def execute(self, task: str, context: str = "") -> str:
        """执行一个子任务, 返回结果。

        Args:
            task: 任务描述
            context: 从父 Agent 传递的相关上下文(经过裁剪, 只包含必要信息)
        """
        self.status = "running"

        user_content = task
        if context:
            user_content = f"Context:\n{context}\n\nTask:\n{task}"

        self.messages.append({"role": "user", "content": user_content})

        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=self.messages,
                temperature=0.3,
                max_tokens=2000,
            )
            self.result = response.choices[0].message.content or ""
            self.messages.append({"role": "assistant", "content": self.result})
            self.status = "done"
        except Exception as e:
            self.result = f"SubAgent error: {e}"
            self.status = "failed"

        return self.result

    def get_summary(self) -> dict:
        """返回子 Agent 的状态摘要。"""
        return {
            "agent_id": self.agent_id,
            "role": self.role,
            "status": self.status,
            "result_length": len(self.result),
            "message_count": len(self.messages),
        }


# =============================================================
#  9.3.2 Orchestrator 模式
# =============================================================

class Orchestrator:
    """编排器: 将复杂任务分解并委派给多个子 Agent。

    工作流程:
    1. 接收用户的复杂任务
    2. 调用模型将任务分解为子任务
    3. 为每个子任务创建独立的子 Agent
    4. 并行或串行执行子任务
    5. 收集所有子 Agent 的结果
    6. 合成最终响应
    """

    def __init__(
        self,
        model: str = MODEL,
        max_sub_agents: int = 5,
        parallel: bool = True,
    ):
        self.model = model
        self.max_sub_agents = max_sub_agents
        self.parallel = parallel
        self.bus = MessageBus()
        self.bus.register("orchestrator")
        self.sub_agents: list[SubAgent] = []
        self._results: dict[str, str] = {}

    def decompose_task(self, task: str) -> list[dict]:
        """将复杂任务分解为子任务列表。

        使用模型进行任务分解, 返回子任务列表。
        每个子任务包含 role(角色)和 task(具体指令)。
        """
        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You decompose complex tasks into independent sub-tasks. "
                        "Return a JSON array where each item has: "
                        "\"role\" (a specialist role like 'researcher', 'coder', 'reviewer'), "
                        "\"task\" (specific instruction for that role). "
                        f"Maximum {self.max_sub_agents} sub-tasks. "
                        "Return ONLY valid JSON, no markdown."
                    ),
                },
                {"role": "user", "content": task},
            ],
            temperature=0.2,
        )

        raw = response.choices[0].message.content or "[]"
        raw = raw.strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:-1])

        try:
            subtasks = json.loads(raw)
            return subtasks[:self.max_sub_agents]
        except json.JSONDecodeError:
            return [{"role": "generalist", "task": task}]

    def spawn_agent(self, role: str, task: str) -> SubAgent:
        """创建一个子 Agent 并注册到消息总线。"""
        agent_id = f"sub_{role}_{uuid.uuid4().hex[:4]}"
        agent = SubAgent(agent_id=agent_id, role=role, model=self.model)
        self.bus.register(agent_id)
        self.sub_agents.append(agent)
        return agent

    def run(self, task: str, context: str = "") -> dict:
        """执行完整的编排流程。

        Returns:
            {
                "subtasks": [...],
                "results": {agent_id: result},
                "synthesis": "final combined answer",
            }
        """
        # 1. 分解任务
        subtasks = self.decompose_task(task)

        # 2. 创建子 Agent 并执行
        if self.parallel:
            results = self._run_parallel(subtasks, context)
        else:
            results = self._run_sequential(subtasks, context)

        # 3. 合成结果
        synthesis = self._synthesize(task, results)

        return {
            "subtasks": subtasks,
            "results": results,
            "synthesis": synthesis,
        }

    def _run_parallel(self, subtasks: list[dict], context: str) -> dict[str, str]:
        """并行执行所有子任务。"""
        results = {}

        def _execute(sub_info):
            agent = self.spawn_agent(sub_info["role"], sub_info["task"])
            result = agent.execute(sub_info["task"], context)
            return agent.agent_id, result

        with ThreadPoolExecutor(max_workers=min(len(subtasks), 3)) as executor:
            futures = [executor.submit(_execute, st) for st in subtasks]
            for future in futures:
                try:
                    agent_id, result = future.result(timeout=60)
                    results[agent_id] = result
                except Exception as e:
                    results[f"error_{uuid.uuid4().hex[:4]}"] = str(e)

        return results

    def _run_sequential(self, subtasks: list[dict], context: str) -> dict[str, str]:
        """串行执行所有子任务, 前一个的结果传给后一个。"""
        results = {}
        accumulated_context = context

        for sub_info in subtasks:
            agent = self.spawn_agent(sub_info["role"], sub_info["task"])
            result = agent.execute(sub_info["task"], accumulated_context)
            results[agent.agent_id] = result
            # 将结果追加到上下文, 供后续子 Agent 使用
            accumulated_context += f"\n\n[Result from {agent.role}]:\n{result[:500]}"

        return results

    def _synthesize(self, original_task: str, results: dict[str, str]) -> str:
        """将所有子 Agent 的结果合成为最终答案。"""
        parts = []
        for agent_id, result in results.items():
            # 截断过长结果
            truncated = result[:800] if len(result) > 800 else result
            parts.append(f"[{agent_id}]:\n{truncated}")

        combined = "\n\n".join(parts)

        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You synthesize results from multiple agents into a "
                        "coherent, well-organized final response."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Original task: {original_task}\n\n"
                        f"Sub-agent results:\n{combined}\n\n"
                        f"Synthesize these into a single, coherent response."
                    ),
                },
            ],
            temperature=0.3,
            max_tokens=2000,
        )
        return response.choices[0].message.content or ""

    def get_status(self) -> str:
        """获取所有子 Agent 的状态概览。"""
        lines = [f"Orchestrator: {len(self.sub_agents)} sub-agents"]
        for agent in self.sub_agents:
            lines.append(
                f"  - {agent.agent_id} [{agent.role}]: {agent.status} "
                f"({len(agent.result)} chars)"
            )
        return "\n".join(lines)


# =============================================================
#  9.3.4 后台任务生命周期管理
# =============================================================

class BackgroundTaskStatus(Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class BackgroundTask:
    """后台任务: 不阻塞主 Agent Loop 的异步任务。

    主 Agent 可以将耗时操作(如大规模搜索、并行测试)
    放入后台执行, 自己继续处理其他工作。
    """

    def __init__(self, task_id: str, description: str, fn, *args, **kwargs):
        self.task_id = task_id
        self.description = description
        self.status = BackgroundTaskStatus.QUEUED
        self.result: str = ""
        self.error: str = ""
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self._future: Future | None = None
        self.created_at = time.time()
        self.finished_at: float | None = None

    def __repr__(self):
        return f"BackgroundTask({self.task_id}, {self.status.value})"


class BackgroundTaskManager:
    """管理后台任务的生命周期。

    提供提交、查询、取消等操作。
    使用线程池执行后台任务。
    """

    def __init__(self, max_workers: int = 3):
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._tasks: dict[str, BackgroundTask] = {}

    def submit(self, task: BackgroundTask) -> str:
        """提交一个后台任务。"""
        self._tasks[task.task_id] = task
        task.status = BackgroundTaskStatus.RUNNING

        def _run():
            try:
                result = task._fn(*task._args, **task._kwargs)
                task.result = str(result)
                task.status = BackgroundTaskStatus.DONE
            except Exception as e:
                task.error = str(e)
                task.status = BackgroundTaskStatus.FAILED
            finally:
                task.finished_at = time.time()

        task._future = self._executor.submit(_run)
        return task.task_id

    def get_status(self, task_id: str) -> dict | None:
        """查询后台任务状态。"""
        task = self._tasks.get(task_id)
        if not task:
            return None
        elapsed = (task.finished_at or time.time()) - task.created_at
        return {
            "task_id": task.task_id,
            "description": task.description,
            "status": task.status.value,
            "elapsed": f"{elapsed:.1f}s",
            "result_length": len(task.result),
            "error": task.error,
        }

    def get_result(self, task_id: str) -> str:
        """获取后台任务的结果(若已完成)。"""
        task = self._tasks.get(task_id)
        if not task:
            return f"Unknown task: {task_id}"
        if task.status == BackgroundTaskStatus.RUNNING:
            return f"Task {task_id} still running..."
        if task.status == BackgroundTaskStatus.FAILED:
            return f"Task {task_id} failed: {task.error}"
        return task.result

    def cancel(self, task_id: str) -> str:
        """取消一个后台任务。"""
        task = self._tasks.get(task_id)
        if not task:
            return f"Unknown task: {task_id}"
        if task.status == BackgroundTaskStatus.RUNNING and task._future:
            task._future.cancel()
            task.status = BackgroundTaskStatus.CANCELLED
            return f"Task {task_id} cancelled"
        return f"Task {task_id} is {task.status.value}, cannot cancel"

    def list_tasks(self) -> str:
        """列出所有后台任务。"""
        if not self._tasks:
            return "(no background tasks)"
        lines = []
        for t in self._tasks.values():
            elapsed = (t.finished_at or time.time()) - t.created_at
            lines.append(f"  {t.task_id} [{t.status.value}] {t.description} ({elapsed:.1f}s)")
        return "\n".join(lines)

    def shutdown(self):
        """关闭线程池。"""
        self._executor.shutdown(wait=False)


# =============================================================
#  演示
# =============================================================

if __name__ == "__main__":
    print("=== 多智能体协作演示 ===\n")

    # 1. 消息总线
    print("--- 消息总线 ---")
    bus = MessageBus()
    bus.register("agent_a")
    bus.register("agent_b")

    msg = AgentMessage(
        sender="agent_a",
        receiver="agent_b",
        msg_type=AgentMessage.Type.TASK,
        payload={"task": "Review the code in main.py"},
    )
    bus.send(msg)
    print(f"  Sent: {msg}")

    received = bus.receive("agent_b")
    print(f"  Agent B received: {len(received)} message(s)")
    if received:
        print(f"  Content: {received[0].to_context()[:100]}")

    # 2. 子 Agent(单独执行)
    print("\n--- 子 Agent ---")
    sub = SubAgent(
        agent_id="sub_researcher_01",
        role="researcher",
    )
    result = sub.execute(
        "List 3 key benefits of using type hints in Python. Be concise."
    )
    print(f"  Status: {sub.status}")
    print(f"  Result preview: {result[:150]}...")

    # 3. Orchestrator(多 Agent 协作)
    print("\n--- Orchestrator ---")
    orch = Orchestrator(max_sub_agents=3, parallel=False)  # 串行以便观察
    output = orch.run(
        "Compare Python and JavaScript for building CLI tools. "
        "Consider: syntax, ecosystem, performance."
    )
    print(f"  Sub-tasks: {len(output['subtasks'])}")
    for st in output["subtasks"]:
        print(f"    - [{st.get('role', '?')}] {st.get('task', '?')[:60]}")
    print(f"\n  Synthesis preview:\n  {output['synthesis'][:200]}...")
    print(f"\n  {orch.get_status()}")

    # 4. 后台任务
    print("\n--- 后台任务 ---")
    bg_mgr = BackgroundTaskManager(max_workers=2)

    def slow_search(query):
        time.sleep(1)  # 模拟耗时操作
        return f"Found 42 results for '{query}'"

    bg_task = BackgroundTask("bg_1", "Search for TODO comments", slow_search, "TODO")
    bg_mgr.submit(bg_task)
    print(f"  Submitted: {bg_task}")

    # 检查状态
    time.sleep(0.3)
    status = bg_mgr.get_status("bg_1")
    print(f"  Status (0.3s): {status}")

    # 等待完成
    time.sleep(1.0)
    status = bg_mgr.get_status("bg_1")
    print(f"  Status (1.3s): {status}")
    print(f"  Result: {bg_mgr.get_result('bg_1')}")

    print(f"\n  All tasks:\n{bg_mgr.list_tasks()}")
    bg_mgr.shutdown()
