"""
8.3 - 插件系统

可扩展的插件加载与注册机制:
- PluginInterface: 插件接口定义
- PluginLoader: 从目录加载插件
- 自定义命令扩展: 通过插件添加 CLI 命令
- 第三方插件集成: 安全加载外部插件

运行方式:
    uv run python chapter08/plugin_system.py
"""
import importlib.util
import json
import os
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable


# =============================================================
#  8.3.1 插件接口
# =============================================================

class PluginInterface(ABC):
    """插件的统一接口。

    每个插件必须实现:
    - name: 插件名称
    - version: 版本号
    - description: 功能描述
    - on_load: 加载时的初始化逻辑
    - on_unload: 卸载时的清理逻辑

    可选实现:
    - register_tools: 注册自定义工具
    - register_hooks: 注册自定义钩子
    - register_commands: 注册自定义 CLI 命令
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """插件名称。"""
        ...

    @property
    @abstractmethod
    def version(self) -> str:
        """插件版本。"""
        ...

    @property
    def description(self) -> str:
        """插件描述。"""
        return ""

    def on_load(self, context: dict) -> None:
        """插件加载时调用。context 包含 agent 的运行时信息。"""
        pass

    def on_unload(self) -> None:
        """插件卸载时调用, 做清理工作。"""
        pass

    def register_tools(self) -> list[dict]:
        """返回要注册的工具列表。

        每个工具是一个字典:
            {"name": str, "function": callable, "category": str}
        """
        return []

    def register_hooks(self) -> list[dict]:
        """返回要注册的钩子列表。

        每个钩子是一个字典:
            {"event": HookEvent, "handler": callable, "name": str, "priority": int}
        """
        return []

    def register_commands(self) -> dict[str, Callable]:
        """返回自定义 CLI 命令。

        键是命令名(不含 / 前缀), 值是处理函数。
        处理函数签名: (args: str, context: dict) -> str
        """
        return {}


# =============================================================
#  8.3.2 插件清单(manifest)
# =============================================================

class PluginManifest:
    """插件清单: 从 plugin.json 读取插件元信息。

    plugin.json 结构:
    {
        "name": "my-plugin",
        "version": "1.0.0",
        "description": "A sample plugin",
        "entry": "main.py",
        "class": "MyPlugin",
        "permissions": ["file_read", "file_write"]
    }
    """

    def __init__(
        self,
        name: str,
        version: str,
        description: str,
        entry: str,
        plugin_class: str,
        permissions: list[str] | None = None,
        path: Path | None = None,
    ):
        self.name = name
        self.version = version
        self.description = description
        self.entry = entry
        self.plugin_class = plugin_class
        self.permissions = permissions or []
        self.path = path

    @classmethod
    def from_file(cls, manifest_path: Path) -> "PluginManifest":
        """从 plugin.json 文件加载清单。"""
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        return cls(
            name=data["name"],
            version=data.get("version", "0.0.1"),
            description=data.get("description", ""),
            entry=data.get("entry", "main.py"),
            plugin_class=data.get("class", "Plugin"),
            permissions=data.get("permissions", []),
            path=manifest_path.parent,
        )


# =============================================================
#  8.3.3 插件加载器
# =============================================================

# 插件被允许请求的权限集合
ALLOWED_PERMISSIONS = {
    "file_read",
    "file_write",
    "command_execute",
    "memory_access",
    "network_access",
}


class PluginLoader:
    """插件加载器: 发现、加载和管理插件。

    插件目录结构:
        plugins/
            my-plugin/
                plugin.json    <- 清单文件
                main.py        <- 入口文件(包含插件类)
            another-plugin/
                plugin.json
                main.py
    """

    def __init__(self, plugins_dir: str | None = None):
        if plugins_dir:
            self.plugins_dir = Path(plugins_dir)
        else:
            self.plugins_dir = Path.cwd() / ".funharness" / "plugins"

        self._loaded: dict[str, PluginInterface] = {}
        self._manifests: dict[str, PluginManifest] = {}

    def discover(self) -> list[PluginManifest]:
        """扫描插件目录, 发现所有可用插件。"""
        manifests = []

        if not self.plugins_dir.exists():
            return manifests

        for item in self.plugins_dir.iterdir():
            if not item.is_dir():
                continue
            manifest_file = item / "plugin.json"
            if manifest_file.exists():
                try:
                    manifest = PluginManifest.from_file(manifest_file)
                    manifests.append(manifest)
                    self._manifests[manifest.name] = manifest
                except (json.JSONDecodeError, KeyError) as e:
                    print(f"  [plugin] Invalid manifest in {item.name}: {e}")

        return manifests

    def _validate_permissions(self, manifest: PluginManifest) -> tuple[bool, str]:
        """验证插件请求的权限是否合法。"""
        invalid = set(manifest.permissions) - ALLOWED_PERMISSIONS
        if invalid:
            return False, f"Unknown permissions: {invalid}"
        return True, "ok"

    def load(self, plugin_name: str) -> PluginInterface | None:
        """加载指定名称的插件。

        加载流程:
        1. 查找清单
        2. 验证权限
        3. 动态导入入口模块
        4. 实例化插件类
        5. 调用 on_load
        """
        if plugin_name in self._loaded:
            return self._loaded[plugin_name]

        manifest = self._manifests.get(plugin_name)
        if not manifest:
            print(f"  [plugin] Plugin '{plugin_name}' not found")
            return None

        # 权限验证
        valid, reason = self._validate_permissions(manifest)
        if not valid:
            print(f"  [plugin] Permission error for '{plugin_name}': {reason}")
            return None

        # 动态导入模块
        entry_path = manifest.path / manifest.entry
        if not entry_path.exists():
            print(f"  [plugin] Entry file not found: {entry_path}")
            return None

        try:
            spec = importlib.util.spec_from_file_location(
                f"plugin_{plugin_name}", str(entry_path)
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception as e:
            print(f"  [plugin] Failed to import '{plugin_name}': {e}")
            return None

        # 实例化插件类
        plugin_cls = getattr(module, manifest.plugin_class, None)
        if plugin_cls is None:
            print(
                f"  [plugin] Class '{manifest.plugin_class}' "
                f"not found in {manifest.entry}"
            )
            return None

        try:
            plugin = plugin_cls()
        except Exception as e:
            print(f"  [plugin] Failed to instantiate '{plugin_name}': {e}")
            return None

        # 调用 on_load
        context = {
            "permissions": manifest.permissions,
            "plugin_dir": str(manifest.path),
        }
        try:
            plugin.on_load(context)
        except Exception as e:
            print(f"  [plugin] on_load failed for '{plugin_name}': {e}")
            return None

        self._loaded[plugin_name] = plugin
        return plugin

    def unload(self, plugin_name: str) -> bool:
        """卸载指定插件。"""
        plugin = self._loaded.get(plugin_name)
        if not plugin:
            return False

        try:
            plugin.on_unload()
        except Exception as e:
            print(f"  [plugin] on_unload error for '{plugin_name}': {e}")

        del self._loaded[plugin_name]
        return True

    def load_all(self) -> list[str]:
        """加载所有已发现的插件。"""
        loaded_names = []
        for name in self._manifests:
            if self.load(name):
                loaded_names.append(name)
        return loaded_names

    def get_all_tools(self) -> list[dict]:
        """收集所有已加载插件注册的工具。"""
        tools = []
        for plugin in self._loaded.values():
            tools.extend(plugin.register_tools())
        return tools

    def get_all_hooks(self) -> list[dict]:
        """收集所有已加载插件注册的钩子。"""
        hooks = []
        for plugin in self._loaded.values():
            hooks.extend(plugin.register_hooks())
        return hooks

    def get_all_commands(self) -> dict[str, Callable]:
        """收集所有已加载插件注册的命令。"""
        commands = {}
        for plugin in self._loaded.values():
            commands.update(plugin.register_commands())
        return commands

    def list_plugins(self) -> str:
        """列出所有插件的状态。"""
        if not self._manifests:
            return "(no plugins discovered)"

        lines = []
        for name, manifest in self._manifests.items():
            status = "loaded" if name in self._loaded else "available"
            lines.append(
                f"  [{status}] {name} v{manifest.version}"
                f" - {manifest.description}"
            )
        return "\n".join(lines)


# =============================================================
#  8.3.4 示例插件(内置, 用于演示)
# =============================================================

class TimestampPlugin(PluginInterface):
    """示例插件: 为每次写入的文件自动添加时间戳注释。"""

    @property
    def name(self) -> str:
        return "timestamp"

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def description(self) -> str:
        return "Adds timestamp header comments to written files"

    def on_load(self, context: dict) -> None:
        print(f"  [timestamp plugin] Loaded with permissions: {context.get('permissions', [])}")

    def on_unload(self) -> None:
        print("  [timestamp plugin] Unloaded")

    def register_commands(self) -> dict[str, Callable]:
        def cmd_timestamp(args: str, context: dict) -> str:
            from datetime import datetime
            return f"Current timestamp: {datetime.now().isoformat()}"

        return {"timestamp": cmd_timestamp}


class StatsPlugin(PluginInterface):
    """示例插件: 提供工具调用统计命令。"""

    @property
    def name(self) -> str:
        return "stats"

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def description(self) -> str:
        return "Tool call statistics and session analytics"

    def register_commands(self) -> dict[str, Callable]:
        def cmd_stats(args: str, context: dict) -> str:
            history = context.get("tool_calls_history", [])
            if not history:
                return "No tool calls recorded yet."

            from collections import Counter
            counter = Counter(h["tool"] for h in history)
            lines = ["Tool call statistics:"]
            for tool, count in counter.most_common():
                lines.append(f"  {tool}: {count}")
            lines.append(f"  Total: {len(history)}")
            return "\n".join(lines)

        return {"stats": cmd_stats}


# =============================================================
#  演示
# =============================================================

if __name__ == "__main__":
    print("=== 插件系统演示 ===\n")

    # 1. 创建示例插件目录结构
    demo_dir = Path(__file__).parent / "_demo_plugins"
    ts_dir = demo_dir / "timestamp-plugin"
    ts_dir.mkdir(parents=True, exist_ok=True)

    # 写入 plugin.json
    manifest_data = {
        "name": "timestamp",
        "version": "1.0.0",
        "description": "Adds timestamp headers",
        "entry": "main.py",
        "class": "TimestampPlugin",
        "permissions": ["file_write"],
    }
    with open(ts_dir / "plugin.json", "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2)

    # 写入 main.py (将内置的 TimestampPlugin 类写入文件)
    plugin_code = '''
from datetime import datetime


class TimestampPlugin:
    @property
    def name(self):
        return "timestamp"

    @property
    def version(self):
        return "1.0.0"

    @property
    def description(self):
        return "Adds timestamp headers"

    def on_load(self, context):
        print(f"  [timestamp] Loaded, permissions: {context.get('permissions', [])}")

    def on_unload(self):
        print("  [timestamp] Unloaded")

    def register_tools(self):
        return []

    def register_hooks(self):
        return []

    def register_commands(self):
        def cmd_timestamp(args, context):
            return f"Current time: {datetime.now().isoformat()}"
        return {"timestamp": cmd_timestamp}
'''
    with open(ts_dir / "main.py", "w", encoding="utf-8") as f:
        f.write(plugin_code)

    # 2. 加载插件
    print("--- Plugin Discovery ---")
    loader = PluginLoader(str(demo_dir))
    manifests = loader.discover()
    print(f"  Found {len(manifests)} plugin(s)")

    for m in manifests:
        print(f"  - {m.name} v{m.version}: {m.description}")

    # 3. 加载插件
    print("\n--- Plugin Loading ---")
    loaded = loader.load_all()
    print(f"  Loaded: {loaded}")

    # 4. 获取命令
    print("\n--- Plugin Commands ---")
    commands = loader.get_all_commands()
    for cmd_name, cmd_func in commands.items():
        result = cmd_func("", {})
        print(f"  /{cmd_name} -> {result}")

    # 5. 列出插件状态
    print("\n--- Plugin Status ---")
    print(loader.list_plugins())

    # 6. 卸载插件
    print("\n--- Unloading ---")
    loader.unload("timestamp")
    print(loader.list_plugins())

    # 7. 清理演示文件
    import shutil
    shutil.rmtree(demo_dir, ignore_errors=True)
    print("\n(demo files cleaned up)")

    # 8. 内置插件演示
    print("\n--- Built-in Plugins ---")
    ts = TimestampPlugin()
    ts.on_load({"permissions": ["file_write"]})
    cmds = ts.register_commands()
    for name, func in cmds.items():
        print(f"  /{name} -> {func('', {})}")

    stats = StatsPlugin()
    stats_cmds = stats.register_commands()
    sample_history = [
        {"tool": "tool_read_file"},
        {"tool": "tool_read_file"},
        {"tool": "tool_write_file"},
    ]
    for name, func in stats_cmds.items():
        result = func("", {"tool_calls_history": sample_history})
        print(f"  /{name} ->\n{result}")
