"""LiteLLM provider implementation for multi-provider support."""

import hashlib
import json
import os
import re
from typing import Any, Awaitable, Callable

import httpx
import litellm
from litellm import acompletion, aresponses

from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest


class LiteLLMProvider(LLMProvider):
    """
    LLM provider using LiteLLM for multi-provider support.
    
    Supports OpenRouter, Anthropic, OpenAI, Gemini, and many other providers through
    a unified interface.
    """
    
    def __init__(
        self, 
        api_key: str | None = None, 
        api_base: str | None = None,
        api_type: str | None = None,
        extra_headers: dict[str, str] | None = None,
        proxy: str | None = None,
        drop_params: bool = False,
        default_model: str = "anthropic/claude-opus-4-5"
    ):
        super().__init__(api_key, api_base)
        self.default_model = default_model
        self.api_type = api_type.lower() if api_type else None
        self.extra_headers = extra_headers
        self.proxy = proxy
        self.drop_params = drop_params
        
        # Detect OpenRouter by api_key prefix or explicit api_base
        self.is_openrouter = (
            (api_key and api_key.startswith("sk-or-")) or
            (api_base and "openrouter" in api_base)
        )

        # Track if using custom endpoint (vLLM, etc.)
        # Don't treat openai/anthropic/deepseek/etc with custom api_base as vLLM
        is_standard_provider = any(provider in default_model.lower() for provider in
                                   ["openai", "gpt", "anthropic", "claude", "deepseek", "gemini"])
        self.is_vllm = bool(api_base) and not self.is_openrouter and not is_standard_provider
        
        # Configure LiteLLM based on provider
        if api_key:
            if self.is_openrouter:
                # OpenRouter mode - set key
                os.environ["OPENROUTER_API_KEY"] = api_key
            elif self.is_vllm:
                # vLLM/custom endpoint - uses OpenAI-compatible API
                os.environ["OPENAI_API_KEY"] = api_key
            elif "deepseek" in default_model:
                os.environ.setdefault("DEEPSEEK_API_KEY", api_key)
            elif "anthropic" in default_model:
                os.environ.setdefault("ANTHROPIC_API_KEY", api_key)
            elif "openai" in default_model or "gpt" in default_model:
                os.environ.setdefault("OPENAI_API_KEY", api_key)
            elif "gemini" in default_model.lower():
                os.environ.setdefault("GEMINI_API_KEY", api_key)
            elif "zhipu" in default_model or "glm" in default_model or "zai" in default_model:
                os.environ.setdefault("ZHIPUAI_API_KEY", api_key)
            elif "groq" in default_model:
                os.environ.setdefault("GROQ_API_KEY", api_key)
        
        if api_base:
            litellm.api_base = api_base

        if proxy:
            os.environ["HTTP_PROXY"] = proxy
            os.environ["HTTPS_PROXY"] = proxy
            os.environ.setdefault("ALL_PROXY", proxy)
        
        # Disable LiteLLM logging noise
        litellm.suppress_debug_info = True
    
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
        model = model or self.default_model

        if self._use_responses_api():
            response = await self._chat_with_responses(
                messages=messages,
                tools=tools,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                session_state=session_state,
                on_delta=on_delta,
            )
            if response.finish_reason == "error" and self._should_fallback_from_responses(response.content):
                return await self._chat_with_completions(
                    messages=messages,
                    tools=tools,
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    session_state=session_state,
                    on_delta=on_delta,
                )
            return response

        return await self._chat_with_completions(
            messages=messages,
            tools=tools,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            session_state=session_state,
            on_delta=on_delta,
        )

    async def _chat_with_completions(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str,
        max_tokens: int,
        temperature: float,
        session_state: dict[str, Any] | None = None,
        on_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        # For OpenRouter, prefix model name if not already prefixed
        if self.is_openrouter and not model.startswith("openrouter/"):
            model = f"openrouter/{model}"

        # For Zhipu/Z.ai, ensure prefix is present
        # Handle cases like "glm-4.7-flash" -> "zai/glm-4.7-flash"
        if ("glm" in model.lower() or "zhipu" in model.lower()) and not (
            model.startswith("zhipu/") or
            model.startswith("zai/") or
            model.startswith("openrouter/")
        ):
            model = f"zai/{model}"

        # For vLLM, use hosted_vllm/ prefix per LiteLLM docs
        # Convert openai/ prefix to hosted_vllm/ if user specified it
        if self.is_vllm:
            model = f"hosted_vllm/{model}"

        # For Gemini, ensure gemini/ prefix if not already present
        if "gemini" in model.lower() and not model.startswith("gemini/"):
            model = f"gemini/{model}"

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
        }
        if not self.drop_params:
            kwargs["max_tokens"] = max_tokens
            kwargs["temperature"] = temperature

        # Pass api_base directly for custom endpoints (vLLM, etc.)
        if self.api_base:
            kwargs["api_base"] = self.api_base

        if tools:
            kwargs["tools"] = tools
        if self.extra_headers:
            kwargs["extra_headers"] = self.extra_headers

        try:
            if on_delta:
                kwargs["stream"] = True
                response = await acompletion(**kwargs)
                if hasattr(response, "__aiter__"):
                    return await self._parse_completions_stream(response, on_delta=on_delta)

            response = await acompletion(**kwargs)
            parsed = self._parse_response(response)
            if on_delta and parsed.content:
                await on_delta(parsed.content)
            return parsed
        except Exception as e:
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
        return False

    def _use_responses_api(self) -> bool:
        if not self.api_type:
            return False
        return self.api_type in {"openai-responses", "openai_responses", "responses"}

    async def _chat_with_responses(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str,
        max_tokens: int,
        temperature: float,
        session_state: dict[str, Any] | None = None,
        on_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        if self.api_base:
            return await self._chat_with_direct_responses(
                messages=messages,
                tools=tools,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                session_state=session_state,
                on_delta=on_delta,
            )

        input_items = self._messages_to_responses_input(messages)

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
            kwargs["tool_choice"] = "auto"
        if self.extra_headers:
            kwargs["extra_headers"] = self.extra_headers

        try:
            response = await aresponses(**kwargs)
            if hasattr(response, "__aiter__"):
                return await self._parse_responses_stream(response, on_delta=on_delta)
            parsed = self._parse_responses_response(response)
            if on_delta and parsed.content:
                await on_delta(parsed.content)
            return parsed
        except Exception as e:
            return LLMResponse(
                content=f"Error calling LLM: {str(e)}",
                finish_reason="error",
            )

    async def _chat_with_direct_responses(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str,
        max_tokens: int,
        temperature: float,
        session_state: dict[str, Any] | None = None,
        on_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        input_items = self._messages_to_responses_input(messages)
        base_body: dict[str, Any] = {
            "model": model,
            "input": input_items,
        }
        if not self.drop_params:
            base_body["max_output_tokens"] = max_tokens
            base_body["temperature"] = temperature
        if tools:
            base_body["tools"] = self._convert_tools_to_responses(tools)
            base_body["tool_choice"] = "auto"
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
        last_error: str | None = None

        try:
            client_kwargs: dict[str, Any] = {"timeout": 60.0}
            if self.proxy:
                client_kwargs["proxy"] = self.proxy
            async with httpx.AsyncClient(**client_kwargs) as client:
                for url in urls:
                    # If streaming is requested, try SSE first.
                    if on_delta:
                        stream_body = dict(base_body)
                        stream_body["stream"] = True
                        headers = dict(base_headers)
                        headers["Accept"] = "text/event-stream"
                        try:
                            async with client.stream("POST", url, headers=headers, json=stream_body) as response:
                                if response.status_code == 200:
                                    return await self._consume_responses_sse(response, on_delta=on_delta)
                                raw = await response.aread()
                                raw_text = raw.decode("utf-8", "ignore")
                                if response.status_code < 500:
                                    return LLMResponse(
                                        content=f"Error calling LLM: HTTP {response.status_code} {raw_text}",
                                        finish_reason="error",
                                    )
                                last_error = f"HTTP {response.status_code} {raw_text}"
                        except Exception as e:
                            last_error = str(e)

                    # Try non-stream (most compatible / fallback)
                    headers = dict(base_headers)
                    headers["Accept"] = "application/json"
                    try:
                        response = await client.post(url, headers=headers, json=base_body)
                        if response.status_code == 200:
                            parsed = self._parse_responses_response(response.json())
                            if on_delta and parsed.content:
                                await on_delta(parsed.content)
                            return parsed
                        raw = response.text
                        if response.status_code < 500:
                            return LLMResponse(
                                content=f"Error calling LLM: HTTP {response.status_code} {raw}",
                                finish_reason="error",
                            )
                        last_error = f"HTTP {response.status_code} {raw}"
                    except Exception as e:
                        last_error = str(e)

            return LLMResponse(
                content=f"Error calling LLM: {last_error or 'unknown error'}",
                finish_reason="error",
            )
        except Exception as e:
            return LLMResponse(
                content=f"Error calling LLM: {str(e)}",
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
        content_parts: list[str] = []
        completed_response: dict[str, Any] | None = None

        async for event in self._iter_sse(response):
            event_type = event.get("type")
            if event_type == "response.output_text.delta":
                delta = event.get("delta") or ""
                if delta:
                    if on_delta:
                        await on_delta(delta)
                    content_parts.append(delta)
            elif event_type == "response.completed":
                completed_response = event.get("response")

        if completed_response:
            return self._parse_responses_response(completed_response)

        return LLMResponse(
            content="".join(content_parts),
            finish_reason="stop",
        )

    async def _iter_sse(self, response: httpx.Response):
        buffer: list[str] = []
        async for line in response.aiter_lines():
            if line == "":
                if buffer:
                    data_lines = [l[5:].strip() for l in buffer if l.startswith("data:")]
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
        content_parts: list[str] = []
        async for event in stream:
            event_type = self._get_event_type(event)
            if event_type == "response.output_text.delta":
                delta = self._get_event_value(event, "delta")
                if delta:
                    if on_delta:
                        await on_delta(delta)
                    content_parts.append(delta)

        completed = getattr(stream, "completed_response", None)
        if completed is not None:
            response_obj = getattr(completed, "response", None)
            if response_obj is not None:
                return self._parse_responses_response(response_obj)

        content = "".join(content_parts)
        return LLMResponse(
            content=content,
            finish_reason="stop",
        )

    async def _parse_completions_stream(
        self,
        stream: Any,
        on_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        content_parts: list[str] = []
        tool_calls: list[ToolCallRequest] = []
        finish_reason = "stop"

        async for chunk in stream:
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

        return LLMResponse(
            content="".join(content_parts),
            tool_calls=tool_calls,
            finish_reason=finish_reason,
        )

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
        choice = response.choices[0]
        message = choice.message
        
        tool_calls = []
        if hasattr(message, "tool_calls") and message.tool_calls:
            for tc in message.tool_calls:
                # Parse arguments from JSON string if needed
                args = tc.function.arguments
                if isinstance(args, str):
                    import json
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

        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "stop",
            usage=usage,
            response_id=response_id,
            conversation_id=conversation_id,
            model=model,
        )

    def _parse_responses_response(self, response: Any) -> LLMResponse:
        """Parse LiteLLM Responses API response into our standard format."""
        content = ""
        if hasattr(response, "output_text"):
            content = response.output_text or ""
        elif isinstance(response, dict):
            content = response.get("output_text") or ""

        tool_calls: list[ToolCallRequest] = []
        output_items = getattr(response, "output", None)
        if output_items is None and isinstance(response, dict):
            output_items = response.get("output")
        if not content and output_items:
            content = self._extract_output_text(output_items)
        if output_items:
            for item in output_items:
                item_type = item.get("type") if isinstance(item, dict) else getattr(item, "type", None)
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
        status = getattr(response, "status", None)
        if status and status != "completed":
            finish_reason = status

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
            response_id=response_id,
            conversation_id=conversation_id,
            model=model,
        )

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

        for msg in messages:
            role = msg.get("role")
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
    
    def get_default_model(self) -> str:
        """Get the default model."""
        return self.default_model

    def supports_native_session(self) -> bool:
        """Return True when using Responses API with previous_response_id support."""
        return self._use_responses_api()
