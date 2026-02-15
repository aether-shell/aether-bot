import asyncio
from types import SimpleNamespace

from nanobot.agent.tools import web as web_tools
from nanobot.agent.tools.web import WebSearchTool


def test_web_search_reports_missing_provider_credentials() -> None:
    tool = WebSearchTool(
        provider="openai_hosted",
        fallback_providers=["brave", "tavily", "searxng"],
        brave_api_key="",
        tavily_api_key="",
        searxng_base_url="",
        openai_api_key="",
    )

    result = asyncio.run(tool.execute(query="latest python release"))

    assert "Error: web_search failed" in result
    assert "OPENAI_API_KEY not configured" in result
    assert "BRAVE_API_KEY not configured" in result


def test_web_search_falls_back_to_tavily(monkeypatch) -> None:
    tool = WebSearchTool(
        provider="brave",
        fallback_providers=["tavily"],
        brave_api_key="",
        tavily_api_key="tvly-test",
    )

    async def _fake_tavily(self, query: str, count: int):
        return (
            [
                {
                    "title": "Example Result",
                    "url": "https://example.com",
                    "description": f"match for {query} ({count})",
                }
            ],
            "",
        )

    monkeypatch.setattr(WebSearchTool, "_search_tavily", _fake_tavily)

    result = asyncio.run(tool.execute(query="nanobot", count=1))

    assert "provider: tavily" in result
    assert "https://example.com" in result


def test_extract_openai_citations_and_summary() -> None:
    tool = WebSearchTool(provider="openai_hosted", openai_api_key="sk-test")

    payload = {
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": "Top result is from Example Docs.",
                        "annotations": [
                            {
                                "type": "url_citation",
                                "title": "Example Docs",
                                "url": "https://docs.example.com/search",
                            }
                        ],
                    }
                ],
            }
        ]
    }

    citations = tool._extract_openai_citations(payload, limit=5)
    summary = tool._extract_openai_output_text(payload)

    assert citations == [
        {
            "title": "Example Docs",
            "url": "https://docs.example.com/search",
            "description": "",
        }
    ]
    assert summary == "Top result is from Example Docs."


def test_from_config_uses_legacy_brave_api_key() -> None:
    cfg = SimpleNamespace(
        api_key="legacy-brave-key",
        max_results=5,
        provider="auto",
        fallback_providers=[],
        brave_api_key="",
        tavily_api_key="",
        searxng_base_url="",
        openai_api_key="",
        openai_api_base="",
        openai_model="gpt-4.1-mini",
        openai_proxy="",
        openai_headers={},
        timeout_seconds=10.0,
    )

    tool = WebSearchTool.from_config(cfg)

    assert tool.brave_api_key == "legacy-brave-key"


def test_from_config_reads_openai_proxy_and_headers() -> None:
    cfg = SimpleNamespace(
        api_key="",
        max_results=5,
        provider="openai_hosted",
        fallback_providers=[],
        brave_api_key="",
        tavily_api_key="",
        searxng_base_url="",
        openai_api_key="sk-test",
        openai_api_base="https://example.com/v1",
        openai_model="gpt-4.1-mini",
        openai_proxy="http://127.0.0.1:7897",
        openai_headers={"OpenAI-Beta": "responses=v1", "X-Test": "1"},
        timeout_seconds=10.0,
    )

    tool = WebSearchTool.from_config(cfg)

    assert tool.openai_proxy == "http://127.0.0.1:7897"
    assert tool.openai_headers.get("X-Test") == "1"


def test_openai_hosted_request_uses_proxy_and_headers(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "Summary text.",
                                "annotations": [
                                    {
                                        "type": "url_citation",
                                        "title": "Example",
                                        "url": "https://example.com",
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }

    class _FakeAsyncClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = dict(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url: str, json: dict[str, object], headers: dict[str, str]):
            captured["url"] = url
            captured["json"] = dict(json)
            captured["headers"] = dict(headers)
            return _FakeResponse()

    monkeypatch.setattr(web_tools.httpx, "AsyncClient", _FakeAsyncClient)

    tool = WebSearchTool(
        provider="openai_hosted",
        openai_api_key="sk-test",
        openai_api_base="https://example.com/v1",
        openai_model="gpt-5.3-codex",
        openai_proxy="http://127.0.0.1:7897",
        openai_headers={"OpenAI-Beta": "responses=v1", "X-Test": "1"},
    )

    result = asyncio.run(tool.execute(query="today ai news", count=3))

    client_kwargs = captured["client_kwargs"]
    assert isinstance(client_kwargs, dict)
    assert client_kwargs.get("proxy") == "http://127.0.0.1:7897"
    assert float(client_kwargs.get("timeout", 0)) > 0

    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers.get("Authorization") == "Bearer sk-test"
    assert headers.get("OpenAI-Beta") == "responses=v1"
    assert headers.get("X-Test") == "1"

    payload = captured["json"]
    assert isinstance(payload, dict)
    assert payload.get("tools", [{}])[0].get("type") == "web_search"
    assert isinstance(payload.get("input"), list)

    assert captured.get("url") == "https://example.com/v1/responses"
    assert "provider: openai_hosted" in result
    assert "https://example.com" in result


def test_openai_hosted_falls_back_tool_type_to_preview(monkeypatch) -> None:
    calls: list[str] = []

    class _FakeResponse:
        def __init__(self, status_code: int, ok_payload: dict[str, object] | None = None):
            self.status_code = status_code
            self._ok_payload = ok_payload or {}

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                request = web_tools.httpx.Request("POST", "https://example.com/v1/responses")
                response = web_tools.httpx.Response(self.status_code, request=request)
                raise web_tools.httpx.HTTPStatusError(
                    f"{self.status_code} error",
                    request=request,
                    response=response,
                )

        def json(self) -> dict[str, object]:
            return self._ok_payload

    class _FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url: str, json: dict[str, object], headers: dict[str, str]):
            tool_type = str(json.get("tools", [{}])[0].get("type", ""))
            calls.append(tool_type)
            if tool_type == "web_search":
                return _FakeResponse(502)
            return _FakeResponse(
                200,
                {
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "Recovered with preview tool.",
                                    "annotations": [
                                        {
                                            "type": "url_citation",
                                            "title": "Preview",
                                            "url": "https://example.com/preview",
                                        }
                                    ],
                                }
                            ],
                        }
                    ]
                },
            )

    monkeypatch.setattr(web_tools.httpx, "AsyncClient", _FakeAsyncClient)

    tool = WebSearchTool(
        provider="openai_hosted",
        openai_api_key="sk-test",
        openai_api_base="https://example.com/v1",
        openai_model="gpt-5.3-codex",
    )

    result = asyncio.run(tool.execute(query="today ai news", count=1))

    assert calls[0] == "web_search"
    assert "web_search_preview" in calls
    assert "provider: openai_hosted" in result
    assert "https://example.com/preview" in result


def test_openai_hosted_switches_to_alternate_responses_endpoint(monkeypatch) -> None:
    calls: list[str] = []

    class _FakeResponse:
        def __init__(self, status_code: int, payload: dict[str, object] | None = None):
            self.status_code = status_code
            self._payload = payload or {}

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                request = web_tools.httpx.Request("POST", "https://example.com/v1/responses")
                response = web_tools.httpx.Response(self.status_code, request=request)
                raise web_tools.httpx.HTTPStatusError(
                    f"{self.status_code} error",
                    request=request,
                    response=response,
                )

        def json(self) -> dict[str, object]:
            return self._payload

    class _FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url: str, json: dict[str, object], headers: dict[str, str]):
            calls.append(url)
            if "/v1/responses" in url:
                return _FakeResponse(502)
            return _FakeResponse(
                200,
                {
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "Recovered via alternate endpoint.",
                                    "annotations": [
                                        {
                                            "type": "url_citation",
                                            "title": "Alt",
                                            "url": "https://example.com/alt",
                                        }
                                    ],
                                }
                            ],
                        }
                    ]
                },
            )

    monkeypatch.setattr(web_tools.httpx, "AsyncClient", _FakeAsyncClient)

    tool = WebSearchTool(
        provider="openai_hosted",
        openai_api_key="sk-test",
        openai_api_base="https://example.com/v1",
        openai_model="gpt-5.3-codex",
    )

    result = asyncio.run(tool.execute(query="today ai news", count=1))

    assert any("/v1/responses" in c for c in calls)
    assert any(c.endswith("/responses") and "/v1/responses" not in c for c in calls)
    assert "provider: openai_hosted" in result
    assert "https://example.com/alt" in result


def test_openai_hosted_retries_once_on_503(monkeypatch) -> None:
    captured = {"calls": 0}

    class _FakeResponse:
        def __init__(self, status_code: int):
            self.status_code = status_code

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                request = web_tools.httpx.Request("POST", "https://example.com/v1/responses")
                response = web_tools.httpx.Response(self.status_code, request=request)
                raise web_tools.httpx.HTTPStatusError(
                    f"{self.status_code} error",
                    request=request,
                    response=response,
                )

        def json(self) -> dict[str, object]:
            return {
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "Recovered after retry.",
                                "annotations": [
                                    {
                                        "type": "url_citation",
                                        "title": "Recovered",
                                        "url": "https://example.com/recovered",
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }

    class _FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url: str, json: dict[str, object], headers: dict[str, str]):
            captured["calls"] += 1
            if captured["calls"] == 1:
                return _FakeResponse(503)
            return _FakeResponse(200)

    monkeypatch.setattr(web_tools.httpx, "AsyncClient", _FakeAsyncClient)

    tool = WebSearchTool(
        provider="openai_hosted",
        openai_api_key="sk-test",
        openai_api_base="https://example.com/v1",
        openai_model="gpt-5.3-codex",
    )

    result = asyncio.run(tool.execute(query="today ai news", count=1))

    assert captured["calls"] == 2
    assert "provider: openai_hosted" in result
    assert "https://example.com/recovered" in result


def test_openai_hosted_retries_on_timeout(monkeypatch) -> None:
    captured = {"calls": 0}

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "Recovered after timeout.",
                                "annotations": [
                                    {
                                        "type": "url_citation",
                                        "title": "Recovered",
                                        "url": "https://example.com/timeout-recovered",
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }

    class _FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url: str, json: dict[str, object], headers: dict[str, str]):
            captured["calls"] += 1
            if captured["calls"] == 1:
                raise web_tools.httpx.ReadTimeout("timeout", request=web_tools.httpx.Request("POST", url))
            return _FakeResponse()

    monkeypatch.setattr(web_tools.httpx, "AsyncClient", _FakeAsyncClient)

    tool = WebSearchTool(
        provider="openai_hosted",
        openai_api_key="sk-test",
        openai_api_base="https://example.com/v1",
        openai_model="gpt-5.3-codex",
    )

    result = asyncio.run(tool.execute(query="today ai news", count=1))

    assert captured["calls"] >= 2
    assert "provider: openai_hosted" in result
    assert "https://example.com/timeout-recovered" in result


def test_build_openai_responses_urls_from_v1_base() -> None:
    tool = WebSearchTool(
        provider="openai_hosted",
        openai_api_key="sk-test",
        openai_api_base="https://example.com/v1",
    )
    urls = tool._build_openai_responses_urls()
    assert urls == ["https://example.com/v1/responses", "https://example.com/responses"]


def test_web_search_falls_back_to_bing_news_jina(monkeypatch) -> None:
    tool = WebSearchTool(
        provider="openai_hosted",
        fallback_providers=["bing_news_jina"],
        openai_api_key="sk-test",
    )

    async def _fake_openai(self, query: str, count: int):
        raise RuntimeError("503 Service Unavailable")

    async def _fake_bing(self, query: str, count: int):
        return (
            [
                {
                    "title": "AI policy update",
                    "url": "https://example.org/ai-policy",
                    "description": f"for {query} ({count})",
                }
            ],
            "",
        )

    monkeypatch.setattr(WebSearchTool, "_search_openai_hosted", _fake_openai)
    monkeypatch.setattr(WebSearchTool, "_search_bing_news_jina", _fake_bing)

    result = asyncio.run(tool.execute(query="today ai news", count=1))

    assert "provider: bing_news_jina" in result
    assert "https://example.org/ai-policy" in result


def test_parse_bing_news_markdown_extracts_links() -> None:
    tool = WebSearchTool(provider="bing_news_jina")
    sample = """
Title: AI news today - Search News
URL Source: http://www.bing.com/news/search?q=AI+news+today

[Exclusive: Pentagon threatens to cut off Anthropic in AI safeguards dispute ---------------------------------------------------------------------------](https://www.axios.com/2026/02/15/claude-pentagon-anthropic-contract-maduro)
Anthropic has not agreed to the Pentagon's terms and defense officials are getting fed up after months of difficult negotiations.

[Pentagon threatens to cut off Anthropic in AI safeguards dispute, Axios reports -------------------------------------------------------------------------------](https://www.yahoo.com/news/articles/pentagon-threatens-cut-off-anthropic-022638299.html)
The Pentagon is considering severing its relationship with Anthropic over the artificial intelligence firm's insistence on ...
"""

    results = tool._parse_bing_news_markdown(sample, count=2)

    assert len(results) == 2
    assert results[0]["title"].startswith("Exclusive: Pentagon threatens to cut off Anthropic")
    assert results[0]["url"] == "https://www.axios.com/2026/02/15/claude-pentagon-anthropic-contract-maduro"
    assert "defense officials" in results[0]["description"]
