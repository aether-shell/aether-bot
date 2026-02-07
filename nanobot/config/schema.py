"""Configuration schema using Pydantic."""

from pathlib import Path
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


class WhatsAppConfig(BaseModel):
    """WhatsApp channel configuration."""
    enabled: bool = False
    bridge_url: str = "ws://localhost:3001"
    allow_from: list[str] = Field(default_factory=list)  # Allowed phone numbers


class TelegramConfig(BaseModel):
    """Telegram channel configuration."""
    enabled: bool = False
    token: str = ""  # Bot token from @BotFather
    allow_from: list[str] = Field(default_factory=list)  # Allowed user IDs or usernames
    proxy: str | None = None  # HTTP/SOCKS5 proxy URL, e.g. "http://127.0.0.1:7890" or "socks5://127.0.0.1:1080"


class FeishuConfig(BaseModel):
    """Feishu/Lark channel configuration using WebSocket long connection."""
    enabled: bool = False
    app_id: str = ""  # App ID from Feishu Open Platform
    app_secret: str = ""  # App Secret from Feishu Open Platform
    encrypt_key: str = ""  # Encrypt Key for event subscription (optional)
    verification_token: str = ""  # Verification Token for event subscription (optional)
    allow_from: list[str] = Field(default_factory=list)  # Allowed user open_ids
    auto_react: bool = False  # Add reaction emoji on incoming messages
    show_context: bool = False  # Append context status to outbound messages


class DiscordConfig(BaseModel):
    """Discord channel configuration."""
    enabled: bool = False
    token: str = ""  # Bot token from Discord Developer Portal
    allow_from: list[str] = Field(default_factory=list)  # Allowed user IDs
    gateway_url: str = "wss://gateway.discord.gg/?v=10&encoding=json"
    intents: int = 37377  # GUILDS + GUILD_MESSAGES + DIRECT_MESSAGES + MESSAGE_CONTENT


class WebChannelConfig(BaseModel):
    """Web/PWA channel configuration."""
    enabled: bool = False
    host: str = "0.0.0.0"
    port: int = 8080
    secret: str = ""  # Invite code / JWT secret
    token_expiry_days: int = 30
    rate_limit_rpm: int = 20
    allow_from: list[str] = Field(default_factory=list)
    show_context: bool = False  # Show context status (mode/tokens/ratio) in messages
    max_upload_mb: int = 10  # Max file upload size in MB


class ChannelsConfig(BaseModel):
    """Configuration for chat channels."""
    whatsapp: WhatsAppConfig = Field(default_factory=WhatsAppConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    discord: DiscordConfig = Field(default_factory=DiscordConfig)
    feishu: FeishuConfig = Field(default_factory=FeishuConfig)
    web: WebChannelConfig = Field(default_factory=WebChannelConfig)


class ContextConfig(BaseModel):
    """Conversation context configuration."""
    window_tokens: int = 32000
    reserve_tokens: int = 1024
    summarize_threshold: float = 0.75
    hard_limit_threshold: float = 0.9
    recent_messages: int = 20
    min_recent_messages: int = 6
    summary_max_tokens: int = 1200
    summary_model: str | None = None
    enable_native_session: bool = True


class AgentDefaults(BaseModel):
    """Default agent configuration."""
    workspace: str = "~/.nanobot/workspace"
    model: str = "anthropic/claude-opus-4-5"
    max_tokens: int = 8192
    temperature: float = 0.7
    max_tool_iterations: int = 20
    stream: bool = False
    stream_min_chars: int = 120
    stream_min_interval_s: float = 0.5
    context: ContextConfig = Field(default_factory=ContextConfig)


class AgentsConfig(BaseModel):
    """Agent configuration."""
    defaults: AgentDefaults = Field(default_factory=AgentDefaults)


class ProviderConfig(BaseModel):
    """LLM provider configuration."""
    api_key: str = ""
    api_base: str | None = None
    api_type: str | None = None  # e.g. "openai-responses" for /v1/responses
    headers: dict[str, str] | None = None  # Extra headers for provider requests
    proxy: str | None = None  # Optional proxy URL, e.g. "http://127.0.0.1:7897"
    drop_params: bool = False  # Drop optional params for strict gateways
    extra_headers: dict[str, str] | None = None  # Custom headers (e.g. APP-Code for AiHubMix)


class ProvidersConfig(BaseModel):
    """Configuration for LLM providers."""
    anthropic: ProviderConfig = Field(default_factory=ProviderConfig)
    openai: ProviderConfig = Field(default_factory=ProviderConfig)
    openrouter: ProviderConfig = Field(default_factory=ProviderConfig)
    deepseek: ProviderConfig = Field(default_factory=ProviderConfig)
    groq: ProviderConfig = Field(default_factory=ProviderConfig)
    zhipu: ProviderConfig = Field(default_factory=ProviderConfig)
    dashscope: ProviderConfig = Field(default_factory=ProviderConfig)  # 阿里云通义千问
    vllm: ProviderConfig = Field(default_factory=ProviderConfig)
    gemini: ProviderConfig = Field(default_factory=ProviderConfig)
    moonshot: ProviderConfig = Field(default_factory=ProviderConfig)
    aihubmix: ProviderConfig = Field(default_factory=ProviderConfig)  # AiHubMix API gateway


class GatewayConfig(BaseModel):
    """Gateway/server configuration."""
    host: str = "0.0.0.0"
    port: int = 18790


class WebSearchConfig(BaseModel):
    """Web search tool configuration."""
    api_key: str = ""  # Brave Search API key
    max_results: int = 5


class WebToolsConfig(BaseModel):
    """Web tools configuration."""
    search: WebSearchConfig = Field(default_factory=WebSearchConfig)


class ExecToolConfig(BaseModel):
    """Shell exec tool configuration."""
    timeout: int = 60


class ToolsConfig(BaseModel):
    """Tools configuration."""
    web: WebToolsConfig = Field(default_factory=WebToolsConfig)
    exec: ExecToolConfig = Field(default_factory=ExecToolConfig)
    restrict_to_workspace: bool = False  # If true, restrict all tool access to workspace directory


class Config(BaseSettings):
    """Root configuration for nanobot."""
    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    
    @property
    def workspace_path(self) -> Path:
        """Get expanded workspace path."""
        return Path(self.agents.defaults.workspace).expanduser()
    
    # Default base URLs for API gateways
    _GATEWAY_DEFAULTS = {"openrouter": "https://openrouter.ai/api/v1", "aihubmix": "https://aihubmix.com/v1"}

    def get_provider(self, model: str | None = None) -> ProviderConfig | None:
        """Get matched provider config (api_key, api_base, extra_headers). Falls back to first available."""
        model = (model or self.agents.defaults.model).lower()
        p = self.providers
        def _ready(provider: ProviderConfig, allow_api_base: bool = False) -> bool:
            if provider.api_key:
                return True
            if allow_api_base and provider.api_base:
                return True
            return False
        # Keyword → provider mapping (order matters: gateways first)
        keyword_map = {
            "aihubmix": p.aihubmix, "openrouter": p.openrouter,
            "deepseek": p.deepseek, "anthropic": p.anthropic, "claude": p.anthropic,
            "openai": p.openai, "gpt": p.openai, "gemini": p.gemini,
            "zhipu": p.zhipu, "glm": p.zhipu, "zai": p.zhipu,
            "dashscope": p.dashscope, "qwen": p.dashscope,
            "groq": p.groq, "moonshot": p.moonshot, "kimi": p.moonshot, "vllm": p.vllm,
        }
        for kw, provider in keyword_map.items():
            if kw in model and _ready(provider, allow_api_base=(provider is p.vllm)):
                return provider
        # Fallback: gateways first (can serve any model), then specific providers
        all_providers = [p.openrouter, p.aihubmix, p.anthropic, p.openai, p.deepseek,
                         p.gemini, p.zhipu, p.dashscope, p.moonshot, p.vllm, p.groq]
        for provider in all_providers:
            if _ready(provider, allow_api_base=(provider is p.vllm)):
                return provider
        return None

    def get_api_key(self, model: str | None = None) -> str | None:
        """Get API key for the given model. Falls back to first available key."""
        p = self.get_provider(model)
        return p.api_key if p else None
    
    def get_api_base(self, model: str | None = None) -> str | None:
        """Get API base URL for the given model. Applies default URLs for known gateways."""
        p = self.get_provider(model)
        if p and p.api_base:
            return p.api_base
        # Default URLs for known gateways (openrouter, aihubmix)
        for name, url in self._GATEWAY_DEFAULTS.items():
            if p == getattr(self.providers, name):
                return url
        return None

    def get_api_type(self, model: str | None = None) -> str | None:
        """Get API type for the active provider (e.g. openai-responses)."""
        provider = self.get_provider(model)
        return provider.api_type if provider else None

    def get_api_headers(self, model: str | None = None) -> dict[str, str] | None:
        """Get extra headers for the active provider."""
        provider = self.get_provider(model)
        if not provider:
            return None
        return provider.extra_headers or provider.headers

    def get_api_proxy(self, model: str | None = None) -> str | None:
        """Get proxy for the active provider."""
        provider = self.get_provider(model)
        return provider.proxy if provider else None

    def get_drop_params(self, model: str | None = None) -> bool:
        """Check if optional params should be dropped for the active provider."""
        provider = self.get_provider(model)
        return bool(provider.drop_params) if provider else False
    
    class Config:
        env_prefix = "NANOBOT_"
        env_nested_delimiter = "__"
