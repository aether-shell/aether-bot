"""工具注册表，用于动态管理工具

本模块提供了工具的注册、查找和执行功能。

ToolRegistry是工具系统的核心，负责:
- 注册和注销工具
- 按名称查找工具
- 生成工具定义列表（供LLM使用）
- 验证和执行工具调用

工具调用流程:
    LLM返回tool_call → AgentLoop调用registry.execute() →
    验证参数 → 执行工具 → 返回结果
"""

from typing import Any

from nanobot.agent.tools.base import Tool


class ToolRegistry:
    """工具注册表

    管理所有可用工具的注册表，提供统一的工具管理接口。

    主要功能:
    1. **注册管理**: register()、unregister()
    2. **工具查找**: get()、has()
    3. **定义生成**: get_definitions()
    4. **工具执行**: execute()

    使用示例:
    ```python
    registry = ToolRegistry()
    registry.register(ReadFileTool())
    registry.register(WebSearchTool())

    # 获取所有工具定义（发送给LLM）
    tools = registry.get_definitions()

    # 执行工具调用
    result = await registry.execute("read_file", {"path": "test.txt"})
    ```

    Attributes:
        _tools: 工具名称到工具实例的映射
    """

    def __init__(self):
        """初始化空的工具注册表"""
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """注册一个工具

        将工具添加到注册表中，使其可被LLM调用。
        如果工具名称已存在，会覆盖旧工具。

        Args:
            tool: 工具实例（必须继承自Tool基类）
        """
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        """按名称注销工具

        从注册表中移除指定工具，LLM将无法再调用它。
        如果工具不存在，不会抛出错误。

        Args:
            name: 工具名称
        """
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        """按名称获取工具实例

        Args:
            name: 工具名称

        Returns:
            Tool | None: 工具实例，如果不存在则返回None
        """
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        """检查工具是否已注册

        Args:
            name: 工具名称

        Returns:
            bool: 工具是否存在
        """
        return name in self._tools

    def get_definitions(self) -> list[dict[str, Any]]:
        """获取所有工具的定义（OpenAI格式）

        生成符合OpenAI Function Calling规范的工具定义列表，
        会被传递给LLM，让LLM知道有哪些工具可用。

        Returns:
            list[dict]: 工具定义列表

        Example:
            ```python
            [
                {
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "description": "Read a file",
                        "parameters": { ... }
                    }
                },
                ...
            ]
            ```
        """
        return [tool.to_schema() for tool in self._tools.values()]

    async def execute(self, name: str, params: dict[str, Any]) -> str:
        """按名称执行工具

        执行流程:
        1. 查找工具（如果不存在，返回错误）
        2. 验证参数（根据JSON Schema）
        3. 执行工具
        4. 捕获异常并返回错误信息

        Args:
            name: 工具名称
            params: 工具参数字典

        Returns:
            str: 工具执行结果或错误信息

        Example:
            ```python
            result = await registry.execute(
                "read_file",
                {"path": "/path/to/file.txt"}
            )
            ```
        """
        tool = self._tools.get(name)
        if not tool:
            return f"Error: Tool '{name}' not found"

        try:
            # 验证参数
            errors = tool.validate_params(params)
            if errors:
                return f"Error: Invalid parameters for tool '{name}': " + "; ".join(errors)
            # 执行工具
            return await tool.execute(**params)
        except Exception as e:
            return f"Error executing {name}: {str(e)}"

    @property
    def tool_names(self) -> list[str]:
        """获取所有已注册的工具名称列表

        Returns:
            list[str]: 工具名称列表
        """
        return list(self._tools.keys())

    def __len__(self) -> int:
        """返回已注册的工具数量"""
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        """支持 'in' 操作符检查工具是否存在"""
        return name in self._tools
