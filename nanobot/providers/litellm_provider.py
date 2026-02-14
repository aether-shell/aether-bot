"""LiteLLM provider implementation for multi-provider support."""

import asyncio
import hashlib
import json
import os
import re
import time
from typing import Any, Awaitable, Callable

import httpx
import litellm
from litellm import acompletion, aresponses
from loguru import logger

from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from nanobot.providers.registry import find_by_model, find_gateway


class LiteLLMProvider(LLMProvider):
    """
    LLM provider using LiteLLM for multi-provider support.
    Supports OpenRouter, Anthropic, OpenAI, Gemini, MiniMax, and many other providers through
    a unified interface.  Provider-specific logic is driven by the registry
    (see providers/registry.py) — no if-elif chains needed here.
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        api_type: str | None = None,
        proxy: str | None = None,
        drop_params: bool = False,
        extra_headers: dict[str, str] | None = None,
        default_model: str = "anthropic/claude-opus-4-5",
        session_mode: str | None = None,
        provider_name: str | None = None,
    ):
        super().__init__(api_key, api_base)
        self.default_model = default_model
        self.api_type = api_type.lower() if api_type else None
        self.extra_headers = extra_headers or {}
        self.proxy = proxy
        self.drop_params = drop_params
        # Session mode: "native", "stateless", or None (auto-detect)
        self.session_mode = session_mode.lower().strip() if session_mode else None
        # Runtime flag: set to True when API rejects previous_response_id
        self._native_session_disabled = False

        # Detect gateway / local deployment.
        # provider_name (from config key) is the primary signal;
        # api_key / api_base are fallback for auto-detection.
        self._gateway = find_gateway(provider_name, api_key, api_base)

        # Backwards-compatible flags (used by tests and possibly external code)
        self.is_openrouter = bool(self._gateway and self._gateway.name == "openrouter")
        self.is_aihubmix = bool(self._gateway and self._gateway.name == "aihubmix")
        self.is_vllm = bool(self._gateway and self._gateway.is_local)

        # Configure environment variables
        if api_key:
            self._setup_env(api_key, api_base, default_model)

        if api_base:
            litellm.api_base = api_base

        if proxy:
            # Only set env vars if not already present, to avoid breaking
            # other components (e.g. Feishu WebSocket) that don't need proxy.
            # The proxy is passed directly to httpx client in _chat_with_direct_responses.
            pass

        # Disable LiteLLM logging noise
        litellm.suppress_debug_info = True
        # Drop unsupported parameters for providers (e.g., gpt-5 rejects some params)
        litellm.drop_params = True

    def _setup_env(self, api_key: str, api_base: str | None, model: str) -> None:
        """Set environment variables based on detected provider."""
        spec = self._gateway or find_by_model(model)
        if not spec:
            return

        # Gateway/local overrides existing env; standard provider doesn't
        if self._gateway:
            os.environ[spec.env_key] = api_key
        else:
            os.environ.setdefault(spec.env_key, api_key)

        # Resolve env_extras placeholders:
        #   {api_key}  → user's API key
        #   {api_base} → user's api_base, falling back to spec.default_api_base
        effective_base = api_base or spec.default_api_base
        for env_name, env_val in spec.env_extras:
            resolved = env_val.replace("{api_key}", api_key)
            resolved = resolved.replace("{api_base}", effective_base)
            os.environ.setdefault(env_name, resolved)

    def _resolve_model(self, model: str) -> str:
        """Resolve model name by applying provider/gateway prefixes."""
        if self._gateway:
            # Gateway mode: apply gateway prefix, skip provider-specific prefixes
            prefix = self._gateway.litellm_prefix
            if self._gateway.strip_model_prefix:
                model = model.split("/")[-1]
            if prefix and not model.startswith(f"{prefix}/"):
                model = f"{prefix}/{model}"
            return model

        # Standard mode: auto-prefix for known providers
        spec = find_by_model(model)
        if spec and spec.litellm_prefix:
            if not any(model.startswith(s) for s in spec.skip_prefixes):
                model = f"{spec.litellm_prefix}/{model}"

        return model

    def _apply_model_overrides(self, model: str, kwargs: dict[str, Any]) -> None:
        """Apply model-specific parameter overrides from the registry."""
        model_lower = model.lower()
        spec = find_by_model(model)
        if spec:
            for pattern, overrides in spec.model_overrides:
                if pattern in model_lower:
                    kwargs.update(overrides)
                    return

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        session_state: dict[str, Any] | None = None,
        on_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        """
        Send a chat completion request via LiteLLM.

        Args:
            messages: List of message dicts with 'role' and 'content'.
            tools: Optional list of tool definitions in OpenAI format.
            model: Model identifier (e.g., 'anthropic/claude-sonnet-4-5').
            max_tokens: Maximum tokens in response.
            temperature: Sampling temperature.

        Returns:
            LLMResponse with content and/or tool calls.
        """
        t_start = time.monotonic()
        model = model or self.default_model
        use_responses = self._use_responses_api()
        logger.debug(
            f"LiteLLM chat start model={model} mode={'responses' if use_responses else 'completions'} "
            f"messages={len(messages)} tools={len(tools or [])} "
            f"tool_choice={tool_choice or 'auto'} "
            f"prev_id={bool(session_state and session_state.get('previous_response_id'))} "
            f"stream={on_delta is not None}"
        )
        tool_names = self._tool_names_from_definitions(tools)
        logger.debug(
            f"LiteLLM chat request details model={model} "
            f"session_state_keys={list((session_state or {}).keys()) if isinstance(session_state, dict) else []} "
            f"tool_names={tool_names}"
        )
        self._log_messages_snapshot("chat_input", messages)

        if use_responses:
            # If native session is disabled (runtime or config), strip previous_response_id
            if self._native_session_disabled or self.session_mode == "stateless":
                if session_state:
                    session_state = {k: v for k, v in session_state.items() if k != "previous_response_id"}

            response = await self._chat_with_responses(
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                session_state=session_state,
                on_delta=on_delta,
            )
            if response.finish_reason == "error":
                # Check if API explicitly rejects previous_response_id
                if self._should_disable_native_session(response.content):
                    logger.warning(
                        "API does not support previous_response_id, "
                        "disabling native session permanently for this provider"
                    )
                    self._native_session_disabled = True
                    # Retry without previous_response_id
                    clean_state = None
                    if session_state:
                        clean_state = {k: v for k, v in session_state.items() if k != "previous_response_id"}
                    retry_response = await self._chat_with_responses(
                        messages=messages,
                        tools=tools,
                        tool_choice=tool_choice,
                        model=model,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        session_state=clean_state,
                        on_delta=on_delta,
                    )
                    self._log_chat_result("responses_retry_without_prev_id", retry_response, time.monotonic() - t_start)
                    return retry_response
                if self._should_fallback_from_responses(response.content):
                    fallback = await self._chat_with_completions(
                        messages=messages,
                        tools=tools,
                        tool_choice=tool_choice,
                        model=model,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        session_state=session_state,
                        on_delta=on_delta,
                    )
                    self._log_chat_result("completions_fallback", fallback, time.monotonic() - t_start)
                    return fallback
            self._log_chat_result("responses", response, time.monotonic() - t_start)
            return response

        completions_response = await self._chat_with_completions(
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            session_state=session_state,
            on_delta=on_delta,
        )
        self._log_chat_result("completions", completions_response, time.monotonic() - t_start)
        return completions_response

    async def _chat_with_completions(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        tool_choice: str | dict[str, Any] | None,
        model: str,
        max_tokens: int,
        temperature: float,
        session_state: dict[str, Any] | None = None,
        on_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        t_start = time.monotonic()
        model = self._resolve_model(model or self.default_model)

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
        }
        if not self.drop_params:
            kwargs["max_tokens"] = max_tokens
            kwargs["temperature"] = temperature
            # Apply model-specific overrides (e.g. kimi-k2.5 temperature)
            self._apply_model_overrides(model, kwargs)

        # Pass api_key directly — more reliable than env vars alone
        if self.api_key:
            kwargs["api_key"] = self.api_key

        # Pass api_base for custom endpoints
        if self.api_base:
            kwargs["api_base"] = self.api_base
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice or "auto"
        if self.extra_headers:
            kwargs["extra_headers"] = self.extra_headers

        logger.debug(
            f"LiteLLM completions request model={model} messages={len(messages)} "
            f"tools={len(tools or [])} tool_names={self._tool_names_from_definitions(tools)} "
            f"stream={on_delta is not None}"
        )
        self._log_messages_snapshot("completions_request", messages)
        try:
            if on_delta:
                kwargs["stream"] = True
                response = await acompletion(**kwargs)
                if hasattr(response, "__aiter__"):
                    parsed_stream = await self._parse_completions_stream(response, on_delta=on_delta)
                    logger.debug(
                        f"LiteLLM completions stream parsed tool_calls={len(parsed_stream.tool_calls)} "
                        f"content_chars={len(parsed_stream.content or '')} "
                        f"elapsed={(time.monotonic() - t_start):.3f}s"
                    )
                    return parsed_stream

            response = await acompletion(**kwargs)
            parsed = self._parse_response(response)
            if on_delta and parsed.content:
                await on_delta(parsed.content)
            logger.debug(
                f"LiteLLM completions parsed tool_calls={len(parsed.tool_calls)} "
                f"content_chars={len(parsed.content or '')} "
                f"elapsed={(time.monotonic() - t_start):.3f}s"
            )
            return parsed
        except Exception as e:
            logger.warning(f"LiteLLM completions failed after {(time.monotonic() - t_start):.3f}s: {e}")
            # Return error as content for graceful handling
            return LLMResponse(
                content=f"Error calling LLM: {str(e)}",
                finish_reason="error",
            )

    def _should_fallback_from_responses(self, content: str | None) -> bool:
        if not self.api_base:
            return False
        text = (content or "").lower()
        if "unknown error" in text:
            return True
        if "http 404" in text or "http 405" in text:
            return True
        if "not found" in text or "no route" in text:
            return True
        # 5xx gateway errors
        if "http 502" in text or "http 503" in text or "bad gateway" in text:
            return True
        if "error code: 502" in text:
            return True
        return False

    def _should_disable_native_session(self, content: str | None) -> bool:
        """Check if the error indicates previous_response_id is not supported."""
        text = (content or "").lower()
        if "unsupported parameter" in text and "previous_response_id" in text:
            return True
        return False

    def _use_responses_api(self) -> bool:
        if not self.api_type:
            return False
        return self.api_type in {"openai-responses", "openai_responses", "responses"}

    def _log_chat_result(self, mode: str, response: LLMResponse, elapsed: float) -> None:
        logger.debug(
            f"LiteLLM chat done mode={mode} finish={response.finish_reason} "
            f"tool_calls={len(response.tool_calls)} content_chars={len(response.content or '')} "
            f"elapsed={elapsed:.3f}s"
        )

    async def _chat_with_responses(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        tool_choice: str | dict[str, Any] | None,
        model: str,
        max_tokens: int,
        temperature: float,
        session_state: dict[str, Any] | None = None,
        on_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        t_start = time.monotonic()
        if self.api_base:
            direct = await self._chat_with_direct_responses(
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                session_state=session_state,
                on_delta=on_delta,
            )
            logger.debug(
                f"LiteLLM responses(direct) done tool_calls={len(direct.tool_calls)} "
                f"content_chars={len(direct.content or '')} "
                f"elapsed={(time.monotonic() - t_start):.3f}s"
            )
            return direct

        input_items = self._messages_to_responses_input(messages)
        self._log_responses_input_snapshot("responses_input", input_items)

        kwargs: dict[str, Any] = {
            "model": model,
            "input": input_items,
            "max_output_tokens": max_tokens,
            "temperature": temperature,
            "custom_llm_provider": "openai",
            "stream": True,
        }
        if session_state:
            prev_id = session_state.get("previous_response_id")
            if prev_id:
                kwargs["previous_response_id"] = prev_id

        if self.api_base:
            kwargs["api_base"] = self.api_base

        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice or "auto"
        if self.extra_headers:
            kwargs["extra_headers"] = self.extra_headers

        logger.debug(
            f"LiteLLM responses request model={model} input_items={len(input_items)} "
            f"tools={len(tools or [])} tool_names={self._tool_names_from_definitions(tools)} "
            f"stream=True prev_id={bool(kwargs.get('previous_response_id'))}"
        )
        try:
            response = await aresponses(**kwargs)
            if hasattr(response, "__aiter__"):
                parsed_stream = await self._parse_responses_stream(response, on_delta=on_delta)
                logger.debug(
                    f"LiteLLM responses stream parsed tool_calls={len(parsed_stream.tool_calls)} "
                    f"content_chars={len(parsed_stream.content or '')} "
                    f"elapsed={(time.monotonic() - t_start):.3f}s"
                )
                return parsed_stream
            parsed = self._parse_responses_response(response)
            if on_delta and parsed.content:
                await on_delta(parsed.content)
            logger.debug(
                f"LiteLLM responses parsed tool_calls={len(parsed.tool_calls)} "
                f"content_chars={len(parsed.content or '')} "
                f"elapsed={(time.monotonic() - t_start):.3f}s"
            )
            return parsed
        except Exception as e:
            logger.warning(f"LiteLLM responses failed after {(time.monotonic() - t_start):.3f}s: {e}")
            return LLMResponse(
                content=f"Error calling LLM: {str(e)}",
                finish_reason="error",
            )

    async def _chat_with_direct_responses(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        tool_choice: str | dict[str, Any] | None,
        model: str,
        max_tokens: int,
        temperature: float,
        session_state: dict[str, Any] | None = None,
        on_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        t_start = time.monotonic()
        input_items = self._messages_to_responses_input(messages)
        self._log_responses_input_snapshot("direct_responses_input", input_items)
        base_body: dict[str, Any] = {
            "model": model,
            "input": input_items,
        }
        if not self.drop_params:
            base_body["max_output_tokens"] = max_tokens
            base_body["temperature"] = temperature
        if tools:
            base_body["tools"] = self._convert_tools_to_responses(tools)
            base_body["tool_choice"] = tool_choice or "auto"
        if session_state:
            prev_id = session_state.get("previous_response_id")
            if prev_id:
                base_body["previous_response_id"] = prev_id

        base_headers = {
            "Authorization": f"Bearer {self.api_key}" if self.api_key else "",
            "Content-Type": "application/json",
        }
        if self.extra_headers:
            base_headers.update(self.extra_headers)
        base_headers = {k: v for k, v in base_headers.items() if v}

        urls = self._build_responses_urls()
        if not urls:
            return LLMResponse(
                content="Error calling LLM: responses api_base not configured",
                finish_reason="error",
            )

        logger.debug(
            f"Responses API request urls={urls} headers={list(base_headers.keys())} "
            f"body_keys={list(base_body.keys())} body_size={len(json.dumps(base_body, ensure_ascii=False))} "
            f"tool_names={self._tool_names_from_definitions(tools)} "
            f"session_state_keys={list((session_state or {}).keys()) if isinstance(session_state, dict) else []}"
        )

        max_retries = 3
        retry_delays = [2, 5, 10]  # seconds between retries

        for attempt in range(max_retries + 1):
            attempt_started = time.monotonic()
            last_error: str | None = None
            is_retryable = False

            try:
                client_kwargs: dict[str, Any] = {"timeout": 120.0}
                if self.proxy:
                    client_kwargs["proxy"] = self.proxy
                async with httpx.AsyncClient(**client_kwargs) as client:
                    for url in urls:
                        # Always prefer streaming to avoid Cloudflare 502 on long responses
                        stream_body = dict(base_body)
                        stream_body["stream"] = True
                        headers = dict(base_headers)
                        headers["Accept"] = "text/event-stream"
                        try:
                            async with client.stream("POST", url, headers=headers, json=stream_body) as response:
                                if response.status_code == 200:
                                    parsed = await self._consume_responses_sse(response, on_delta=on_delta)
                                    logger.debug(
                                        f"Responses SSE success url={url} attempt={attempt + 1} "
                                        f"elapsed={(time.monotonic() - attempt_started):.3f}s"
                                    )
                                    logger.debug(
                                        f"Responses direct total elapsed={(time.monotonic() - t_start):.3f}s"
                                    )
                                    return parsed
                                raw = await response.aread()
                                raw_text = raw.decode("utf-8", "ignore")
                                if response.status_code < 500:
                                    # Filter HTML from error messages
                                    error_text = raw_text
                                    if "<html" in error_text.lower() or len(error_text) > 500:
                                        error_text = error_text[:200]
                                    return LLMResponse(
                                        content=f"Error calling LLM: HTTP {response.status_code} {error_text}",
                                        finish_reason="error",
                                    )
                                # For 5xx: don't include raw HTML in error
                                short_error = raw_text[:200] if "<html" not in raw_text.lower() else ""
                                last_error = f"HTTP {response.status_code} {short_error}".strip()
                                is_retryable = True
                        except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout) as e:
                            last_error = str(e)
                            is_retryable = True
                        except Exception as e:
                            last_error = str(e)

                        # Fallback: non-stream (for servers that don't support SSE)
                        headers = dict(base_headers)
                        headers["Accept"] = "application/json"
                        try:
                            response = await client.post(url, headers=headers, json=base_body)
                            if response.status_code == 200:
                                parsed = self._parse_responses_response(response.json())
                                if on_delta and parsed.content:
                                    await on_delta(parsed.content)
                                logger.debug(
                                    f"Responses JSON success url={url} attempt={attempt + 1} "
                                    f"elapsed={(time.monotonic() - attempt_started):.3f}s"
                                )
                                logger.debug(
                                    f"Responses direct total elapsed={(time.monotonic() - t_start):.3f}s"
                                )
                                return parsed
                            raw = response.text
                            if response.status_code < 500:
                                error_text = raw
                                if "<html" in error_text.lower() or len(error_text) > 500:
                                    error_text = error_text[:200]
                                return LLMResponse(
                                    content=f"Error calling LLM: HTTP {response.status_code} {error_text}",
                                    finish_reason="error",
                                )
                            short_error = raw[:200] if "<html" not in raw.lower() else ""
                            last_error = f"HTTP {response.status_code} {short_error}".strip()
                            is_retryable = True
                        except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout) as e:
                            last_error = str(e)
                            is_retryable = True
                        except Exception as e:
                            last_error = str(e)

            except Exception as e:
                last_error = str(e)
                is_retryable = True

            # Retry if it's a retryable error and we have attempts left
            if is_retryable and attempt < max_retries:
                delay = retry_delays[min(attempt, len(retry_delays) - 1)]
                logger.warning(
                    f"LLM request failed attempt={attempt + 1}/{max_retries + 1} "
                    f"error={last_error} retry_in={delay}s elapsed={(time.monotonic() - attempt_started):.3f}s"
                )
                await asyncio.sleep(delay)
                continue

            # No more retries
            logger.warning(
                f"Responses direct failed after {(time.monotonic() - t_start):.3f}s "
                f"last_error={last_error or 'unknown error'}"
            )
            return LLMResponse(
                content=f"Error calling LLM: {last_error or 'unknown error'}",
                finish_reason="error",
            )

    def _build_responses_urls(self) -> list[str]:
        if not self.api_base:
            return []
        base = self.api_base.rstrip("/")
        urls: list[str] = []

        def add(url: str) -> None:
            if url not in urls:
                urls.append(url)

        if base.endswith("/responses"):
            add(base)
            return urls

        if base.endswith("/v1"):
            add(base + "/responses")
            add(base[:-3] + "/responses")
            return urls

        add(base + "/responses")
        add(base + "/v1/responses")
        return urls

    async def _consume_responses_sse(
        self,
        response: httpx.Response,
        on_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        t_start = time.monotonic()
        content_parts: list[str] = []
        completed_response: dict[str, Any] | None = None
        event_counts: dict[str, int] = {}
        delta_chars = 0

        async for event in self._iter_sse(response):
            event_type = event.get("type")
            event_counts[event_type] = event_counts.get(event_type, 0) + 1
            if event_type == "response.output_text.delta":
                delta = event.get("delta") or ""
                if delta:
                    if on_delta:
                        await on_delta(delta)
                    content_parts.append(delta)
                    delta_chars += len(delta)
            elif event_type == "response.completed":
                completed_response = event.get("response")

        if completed_response:
            parsed = self._parse_responses_response(completed_response)
            logger.debug(
                f"Responses SSE consumed events with completed payload "
                f"event_counts={event_counts} delta_chars={delta_chars} "
                f"tool_calls={len(parsed.tool_calls)} elapsed={(time.monotonic() - t_start):.3f}s"
            )
            return parsed

        parsed = LLMResponse(
            content="".join(content_parts),
            finish_reason="stop",
        )
        logger.debug(
            f"Responses SSE consumed fallback content_chars={len(parsed.content or '')} "
            f"event_counts={event_counts} delta_chars={delta_chars} "
            f"elapsed={(time.monotonic() - t_start):.3f}s"
        )
        return parsed

    async def _iter_sse(self, response: httpx.Response):
        buffer: list[str] = []
        async for line in response.aiter_lines():
            if line == "":
                if buffer:
                    data_lines = [line[5:].strip() for line in buffer if line.startswith("data:")]
                    buffer = []
                    if not data_lines:
                        continue
                    data = "\n".join(data_lines).strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        yield json.loads(data)
                    except Exception:
                        continue
                continue
            buffer.append(line)

    async def _parse_responses_stream(
        self,
        stream: Any,
        on_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        t_start = time.monotonic()
        content_parts: list[str] = []
        event_counts: dict[str, int] = {}
        delta_chars = 0
        async for event in stream:
            event_type = self._get_event_type(event)
            event_counts[str(event_type)] = event_counts.get(str(event_type), 0) + 1
            if event_type == "response.output_text.delta":
                delta = self._get_event_value(event, "delta")
                if delta:
                    if on_delta:
                        await on_delta(delta)
                    content_parts.append(delta)
                    delta_chars += len(delta)

        completed = getattr(stream, "completed_response", None)
        if completed is not None:
            response_obj = getattr(completed, "response", None)
            if response_obj is not None:
                parsed = self._parse_responses_response(response_obj)
                logger.debug(
                    f"Responses stream parsed completed_response tool_calls={len(parsed.tool_calls)} "
                    f"event_counts={event_counts} delta_chars={delta_chars} "
                    f"elapsed={(time.monotonic() - t_start):.3f}s"
                )
                return parsed

        content = "".join(content_parts)
        parsed = LLMResponse(
            content=content,
            finish_reason="stop",
        )
        logger.debug(
            f"Responses stream parsed content-only chars={len(content)} "
            f"event_counts={event_counts} delta_chars={delta_chars} "
            f"elapsed={(time.monotonic() - t_start):.3f}s"
        )
        return parsed

    async def _parse_completions_stream(
        self,
        stream: Any,
        on_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        t_start = time.monotonic()
        content_parts: list[str] = []
        tool_calls: list[ToolCallRequest] = []
        finish_reason = "stop"
        chunk_count = 0
        delta_chars = 0

        async for chunk in stream:
            chunk_count += 1
            try:
                choice = chunk.choices[0]
                finish_reason = choice.finish_reason or finish_reason
                delta = getattr(choice, "delta", None)
                if not delta:
                    continue

                text = getattr(delta, "content", None)
                if text:
                    if on_delta:
                        await on_delta(text)
                    content_parts.append(text)
                    delta_chars += len(text)

                # Some providers may stream tool_calls; best-effort support.
                tc_list = getattr(delta, "tool_calls", None)
                if tc_list:
                    for tc in tc_list:
                        func = getattr(tc, "function", None)
                        if not func:
                            continue
                        name = getattr(func, "name", None)
                        args = getattr(func, "arguments", None)
                        if not name:
                            continue
                        if isinstance(args, str):
                            try:
                                parsed_args = json.loads(args)
                            except json.JSONDecodeError:
                                parsed_args = {"raw": args}
                        else:
                            parsed_args = args
                        tool_calls.append(ToolCallRequest(
                            id=getattr(tc, "id", "") or "",
                            name=name,
                            arguments=parsed_args or {},
                        ))
            except Exception:
                continue

        parsed = LLMResponse(
            content="".join(content_parts),
            tool_calls=tool_calls,
            finish_reason=finish_reason,
        )
        logger.debug(
            f"Completions stream parsed finish={finish_reason} tool_calls={len(tool_calls)} "
            f"content_chars={len(parsed.content or '')} chunks={chunk_count} "
            f"delta_chars={delta_chars} elapsed={(time.monotonic() - t_start):.3f}s"
        )
        return parsed

    def _get_event_type(self, event: Any) -> str | None:
        if isinstance(event, dict):
            return event.get("type")
        return getattr(event, "type", None)

    def _get_event_value(self, event: Any, key: str) -> Any:
        if isinstance(event, dict):
            return event.get(key)
        return getattr(event, key, None)


    def _parse_response(self, response: Any) -> LLMResponse:
        """Parse LiteLLM response into our standard format."""
        t_start = time.monotonic()
        choice = response.choices[0]
        message = choice.message

        tool_calls = []
        if hasattr(message, "tool_calls") and message.tool_calls:
            for tc in message.tool_calls:
                # Parse arguments from JSON string if needed
                args = tc.function.arguments
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {"raw": args}

                tool_calls.append(ToolCallRequest(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=args,
                ))

        usage = {}
        if hasattr(response, "usage") and response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }

        response_id = getattr(response, "id", None)
        model = getattr(response, "model", None)
        conversation_id = None
        if isinstance(response, dict):
            response_id = response.get("id") or response_id
            model = response.get("model") or model
            conversation = response.get("conversation") or {}
            conversation_id = response.get("conversation_id") or conversation.get("id")

        # chat/completions returns chatcmpl-* IDs, not valid for Responses API sessions
        if response_id and not response_id.startswith("resp_"):
            response_id = None

        reasoning_content = getattr(message, "reasoning_content", None)

        parsed = LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "stop",
            usage=usage,
            response_id=response_id,
            conversation_id=conversation_id,
            model=model,
            reasoning_content=reasoning_content,
        )
        content_preview = self._preview_text(parsed.content)
        logger.debug(
            f"LiteLLM parse completion finish={parsed.finish_reason} "
            f"tool_calls={len(parsed.tool_calls)} content_chars={len(parsed.content or '')} "
            f"response_id={parsed.response_id or 'n/a'} model={parsed.model or 'n/a'} "
            f"usage={parsed.usage or {}} content_preview={content_preview} "
            f"elapsed={(time.monotonic() - t_start):.3f}s"
        )
        for idx, tc in enumerate(parsed.tool_calls):
            logger.debug(
                f"LiteLLM parse completion tool_call[{idx}] id={tc.id} name={tc.name} "
                f"args_preview={self._preview_text(tc.arguments)}"
            )
        return parsed

    def _parse_responses_response(self, response: Any) -> LLMResponse:
        """Parse LiteLLM Responses API response into our standard format."""
        t_start = time.monotonic()
        content = ""
        if hasattr(response, "output_text"):
            content = response.output_text or ""
        elif isinstance(response, dict):
            content = response.get("output_text") or ""

        tool_calls: list[ToolCallRequest] = []
        output_items = getattr(response, "output", None)
        if output_items is None and isinstance(response, dict):
            output_items = response.get("output")
        output_types: list[str] = []
        if not content and output_items:
            content = self._extract_output_text(output_items)
        if output_items:
            for item in output_items:
                item_type = item.get("type") if isinstance(item, dict) else getattr(item, "type", None)
                output_types.append(str(item_type))
                if item_type != "function_call":
                    continue
                name = item.get("name") if isinstance(item, dict) else getattr(item, "name", "")
                arguments = item.get("arguments") if isinstance(item, dict) else getattr(item, "arguments", "")
                call_id = (
                    item.get("call_id") if isinstance(item, dict) else getattr(item, "call_id", None)
                ) or (
                    item.get("id") if isinstance(item, dict) else getattr(item, "id", None)
                ) or ""

                if isinstance(arguments, str):
                    try:
                        parsed_args = json.loads(arguments)
                    except json.JSONDecodeError:
                        parsed_args = {"raw": arguments}
                else:
                    parsed_args = arguments

                tool_calls.append(ToolCallRequest(
                    id=call_id,
                    name=name or "",
                    arguments=parsed_args or {},
                ))

        usage = {}
        if hasattr(response, "usage") and response.usage:
            usage = {
                "prompt_tokens": response.usage.input_tokens,
                "completion_tokens": response.usage.output_tokens,
                "total_tokens": response.usage.total_tokens,
            }

        response_id = None
        model = None
        conversation_id = None
        if isinstance(response, dict):
            response_id = response.get("id")
            model = response.get("model")
            conversation = response.get("conversation") or {}
            conversation_id = response.get("conversation_id") or conversation.get("id")
        else:
            response_id = getattr(response, "id", None)
            model = getattr(response, "model", None)
            conversation_id = getattr(response, "conversation_id", None)
            conv_obj = getattr(response, "conversation", None)
            if not conversation_id and isinstance(conv_obj, dict):
                conversation_id = conv_obj.get("id")

        finish_reason = "stop"
        status = response.get("status") if isinstance(response, dict) else getattr(response, "status", None)
        if status and status != "completed":
            finish_reason = status

        parsed = LLMResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
            response_id=response_id,
            conversation_id=conversation_id,
            model=model,
        )
        content_preview = self._preview_text(parsed.content)
        logger.debug(
            f"LiteLLM parse responses finish={parsed.finish_reason} "
            f"tool_calls={len(parsed.tool_calls)} content_chars={len(parsed.content or '')} "
            f"response_id={parsed.response_id or 'n/a'} conversation_id={parsed.conversation_id or 'n/a'} "
            f"model={parsed.model or 'n/a'} status={status or 'completed'} "
            f"output_items={len(output_items or [])} output_types={output_types[:20]} "
            f"usage={parsed.usage or {}} content_preview={content_preview} "
            f"elapsed={(time.monotonic() - t_start):.3f}s"
        )
        for idx, tc in enumerate(parsed.tool_calls):
            logger.debug(
                f"LiteLLM parse responses tool_call[{idx}] id={tc.id} name={tc.name} "
                f"args_preview={self._preview_text(tc.arguments)}"
            )
        return parsed

    def _extract_output_text(self, output_items: list[Any]) -> str:
        parts: list[str] = []
        for item in output_items:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "message":
                continue
            content = item.get("content") or []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "output_text":
                    text = block.get("text")
                    if text:
                        parts.append(text)
        return "".join(parts)

    def _messages_to_responses_input(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        msg_index = 0
        role_counts: dict[str, int] = {}

        for msg in messages:
            role = msg.get("role")
            role_key = str(role or "unknown")
            role_counts[role_key] = role_counts.get(role_key, 0) + 1
            if role == "tool":
                items.append(self._tool_output_to_response_item(msg))
                continue

            if role == "assistant":
                tool_calls = msg.get("tool_calls") or []
                content = msg.get("content")
                has_content = content not in (None, "") or (isinstance(content, list) and len(content) > 0)
                if has_content:
                    assistant_item = self._assistant_message_to_response_item(content, msg_index)
                    if assistant_item:
                        items.append(assistant_item)
                        msg_index += 1
                elif not tool_calls:
                    assistant_item = self._assistant_message_to_response_item("", msg_index)
                    if assistant_item:
                        items.append(assistant_item)
                        msg_index += 1

                for tc in tool_calls:
                    items.append(self._tool_call_to_response_item(tc))
                continue

            if role in {"system", "developer"}:
                items.append(self._system_message_to_response_item(role, msg.get("content")))
                continue

            items.append(self._user_message_to_response_item(msg.get("content")))

        logger.debug(
            f"LiteLLM mapped messages->responses items messages={len(messages)} "
            f"items={len(items)} role_counts={role_counts}"
        )
        return items

    def _assistant_message_to_response_item(self, content: Any, index: int) -> dict[str, Any] | None:
        output_items = self._convert_assistant_content(content)
        if not output_items:
            return None
        msg_id = f"msg_{index}"
        if len(msg_id) > 64:
            msg_id = f"msg_{self._short_hash(msg_id)}"
        return {
            "type": "message",
            "role": "assistant",
            "content": output_items,
            "status": "completed",
            "id": msg_id,
        }

    def _make_output_text(self, text: str, annotations: list[Any] | None = None) -> dict[str, Any]:
        return {
            "type": "output_text",
            "text": text,
            "annotations": annotations or [],
        }

    def _convert_assistant_content(self, content: Any) -> list[dict[str, Any]]:
        if content is None:
            return []
        if isinstance(content, str):
            return [self._make_output_text(content)]
        if isinstance(content, list):
            converted: list[dict[str, Any]] = []
            for item in content:
                if isinstance(item, str):
                    converted.append(self._make_output_text(item))
                    continue
                if isinstance(item, dict):
                    item_type = item.get("type")
                    if item_type == "output_text":
                        converted.append(
                            self._make_output_text(
                                item.get("text", ""),
                                annotations=item.get("annotations"),
                            )
                        )
                        continue
                    if item_type in {"text", "input_text"}:
                        converted.append(self._make_output_text(item.get("text", "")))
                        continue
                    converted.append(self._make_output_text(json.dumps(item, ensure_ascii=False)))
                    continue
                converted.append(self._make_output_text(str(item)))
            return converted
        return [self._make_output_text(str(content))]

    def _user_message_to_response_item(self, content: Any) -> dict[str, Any]:
        return {
            "role": "user",
            "content": self._convert_user_content(content),
        }

    def _system_message_to_response_item(self, role: str, content: Any) -> dict[str, Any]:
        if isinstance(content, str) or content is None:
            return {"role": role, "content": content or ""}
        return {"role": role, "content": self._convert_user_content(content)}

    def _convert_user_content(self, content: Any) -> list[dict[str, Any]]:
        if content is None:
            return [{"type": "input_text", "text": ""}]
        if isinstance(content, str):
            return [{"type": "input_text", "text": content}]
        if isinstance(content, list):
            converted: list[dict[str, Any]] = []
            for item in content:
                if isinstance(item, str):
                    converted.append({"type": "input_text", "text": item})
                    continue
                if isinstance(item, dict):
                    item_type = item.get("type")
                    if item_type in {"input_text", "input_image"}:
                        converted.append(item)
                        continue
                    if item_type == "text":
                        converted.append({"type": "input_text", "text": item.get("text", "")})
                        continue
                    if item_type == "image_url":
                        image_url = item.get("image_url")
                        if isinstance(image_url, dict):
                            image_url = image_url.get("url")
                        if image_url:
                            converted.append(
                                {"type": "input_image", "image_url": image_url, "detail": "auto"}
                            )
                            continue
                    converted.append({"type": "input_text", "text": json.dumps(item, ensure_ascii=False)})
                    continue
                converted.append({"type": "input_text", "text": str(item)})
            return converted
        return [{"type": "input_text", "text": str(content)}]

    def _short_hash(self, value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]

    def _normalize_call_id(self, value: str | None) -> str:
        if not value:
            return ""
        sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", value)
        if len(sanitized) > 64:
            sanitized = sanitized[:64]
        return sanitized

    def _normalize_item_id(self, value: str | None) -> str | None:
        if not value:
            return None
        sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", value)
        normalized = sanitized if sanitized.startswith("fc") else f"fc_{sanitized}"
        if len(normalized) > 64:
            normalized = f"fc_{self._short_hash(normalized)}"
        if len(normalized) > 64:
            normalized = normalized[:64]
        return normalized

    def _convert_tools_to_responses(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            if tool.get("type") == "function" and "function" in tool:
                func = tool.get("function") or {}
                converted.append(
                    {
                        "type": "function",
                        "name": func.get("name", ""),
                        "description": func.get("description", ""),
                        "parameters": func.get("parameters") or {},
                        "strict": False,
                    }
                )
                continue
            if tool.get("type") == "function" and "name" in tool:
                normalized = dict(tool)
                normalized.setdefault("strict", False)
                converted.append(normalized)
                continue
            converted.append(tool)
        return converted

    def _tool_call_to_response_item(self, tool_call: dict[str, Any]) -> dict[str, Any]:
        call_id = tool_call.get("id") or tool_call.get("call_id") or ""
        func = tool_call.get("function", {}) if isinstance(tool_call, dict) else {}
        name = func.get("name") or tool_call.get("name") or ""
        arguments = func.get("arguments") or tool_call.get("arguments") or ""
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments)
        if not call_id:
            call_id = f"{name}_{self._short_hash(arguments)}"
        normalized_call_id = self._normalize_call_id(call_id)
        item_id = self._normalize_item_id(call_id)
        return {
            "type": "function_call",
            "call_id": normalized_call_id,
            "name": name,
            "arguments": arguments,
            "id": item_id,
        }

    def _tool_output_to_response_item(self, msg: dict[str, Any]) -> dict[str, Any]:
        call_id = msg.get("tool_call_id") or msg.get("id") or ""
        normalized_call_id = self._normalize_call_id(call_id)
        output = msg.get("content", "")
        if not isinstance(output, str):
            output = json.dumps(output)
        return {
            "type": "function_call_output",
            "call_id": normalized_call_id,
            "output": output,
        }

    def _preview_text(self, value: Any, max_chars: int = 180) -> str:
        try:
            if isinstance(value, str):
                text = value
            else:
                text = json.dumps(value, ensure_ascii=False)
        except Exception:
            text = str(value)
        text = text.replace("\n", "\\n")
        if len(text) <= max_chars:
            return text
        return f"{text[:max_chars]}...(truncated)"

    def _tool_names_from_definitions(self, tools: list[dict[str, Any]] | None) -> list[str]:
        if not tools:
            return []
        names: list[str] = []
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            if tool.get("type") == "function" and isinstance(tool.get("function"), dict):
                names.append(tool["function"].get("name") or "unknown")
                continue
            names.append(tool.get("name") or "unknown")
        return names

    def _summarize_message_content(self, content: Any) -> tuple[int, str]:
        if isinstance(content, str):
            return len(content), self._preview_text(content)
        if isinstance(content, list):
            block_types: list[str] = []
            for block in content[:8]:
                if isinstance(block, dict):
                    block_types.append(str(block.get("type") or "dict"))
                else:
                    block_types.append(type(block).__name__)
            try:
                chars = len(json.dumps(content, ensure_ascii=False))
            except Exception:
                chars = len(str(content))
            return chars, f"list[{len(content)}] block_types={block_types}"
        text = str(content)
        return len(text), self._preview_text(text)

    def _log_messages_snapshot(self, stage: str, messages: list[dict[str, Any]]) -> None:
        logger.debug(f"LiteLLM {stage} messages_snapshot count={len(messages)}")
        for idx, msg in enumerate(messages):
            role = msg.get("role")
            content_chars, preview = self._summarize_message_content(msg.get("content"))
            tool_calls = msg.get("tool_calls") or []
            logger.debug(
                f"LiteLLM {stage} msg[{idx}] role={role} content_chars={content_chars} "
                f"tool_calls={len(tool_calls)} keys={list(msg.keys())} preview={preview}"
            )

    def _log_responses_input_snapshot(self, stage: str, items: list[dict[str, Any]]) -> None:
        type_counts: dict[str, int] = {}
        for item in items:
            item_type = str(item.get("type") or "message")
            type_counts[item_type] = type_counts.get(item_type, 0) + 1
        logger.debug(
            f"LiteLLM {stage} snapshot items={len(items)} type_counts={type_counts}"
        )
        for idx, item in enumerate(items):
            item_type = str(item.get("type") or "message")
            if item_type == "message":
                role = item.get("role", "assistant")
                content = item.get("content")
                content_chars, preview = self._summarize_message_content(content)
                logger.debug(
                    f"LiteLLM {stage} item[{idx}] type={item_type} role={role} "
                    f"content_chars={content_chars} preview={preview}"
                )
                continue
            if item_type == "function_call":
                logger.debug(
                    f"LiteLLM {stage} item[{idx}] type=function_call "
                    f"name={item.get('name')} call_id={item.get('call_id')} "
                    f"args_preview={self._preview_text(item.get('arguments'))}"
                )
                continue
            if item_type == "function_call_output":
                logger.debug(
                    f"LiteLLM {stage} item[{idx}] type=function_call_output "
                    f"call_id={item.get('call_id')} "
                    f"output_preview={self._preview_text(item.get('output'))}"
                )
                continue
            logger.debug(
                f"LiteLLM {stage} item[{idx}] type={item_type} "
                f"preview={self._preview_text(item)}"
            )

    def get_default_model(self) -> str:
        """Get the default model."""
        return self.default_model

    def supports_native_session(self) -> bool:
        """Return True when using Responses API with previous_response_id support."""
        if not self._use_responses_api():
            return False
        # Config explicitly says stateless
        if self.session_mode == "stateless":
            return False
        # Runtime detection: API rejected previous_response_id
        if self._native_session_disabled:
            return False
        return True
