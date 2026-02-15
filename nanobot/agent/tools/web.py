"""Web tools: web_search and web_fetch."""

import asyncio
import html
import json
import os
import re
import time
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx
from loguru import logger

from nanobot.agent.tools.base import Tool

# Shared constants
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7_2) AppleWebKit/537.36"
MAX_REDIRECTS = 5  # Limit redirects to prevent DoS attacks


def _strip_tags(text: str) -> str:
    """Remove HTML tags and decode entities."""
    text = re.sub(r'<script[\s\S]*?</script>', '', text, flags=re.I)
    text = re.sub(r'<style[\s\S]*?</style>', '', text, flags=re.I)
    text = re.sub(r'<[^>]+>', '', text)
    return html.unescape(text).strip()


def _normalize(text: str) -> str:
    """Normalize whitespace."""
    text = re.sub(r'[ \t]+', ' ', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def _validate_url(url: str) -> tuple[bool, str]:
    """Validate URL: must be http(s) with valid domain."""
    try:
        p = urlparse(url)
        if p.scheme not in ('http', 'https'):
            return False, f"Only http/https allowed, got '{p.scheme or 'none'}'"
        if not p.netloc:
            return False, "Missing domain"
        return True, ""
    except Exception as e:
        return False, str(e)


class WebSearchTool(Tool):
    """Search the web via multiple providers with fallback."""

    name = "web_search"
    description = "Search the web. Returns titles, URLs, and snippets."
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "count": {"type": "integer", "description": "Results (1-10)", "minimum": 1, "maximum": 10}
        },
        "required": ["query"]
    }

    SUPPORTED_PROVIDERS = {
        "auto",
        "openai_hosted",
        "bing_news_jina",
        "hn_algolia",
        "brave",
        "tavily",
        "searxng",
    }
    DEFAULT_PROVIDER_ORDER = (
        "openai_hosted",
        "bing_news_jina",
        "hn_algolia",
        "brave",
        "tavily",
        "searxng",
    )

    @classmethod
    def from_config(
        cls,
        config: Any | None,
        legacy_brave_api_key: str | None = None,
    ) -> "WebSearchTool":
        """Create a tool instance from a WebSearchConfig-like object."""

        def _get(name: str, default: Any) -> Any:
            if config is None:
                return default
            value = getattr(config, name, default)
            return default if value is None else value

        raw_fallback = _get("fallback_providers", [])
        fallback = raw_fallback if isinstance(raw_fallback, list) else []
        timeout_raw = _get("timeout_seconds", 10.0)
        try:
            timeout_seconds = float(timeout_raw)
        except (TypeError, ValueError):
            timeout_seconds = 10.0

        return cls(
            api_key=_get("api_key", "") or legacy_brave_api_key or None,
            max_results=_get("max_results", 5),
            provider=_get("provider", "auto"),
            fallback_providers=fallback,
            brave_api_key=_get("brave_api_key", ""),
            tavily_api_key=_get("tavily_api_key", ""),
            searxng_base_url=_get("searxng_base_url", ""),
            openai_api_key=_get("openai_api_key", ""),
            openai_api_base=_get("openai_api_base", ""),
            openai_model=_get("openai_model", "gpt-4.1-mini"),
            openai_proxy=_get("openai_proxy", ""),
            openai_headers=_get("openai_headers", {}),
            timeout_seconds=timeout_seconds,
        )

    def __init__(
        self,
        api_key: str | None = None,
        max_results: int = 5,
        provider: str = "auto",
        fallback_providers: list[str] | None = None,
        brave_api_key: str | None = None,
        tavily_api_key: str | None = None,
        searxng_base_url: str | None = None,
        openai_api_key: str | None = None,
        openai_api_base: str | None = None,
        openai_model: str = "gpt-4.1-mini",
        openai_proxy: str | None = None,
        openai_headers: dict[str, str] | None = None,
        timeout_seconds: float = 10.0,
    ):
        provider_name = self._normalize_provider(provider, allow_auto=True)
        self.provider = provider_name or "auto"
        self.max_results = max_results
        self.timeout_seconds = timeout_seconds if timeout_seconds > 0 else 10.0
        self.fallback_providers = self._normalize_provider_list(fallback_providers or [])

        # Legacy api_key remains Brave-compatible for backward compatibility.
        self.brave_api_key = brave_api_key or api_key or os.environ.get("BRAVE_API_KEY", "")
        self.tavily_api_key = tavily_api_key or os.environ.get("TAVILY_API_KEY", "")
        self.searxng_base_url = searxng_base_url or os.environ.get("SEARXNG_BASE_URL", "")
        self.openai_api_key = openai_api_key or os.environ.get("OPENAI_API_KEY", "")
        self.openai_api_base = (
            openai_api_base
            or os.environ.get("OPENAI_API_BASE", "")
            or "https://api.openai.com/v1"
        )
        self.openai_model = openai_model or "gpt-4.1-mini"
        self.openai_proxy = openai_proxy or os.environ.get("OPENAI_PROXY", "")
        self.openai_headers = dict(openai_headers or {})

    async def execute(self, query: str, count: int | None = None, **kwargs: Any) -> str:
        t_start = time.monotonic()
        query = (query or "").strip()
        if not query:
            return "Error: query cannot be empty"

        n = min(max(count or self.max_results, 1), 10)
        provider_order = self._resolve_provider_order()
        if not provider_order:
            return "Error: no valid web search providers configured"

        errors: list[str] = []
        for provider in provider_order:
            ready, reason = self._provider_ready(provider)
            if not ready:
                errors.append(f"{provider} skipped ({reason})")
                continue

            try:
                results, summary = await self._search(provider, query, n)
                if results:
                    logger.debug(
                        f"WebSearchTool query={query!r} provider={provider} "
                        f"results={len(results)} elapsed={(time.monotonic() - t_start):.3f}s"
                    )
                    return self._format_results(query, provider, results, summary)
                if summary:
                    logger.debug(
                        f"WebSearchTool query={query!r} provider={provider} "
                        f"summary_chars={len(summary)} elapsed={(time.monotonic() - t_start):.3f}s"
                    )
                    return self._format_summary(query, provider, summary)
                errors.append(f"{provider} returned no results")
            except Exception as e:
                errors.append(f"{provider}: {e}")
                logger.warning(
                    f"WebSearchTool failed query={query!r} provider={provider} "
                    f"elapsed={(time.monotonic() - t_start):.3f}s error={e}"
                )

        if errors:
            details = "; ".join(errors[:4])
            if len(errors) > 4:
                details += "; ..."
            return f"Error: web_search failed ({details})"
        return f"No results for: {query}"

    def _normalize_provider(self, provider: str, allow_auto: bool = False) -> str | None:
        value = str(provider or "").strip().lower()
        aliases = {
            "openai": "openai_hosted",
            "openai_hosted": "openai_hosted",
            "bing": "bing_news_jina",
            "bing_news": "bing_news_jina",
            "bing_news_jina": "bing_news_jina",
            "hackernews": "hn_algolia",
            "hn": "hn_algolia",
            "hn_algolia": "hn_algolia",
            "brave": "brave",
            "tavily": "tavily",
            "searx": "searxng",
            "searxng": "searxng",
            "auto": "auto",
        }
        normalized = aliases.get(value)
        if normalized == "auto" and not allow_auto:
            return None
        return normalized

    def _normalize_provider_list(self, providers: list[str]) -> list[str]:
        normalized: list[str] = []
        for item in providers:
            name = self._normalize_provider(str(item), allow_auto=False)
            if not name or name in normalized:
                continue
            normalized.append(name)
        return normalized

    def _resolve_provider_order(self) -> list[str]:
        seed: list[str]
        if self.provider == "auto":
            seed = list(self.DEFAULT_PROVIDER_ORDER)
        else:
            seed = [self.provider]
        seed.extend(self.fallback_providers)
        return self._normalize_provider_list(seed)

    def _provider_ready(self, provider: str) -> tuple[bool, str]:
        if provider == "openai_hosted":
            if self.openai_api_key:
                return True, ""
            return False, "OPENAI_API_KEY not configured"
        if provider == "bing_news_jina":
            return True, ""
        if provider == "hn_algolia":
            return True, ""
        if provider == "brave":
            if self.brave_api_key:
                return True, ""
            return False, "BRAVE_API_KEY not configured"
        if provider == "tavily":
            if self.tavily_api_key:
                return True, ""
            return False, "TAVILY_API_KEY not configured"
        if provider == "searxng":
            if self.searxng_base_url:
                return True, ""
            return False, "SEARXNG_BASE_URL not configured"
        return False, "unsupported provider"

    async def _search(
        self,
        provider: str,
        query: str,
        count: int,
    ) -> tuple[list[dict[str, str]], str]:
        if provider == "openai_hosted":
            return await self._search_openai_hosted(query, count)
        if provider == "bing_news_jina":
            return await self._search_bing_news_jina(query, count)
        if provider == "hn_algolia":
            return await self._search_hn_algolia(query, count)
        if provider == "brave":
            return await self._search_brave(query, count)
        if provider == "tavily":
            return await self._search_tavily(query, count)
        if provider == "searxng":
            return await self._search_searxng(query, count)
        raise ValueError(f"unsupported provider: {provider}")

    async def _search_brave(self, query: str, count: int) -> tuple[list[dict[str, str]], str]:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            r = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": count},
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": self.brave_api_key,
                },
            )
            r.raise_for_status()
        payload = r.json()
        results = payload.get("web", {}).get("results", [])
        normalized = self._normalize_result_items(results, count, description_key="description")
        return normalized, ""

    async def _search_tavily(self, query: str, count: int) -> tuple[list[dict[str, str]], str]:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            r = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": self.tavily_api_key,
                    "query": query,
                    "max_results": count,
                    "search_depth": "basic",
                    "include_answer": False,
                },
                headers={"Accept": "application/json"},
            )
            r.raise_for_status()
        payload = r.json()
        results = payload.get("results", [])
        normalized = self._normalize_result_items(results, count, description_key="content")
        return normalized, ""

    async def _search_searxng(self, query: str, count: int) -> tuple[list[dict[str, str]], str]:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            r = await client.get(
                self._build_searxng_search_url(),
                params={"q": query, "format": "json"},
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            )
            r.raise_for_status()
        payload = r.json()
        results = payload.get("results", [])
        normalized = self._normalize_result_items(
            results,
            count,
            url_keys=("url", "link"),
            description_key="content",
        )
        return normalized, ""

    async def _search_openai_hosted(self, query: str, count: int) -> tuple[list[dict[str, str]], str]:
        prompt = (
            f"Search the web for: {query}\n"
            f"Return up to {count} relevant sources with title and URL."
        )
        client_kwargs: dict[str, Any] = {"timeout": self.timeout_seconds}
        if self.openai_proxy:
            client_kwargs["proxy"] = self.openai_proxy
        headers = {
            "Authorization": f"Bearer {self.openai_api_key}",
            "Content-Type": "application/json",
            "OpenAI-Beta": "responses=v1",
            "User-Agent": USER_AGENT,
        }
        if self.openai_headers:
            headers.update(self.openai_headers)

        tool_types = ("web_search", "web_search_preview")
        response_urls = self._build_openai_responses_urls()
        last_exc: Exception | None = None
        attempts = 3
        async with httpx.AsyncClient(**client_kwargs) as client:
            for tool_type in tool_types:
                payload = {
                    "model": self.openai_model,
                    "input": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "input_text", "text": prompt}
                            ],
                        }
                    ],
                    "tools": [{"type": tool_type}],
                }
                response_payload: dict[str, Any] | None = None
                try:
                    for url in response_urls:
                        for attempt in range(1, attempts + 1):
                            try:
                                r = await client.post(
                                    url,
                                    json=payload,
                                    headers=headers,
                                )
                                r.raise_for_status()
                                response_payload = r.json()
                                break
                            except httpx.HTTPStatusError as exc:
                                status = int(exc.response.status_code) if exc.response is not None else 0
                                # For gateway-side instability, retry with backoff.
                                if status in {500, 502, 503, 504} and attempt < attempts:
                                    await asyncio.sleep(0.35 * attempt)
                                    continue
                                # 5xx on this endpoint: try the alternate endpoint.
                                if status in {500, 502, 503, 504}:
                                    break
                                raise
                            except (httpx.TimeoutException, httpx.TransportError):
                                if attempt < attempts:
                                    await asyncio.sleep(0.35 * attempt)
                                    continue
                                break
                        if response_payload is not None:
                            break
                except Exception as exc:
                    last_exc = exc
                    # Prefer new tool type, but keep compatibility with older proxies.
                    if tool_type == "web_search":
                        continue
                    raise

                if response_payload is None:
                    continue
                results = self._extract_openai_citations(response_payload, limit=count)
                summary = self._extract_openai_output_text(response_payload)
                return results, summary

        if last_exc:
            raise last_exc
        return [], ""

    async def _search_bing_news_jina(self, query: str, count: int) -> tuple[list[dict[str, str]], str]:
        target_url = (
            "http://www.bing.com/news/search?"
            + urlencode({"q": query, "qft": 'sortbydate="1"', "form": "YFNR"})
        )
        fetch_url = f"https://r.jina.ai/{target_url}"

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            r = await client.get(
                fetch_url,
                headers={"User-Agent": USER_AGENT, "Accept": "text/plain; charset=utf-8"},
            )
            r.raise_for_status()

        results = self._parse_bing_news_markdown(r.text, count)
        return results, ""

    async def _search_hn_algolia(self, query: str, count: int) -> tuple[list[dict[str, str]], str]:
        fetch_count = min(max(count * 2, 10), 40)
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            r = await client.get(
                "https://hn.algolia.com/api/v1/search_by_date",
                params={"query": query, "tags": "story", "hitsPerPage": fetch_count},
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            )
            r.raise_for_status()

        payload = r.json()
        hits = payload.get("hits", [])
        results: list[dict[str, str]] = []
        seen_urls: set[str] = set()
        for hit in hits:
            if not isinstance(hit, dict):
                continue
            title = str(hit.get("title") or "").strip()
            if not title:
                continue
            story_url = str(hit.get("url") or "").strip()
            if not story_url:
                story_id = str(hit.get("objectID") or "").strip()
                if not story_id:
                    continue
                story_url = f"https://news.ycombinator.com/item?id={story_id}"
            if story_url in seen_urls:
                continue

            created_at = str(hit.get("created_at") or "").strip()
            points = hit.get("points")
            comments = hit.get("num_comments")
            description_parts = []
            if created_at:
                description_parts.append(f"HN time: {created_at}")
            if isinstance(points, int):
                description_parts.append(f"score: {points}")
            if isinstance(comments, int):
                description_parts.append(f"comments: {comments}")
            description = " | ".join(description_parts)

            results.append({"title": title, "url": story_url, "description": description})
            seen_urls.add(story_url)
            if len(results) >= count:
                break
        return results, ""

    def _parse_bing_news_markdown(self, text: str, count: int) -> list[dict[str, str]]:
        lines = [line.strip() for line in text.splitlines()]
        results: list[dict[str, str]] = []
        seen_urls: set[str] = set()
        link_re = re.compile(r"^\[(?P<title>.+?)\]\((?P<url>https?://[^)]+)\)$")

        for idx, raw_line in enumerate(lines):
            line = re.sub(r"^\*\s+", "", raw_line).strip()
            if not line or line.startswith("!["):
                continue

            m = link_re.match(line)
            if not m:
                continue

            title = m.group("title")
            url = m.group("url").strip()
            host = urlparse(url).netloc.lower()
            if "![image" in title.lower():
                continue
            if (
                "bing.com/news/search?q=site%3a" in url
                or "bing.com" in host
                or url.startswith("blob:")
            ):
                continue
            if url in seen_urls:
                continue

            title = html.unescape(title)
            title = re.sub(r"\*+", "", title)
            title = re.sub(r"\s*-{2,}\s*$", "", title).strip()
            title = re.sub(r"\s{2,}", " ", title)
            if len(title) < 6:
                continue

            description = ""
            for look_ahead in range(idx + 1, min(idx + 4, len(lines))):
                candidate = re.sub(r"^\*\s+", "", lines[look_ahead]).strip()
                if not candidate or candidate.startswith("[") or candidate.startswith("!["):
                    continue
                if candidate.lower().startswith(("title:", "url source:", "markdown content:")):
                    continue
                description = candidate
                break

            results.append({"title": title, "url": url, "description": description})
            seen_urls.add(url)
            if len(results) >= count:
                break
        return results

    def _build_searxng_search_url(self) -> str:
        base = self.searxng_base_url.strip().rstrip("/")
        if base.endswith("/search"):
            return base
        return f"{base}/search"

    def _build_openai_responses_url(self) -> str:
        base = self.openai_api_base.strip().rstrip("/")
        if base.endswith("/responses"):
            return base
        if base.endswith("/v1"):
            return f"{base}/responses"
        return f"{base}/v1/responses"

    def _build_openai_responses_urls(self) -> list[str]:
        base = self.openai_api_base.strip().rstrip("/")
        urls: list[str] = []
        if base.endswith("/responses"):
            urls.append(base)
            base_root = re.sub(r"/responses$", "", base)
            if base_root:
                urls.append(f"{base_root}/responses")
        elif base.endswith("/v1"):
            urls.append(f"{base}/responses")
            base_root = re.sub(r"/v1$", "", base)
            if base_root:
                urls.append(f"{base_root}/responses")
        else:
            urls.append(f"{base}/v1/responses")
            urls.append(f"{base}/responses")
        deduped: list[str] = []
        for u in urls:
            if u not in deduped:
                deduped.append(u)
        return deduped

    def _extract_openai_output_text(self, payload: dict[str, Any]) -> str:
        output_text = payload.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text.strip()

        parts: list[str] = []
        for item in payload.get("output", []) or []:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "message":
                continue
            for block in item.get("content", []) or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") != "output_text":
                    continue
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
        return "\n".join(parts)

    def _extract_openai_citations(
        self,
        payload: dict[str, Any],
        limit: int,
    ) -> list[dict[str, str]]:
        citations: list[dict[str, str]] = []
        seen_urls: set[str] = set()

        for item in payload.get("output", []) or []:
            if not isinstance(item, dict):
                continue
            content_blocks = item.get("content", [])
            if not isinstance(content_blocks, list):
                continue
            for block in content_blocks:
                if not isinstance(block, dict):
                    continue
                annotations = block.get("annotations", [])
                if not isinstance(annotations, list):
                    continue
                for annotation in annotations:
                    if not isinstance(annotation, dict):
                        continue
                    if annotation.get("type") != "url_citation":
                        continue
                    url = str(annotation.get("url") or "").strip()
                    if not url or url in seen_urls:
                        continue
                    title = str(annotation.get("title") or "").strip() or url
                    citations.append({"title": title, "url": url, "description": ""})
                    seen_urls.add(url)
                    if len(citations) >= limit:
                        return citations

        return citations

    def _normalize_result_items(
        self,
        items: list[dict[str, Any]],
        count: int,
        url_keys: tuple[str, ...] = ("url",),
        description_key: str = "description",
    ) -> list[dict[str, str]]:
        normalized: list[dict[str, str]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            url = ""
            for key in url_keys:
                value = item.get(key)
                if value:
                    url = str(value).strip()
                    break
            if not url:
                continue

            title = str(item.get("title") or "").strip() or url
            description = str(item.get(description_key) or "").strip()
            normalized.append(
                {
                    "title": title,
                    "url": url,
                    "description": description,
                }
            )
            if len(normalized) >= count:
                break
        return normalized

    def _format_results(
        self,
        query: str,
        provider: str,
        results: list[dict[str, str]],
        summary: str = "",
    ) -> str:
        lines = [f"Results for: {query} (provider: {provider})\n"]
        for i, item in enumerate(results, 1):
            title = item.get("title", "")
            url = item.get("url", "")
            desc = item.get("description", "")
            lines.append(f"{i}. {title}\n   {url}")
            if desc:
                lines.append(f"   {desc}")
        if summary and provider == "openai_hosted":
            lines.append("\nSummary:")
            lines.append(summary.strip())
        return "\n".join(lines)

    def _format_summary(self, query: str, provider: str, summary: str) -> str:
        return f"Results for: {query} (provider: {provider})\n\n{summary.strip()}"


class WebFetchTool(Tool):
    """Fetch and extract content from a URL using Readability."""

    name = "web_fetch"
    description = "Fetch URL and extract readable content (HTML → markdown/text)."
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to fetch"},
            "extractMode": {"type": "string", "enum": ["markdown", "text"], "default": "markdown"},
            "maxChars": {"type": "integer", "minimum": 100}
        },
        "required": ["url"]
    }

    def __init__(self, max_chars: int = 50000):
        self.max_chars = max_chars

    async def execute(self, url: str, extractMode: str = "markdown", maxChars: int | None = None, **kwargs: Any) -> str:
        t_start = time.monotonic()
        from readability import Document

        max_chars = maxChars or self.max_chars

        # Validate URL before fetching
        is_valid, error_msg = _validate_url(url)
        if not is_valid:
            return json.dumps({"error": f"URL validation failed: {error_msg}", "url": url})

        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                max_redirects=MAX_REDIRECTS,
                timeout=30.0
            ) as client:
                r = await client.get(url, headers={"User-Agent": USER_AGENT})
                r.raise_for_status()

            ctype = r.headers.get("content-type", "")

            # JSON
            if "application/json" in ctype:
                text, extractor = json.dumps(r.json(), indent=2), "json"
            # HTML
            elif "text/html" in ctype or r.text[:256].lower().startswith(("<!doctype", "<html")):
                doc = Document(r.text)
                content = self._to_markdown(doc.summary()) if extractMode == "markdown" else _strip_tags(doc.summary())
                text = f"# {doc.title()}\n\n{content}" if doc.title() else content
                extractor = "readability"
            else:
                text, extractor = r.text, "raw"

            truncated = len(text) > max_chars
            if truncated:
                text = text[:max_chars]

            logger.debug(
                f"WebFetchTool url={url} status={r.status_code} extractor={extractor} "
                f"text_chars={len(text)} truncated={truncated} "
                f"elapsed={(time.monotonic() - t_start):.3f}s"
            )
            return json.dumps({"url": url, "finalUrl": str(r.url), "status": r.status_code,
                              "extractor": extractor, "truncated": truncated, "length": len(text), "text": text})
        except Exception as e:
            logger.warning(
                f"WebFetchTool failed url={url} elapsed={(time.monotonic() - t_start):.3f}s error={e}"
            )
            return json.dumps({"error": str(e), "url": url})

    def _to_markdown(self, html: str) -> str:
        """Convert HTML to markdown."""
        # Convert links, headings, lists before stripping tags
        text = re.sub(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>([\s\S]*?)</a>',
                      lambda m: f'[{_strip_tags(m[2])}]({m[1]})', html, flags=re.I)
        text = re.sub(r'<h([1-6])[^>]*>([\s\S]*?)</h\1>',
                      lambda m: f'\n{"#" * int(m[1])} {_strip_tags(m[2])}\n', text, flags=re.I)
        text = re.sub(r'<li[^>]*>([\s\S]*?)</li>', lambda m: f'\n- {_strip_tags(m[1])}', text, flags=re.I)
        text = re.sub(r'</(p|div|section|article)>', '\n\n', text, flags=re.I)
        text = re.sub(r'<(br|hr)\s*/?>', '\n', text, flags=re.I)
        return _normalize(_strip_tags(text))
