"""工具的抽象基类

本模块定义了所有代理工具的基类（Tool），提供统一的接口规范。

工具（Tool）是代理与外部环境交互的能力，包括:
- 文件操作（读写编辑）
- Shell命令执行
- Web搜索和抓取
- 消息发送
- 定时任务
- 子代理创建

所有工具必须实现:
1. name: 工具名称
2. description: 功能描述
3. parameters: JSON Schema参数定义
4. execute(): 执行逻辑

工具调用流程:
    LLM生成工具调用 → ToolRegistry查找工具 → 验证参数 → execute() → 返回结果
"""

from abc import ABC, abstractmethod
from typing import Any


class Tool(ABC):
    """代理工具的抽象基类

    工具是代理可以调用的功能单元，使代理能够:
    - 与文件系统交互
    - 执行系统命令
    - 访问网络资源
    - 发送消息到其他渠道
    - 管理定时任务
    - 创建子代理

    工具设计原则:
    1. **幂等性**: 相同参数应产生相同结果
    2. **安全性**: 验证参数，限制危险操作
    3. **错误处理**: 捕获异常，返回清晰的错误信息
    4. **简洁性**: 返回结果应简洁明了

    实现自定义工具的步骤:
    1. 继承Tool类
    2. 实现name、description、parameters属性
    3. 实现execute()方法
    4. 在ToolRegistry中注册

    Example:
        ```python
        class MyTool(Tool):
            @property
            def name(self) -> str:
                return "my_tool"

            @property
            def description(self) -> str:
                return "Does something useful"

            @property
            def parameters(self) -> dict[str, Any]:
                return {
                    "type": "object",
                    "properties": {
                        "arg1": {"type": "string", "description": "First argument"}
                    },
                    "required": ["arg1"]
                }

            async def execute(self, arg1: str) -> str:
                return f"Result: {arg1}"
        ```
    """

    # JSON Schema类型到Python类型的映射
    _TYPE_MAP = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "array": list,
        "object": dict,
    }

    @property
    @abstractmethod
    def name(self) -> str:
        """工具名称（用于LLM的函数调用）

        命名规范:
        - 使用snake_case（如read_file、web_search）
        - 简洁明了，不超过20个字符
        - 避免特殊字符

        Returns:
            str: 工具的唯一标识符
        """
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """工具功能描述

        描述应该:
        - 清晰说明工具的作用
        - 包含主要参数的说明
        - 提供使用场景示例
        - 不超过200个字符

        LLM会根据描述来决定是否调用该工具。

        Returns:
            str: 工具的功能描述
        """
        pass

    @property
    @abstractmethod
    def parameters(self) -> dict[str, Any]:
        """工具参数的JSON Schema定义

        必须是一个object类型的JSON Schema，定义:
        - properties: 参数名称和类型
        - required: 必需参数列表
        - 可选的约束（enum、minimum、maximum等）

        Example:
            ```python
            {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path"
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["read", "write"],
                        "description": "Access mode"
                    }
                },
                "required": ["path"]
            }
            ```

        Returns:
            dict: JSON Schema定义
        """
        pass

    @abstractmethod
    async def execute(self, **kwargs: Any) -> str:
        """执行工具逻辑

        这是工具的核心方法，实现具体功能。

        规范:
        - 必须是异步方法（async def）
        - 返回值必须是字符串（LLM只能处理文本）
        - 捕获并处理所有异常，返回错误信息
        - 大型结果应截断或摘要（避免超出上下文窗口）

        Args:
            **kwargs: 工具参数（根据parameters定义）

        Returns:
            str: 工具执行结果（成功或错误信息）

        Example:
            ```python
            async def execute(self, path: str) -> str:
                try:
                    with open(path) as f:
                        return f.read()
                except FileNotFoundError:
                    return f"Error: File {path} not found"
            ```
        """
        pass

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        """验证工具参数是否符合JSON Schema定义

        在执行工具之前调用，确保参数类型和值符合要求。

        验证项:
        - 类型检查（string、integer、boolean等）
        - 必需参数检查（required）
        - 枚举值检查（enum）
        - 数值范围检查（minimum、maximum）
        - 字符串长度检查（minLength、maxLength）
        - 对象和数组的递归验证

        Args:
            params: 待验证的参数字典

        Returns:
            list[str]: 错误列表（空列表表示验证通过）

        Example:
            ```python
            errors = tool.validate_params({"path": 123})
            # 返回: ["path should be string"]
            ```
        """
        schema = self.parameters or {}
        if schema.get("type", "object") != "object":
            raise ValueError(f"Schema must be object type, got {schema.get('type')!r}")
        return self._validate(params, {**schema, "type": "object"}, "")

    def _validate(self, val: Any, schema: dict[str, Any], path: str) -> list[str]:
        """递归验证参数（内部方法）

        Args:
            val: 待验证的值
            schema: JSON Schema定义
            path: 参数路径（用于错误消息）

        Returns:
            list[str]: 错误列表
        """
        t, label = schema.get("type"), path or "parameter"
        if t in self._TYPE_MAP and not isinstance(val, self._TYPE_MAP[t]):
            return [f"{label} should be {t}"]

        errors = []
        if "enum" in schema and val not in schema["enum"]:
            errors.append(f"{label} must be one of {schema['enum']}")
        if t in ("integer", "number"):
            if "minimum" in schema and val < schema["minimum"]:
                errors.append(f"{label} must be >= {schema['minimum']}")
            if "maximum" in schema and val > schema["maximum"]:
                errors.append(f"{label} must be <= {schema['maximum']}")
        if t == "string":
            if "minLength" in schema and len(val) < schema["minLength"]:
                errors.append(f"{label} must be at least {schema['minLength']} chars")
            if "maxLength" in schema and len(val) > schema["maxLength"]:
                errors.append(f"{label} must be at most {schema['maxLength']} chars")
        if t == "object":
            props = schema.get("properties", {})
            for k in schema.get("required", []):
                if k not in val:
                    errors.append(f"missing required {path + '.' + k if path else k}")
            for k, v in val.items():
                if k in props:
                    errors.extend(self._validate(v, props[k], path + '.' + k if path else k))
        if t == "array" and "items" in schema:
            for i, item in enumerate(val):
                errors.extend(self._validate(item, schema["items"], f"{path}[{i}]" if path else f"[{i}]"))
        return errors

    def to_schema(self) -> dict[str, Any]:
        """转换为OpenAI函数调用格式

        生成符合OpenAI Function Calling规范的工具定义，
        LLM Provider会使用此格式向LLM描述可用工具。

        返回格式:
        ```json
        {
            "type": "function",
            "function": {
                "name": "tool_name",
                "description": "Tool description",
                "parameters": { ... }
            }
        }
        ```

        Returns:
            dict: OpenAI格式的工具定义
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            }
        }
