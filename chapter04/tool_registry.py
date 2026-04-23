"""
4.2 - 工具注册表
提供 ToolRegistry 类, 通过 @tool 装饰器实现:
- 从函数签名和类型提示自动生成 OpenAI 工具 Schema
- 从 Google 风格文档字符串提取工具描述和参数说明
- 按类别管理工具, 支持查询与发现

运行方式:
    uv run python chapter04/tool_registry.py
"""
import inspect
import json
from typing import get_type_hints

# Python 类型 -> JSON Schema 类型
_TYPE_MAP = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


def _parse_docstring(doc: str) -> tuple[str, dict[str, str]]:
    """解析 Google 风格文档字符串, 提取函数描述和参数说明。

    输入示例:
        '''读取文件内容。

        Args:
            path: 文件路径
        '''
    返回: ("读取文件内容。", {"path": "文件路径"})
    """
    lines = doc.strip().split("\n")
    desc_lines = []
    param_docs = {}
    in_args = False

    for line in lines:
        stripped = line.strip()
        if stripped.lower().startswith("args:"):
            in_args = True
            continue
        if stripped.lower().startswith(("returns:", "raises:", "example")):
            in_args = False
            continue
        if in_args and ":" in stripped:
            key, val = stripped.split(":", 1)
            param_docs[key.strip()] = val.strip()
        elif not in_args and stripped:
            desc_lines.append(stripped)

    return " ".join(desc_lines), param_docs


class ToolRegistry:
    """工具注册表: 管理工具的注册、Schema 生成与发现。"""

    def __init__(self):
        self._tools: dict[str, dict] = {}

    def tool(self, *, category: str = "general"):
        """装饰器: 将一个普通函数注册为 Agent 可调用的工具。

        用法:
            @registry.tool(category="file")
            def read_file(path: str) -> str:
                '''读取文件内容。

                Args:
                    path: 文件路径
                '''
                return Path(path).read_text()
        """
        def decorator(func):
            name = func.__name__
            doc = inspect.getdoc(func) or name
            func_desc, param_docs = _parse_docstring(doc)

            # 从函数签名和类型提示构建参数 Schema
            hints = get_type_hints(func)
            sig = inspect.signature(func)
            properties = {}
            required = []

            for pname, param in sig.parameters.items():
                ptype = hints.get(pname, str)
                json_type = _TYPE_MAP.get(ptype, "string")
                prop: dict = {"type": json_type}
                if pname in param_docs:
                    prop["description"] = param_docs[pname]
                properties[pname] = prop
                if param.default is inspect.Parameter.empty:
                    required.append(pname)

            schema = {
                "type": "function",
                "function": {
                    "name": name,
                    "description": func_desc,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                },
            }

            self._tools[name] = {
                "function": func,
                "schema": schema,
                "category": category,
            }
            return func
        return decorator

    def get_openai_schemas(self) -> list[dict]:
        """返回所有工具的 OpenAI 格式 Schema 列表, 可直接传给 API。"""
        return [t["schema"] for t in self._tools.values()]

    def get_function(self, name: str):
        """根据工具名获取对应的 Python 函数, 不存在返回 None。"""
        entry = self._tools.get(name)
        return entry["function"] if entry else None

    def get_schema(self, name: str) -> dict | None:
        """根据工具名获取对应的 Schema。"""
        entry = self._tools.get(name)
        return entry["schema"] if entry else None

    def list_tools(self, category: str | None = None) -> dict:
        """列出工具, 可按类别过滤。"""
        if category:
            return {n: t for n, t in self._tools.items()
                    if t["category"] == category}
        return dict(self._tools)

    def get_categories(self) -> list[str]:
        """获取所有已注册的工具类别。"""
        return list({t["category"] for t in self._tools.values()})

    def __len__(self):
        return len(self._tools)

    def __contains__(self, name):
        return name in self._tools

    def __repr__(self):
        return f"ToolRegistry({len(self._tools)} tools)"


# =============================================================
#  演示: 装饰器注册 + 自动 Schema 生成
# =============================================================

if __name__ == "__main__":
    registry = ToolRegistry()

    @registry.tool(category="math")
    def add(a: int, b: int) -> int:
        """将两个整数相加并返回结果。

        Args:
            a: 第一个加数
            b: 第二个加数
        """
        return a + b

    @registry.tool(category="text")
    def count_words(text: str) -> int:
        """统计文本中的单词数量。

        Args:
            text: 要统计的文本内容
        """
        return len(text.split())

    print("=== 工具注册表演示 ===\n")
    print(f"已注册 {len(registry)} 个工具")
    print(f"类别: {registry.get_categories()}\n")

    # 展示自动生成的 Schema
    for schema in registry.get_openai_schemas():
        print(json.dumps(schema, indent=2, ensure_ascii=False))
        print()

    # 直接调用已注册的函数
    print(f"add(3, 5) = {registry.get_function('add')(3, 5)}")
    print(f"count_words('hello world foo') = "
          f"{registry.get_function('count_words')('hello world foo')}")
