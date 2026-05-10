"""网页抓取：URL 检测、预取和代理级抓取。"""

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

def prefetch_urls_into_messages(messages: list) -> None:
    """抓取用户消息中的 URL 并将内容追加为上下文。

    这样模型可以直接获得网页内容，无需发起 web_fetch 工具调用。
    """
    all_urls: list[str] = []
    for msg in messages:
        if msg.get("role") in ("user", "system"):
            content = msg.get("content", "")
            if isinstance(content, str):
                all_urls.extend(extract_urls_from_text(content))

    if not all_urls:
        return

    # 去重，保持顺序，跳过 localhost/内部 URL
    seen = set()
    urls = []
    for u in all_urls:
        if u not in seen and not ('127.0.0.1' in u or 'localhost' in u or '0.0.0.0' in u or '::1' in u):
            seen.add(u)
            urls.append(u)

    max_urls = config.get("web_fetch_max_urls", 5)
    fetch_timeout = config.get("web_fetch_timeout", 10)
    max_body = config.get("web_fetch_max_body", 80000)

    fetched: dict[str, str] = {}
    for url in urls[:max_urls]:
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
                fetched[url] = text
                logger.info(f"Pre-fetched {url} -> {len(text)} chars")
        except (OSError, ValueError, TimeoutError) as e:
            logger.warning(f"Pre-fetch failed for {url}: {e}")

    if fetched:
        context = "\n\n---\n\n".join(
            f"[Web content from {url}]\n{content}"
            for url, content in fetched.items()
        )
        context = f"\n\n[Pre-fetched web content — use this directly, no need to call web_fetch]\n\n{context}"
        for msg in reversed(messages):
            if msg.get("role") == "user" and isinstance(msg.get("content"), str):
                msg["content"] = msg["content"] + context
                break
        else:
            messages.append({"role": "user", "content": context})


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
