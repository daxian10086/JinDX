"""网页抓取：URL 检测、预取和代理级抓取。"""

import asyncio
import json
import logging
import re
import urllib.request

import httpx

from .config import config

logger = logging.getLogger(__name__)

# ── 常量和工具定义 ─────────────────────────────────────────────────

WEB_FETCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_fetch",
        "description": "Fetch content from a URL over HTTP/HTTPS. Use this instead of curl, wget, or other shell-based HTTP tools. Returns HTTP status and response body. Supports GET, HEAD, POST, PUT, DELETE, PATCH, OPTIONS methods.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to fetch (http:// or https://)"},
                "method": {"type": "string", "enum": ["GET", "HEAD", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"], "description": "HTTP method (default: GET)"},
                "headers": {"type": "object", "description": "Optional HTTP headers as key-value pairs"},
                "body": {"type": "string", "description": "Request body for POST/PUT/PATCH requests"},
            },
            "required": ["url"],
        },
    },
}

WEB_FETCH_HINT = (
    "A web_fetch tool is available for HTTP/HTTPS requests. "
    "Use it instead of curl, wget, or shell-based HTTP tools. "
    "The tool accepts: url (required), method (GET default), headers (optional), body (optional)."
)

WEB_FETCH_TIMEOUT = 20
WEB_FETCH_MAX_BODY = 80000
MAX_FETCH_LOOPS = 5


# ── URL 检测 ─────────────────────────────────────────────────────────

def has_urls_in_messages(messages: list) -> bool:
    """检查消息内容是否包含 HTTP/HTTPS URL。"""
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str) and ("http://" in content or "https://" in content):
            return True
        if isinstance(content, list):
            for part in content:
                text = part.get("text", "") if isinstance(part, dict) else str(part)
                if "http://" in text or "https://" in text:
                    return True
    return False


def extract_urls_from_text(text: str) -> list[str]:
    """从文本中提取 HTTP/HTTPS URL。"""
    return re.findall(r'https?://[^\s<>"{}|\\^`\[\]]+', text)


def ensure_web_fetch_tool(tools: list) -> list:
    """添加 web_fetch 工具（如果尚未存在）。"""
    result = list(tools)
    for t in result:
        if t.get("type") == "function" and t.get("function", {}).get("name") == "web_fetch":
            return result
        if t.get("type") == "web_fetch" or t.get("name") == "web_fetch":
            return result
    result.append(WEB_FETCH_TOOL)
    return result


def ensure_web_fetch_hint(messages: list) -> list:
    """添加 web_fetch 提示消息（如果尚未存在）。"""
    for msg in messages:
        if msg.get("role") == "user" and msg.get("content") == WEB_FETCH_HINT:
            return messages
    return [*messages, {"role": "user", "content": WEB_FETCH_HINT}]


# ── URL 预取 ────────────────────────────────────────────────────────

def _extract_urls_from_messages(messages: list) -> list[str]:
    """从消息列表中提取去重后的 URL 列表（跳过 localhost/内部 URL）。"""
    all_urls: list[str] = []
    for msg in messages:
        if msg.get("role") in ("user", "system"):
            content = msg.get("content", "")
            if isinstance(content, str):
                all_urls.extend(extract_urls_from_text(content))

    seen = set()
    urls = []
    for u in all_urls:
        if u not in seen and not ('127.0.0.1' in u or 'localhost' in u or '0.0.0.0' in u or '::1' in u):
            seen.add(u)
            urls.append(u)
    return urls


def _fetch_url_sync(url: str, fetch_timeout: int, max_body: int) -> tuple[str, str | None]:
    """同步抓取单个 URL，返回 (url, content_or_None_on_error)。"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; ChatProxy/1.0)"})
        with urllib.request.urlopen(req, timeout=fetch_timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            ct = resp.headers.get("Content-Type", "")
            if "html" in ct:
                text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
                text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
                text = re.sub(r'<[^>]+>', ' ', text)
                text = re.sub(r'\s+', ' ', text).strip()
            if len(text) > max_body:
                text = text[:max_body] + f"\n...[truncated, {len(text) - max_body} chars]"
            return (url, text)
    except (OSError, ValueError, TimeoutError) as e:
        logger.warning(f"Pre-fetch failed for {url}: {e}")
        return (url, None)


def _inject_fetched_context(messages: list, fetched: dict[str, str]) -> None:
    """将抓取到的网页内容注入用户消息末尾。"""
    if not fetched:
        return
    context = "\n\n---\n\n".join(
        f"[Web content from {url}]\n{content}"
        for url, content in fetched.items()
    )
    context = f"\n\n[Pre-fetched web content — use this directly, no need to call web_fetch]\n\n{context}"
    for msg in reversed(messages):
        if msg.get("role") == "user" and isinstance(msg.get("content"), str):
            msg["content"] = msg["content"] + context
            return
    messages.append({"role": "user", "content": context})


def prefetch_urls_into_messages(messages: list) -> None:
    """抓取用户消息中的 URL 并将内容追加为上下文（同步，在 executor 中运行）。

    此函数使用 urllib 做同步请求，调用方应在 async 上下文中通过
    run_in_executor 调用以避免阻塞事件循环。
    """
    urls = _extract_urls_from_messages(messages)
    if not urls:
        return

    max_urls = config.get("web_fetch_max_urls", 5)
    fetch_timeout = config.get("web_fetch_timeout", 10)
    max_body = config.get("web_fetch_max_body", 80000)

    fetched: dict[str, str] = {}
    for url in urls[:max_urls]:
        url, content = _fetch_url_sync(url, fetch_timeout, max_body)
        if content is not None:
            fetched[url] = content
            logger.info(f"Pre-fetched {url} -> {len(content)} chars")

    _inject_fetched_context(messages, fetched)


async def prefetch_urls_async(messages: list) -> None:
    """抓取用户消息中的 URL（异步版本，在线程池中执行 HTTP 请求）。"""
    urls = _extract_urls_from_messages(messages)
    if not urls:
        return

    max_urls = config.get("web_fetch_max_urls", 5)
    fetch_timeout = config.get("web_fetch_timeout", 10)
    max_body = config.get("web_fetch_max_body", 80000)

    loop = asyncio.get_running_loop()
    tasks = [
        loop.run_in_executor(None, _fetch_url_sync, url, fetch_timeout, max_body)
        for url in urls[:max_urls]
    ]
    results = await asyncio.gather(*tasks)

    fetched = {url: content for url, content in results if content is not None}
    for url, content in fetched.items():
        logger.info(f"Pre-fetched {url} -> {len(content)} chars")

    _inject_fetched_context(messages, fetched)


# ── 代理级 web_fetch 执行 ─────────────────────────────────────────

async def execute_web_fetch(args_str: str, http_client: httpx.AsyncClient) -> str:
    """在服务器端执行 web_fetch 工具调用。"""
    try:
        args = json.loads(args_str) if isinstance(args_str, str) else args_str
    except (json.JSONDecodeError, TypeError):
        return "Error: invalid JSON arguments"
    url = args.get("url", "")
    if not url:
        return "Error: no URL provided"
    method = args.get("method", "GET").upper()
    headers = args.get("headers") or {}
    req_body = args.get("body")

    try:
        if method == "GET":
            jina_url = f"https://r.jina.ai/{url}"
            resp = await http_client.get(
                jina_url,
                headers={"Accept": "text/plain", "X-Return-Format": "markdown"},
                timeout=WEB_FETCH_TIMEOUT,
            )
            if resp.status_code == 200:
                text = resp.text
                if len(text) > WEB_FETCH_MAX_BODY:
                    text = text[:WEB_FETCH_MAX_BODY] + f"\n...[truncated, {len(text) - WEB_FETCH_MAX_BODY} chars]"
                return text
            return await _raw_fetch(http_client, url, method, headers, req_body)
        return await _raw_fetch(http_client, url, method, headers, req_body)
    except httpx.TimeoutException:
        return f"Error: request to {url} timed out ({WEB_FETCH_TIMEOUT}s)"
    except httpx.ConnectError as e:
        return f"Fetch error: {e}"


async def _raw_fetch(client: httpx.AsyncClient, url: str, method: str, headers: dict, req_body: str | None) -> str:
    """直接 HTTP 抓取回退。"""
    if "User-Agent" not in headers:
        headers["User-Agent"] = "Mozilla/5.0 (compatible; ChatProxy/1.0)"
    try:
        fetch_args = {"method": method, "url": url, "headers": headers, "timeout": WEB_FETCH_TIMEOUT, "follow_redirects": True}
        if req_body and method in ("POST", "PUT", "PATCH"):
            fetch_args["content"] = req_body
        resp = await client.request(**fetch_args)
        ct = resp.headers.get("content-type", "")
        status_line = f"HTTP {resp.status_code} {resp.reason_phrase}"
        if method in ("HEAD", "OPTIONS"):
            hdr_lines = [f"{k}: {v}" for k, v in resp.headers.items()]
            return f"{status_line}\n{chr(10).join(hdr_lines)}"
        if any(t in ct for t in ("image", "audio", "video", "octet-stream")):
            return f"{status_line}\nContent-Type: {ct}\n(binary content, not shown)"
        text = resp.text
        if len(text) > WEB_FETCH_MAX_BODY:
            text = text[:WEB_FETCH_MAX_BODY] + f"\n...[truncated, {len(text) - WEB_FETCH_MAX_BODY} chars]"
        return f"{status_line}\n\n{text}"
    except (httpx.TimeoutException, httpx.ConnectError) as e:
        return f"Fetch error: {e}"
