"""
5.2 - 上下文发现与注入

实现三个上下文发现机制:
  - 项目配置文件的自动检测(pyproject.toml, package.json 等)
  - 目录结构映射(生成简洁的项目树)
  - 选择性加载与按需注入策略

运行方式:
    uv run python chapter05/context_discovery.py
"""
import os
from pathlib import Path

# 智能体需要感知的项目配置文件(按优先级排列)
_CONFIG_FILES = [
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "Makefile",
    "Dockerfile",
    "docker-compose.yml",
    "README.md",
]

# 目录结构映射时需要跳过的目录
_SKIP_DIRS = {
    ".git", ".svn", ".hg",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "node_modules", ".venv", "venv", ".tox",
    ".next", "dist", "build", "target",
    ".idea", ".vscode",
}

# 目录结构映射的默认限制
_MAX_DEPTH = 3
_MAX_ENTRIES = 80


# =============================================================
#  5.2.1 项目配置文件的自动检测
# =============================================================

def detect_project_configs(cwd: str | None = None) -> dict[str, str]:
    """扫描工作目录, 检测并读取已知的项目配置文件。

    返回一个字典: {文件名: 文件内容}。只包含实际存在的文件。
    内容超过 2000 字符时截断, 避免单个配置文件占用过多上下文空间。

    Args:
        cwd: 要扫描的目录, 默认使用当前工作目录
    """
    cwd = Path(cwd or os.getcwd())
    configs = {}

    for name in _CONFIG_FILES:
        path = cwd / name
        if path.is_file():
            try:
                text = path.read_text(encoding="utf-8")
                if len(text) > 2000:
                    text = text[:2000] + "\n...(truncated)"
                configs[name] = text
            except (UnicodeDecodeError, PermissionError):
                continue

    return configs


# =============================================================
#  5.2.2 目录结构映射
# =============================================================

def map_directory_structure(
    cwd: str | None = None,
    max_depth: int = _MAX_DEPTH,
    max_entries: int = _MAX_ENTRIES,
) -> str:
    """生成工作目录的树形结构字符串。

    类似 tree 命令的输出, 但有两个重要的过滤策略:
    - 跳过构建产物、缓存、版本控制等无关目录
    - 限制最大深度和最大条目数, 防止大型项目撑爆上下文

    Args:
        cwd: 要映射的目录
        max_depth: 最大递归深度
        max_entries: 最大条目数(超过后停止并标注)
    """
    root = Path(cwd or os.getcwd())
    lines = [f"{root.name}/"]
    count = [0]  # 用列表模拟可变计数器
    truncated = [False]

    def _walk(directory: Path, prefix: str, depth: int):
        if depth > max_depth or truncated[0]:
            return

        try:
            entries = sorted(
                directory.iterdir(),
                key=lambda x: (x.is_file(), x.name.lower()),
            )
        except PermissionError:
            return

        # 过滤隐藏文件和跳过目录
        entries = [
            e for e in entries
            if not e.name.startswith(".") and e.name not in _SKIP_DIRS
        ]

        for i, entry in enumerate(entries):
            if count[0] >= max_entries:
                truncated[0] = True
                lines.append(f"{prefix}... ({count[0]}+ entries, truncated)")
                return

            is_last = i == len(entries) - 1
            connector = "--- " if is_last else "|-- "
            next_prefix = prefix + ("    " if is_last else "|   ")

            if entry.is_dir():
                lines.append(f"{prefix}{connector}{entry.name}/")
                count[0] += 1
                _walk(entry, next_prefix, depth + 1)
            else:
                # 附带文件大小
                try:
                    size = entry.stat().st_size
                    if size < 1024:
                        size_str = f"{size}B"
                    elif size < 1024 * 1024:
                        size_str = f"{size / 1024:.1f}KB"
                    else:
                        size_str = f"{size / 1024 / 1024:.1f}MB"
                except OSError:
                    size_str = "?"
                lines.append(f"{prefix}{connector}{entry.name} ({size_str})")
                count[0] += 1

    _walk(root, "", 0)
    return "\n".join(lines)


# =============================================================
#  5.2.3 选择性加载与按需注入策略
# =============================================================

def build_context_block(cwd: str | None = None) -> str:
    """组装完整的项目上下文块, 用于注入 System Prompt。

    按照信息价值从高到低排列:
    1. 项目配置(告诉模型这是什么类型的项目、用了什么依赖)
    2. 目录结构(告诉模型项目的文件组织)

    返回格式化的 Markdown 字符串, 可直接拼接到 System Prompt 中。

    Args:
        cwd: 项目根目录
    """
    cwd = cwd or os.getcwd()
    sections = ["# Project Context"]

    # 1. 项目配置
    configs = detect_project_configs(cwd)
    if configs:
        sections.append("\n## Project Configuration")
        for name, content in configs.items():
            sections.append(f"\n### {name}")
            sections.append(f"```\n{content}\n```")

    # 2. 目录结构
    tree = map_directory_structure(cwd)
    sections.append("\n## Directory Structure")
    sections.append(f"```\n{tree}\n```")

    return "\n".join(sections)


# =============================================================
#  演示
# =============================================================

if __name__ == "__main__":
    print("=== 上下文发现与注入演示 ===\n")

    # 1. 检测项目配置
    print("--- 检测到的项目配置文件 ---")
    configs = detect_project_configs()
    if configs:
        for name, content in configs.items():
            preview = content[:80].replace("\n", " ")
            print(f"  {name}: {preview}...")
    else:
        print("  (未检测到已知配置文件)")
    print()

    # 2. 目录结构
    print("--- 目录结构映射 ---")
    tree = map_directory_structure()
    print(tree)
    print()

    # 3. 完整上下文块
    context = build_context_block()
    char_count = len(context)
    print(f"--- 完整上下文块 ({char_count} 字符) ---")
    print(context[:500] + "..." if len(context) > 500 else context)
