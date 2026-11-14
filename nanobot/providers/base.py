"""LLM提供者的基类接口

本模块定义了与大语言模型（LLM）交互的统一接口。

LLM Provider是代理与AI模型之间的桥梁，负责:
- 发送聊天请求到LLM API
- 处理工具调用请求和响应
- 管理流式输出
- 处理会话状态（如Claude的原生会话）

支持的提供者:
- LiteLLMProvider: 统一调用层（支持多个提供商）
  - Anthropic (Claude)
  - OpenAI (GPT)
  - Google (Gemini)
  - DeepSeek, Moonshot, Zhipu等

设计模式:
    AgentLoop → LLMProvider.chat() → LLM API → LLMResponse
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


@dataclass
class ToolCallRequest:
    """LLM请求的工具调用

    当LLM决定调用工具时，会在响应中返回此结构。

    Attributes:
        id: 工具调用的唯一ID（用于匹配工具结果）
        name: 工具名称（如"read_file"、"web_search"）
        arguments: 工具参数字典
    """
    id: str  # 工具调用ID（如"call_abc123"）
    name: str  # 工具名称
    arguments: dict[str, Any]  # 工具参数


@dataclass
class LLMResponse:
    """LLM提供者的响应

    封装了LLM的响应，可能包含:
    - 文本内容（content）
    - 工具调用请求（tool_calls）
    - 推理过程（reasoning_content，用于思维链模型）
    - 使用统计（usage）
    - 会话信息（response_id、conversation_id）

    Attributes:
        content: LLM生成的文本内容（可为None，如果只有工具调用）
        tool_calls: 工具调用请求列表
        finish_reason: 完成原因（"stop"、"tool_calls"、"length"、"error"等）
        usage: Token使用统计（prompt_tokens、completion_tokens、total_tokens）
        response_id: 响应ID（用于原生会话，如Claude的resp_xxx）
        conversation_id: 会话ID（用于原生会话）
        model: 实际使用的模型名称
        reasoning_content: 推理过程文本（Kimi、DeepSeek-R1等思维链模型）
    """
    content: str | None  # 文本响应
    tool_calls: list[ToolCallRequest] = field(default_factory=list)  # 工具调用列表
    finish_reason: str = "stop"  # 完成原因
    usage: dict[str, int] = field(default_factory=dict)  # Token使用量
    response_id: str | None = None  # 响应ID（原生会话）
    conversation_id: str | None = None  # 会话ID（原生会话）
    model: str | None = None  # 实际模型名称
    reasoning_content: str | None = None  # 推理过程（思维链模型）

    @property
    def has_tool_calls(self) -> bool:
        """检查响应是否包含工具调用

        Returns:
            bool: True表示需要执行工具
        """
        return len(self.tool_calls) > 0


class LLMProvider(ABC):
    """LLM提供者的抽象基类

    定义了与LLM交互的统一接口。每个具体实现（如LiteLLMProvider）
    需要处理特定提供商的API细节，但对外提供一致的接口。

    核心功能:
    1. **chat()**: 发送聊天请求，获取响应
    2. **流式输出**: 通过on_delta回调实时返回文本
    3. **工具调用**: 解析LLM的工具调用请求
    4. **会话管理**: 支持原生会话状态（可选）

    实现要点:
    - 将messages转换为提供商的API格式
    - 将工具定义转换为提供商的格式
    - 处理流式和非流式响应
    - 解析工具调用并转换为ToolCallRequest
    - 处理错误和重试

    Attributes:
        api_key: API密钥
        api_base: API基地址（用于自定义端点或代理）
    """

    def __init__(self, api_key: str | None = None, api_base: str | None = None):
        """初始化LLM提供者

        Args:
            api_key: API密钥（可选，也可通过环境变量设置）
            api_base: API基地址（可选，用于自定义端点）
        """
        self.api_key = api_key
        self.api_base = api_base

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        session_state: dict[str, Any] | None = None,
        on_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        """发送聊天补全请求

        这是与LLM交互的核心方法，发送消息并获取响应。

        消息格式（OpenAI标准）:
        ```python
        [
            {"role": "system", "content": "You are a helpful assistant"},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi! How can I help?"},
            {"role": "tool", "tool_call_id": "xxx", "content": "tool result"}
        ]
        ```

        工具定义格式:
        ```python
        [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read a file",
                    "parameters": {...}  # JSON Schema
                }
            }
        ]
        ```

        流式输出:
        如果提供了on_delta回调，会在生成文本时实时调用:
        ```python
        async def on_delta(text: str):
            print(text, end="", flush=True)
        ```

        原生会话状态（Claude）:
        Claude支持使用previous_response_id减少上下文token消耗:
        ```python
        session_state = {"previous_response_id": "resp_xxx"}
        ```

        Args:
            messages: 消息列表（OpenAI格式）
            tools: 工具定义列表（可选）
            model: 模型标识符（如"claude-3-5-sonnet-20241022"）
            max_tokens: 最大生成token数
            temperature: 采样温度（0.0-2.0，越高越随机）
            session_state: 提供商特定的会话状态（可选）
            on_delta: 流式输出的回调函数（可选）

        Returns:
            LLMResponse: 包含文本内容和/或工具调用的响应

        Example:
            ```python
            response = await provider.chat(
                messages=[
                    {"role": "system", "content": "You are helpful"},
                    {"role": "user", "content": "What's 2+2?"}
                ],
                model="claude-3-5-sonnet-20241022"
            )
            print(response.content)  # "2+2 equals 4"
            ```
        """
        pass

    def supports_native_session(self) -> bool:
        """返回提供者是否支持原生服务端会话

        原生会话（如Claude的会话状态管理）可以:
        - 减少上下文token消耗
        - 提高响应速度
        - 保持更长的对话历史

        Returns:
            bool: True表示支持原生会话
        """
        return False

    @abstractmethod
    def get_default_model(self) -> str:
        """获取此提供者的默认模型

        当用户未指定模型时，使用此默认值。

        Returns:
            str: 默认模型名称（如"claude-3-5-sonnet-20241022"）
        """
        pass
