"""提供者注册表 - LLM提供者元数据的单一数据源

本模块是所有LLM提供者配置的中央注册表，包含:
- 提供者的识别信息（名称、关键词）
- 模型名称前缀规则
- API密钥和环境变量映射
- 网关和本地部署的检测逻辑
- 每个模型的特殊参数覆盖

添加新提供者的步骤:
  1. 在下方PROVIDERS元组中添加一个ProviderSpec
  2. 在config/schema.py的ProvidersConfig中添加对应字段
  完成！环境变量、前缀、配置匹配、状态显示都会自动生效

注册表顺序很重要:
- 决定了匹配优先级和后备选择
- 网关提供者（OpenRouter、AiHubMix）放在最前面
- 每个条目都写明所有字段，方便复制作为模板

术语说明:
- **网关（Gateway）**: 可以路由任意模型的中转服务（如OpenRouter）
- **本地部署（Local）**: 本地运行的模型服务（如vLLM、Ollama）
- **标准提供者**: 官方API服务（如Anthropic、OpenAI）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProviderSpec:
    """单个LLM提供者的元数据规范

    这是提供者配置的核心数据结构，包含了提供者的所有特征信息。
    参考下方PROVIDERS元组中的实际示例来理解每个字段的用法。

    占位符（用于env_extras值）:
      {api_key}  — 用户的API密钥
      {api_base} — 来自config的api_base，或此规范的default_api_base

    字段分类:

    **身份识别**:
    - name: 配置字段名（如"dashscope"、"anthropic"）
    - keywords: 用于模型名称匹配的关键词（小写）
    - env_key: LiteLLM��境变量名（如"DASHSCOPE_API_KEY"）
    - display_name: 在`nanobot status`中显示的名称

    **模型前缀处理**:
    - litellm_prefix: 添加到模型名前的前缀（如"dashscope"会使模型变成"dashscope/qwen-max"）
    - skip_prefixes: 如果模型名已包含这些前缀，则跳过添加

    **环境变量**:
    - env_extras: 额外的环境变量，格式为元组的元组
      例如: (("ZHIPUAI_API_KEY", "{api_key}"),)

    **网关和本地��测**:
    - is_gateway: 是否为网关服务（可路由任意模型）
    - is_local: 是否为本地部署
    - detect_by_key_prefix: 通过API密钥前缀检测（如"sk-or-"）
    - detect_by_base_keyword: 通过api_base中的关键词检测（如"openrouter"）
    - default_api_base: 默认的API基地址

    **特殊行为**:
    - strip_model_prefix: 在重新添加前缀前，先剥离模型名中的"provider/"前缀
    - model_overrides: 针对特定模型的参数覆盖
      例如: (("kimi-k2.5", {"temperature": 1.0}),)

    Attributes:
        name: 配置字段名
        keywords: 模型名匹配关键词
        env_key: LiteLLM环境变量
        display_name: 显示名称
        litellm_prefix: LiteLLM路由前缀
        skip_prefixes: 跳过添加前缀的条件
        env_extras: 额外环境变量
        is_gateway: 是否为网关
        is_local: 是否为本地部署
        detect_by_key_prefix: API密钥前缀检测
        detect_by_base_keyword: API基地址关键词检测
        default_api_base: 默认API基地址
        strip_model_prefix: 是否剥离模型前缀
        model_overrides: 模型参数覆盖
    """

    # 身份识别
    name: str                       # 配置字段名，如"dashscope"
    keywords: tuple[str, ...]       # 模型名匹配关键词（小写）
    env_key: str                    # LiteLLM环境变量，如"DASHSCOPE_API_KEY"
    display_name: str = ""          # 在`nanobot status`中显示

    # 模型前缀处理
    litellm_prefix: str = ""                 # "dashscope" → 模型变成 "dashscope/{model}"
    skip_prefixes: tuple[str, ...] = ()      # 如果模型已有这些前缀，不再添加

    # 额外环境变量，如 (("ZHIPUAI_API_KEY", "{api_key}"),)
    env_extras: tuple[tuple[str, str], ...] = ()

    # 网关 / 本地部署检测
    is_gateway: bool = False                 # 可路由任意模型（OpenRouter、AiHubMix）
    is_local: bool = False                   # 本地部署（vLLM、Ollama）
    detect_by_key_prefix: str = ""           # 匹配api_key前缀，如"sk-or-"
    detect_by_base_keyword: str = ""         # 匹配api_base中的关键词
    default_api_base: str = ""               # 默认基地址

    # 网关特殊行为
    strip_model_prefix: bool = False         # 先剥离"provider/"再重新添加前缀

    # 每个模型的参数覆盖，如 (("kimi-k2.5", {"temperature": 1.0}),)
    model_overrides: tuple[tuple[str, dict[str, Any]], ...] = ()

    @property
    def label(self) -> str:
        """返回显示标签（优先使用display_name）

        Returns:
            str: 显示名称或首字母大写的name
        """
        return self.display_name or self.name.title()


# ---------------------------------------------------------------------------
# 提供者注册表 - 核心配置。顺序 = 优先级。可复制任意条目作为模板。
# ---------------------------------------------------------------------------

PROVIDERS: tuple[ProviderSpec, ...] = (

    # === 网关服务（通过api_key/api_base检测，而非模型名） ===================
    # 网关可以路由任意模型，所以在后备选择中优先级最高。

    # OpenRouter: 全球网关服务，密钥以"sk-or-"开头
    ProviderSpec(
        name="openrouter",
        keywords=("openrouter",),
        env_key="OPENROUTER_API_KEY",
        display_name="OpenRouter",
        litellm_prefix="openrouter",        # claude-3 → openrouter/claude-3
        skip_prefixes=(),
        env_extras=(),
        is_gateway=True,
        is_local=False,
        detect_by_key_prefix="sk-or-",      # 通过密钥前缀自动检测
        detect_by_base_keyword="openrouter",
        default_api_base="https://openrouter.ai/api/v1",
        strip_model_prefix=False,
        model_overrides=(),
    ),

    # AiHubMix: 全球网关服务，OpenAI兼容接口
    # strip_model_prefix=True: 它不理解"anthropic/claude-3"格式，
    # 所以先剥离为"claude-3"，然后重新添加前缀为"openai/claude-3"
    ProviderSpec(
        name="aihubmix",
        keywords=("aihubmix",),
        env_key="OPENAI_API_KEY",           # OpenAI兼容
        display_name="AiHubMix",
        litellm_prefix="openai",            # → openai/{model}
        skip_prefixes=(),
        env_extras=(),
        is_gateway=True,
        is_local=False,
        detect_by_key_prefix="",
        detect_by_base_keyword="aihubmix",  # 通过URL关键词检测
        default_api_base="https://aihubmix.com/v1",
        strip_model_prefix=True,            # anthropic/claude-3 → claude-3 → openai/claude-3
        model_overrides=(),
    ),

    # === 标准提供者（通过模型名关键词匹配） ==============================

    # Anthropic: LiteLLM原生识别"claude-*"，无需前缀
    ProviderSpec(
        name="anthropic",
        keywords=("anthropic", "claude"),
        env_key="ANTHROPIC_API_KEY",
        display_name="Anthropic",
        litellm_prefix="",                  # 无需前缀
        skip_prefixes=(),
        env_extras=(),
        is_gateway=False,
        is_local=False,
        detect_by_key_prefix="",
        detect_by_base_keyword="",
        default_api_base="",
        strip_model_prefix=False,
        model_overrides=(),
    ),

    # OpenAI: LiteLLM原生识别"gpt-*"，无需前缀
    ProviderSpec(
        name="openai",
        keywords=("openai", "gpt"),
        env_key="OPENAI_API_KEY",
        display_name="OpenAI",
        litellm_prefix="",
        skip_prefixes=(),
        env_extras=(),
        is_gateway=False,
        is_local=False,
        detect_by_key_prefix="",
        detect_by_base_keyword="",
        default_api_base="",
        strip_model_prefix=False,
        model_overrides=(),
    ),

    # DeepSeek: 需要"deepseek/"前缀用于LiteLLM路由
    ProviderSpec(
        name="deepseek",
        keywords=("deepseek",),
        env_key="DEEPSEEK_API_KEY",
        display_name="DeepSeek",
        litellm_prefix="deepseek",          # deepseek-chat → deepseek/deepseek-chat
        skip_prefixes=("deepseek/",),       # 避免重复前缀
        env_extras=(),
        is_gateway=False,
        is_local=False,
        detect_by_key_prefix="",
        detect_by_base_keyword="",
        default_api_base="",
        strip_model_prefix=False,
        model_overrides=(),
    ),

    # Gemini: 需要"gemini/"前缀用于LiteLLM
    ProviderSpec(
        name="gemini",
        keywords=("gemini",),
        env_key="GEMINI_API_KEY",
        display_name="Gemini",
        litellm_prefix="gemini",            # gemini-pro → gemini/gemini-pro
        skip_prefixes=("gemini/",),
        env_extras=(),
        is_gateway=False,
        is_local=False,
        detect_by_key_prefix="",
        detect_by_base_keyword="",
        default_api_base="",
        strip_model_prefix=False,
        model_overrides=(),
    ),

    # Zhipu（智谱AI）: LiteLLM使用"zai/"前缀
    # 同时镜像密钥到ZHIPUAI_API_KEY（某些LiteLLM路径会检查）
    # skip_prefixes: 当已通过网关路由时不添加"zai/"
    ProviderSpec(
        name="zhipu",
        keywords=("zhipu", "glm", "zai"),
        env_key="ZAI_API_KEY",
        display_name="Zhipu AI",
        litellm_prefix="zai",              # glm-4 → zai/glm-4
        skip_prefixes=("zhipu/", "zai/", "openrouter/", "hosted_vllm/"),
        env_extras=(
            ("ZHIPUAI_API_KEY", "{api_key}"),  # 镜像密钥
        ),
        is_gateway=False,
        is_local=False,
        detect_by_key_prefix="",
        detect_by_base_keyword="",
        default_api_base="",
        strip_model_prefix=False,
        model_overrides=(),
    ),

    # DashScope（阿里云灵积）: Qwen模型，需要"dashscope/"前缀
    ProviderSpec(
        name="dashscope",
        keywords=("qwen", "dashscope"),
        env_key="DASHSCOPE_API_KEY",
        display_name="DashScope",
        litellm_prefix="dashscope",         # qwen-max → dashscope/qwen-max
        skip_prefixes=("dashscope/", "openrouter/"),
        env_extras=(),
        is_gateway=False,
        is_local=False,
        detect_by_key_prefix="",
        detect_by_base_keyword="",
        default_api_base="",
        strip_model_prefix=False,
        model_overrides=(),
    ),

    # Moonshot（月之暗面）: Kimi模型，需要"moonshot/"前缀
    # LiteLLM需要MOONSHOT_API_BASE环境变量来定位端点
    # Kimi K2.5 API强制要求temperature >= 1.0
    ProviderSpec(
        name="moonshot",
        keywords=("moonshot", "kimi"),
        env_key="MOONSHOT_API_KEY",
        display_name="Moonshot",
        litellm_prefix="moonshot",          # kimi-k2.5 → moonshot/kimi-k2.5
        skip_prefixes=("moonshot/", "openrouter/"),
        env_extras=(
            ("MOONSHOT_API_BASE", "{api_base}"),
        ),
        is_gateway=False,
        is_local=False,
        detect_by_key_prefix="",
        detect_by_base_keyword="",
        default_api_base="https://api.moonshot.ai/v1",   # 国际版；中国用api.moonshot.cn
        strip_model_prefix=False,
        model_overrides=(
            ("kimi-k2.5", {"temperature": 1.0}),  # Kimi K2.5的温度限制
        ),
    ),

    # MiniMax: 需要"minimax/"前缀用于LiteLLM路由
    # 使用OpenAI兼容API（api.minimax.io/v1）
    ProviderSpec(
        name="minimax",
        keywords=("minimax",),
        env_key="MINIMAX_API_KEY",
        display_name="MiniMax",
        litellm_prefix="minimax",            # MiniMax-M2.1 → minimax/MiniMax-M2.1
        skip_prefixes=("minimax/", "openrouter/"),
        env_extras=(),
        is_gateway=False,
        is_local=False,
        detect_by_key_prefix="",
        detect_by_base_keyword="",
        default_api_base="https://api.minimax.io/v1",
        strip_model_prefix=False,
        model_overrides=(),
    ),

    # === 本地部署（通过配置键检测，而非api_base） ========================

    # vLLM / 任何OpenAI兼容的本地服务器
    # 当配置键为"vllm"时检测（provider_name="vllm"）
    ProviderSpec(
        name="vllm",
        keywords=("vllm",),
        env_key="HOSTED_VLLM_API_KEY",
        display_name="vLLM/Local",
        litellm_prefix="hosted_vllm",      # Llama-3-8B → hosted_vllm/Llama-3-8B
        skip_prefixes=(),
        env_extras=(),
        is_gateway=False,
        is_local=True,
        detect_by_key_prefix="",
        detect_by_base_keyword="",
        default_api_base="",                # 用户必须在配置中提供
        strip_model_prefix=False,
        model_overrides=(),
    ),

    # === 辅助服务（不是主要的LLM提供者） =================================

    # Groq: 主要用于Whisper语音转录，也可用于LLM
    # 需要"groq/"前缀用于LiteLLM路由。放在最后 - 很少作为后备选择
    ProviderSpec(
        name="groq",
        keywords=("groq",),
        env_key="GROQ_API_KEY",
        display_name="Groq",
        litellm_prefix="groq",              # llama3-8b-8192 → groq/llama3-8b-8192
        skip_prefixes=("groq/",),
        env_extras=(),
        is_gateway=False,
        is_local=False,
        detect_by_key_prefix="",
        detect_by_base_keyword="",
        default_api_base="",
        strip_model_prefix=False,
        model_overrides=(),
    ),
)


# ---------------------------------------------------------------------------
# 查找辅助函数
# ---------------------------------------------------------------------------

def find_by_model(model: str) -> ProviderSpec | None:
    """通过模型名关键词匹配标准提供者（不区分大小写）

    跳过网关和本地部署 - 它们通过api_key/api_base匹配。

    匹配逻辑:
    遍历PROVIDERS列表，检查模型名（小写）是否包含任何keywords。

    Args:
        model: 模型名称（如"claude-3-5-sonnet"、"gpt-4"、"qwen-max"）

    Returns:
        ProviderSpec | None: 匹配的提供者规范，未找到返回None

    Example:
        >>> find_by_model("claude-3-5-sonnet-20241022")
        ProviderSpec(name="anthropic", ...)
        >>> find_by_model("qwen-max")
        ProviderSpec(name="dashscope", ...)
    """
    model_lower = model.lower()
    for spec in PROVIDERS:
        if spec.is_gateway or spec.is_local:
            continue
        if any(kw in model_lower for kw in spec.keywords):
            return spec
    return None


def find_gateway(
    provider_name: str | None = None,
    api_key: str | None = None,
    api_base: str | None = None,
) -> ProviderSpec | None:
    """检测网关/本地提供者

    检测优先级:
      1. provider_name - 如果它映射到网关/本地规范，直接使用
      2. api_key前缀 - 如"sk-or-" → OpenRouter
      3. api_base关键词 - 如URL中包含"aihubmix" → AiHubMix

    带自定义api_base的标准提供者（如通过代理的DeepSeek）
    不会被误认为vLLM - 旧的后备逻辑已移除。

    Args:
        provider_name: 配置中的提供者名称（可选）
        api_key: API密钥（可选）
        api_base: API基地址（可选）

    Returns:
        ProviderSpec | None: 匹配的网关/本地提供者规范，未找到返回None

    Example:
        >>> find_gateway(api_key="sk-or-abc123")
        ProviderSpec(name="openrouter", ...)
        >>> find_gateway(api_base="https://aihubmix.com/v1")
        ProviderSpec(name="aihubmix", ...)
    """
    # 1. 通过配置键直接匹配
    if provider_name:
        spec = find_by_name(provider_name)
        if spec and (spec.is_gateway or spec.is_local):
            return spec

    # 2. 通过api_key前缀 / api_base关键词自动检测
    for spec in PROVIDERS:
        if spec.detect_by_key_prefix and api_key and api_key.startswith(spec.detect_by_key_prefix):
            return spec
        if spec.detect_by_base_keyword and api_base and spec.detect_by_base_keyword in api_base:
            return spec

    return None


def find_by_name(name: str) -> ProviderSpec | None:
    """通过配置字段名查找提供者规范

    Args:
        name: 配置字段名（如"dashscope"、"anthropic"、"vllm"）

    Returns:
        ProviderSpec | None: 匹配的提供者规范，未找到返回None

    Example:
        >>> find_by_name("dashscope")
        ProviderSpec(name="dashscope", ...)
    """
    for spec in PROVIDERS:
        if spec.name == name:
            return spec
    return None
