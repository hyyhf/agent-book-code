"""
6.1 - 持久化记忆

实现基于 MEMORY.md 的持久化记忆系统:
  - 跨会话知识沉淀: 将重要信息写入 MEMORY.md
  - 记忆的写入策略: append / update / clear
  - 记忆的检索: 按关键词搜索、全文读取
  - 记忆工具: 注册为 Agent 可调用的工具

运行方式:
    uv run python chapter06/persistent_memory.py
"""
import os
import re
from datetime import datetime
from pathlib import Path

# 记忆文件的默认存储位置
_MEMORY_DIR = ".funharness"
_MEMORY_FILE = "MEMORY.md"


class MemoryStore:
    """基于 Markdown 文件的持久化记忆存储。

    设计理念来自 Claude Code 的 MEMORY.md 模式:
    用一个结构化的 Markdown 文件来保存跨会话的知识沉淀。
    每条记忆以 "## 标题" 为分隔, 包含时间戳和正文内容。

    文件结构示例:
        # FunHarness Memory
        ## 项目使用 FastAPI 框架
        _2025-01-15 14:30_
        用户的项目基于 FastAPI, 入口文件是 main.py ...

        ## 数据库使用 PostgreSQL
        _2025-01-15 15:00_
        连接配置在 .env 文件中 ...
    """

    def __init__(self, project_dir: str | None = None):
        """初始化记忆存储。

        Args:
            project_dir: 项目根目录, 记忆文件存放在其下的 .funharness/ 中
        """
        root = Path(project_dir or os.getcwd())
        self.memory_dir = root / _MEMORY_DIR
        self.memory_path = self.memory_dir / _MEMORY_FILE

    def _ensure_file(self):
        """确保记忆文件和目录存在。"""
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        if not self.memory_path.exists():
            self.memory_path.write_text(
                "# FunHarness Memory\n\n",
                encoding="utf-8",
            )

    def read_all(self) -> str:
        """读取完整的记忆文件内容。

        Returns:
            记忆文件的全文, 如果文件不存在则返回空提示
        """
        if not self.memory_path.exists():
            return "(no memories saved yet)"
        text = self.memory_path.read_text(encoding="utf-8")
        if len(text.strip()) <= len("# FunHarness Memory"):
            return "(no memories saved yet)"
        return text

    def add(self, title: str, content: str) -> str:
        """添加一条新的记忆条目。

        每条记忆由标题、时间戳和正文组成。
        如果已存在同名标题的记忆, 会追加为新条目(不覆盖旧的)。

        Args:
            title: 记忆标题, 简洁描述这条记忆的主题
            content: 记忆正文, 详细的知识内容

        Returns:
            操作结果信息
        """
        self._ensure_file()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = f"\n## {title}\n_{timestamp}_\n\n{content}\n"

        with open(self.memory_path, "a", encoding="utf-8") as f:
            f.write(entry)

        return f"Memory saved: '{title}'"

    def search(self, keyword: str) -> str:
        """按关键词搜索记忆条目。

        在标题和正文中搜索包含关键词的条目, 返回匹配的完整条目。

        Args:
            keyword: 搜索关键词(不区分大小写)

        Returns:
            匹配的记忆条目, 或未找到的提示
        """
        if not self.memory_path.exists():
            return "(no memories to search)"

        text = self.memory_path.read_text(encoding="utf-8")
        # 按 "## " 分割为独立条目
        sections = re.split(r"(?=^## )", text, flags=re.MULTILINE)
        matches = []
        pattern = re.compile(re.escape(keyword), re.IGNORECASE)

        for section in sections:
            if section.startswith("## ") and pattern.search(section):
                matches.append(section.strip())

        if not matches:
            return f"No memories matching '{keyword}'"
        return f"Found {len(matches)} matching memories:\n\n" + "\n\n".join(matches)

    def list_titles(self) -> list[str]:
        """列出所有记忆标题。

        Returns:
            标题列表
        """
        if not self.memory_path.exists():
            return []

        text = self.memory_path.read_text(encoding="utf-8")
        return re.findall(r"^## (.+)$", text, re.MULTILINE)

    def remove(self, title: str) -> str:
        """删除指定标题的记忆条目。

        Args:
            title: 要删除的记忆标题(精确匹配)

        Returns:
            操作结果信息
        """
        if not self.memory_path.exists():
            return "No memory file found"

        text = self.memory_path.read_text(encoding="utf-8")
        sections = re.split(r"(?=^## )", text, flags=re.MULTILINE)

        new_sections = []
        removed = False
        for section in sections:
            if section.startswith(f"## {title}\n") or section.startswith(f"## {title}\r\n"):
                removed = True
            else:
                new_sections.append(section)

        if not removed:
            return f"Memory '{title}' not found"

        self.memory_path.write_text("".join(new_sections), encoding="utf-8")
        return f"Memory '{title}' removed"


# =============================================================
#  将记忆操作注册为 Agent 工具
# =============================================================

# 全局记忆存储实例, 在 FunHarness 启动时初始化
_store: MemoryStore | None = None


def init_memory(project_dir: str | None = None):
    """初始化全局记忆存储。在 FunHarness 启动时调用一次。"""
    global _store
    _store = MemoryStore(project_dir)


def _get_store() -> MemoryStore:
    """获取全局记忆存储, 未初始化则使用当前目录。"""
    global _store
    if _store is None:
        _store = MemoryStore()
    return _store


def read_memory() -> str:
    """读取所有持久化记忆内容。当需要回忆之前保存的知识时使用。"""
    return _get_store().read_all()


def save_memory(title: str, content: str) -> str:
    """保存一条新的持久化记忆。用于记录重要的项目知识、用户偏好或关键发现, 以便在未来的会话中使用。

    Args:
        title: 记忆标题, 简洁描述主题
        content: 记忆正文, 详细内容
    """
    return _get_store().add(title, content)


def search_memory(keyword: str) -> str:
    """按关键词搜索持久化记忆。在记忆标题和正文中查找匹配项。

    Args:
        keyword: 搜索关键词
    """
    return _get_store().search(keyword)


# =============================================================
#  演示
# =============================================================

if __name__ == "__main__":
    import tempfile

    print("=== 持久化记忆演示 ===\n")

    # 使用临时目录演示, 避免污染真实项目
    with tempfile.TemporaryDirectory() as tmpdir:
        store = MemoryStore(tmpdir)

        # 1. 写入记忆
        print("--- 写入记忆 ---")
        print(store.add("项目使用 FastAPI", "入口文件是 main.py, 端口 8000"))
        print(store.add("数据库配置", "PostgreSQL, 连接字符串在 .env 中"))
        print(store.add("用户偏好", "喜欢简洁的代码风格, 不要过多注释"))

        # 2. 读取全部
        print("\n--- 全部记忆 ---")
        print(store.read_all())

        # 3. 搜索
        print("--- 搜索 'FastAPI' ---")
        print(store.search("FastAPI"))

        # 4. 列出标题
        print("\n--- 记忆标题列表 ---")
        titles = store.list_titles()
        for t in titles:
            print(f"  - {t}")

        # 5. 删除
        print("\n--- 删除一条记忆 ---")
        print(store.remove("用户偏好"))
        print(f"  剩余标题: {store.list_titles()}")

        # 6. 查看文件
        print("\n--- 记忆文件路径 ---")
        print(f"  {store.memory_path}")
