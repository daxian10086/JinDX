"""
Chat Completions + Responses API proxy for DeepSeek V4 (and compatible models).
- /v1/chat/completions -> passthrough
- /v1/responses -> convert to Chat Completions -> DeepSeek -> convert back
- Supports HTTP (SSE) and WebSocket streaming
- Supports HTTP CONNECT tunnel (for codex CLI https_proxy) with TLS termination
- Reasoning cache: stores previous thinking, injects into next request
"""

import json
import os
import ssl
import time
import uuid
import logging
import hashlib
import asyncio
from collections import OrderedDict
from threading import Lock
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx
import redis
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Chat-Responses-WebSocket Proxy")


# ── Forward-proxy path middleware ─────────────────────────────────────────────
# cc-switch treats this server as an HTTP forward proxy: it URL-encodes the
# full destination URL as the request path (e.g.
#   POST http%3A//127.0.0.1%3A8080/v1/chat/completions)
# This middleware detects those paths, decodes them, and rewrites the ASGI scope
# so FastAPI routes them to the normal /v1/... handlers.
@app.middleware("http")
async def forward_proxy_middleware(request: Request, call_next):
    raw_path = request.scope.get("path", "")
    if raw_path.startswith("http://") or raw_path.startswith("https://"):
        parsed = urlparse(raw_path)
        new_path = parsed.path or "/"
        if parsed.query:
            new_path += "?" + parsed.query
        request.scope["path"] = new_path
        request.scope["raw_path"] = new_path.encode()
        request.scope["query_string"] = (parsed.query or "").encode()
    return await call_next(request)


DEEPSEEK_BASE = os.environ.get("DEEPSEEK_BASE", "https://api.deepseek.com")
DEEPSEEK_KEY = os.environ.get("DEEPSEEK_KEY", "sk-your-deepseek-api-key")
PROXY_PORT = int(os.environ.get("PROXY_PORT", "8080"))
CONNECT_PORT = int(os.environ.get("CONNECT_PORT", "8443"))
DEFAULT_MODEL = os.environ.get("DEFAULT_MODEL", "deepseek-v4-pro")
TLS_PORT = int(os.environ.get("TLS_PORT", "8444"))
ADMIN_PORT = int(os.environ.get("ADMIN_PORT", "8090"))
REASONING_CACHE_MAX = int(os.environ.get("REASONING_CACHE_MAX", "10"))
REASONING_CACHE_TTL = int(os.environ.get("REASONING_CACHE_TTL", "600"))  # 10 min
DEFAULT_REASONING_EFFORT = os.environ.get("DEFAULT_REASONING_EFFORT", None)  # None = let DeepSeek decide
MAX_POSITION_EMBEDDINGS = int(os.environ.get("MAX_POSITION_EMBEDDINGS", "1000000"))

# Injected into the system prompt when tools are present to force tool-calling behavior.
# DeepSeek V4 tends to chat instead of using tools; this counteracts that tendency.
TOOL_USE_ENFORCEMENT = os.environ.get(
    "TOOL_USE_ENFORCEMENT",
    "You MUST use the provided tools to accomplish the user's task. "
    "Never respond with just text explaining what you would do — actually call the tools. "
    "If tools are available, use them to take real actions: run commands, read/write files, search the web. "
    "Do NOT ask the user for confirmation before using tools. Just do it.",
)

# ── Stats tracking ──────────────────────────────────────────────────────────
# Lightweight in-memory counters (no external deps).  Updated from the request
# handlers; exposed via the admin API for the dashboard.
_stats: dict = {
    "start_time": time.time(),
    "total_requests": 0,
    "active_streams": 0,
    "errors_by_code": {},
    "cache_hits": 0,
    "cache_misses": 0,
    "upstream_errors": {},
}
_stats_lock = Lock()

# Ring buffer for recent error logs (shown in admin dashboard).
_log_buffer: list[dict] = []  # each entry: {"ts": float, "msg": str}
_MAX_LOG_BUFFER = 200


def _record_request():
    with _stats_lock:
        _stats["total_requests"] += 1


def _record_error(code: int):
    with _stats_lock:
        key = str(code)
        _stats["errors_by_code"][key] = _stats["errors_by_code"].get(key, 0) + 1


def _record_upstream_error(msg: str):
    with _stats_lock:
        short = msg[:120]
        _stats["upstream_errors"][short] = _stats["upstream_errors"].get(short, 0) + 1


def _record_cache(hit: bool):
    with _stats_lock:
        if hit:
            _stats["cache_hits"] += 1
        else:
            _stats["cache_misses"] += 1


def _log_error(msg: str):
    """Append to in-memory ring buffer for dashboard log viewer."""
    entry = {"ts": time.time(), "msg": msg[:500]}
    _log_buffer.append(entry)
    if len(_log_buffer) > _MAX_LOG_BUFFER:
        del _log_buffer[:len(_log_buffer) - _MAX_LOG_BUFFER]

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

CERT_DIR = Path(__file__).parent / "certs"
CERT_FILE = CERT_DIR / "tls.crt"
KEY_FILE = CERT_DIR / "tls.key"

# Model names we map to DeepSeek V4 Pro
DEEPSEEK_MODELS = {"deepseek-v4-pro", "deepseek-v4-flash", "deepseek-chat", "deepseek-reasoner"}

# Redis config
REDIS_HOST = os.environ.get("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
REDIS_DB = int(os.environ.get("REDIS_DB", "0"))

try:
    _redis = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, decode_responses=True)
    _redis.ping()
    _redis_available = True
    logger.info(f"Redis connected at {REDIS_HOST}:{REDIS_PORT}")
except Exception as e:
    _redis = None
    _redis_available = False
    logger.warning(f"Redis unavailable ({e}), falling back to in-memory cache")

# In-memory fallback cache: session_id -> list of {"text": ..., "ts": ...}
_reasoning_cache: dict[str, list[dict]] = OrderedDict()
_cache_lock = Lock()

REDIS_KEY_PREFIX = "reasoning:"

# ── Runtime config ────────────────────────────────────────────────────────────

CONFIG_FILE = Path(os.environ.get("PROXY_CONFIG_FILE", "/home/wdmms123/.config/proxy-config.json"))
_runtime_config: dict = {}


def _load_config() -> dict:
    """Load persisted config, falling back to defaults."""
    defaults = {
        "deepseek_key": DEEPSEEK_KEY,
        "deepseek_base": DEEPSEEK_BASE,
        "default_model": DEFAULT_MODEL,
        "model_mapping": {"gpt-5.5": "deepseek-v4-pro", "gpt-5": "deepseek-v4-pro"},
        "reasoning_effort": None,
        "max_position_embeddings": MAX_POSITION_EMBEDDINGS,
        "max_output_tokens": 16384,
        "temperature": None,
        "top_p": None,
        "tool_use_enforcement": True,
        "tool_use_prompt": TOOL_USE_ENFORCEMENT,
        "web_fetch_max_urls": 5,
        "web_fetch_timeout": 10,
        "web_fetch_max_body": 80000,
        "enable_reasoning_cache": True,
        "reasoning_cache_ttl": 600,
    }
    try:
        if CONFIG_FILE.exists():
            saved = json.loads(CONFIG_FILE.read_text())
            defaults.update(saved)
    except Exception as e:
        logger.warning(f"Failed to load config: {e}")
    return defaults


def _save_config(cfg: dict) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))


_runtime_config = _load_config()


def get_config(key: str, default=None):
    return _runtime_config.get(key, default)


def _get_deepseek_key() -> str:
    return get_config("deepseek_key", DEEPSEEK_KEY)


def _get_deepseek_base() -> str:
    return get_config("deepseek_base", DEEPSEEK_BASE)


def _get_auth_headers() -> dict:
    return {
        "Authorization": f"Bearer {_get_deepseek_key()}",
        "Content-Type": "application/json",
    }


def _get_upstream() -> str:
    return f"{_get_deepseek_base()}/v1/chat/completions"


# ── Shared HTTP client pool with connection reuse ────────────────────────────
_http_client: httpx.AsyncClient | None = None


async def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(300.0),
            trust_env=False,
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
        )
    return _http_client


# ── Web Fetch helpers ──────────────────────────────────────────────────────────

def _has_urls_in_messages(messages: list) -> bool:
    """Check if any message content contains HTTP/HTTPS URLs."""
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


def _ensure_web_fetch_tool(tools: list) -> list:
    """Add web_fetch tool if not already present."""
    result = list(tools)
    for t in result:
        if t.get("type") == "function" and t.get("function", {}).get("name") == "web_fetch":
            return result
        if t.get("type") == "web_fetch" or t.get("name") == "web_fetch":
            return result
    result.append(WEB_FETCH_TOOL)
    return result


def _extract_urls_from_text(text: str) -> list[str]:
    """Extract HTTP/HTTPS URLs from a text string."""
    import re
    return re.findall(r'https?://[^\s<>"{}|\\^`\[\]]+', text)


def _prefetch_urls_into_messages(messages: list) -> None:
    """Fetch URLs found in user messages and append the content as context.

    This avoids the need for proxy-level tool interception — the model
    gets the web content directly in the conversation and can answer
    without making web_fetch tool calls.
    """
    import re
    all_urls: list[str] = []
    for msg in messages:
        if msg.get("role") in ("user", "system"):
            content = msg.get("content", "")
            if isinstance(content, str):
                all_urls.extend(_extract_urls_from_text(content))

    if not all_urls:
        return

    # Deduplicate, keep order
    seen = set()
    urls = []
    for u in all_urls:
        if u not in seen:
            seen.add(u)
            urls.append(u)

    max_urls = get_config("web_fetch_max_urls", 5)
    fetch_timeout = get_config("web_fetch_timeout", 10)
    max_body = get_config("web_fetch_max_body", 80000)

    fetched: dict[str, str] = {}
    for url in urls[:max_urls]:
        try:
            import urllib.request
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; ChatProxy/1.0)"})
            with urllib.request.urlopen(req, timeout=fetch_timeout) as resp:
                text = resp.read().decode("utf-8", errors="replace")
                ct = resp.headers.get("Content-Type", "")
                if "html" in ct:
                    # Simple HTML cleanup
                    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
                    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
                    text = re.sub(r'<[^>]+>', ' ', text)
                    text = re.sub(r'\s+', ' ', text).strip()
                if len(text) > max_body:
                    text = text[:max_body] + f"\n...[truncated, {len(text) - max_body} chars]"
                fetched[url] = text
                logger.info(f"Pre-fetched {url} -> {len(text)} chars")
        except Exception as e:
            logger.warning(f"Pre-fetch failed for {url}: {e}")

    if fetched:
        context = "\n\n---\n\n".join(
            f"[Web content from {url}]\n{content}"
            for url, content in fetched.items()
        )
        context = f"\n\n[Pre-fetched web content — use this directly, no need to call web_fetch]\n\n{context}"
        # Append to last user message
        for msg in reversed(messages):
            if msg.get("role") == "user" and isinstance(msg.get("content"), str):
                msg["content"] = msg["content"] + context
                break
        else:
            messages.append({"role": "user", "content": context})


def _ensure_web_fetch_hint(messages: list) -> list:
    """Add web_fetch hint message if not already present."""
    for msg in messages:
        if msg.get("role") == "user" and msg.get("content") == WEB_FETCH_HINT:
            return messages
    return [*messages, {"role": "user", "content": WEB_FETCH_HINT}]


async def _execute_web_fetch(args_str: str) -> str:
    """Execute a web_fetch tool call server-side."""
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

    client = await _get_http_client()
    try:
        if method == "GET":
            # Use Jina Reader for clean markdown
            jina_url = f"https://r.jina.ai/{url}"
            resp = await client.get(
                jina_url,
                headers={"Accept": "text/plain", "X-Return-Format": "markdown"},
                timeout=WEB_FETCH_TIMEOUT,
            )
            if resp.status_code == 200:
                text = resp.text
                if len(text) > WEB_FETCH_MAX_BODY:
                    text = text[:WEB_FETCH_MAX_BODY] + f"\n...[truncated, {len(text) - WEB_FETCH_MAX_BODY} chars]"
                return text
            # Fall back to direct fetch if Jina fails
            return await _raw_fetch(client, url, method, headers, req_body)
        return await _raw_fetch(client, url, method, headers, req_body)
    except httpx.TimeoutException:
        return f"Error: request to {url} timed out ({WEB_FETCH_TIMEOUT}s)"
    except Exception as e:
        return f"Fetch error: {e}"


async def _raw_fetch(client: httpx.AsyncClient, url: str, method: str, headers: dict, req_body: str | None) -> str:
    """Direct HTTP fetch fallback."""
    if "User-Agent" not in headers:
        headers["User-Agent"] = "Mozilla/5.0 (compatible; ChatProxy/1.0)"
    try:
        fetch_args = {"method": method, "url": url, "headers": headers, "timeout": WEB_FETCH_TIMEOUT, "follow_redirects": True}
        if req_body and method in ("POST", "PUT", "PATCH"):
            fetch_args["content"] = req_body
        resp = await _get_http_client().request(**fetch_args)
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
    except Exception as e:
        return f"Fetch error: {e}"




def _get_session_id(data: dict) -> str:
    """Extract a stable session ID from the request.

    Priority: Codex prompt_cache_key > explicit IDs > first-user-message hash.
    """
    # Codex sends a unique prompt_cache_key per conversation — the most reliable identifier
    sid = data.get("prompt_cache_key")
    if sid:
        return str(sid)

    sid = data.get("conversation_id") or data.get("session_id")
    if sid:
        return str(sid)
    meta = data.get("metadata") or {}
    sid = meta.get("session_id") or meta.get("conversation_id") or meta.get("thread_id")
    if sid:
        return str(sid)

    inp = data.get("input", "")
    instructions = data.get("instructions", "") or ""

    first_user_msg = ""
    if isinstance(inp, list):
        for item in inp:
            if isinstance(item, dict):
                role = item.get("role", "")
                itype = item.get("type", "")
                if role == "user" or (itype == "message" and item.get("role") == "user"):
                    content = item.get("content", "")
                    if isinstance(content, list):
                        content = json.dumps(content, sort_keys=True)
                    first_user_msg = str(content)
                    break
    elif isinstance(inp, str):
        first_user_msg = inp

    inst_hash = hashlib.md5(instructions.encode()).hexdigest()[:8]
    seed = f"{inst_hash}||{first_user_msg}"[:1000]
    return hashlib.md5(seed.encode()).hexdigest()[:16]


def _get_cached_reasoning(session_id: str) -> list[str]:
    """Get cached reasoning for a session, Redis-first with memory fallback."""
    if not get_config("enable_reasoning_cache", True):
        return []
    cache_ttl = get_config("reasoning_cache_ttl", 600)
    if _redis_available:
        try:
            key = f"{REDIS_KEY_PREFIX}{session_id}"
            raw = _redis.get(key)
            if raw:
                entries = json.loads(raw)
                now = time.time()
                valid = [e for e in entries if now - e["ts"] < cache_ttl]
                if valid:
                    _redis.set(key, json.dumps(valid, ensure_ascii=False), ex=cache_ttl)
                    _record_cache(True)
                    return [e["text"] for e in valid]
                else:
                    _redis.delete(key)
                    _record_cache(False)
                    return []
            else:
                _record_cache(False)
        except Exception as e:
            logger.warning(f"Redis read error, falling back to memory: {e}")

    with _cache_lock:
        entries = _reasoning_cache.get(session_id, [])
        now = time.time()
        valid = [e for e in entries if now - e["ts"] < cache_ttl]
        if valid:
            _reasoning_cache[session_id] = valid
            _record_cache(True)
            return [e["text"] for e in valid]
        else:
            _reasoning_cache.pop(session_id, None)
            _record_cache(False)
            return []


def _cache_reasoning(session_id: str, reasoning_text: str):
    """Cache reasoning text for a session, Redis-first with memory fallback."""
    if not reasoning_text or not reasoning_text.strip():
        return
    if not get_config("enable_reasoning_cache", True):
        return

    cache_ttl = get_config("reasoning_cache_ttl", 600)
    entry = {"text": reasoning_text, "ts": time.time()}

    if _redis_available:
        try:
            key = f"{REDIS_KEY_PREFIX}{session_id}"
            raw = _redis.get(key)
            entries = json.loads(raw) if raw else []
            entries.append(entry)
            while len(entries) > REASONING_CACHE_MAX:
                entries.pop(0)
            _redis.set(key, json.dumps(entries, ensure_ascii=False), ex=cache_ttl)
            logger.info(f"Redis cached reasoning for session {session_id} ({len(entries)} entries)")
            return
        except Exception as e:
            logger.warning(f"Redis write error, falling back to memory: {e}")

    with _cache_lock:
        if session_id not in _reasoning_cache:
            _reasoning_cache[session_id] = []
        entries = _reasoning_cache[session_id]
        entries.append(entry)
        while len(entries) > REASONING_CACHE_MAX:
            entries.pop(0)
        while len(_reasoning_cache) > 1000:
            _reasoning_cache.popitem(last=False)
    logger.info(f"Memory cached reasoning for session {session_id} ({len(entries)} entries)")


def map_model(name: str) -> str:
    if not name:
        return get_config("default_model", DEFAULT_MODEL)
    low = name.lower()
    if "deepseek" in low:
        return name
    mapping = get_config("model_mapping", {})
    if name in mapping:
        return mapping[name]
    if low in mapping:
        return mapping[low]
    return get_config("default_model", DEFAULT_MODEL)


def _make_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


# ── Chat Completions passthrough ────────────────────────────────────────────

@app.post("/v1/chat/completions")
@app.post("/chat/completions")
async def chat_completions(request: Request):
    _record_request()
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    body["model"] = map_model(body.get("model", ""))

    stream = body.get("stream", False)

    if stream:
        return StreamingResponse(
            _stream_chat(body),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    client = await _get_http_client()
    resp = await client.post(_get_upstream(), json=body, headers=_get_auth_headers())
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return JSONResponse(content=resp.json())


async def _stream_chat(body: dict):
    body["model"] = map_model(body.get("model", ""))
    client = await _get_http_client()
    async with client.stream("POST", _get_upstream(), json=body, headers=_get_auth_headers()) as resp:
        if resp.status_code != 200:
            body_text = await resp.aread()
            body_str = body_text.decode()[:2000]
            logger.error(f"DeepSeek chat/stream {resp.status_code}: {body_str}")
            raise HTTPException(status_code=resp.status_code, detail=body_str)
        async for line in resp.aiter_lines():
            yield line + "\n"


# ── Request conversion: OpenAI Responses -> Chat Completions ────────────────

def _normalize_role(role: str) -> str:
    """Map OpenAI-specific roles to ones DeepSeek accepts."""
    if role == "developer":
        return "system"
    return role


def _extract_message_items(data: dict) -> list:
    results = []

    instructions = data.get("instructions")
    if instructions:
        results.append({"role": "system", "content": instructions})

    inp = data.get("input", "")
    if isinstance(inp, str):
        results.append({"role": "user", "content": inp})
    elif isinstance(inp, list):
        pending_tool_calls: list[dict] = []

        def _flush_pending():
            if pending_tool_calls:
                results.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": pending_tool_calls.copy(),
                })
                pending_tool_calls.clear()

        for item in inp:
            item_type = item.get("type", "")
            if item_type == "message":
                role = _normalize_role(item.get("role", "user"))
                content = item.get("content", "")
                if isinstance(content, list):
                    text_parts = []
                    msg_tool_calls = []
                    for part in content:
                        if isinstance(part, dict):
                            part_type = part.get("type", "")
                            if part_type in ("input_text", "text", "output_text"):
                                text_parts.append(part.get("text", ""))
                            elif part_type == "reasoning_text":
                                text_parts.append(part.get("text", ""))
                            elif part_type == "input_image":
                                text_parts.append("[image]")
                            elif part_type == "input_file":
                                text_parts.append(f"[file: {part.get('filename', '')}]")
                            elif part_type == "function_call":
                                msg_tool_calls.append({
                                    "id": part.get("id", part.get("call_id", "")),
                                    "type": "function",
                                    "function": {
                                        "name": part.get("name", ""),
                                        "arguments": part.get("arguments", ""),
                                    },
                                })
                    content = "\n".join(text_parts) if text_parts else str(content)
                    if msg_tool_calls:
                        _flush_pending()
                        results.append({
                            "role": role,
                            "content": content or None,
                            "tool_calls": msg_tool_calls,
                        })
                    else:
                        _flush_pending()
                        results.append({"role": role, "content": content})
                else:
                    _flush_pending()
                    results.append({"role": role, "content": content})
            elif item_type == "function_call":
                call_id = item.get("call_id", item.get("id", ""))
                pending_tool_calls.append({
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": item.get("name", ""),
                        "arguments": item.get("arguments", ""),
                    },
                })
            elif item_type == "function_call_output":
                _flush_pending()
                call_id = item.get("call_id", "")
                output = item.get("output", "")
                if isinstance(output, dict):
                    output = json.dumps(output, ensure_ascii=False)
                elif not isinstance(output, str):
                    output = str(output)
                results.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": output,
                })
            elif isinstance(item, dict):
                _flush_pending()
                if item.get("role"):
                    role = _normalize_role(item.get("role", "user"))
                    content = item.get("content", "")
                    if isinstance(content, list):
                        text_parts = [p.get("text", "") for p in content if p.get("type") in ("input_text", "text", "output_text")]
                        content = "\n".join(text_parts) or content
                    if role == "assistant" and item.get("tool_calls"):
                        entry = {"role": role, "content": content or None, "tool_calls": item["tool_calls"]}
                    elif role == "tool":
                        entry = {"role": role, "content": content or "", "tool_call_id": item.get("tool_call_id", "")}
                    else:
                        entry = {"role": role, "content": content or ""}
                    results.append(entry)
                elif item.get("type") in ("input_text", "text"):
                    results.append({"role": "user", "content": item.get("text", "")})

        _flush_pending()

    return results


def _fix_tool_message_ordering(messages: list) -> list:
    """Ensure each assistant tool_calls message is followed by its matching tool messages.

    DeepSeek requires: assistant(tool_calls=[A,B]) -> tool(A) -> tool(B).
    Codex may send items in slightly different order, so we reorder here.
    """
    if not messages:
        return messages

    fixed = []
    skip = set()

    for i, msg in enumerate(messages):
        if i in skip:
            continue
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            fixed.append(msg)
            call_ids = {tc["id"] for tc in msg["tool_calls"]}
            for j in range(i + 1, len(messages)):
                mj = messages[j]
                if mj.get("role") == "tool" and mj.get("tool_call_id") in call_ids:
                    fixed.append(mj)
                    skip.add(j)
        elif msg.get("role") == "tool":
            # Orphaned tool message — attach to the last assistant-with-tool_calls
            last_tc = None
            for m in reversed(fixed):
                if m.get("role") == "assistant" and m.get("tool_calls"):
                    last_tc = m
                    break
            if last_tc:
                insert_at = fixed.index(last_tc) + 1
                while insert_at < len(fixed) and fixed[insert_at].get("role") == "tool":
                    insert_at += 1
                fixed.insert(insert_at, msg)
        else:
            fixed.append(msg)

    return fixed


def _ensure_assistant_reasoning(messages: list, cached_reasoning: list[str]) -> None:
    """Ensure every assistant message has a reasoning_content field.

    DeepSeek thinking mode requires reasoning_content on ALL assistant
    messages.  Assistant messages created from Codex function_call items
    (by _extract_message_items) initially lack this field.  We fill it
    from the cache when possible, falling back to an empty string.
    """
    cache_idx = 0
    cache_used = set()  # track which cache entries we assign per turn

    # First pass: copy reasoning_content from neighboring assistant messages
    # within the same turn (assistant messages between user/system messages).
    turn_start = 0
    for i, msg in enumerate(messages):
        if msg.get("role") == "assistant":
            if msg.get("reasoning_content"):
                continue
            # Look for reasoning in the previous assistant message of this turn
            for j in range(i - 1, turn_start - 1, -1):
                mj = messages[j]
                if mj.get("role") == "assistant" and mj.get("reasoning_content"):
                    msg["reasoning_content"] = mj["reasoning_content"]
                    break
            # If still missing, try to use an unused cache entry
            if not msg.get("reasoning_content"):
                while cache_idx < len(cached_reasoning):
                    if cache_idx not in cache_used:
                        msg["reasoning_content"] = cached_reasoning[cache_idx]
                        cache_used.add(cache_idx)
                        break
                    cache_idx += 1
            # Fallback: empty string
            if not msg.get("reasoning_content"):
                msg["reasoning_content"] = ""
        elif msg.get("role") not in ("tool",):
            turn_start = i + 1


def responses_to_chat(data: dict) -> dict:
    messages = _extract_message_items(data)

    if not messages:
        prompt = data.get("prompt", "")
        if prompt:
            messages = [{"role": "user", "content": prompt}]

    if not messages:
        messages = [{"role": "user", "content": ""}]

    messages = _fix_tool_message_ordering(messages)

    session_id = _get_session_id(data)
    cached_reasoning = _get_cached_reasoning(session_id)


    if cached_reasoning:
        # DeepSeek requires reasoning_content on ALL assistant messages from thinking
        # mode. Codex splits each DeepSeek response into multiple items (text-only
        # message + individual function_call items), so one cached reasoning block
        # must be attached to ALL assistant messages of the same turn.
        reasoning_idx = 0
        pending_text_assistant = None
        saw_assistant = False
        for msg in messages:
            if msg.get("role") == "assistant":
                saw_assistant = True
                if reasoning_idx < len(cached_reasoning):
                    rc = cached_reasoning[reasoning_idx]
                    msg["reasoning_content"] = rc
                    if pending_text_assistant is not None:
                        pending_text_assistant["reasoning_content"] = rc
                if msg.get("tool_calls"):
                    pending_text_assistant = None
                else:
                    pending_text_assistant = msg
            elif msg.get("role") not in ("tool",):
                if saw_assistant:
                    reasoning_idx += 1
                    saw_assistant = False
                pending_text_assistant = None
        if reasoning_idx > 0 or any(m.get("reasoning_content") for m in messages if m.get("role") == "assistant"):
            logger.info(f"Attached reasoning_content to assistant messages (used {reasoning_idx + 1} cached entries)")
        else:
            # Fallback: inject as system message context if no assistant+TC messages found
            reasoning_block = "\n\n---\n\n".join(
                f"[Previous thinking #{i+1}]\n{r}"
                for i, r in enumerate(cached_reasoning)
            )
            context_msg = (
                "The following is your internal reasoning from previous turns in this conversation. "
                "Use this to maintain continuity — do NOT repeat this reasoning, just let it inform your next response:\n\n"
                f"{reasoning_block}"
            )
            if messages and messages[0].get("role") == "system":
                messages[0]["content"] = messages[0]["content"] + "\n\n" + context_msg
            else:
                messages.insert(0, {"role": "system", "content": context_msg})
            logger.info(f"Injected {len(cached_reasoning)} cached reasoning entries as system context for session {session_id}")

    # DeepSeek thinking mode requires reasoning_content on EVERY assistant message.
    # Ensure all assistant messages have the field, using cached reasoning for
    # messages created from function_call items when possible.
    _ensure_assistant_reasoning(messages, cached_reasoning)

    # Inject tool-use enforcement into system message when tools are present
    tools = data.get("tools")
    if tools and get_config("tool_use_enforcement", True):
        prompt = get_config("tool_use_prompt", TOOL_USE_ENFORCEMENT)
        if messages and messages[0].get("role") == "system":
            if prompt not in messages[0]["content"]:
                messages[0]["content"] = prompt + "\n\n" + messages[0]["content"]
        else:
            messages.insert(0, {"role": "system", "content": prompt})
        logger.info("Injected tool-use enforcement prompt")

    tools = data.get("tools") or []
    if _has_urls_in_messages(messages):
        _prefetch_urls_into_messages(messages)
        logger.info("Pre-fetched URLs into message context")

    chat = {
        "model": map_model(data.get("model", DEFAULT_MODEL)),
        "messages": messages,
        "stream": data.get("stream", False),
    }

    if data.get("max_output_tokens"):
        chat["max_tokens"] = data["max_output_tokens"]
    elif (cfg_tokens := get_config("max_output_tokens")):
        chat["max_tokens"] = cfg_tokens
    if (cfg_ctx := get_config("max_position_embeddings")):
        chat["max_position_embeddings"] = cfg_ctx
    if data.get("temperature") is not None:
        chat["temperature"] = data["temperature"]
    elif (cfg_temp := get_config("temperature")) is not None:
        chat["temperature"] = cfg_temp
    if data.get("top_p") is not None:
        chat["top_p"] = data["top_p"]
    elif (cfg_top_p := get_config("top_p")) is not None:
        chat["top_p"] = cfg_top_p
    if data.get("stop"):
        chat["stop"] = data["stop"]

    for key in ("max_tokens", "frequency_penalty", "presence_penalty", "max_completion_tokens"):
        val = data.get(key)
        if val is not None and key not in chat:
            if key == "max_completion_tokens":
                chat["max_tokens"] = val
            elif key == "max_tokens":
                chat["max_tokens"] = val
            elif key == "frequency_penalty":
                chat["frequency_penalty"] = val
            elif key == "presence_penalty":
                chat["presence_penalty"] = val

    reasoning = data.get("reasoning")
    if reasoning:
        if isinstance(reasoning, dict):
            effort = reasoning.get("effort")
            if effort:
                chat["reasoning_effort"] = effort
        elif isinstance(reasoning, str):
            chat["reasoning_effort"] = reasoning
    if "reasoning_effort" not in chat and DEFAULT_REASONING_EFFORT:
        chat["reasoning_effort"] = DEFAULT_REASONING_EFFORT
    if "reasoning_effort" not in chat and (cfg_re := get_config("reasoning_effort")):
        chat["reasoning_effort"] = cfg_re

    if tools:
        tool_names = [t.get("function", t).get("name", t.get("type", "?")) for t in tools]
        logger.info(f"Tool names ({len(tools)}): {tool_names}")
        converted = _convert_tools(tools)
        logger.info(f"Converted tool names ({len(converted)}): {[c.get('function',{}).get('name','?') for c in converted]}")
        chat["tools"] = converted
        chat["tool_choice"] = data.get("tool_choice", "auto")

    return chat


def _convert_tools(tools: list) -> list:
    result = []
    for tool in tools:
        if "function" in tool:
            name = tool.get("function", {}).get("name", "")
            if not name:
                continue
            func = tool["function"]
            params = func.get("parameters")
            if not params or not isinstance(params, dict) or params.get("type") != "object":
                func["parameters"] = {"type": "object", "properties": {}}
            result.append(tool)
        else:
            name = tool.get("name", "") or tool.get("type", "")
            if not name:
                continue
            params = tool.get("parameters")
            if not params or not isinstance(params, dict) or params.get("type") != "object":
                params = {"type": "object", "properties": {}}
            result.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool.get("description", ""),
                    "parameters": params,
                },
            })
    return result


# ── Response conversion: Chat Completions -> OpenAI Responses ───────────────

def chat_to_responses(chat_data: dict, model: str) -> dict:
    resp_id = _make_id("resp")
    choice = chat_data.get("choices", [{}])[0]
    message = choice.get("message", {})
    content = message.get("content") or ""
    reasoning = message.get("reasoning_content") or ""
    tool_calls = message.get("tool_calls") or []

    usage = chat_data.get("usage", {})
    msg_id = _make_id("msg")

    output_content = []

    if reasoning:
        output_content.append({
            "type": "reasoning_text",
            "text": reasoning,
            "summary": [],
        })

    if content:
        output_content.append({
            "type": "output_text",
            "text": content,
            "annotations": [],
        })
    elif not reasoning:
        output_content.append({
            "type": "output_text",
            "text": "",
            "annotations": [],
        })

    for tc in tool_calls:
        func = tc.get("function", {})
        output_content.append({
            "id": tc.get("id", _make_id("call")),
            "type": "function_call",
            "call_id": tc.get("id", ""),
            "name": func.get("name", ""),
            "arguments": func.get("arguments", ""),
        })

    output_item = {
        "id": msg_id,
        "type": "message",
        "status": "completed",
        "role": "assistant",
        "content": output_content,
    }

    return {
        "id": resp_id,
        "object": "response",
        "created_at": chat_data.get("created", int(time.time())),
        "status": "completed",
        "model": model,
        "output": [output_item],
        "output_text": content,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "input_token_details": {
                "cached_tokens": usage.get("prompt_tokens_details", {}).get("cached_tokens", 0),
            },
            "output_token_details": {
                "reasoning_tokens": usage.get("completion_tokens_details", {}).get("reasoning_tokens", 0),
            },
        },
        "incomplete_details": None,
        "instructions": None,
        "metadata": {},
    }


# ── FastAPI WebSocket endpoint (plain ws:// for direct use) ─────────────────
# MUST be registered BEFORE HTTP POST routes so Starlette handles WebSocket upgrades correctly

@app.websocket("/v1/responses")
@app.websocket("/responses")
@app.websocket("/backend-api/codex/responses")
async def responses_websocket(ws: WebSocket):
    await ws.accept()
    try:
        await _handle_ws_session(ws)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        try:
            await ws.close()
        except Exception:
            pass


# ── HTTP Responses endpoint (non-streaming) ─────────────────────────────────

@app.post("/v1/responses")
@app.post("/responses")
@app.post("/backend-api/codex/responses")
async def responses_http(request: Request):
    _record_request()
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    stream = body.get("stream", False)
    model = map_model(body.get("model", DEFAULT_MODEL))

    # Skip empty input — DeepSeek hangs on empty user messages
    inp = body.get("input", "")
    if not inp or (isinstance(inp, list) and len(inp) == 0):
        logger.info("HTTP skip empty-input request")
        return JSONResponse(content={
            "id": _make_id("resp"),
            "object": "response",
            "created_at": int(time.time()),
            "status": "completed",
            "model": model,
            "output": [],
            "output_text": "",
            "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        })

    if stream:
        return StreamingResponse(
            _stream_responses_sse(body),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    chat_request = responses_to_chat(body)
    try:
        client = await _get_http_client()
        resp = await client.post(_get_upstream(), json=chat_request, headers=_get_auth_headers())
        if resp.status_code != 200:
            _record_error(resp.status_code)
            _record_upstream_error(resp.text[:2000])
            _log_error(f"DeepSeek non-stream {resp.status_code}: {resp.text[:200]}")
            logger.error(f"DeepSeek non-stream {resp.status_code}: {resp.text[:2000]}")
            logger.error(f"Chat request: {json.dumps(chat_request, ensure_ascii=False)[:3000]}")
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        chat_data = resp.json()
        reasoning = chat_data.get("choices", [{}])[0].get("message", {}).get("reasoning_content", "")
        if reasoning:
            _cache_reasoning(_get_session_id(body), reasoning)
        return JSONResponse(content=chat_to_responses(chat_data, model))
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Upstream timeout")
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ── HTTP SSE streaming ──────────────────────────────────────────────────────

async def _stream_responses_sse(body: dict):
    """Stream Responses API events in OpenAI-compatible SSE format."""
    with _stats_lock:
        _stats["active_streams"] += 1
    try:
        async for event in _stream_responses_sse_inner(body):
            yield event
    finally:
        with _stats_lock:
            _stats["active_streams"] -= 1


async def _stream_responses_sse_inner(body: dict):
    model = map_model(body.get("model", DEFAULT_MODEL))
    chat_request = responses_to_chat(body)
    chat_request["stream"] = True

    resp_id = _make_id("resp")
    msg_id = _make_id("msg")
    output_index = 0
    content_index = 0
    started = False
    sent_text_parts = False
    usage = {}
    tool_calls_by_index: dict[int, dict] = {}

    client = await _get_http_client()
    try:
        async with client.stream("POST", _get_upstream(), json=chat_request, headers=_get_auth_headers()) as upstream:
            if upstream.status_code != 200:
                body_text = await upstream.aread()
                body_str = body_text.decode()[:2000]
                _record_error(upstream.status_code)
                _record_upstream_error(body_str)
                _log_error(f"DeepSeek {upstream.status_code}: {body_str}")
                logger.error(f"DeepSeek stream {upstream.status_code}: {body_str}")
                logger.error(f"Chat request: {json.dumps(chat_request, ensure_ascii=False)[:3000]}")
                err = json.dumps({"type": "error", "error": {"message": body_str, "code": upstream.status_code}})
                yield f"data: {err}\n\n"
                return

            content_buf = ""
            reasoning_buf = ""

            async for line in upstream.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    break

                try:
                    delta = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                choices = delta.get("choices", [])
                if not choices:
                    continue
                d = choices[0].get("delta", {})
                content_delta = d.get("content", "") or ""
                reasoning_delta = d.get("reasoning_content", "") or ""

                if delta.get("usage"):
                    usage = delta["usage"]

                if not started:
                    started = True
                    yield f"event: response.created\ndata: {_sse_event('response.created', response={'id': resp_id, 'object': 'response', 'created_at': delta.get('created', int(time.time())), 'status': 'in_progress', 'model': model, 'output': []})}\n\n"
                    yield f"event: response.in_progress\ndata: {_sse_event('response.in_progress', response={'id': resp_id, 'object': 'response', 'status': 'in_progress', 'model': model})}\n\n"
                    yield f"event: response.output_item.added\ndata: {_sse_event('response.output_item.added', output_index=output_index, item={'id': msg_id, 'type': 'message', 'role': 'assistant', 'status': 'in_progress', 'content': []})}\n\n"

                if reasoning_delta:
                    if not reasoning_buf and not content_buf:
                        yield f"event: response.content_part.added\ndata: {_sse_event('response.content_part.added', item_id=msg_id, output_index=output_index, content_index=content_index, part={'type': 'reasoning_text', 'text': '', 'summary': []})}\n\n"
                        sent_text_parts = True
                    reasoning_buf += reasoning_delta
                    yield f"event: response.reasoning_text.delta\ndata: {_sse_event('response.reasoning_text.delta', item_id=msg_id, output_index=output_index, content_index=content_index, delta=reasoning_delta)}\n\n"

                if content_delta:
                    if not content_buf and not reasoning_buf and not sent_text_parts:
                        yield f"event: response.content_part.added\ndata: {_sse_event('response.content_part.added', item_id=msg_id, output_index=output_index, content_index=content_index, part={'type': 'output_text', 'text': ''})}\n\n"
                        sent_text_parts = True
                    elif reasoning_buf and not content_buf:
                        content_index += 1
                        yield f"event: response.content_part.added\ndata: {_sse_event('response.content_part.added', item_id=msg_id, output_index=output_index, content_index=content_index, part={'type': 'output_text', 'text': ''})}\n\n"
                    content_buf += content_delta
                    yield f"event: response.output_text.delta\ndata: {_sse_event('response.output_text.delta', item_id=msg_id, output_index=output_index, content_index=content_index, delta=content_delta)}\n\n"

                # Accumulate tool call deltas
                tc_deltas = d.get("tool_calls") or []
                for tc in tc_deltas:
                    idx = tc.get("index", 0)
                    if idx not in tool_calls_by_index:
                        tool_calls_by_index[idx] = {
                            "id": tc.get("id", ""),
                            "name": tc.get("function", {}).get("name", ""),
                            "arguments": "",
                        }
                    cur = tool_calls_by_index[idx]
                    if tc.get("id"):
                        cur["id"] = tc["id"]
                    func = tc.get("function", {})
                    if func.get("name"):
                        cur["name"] = func["name"]
                    if func.get("arguments"):
                        cur["arguments"] += func["arguments"]

            # ── Emit text message output items ─────────────────────────
            final_content = []
            display_text = content_buf or reasoning_buf
            if display_text:
                final_content.append({"type": "output_text", "text": display_text, "annotations": []})

            if sent_text_parts:
                yield f"event: response.content_part.done\ndata: {_sse_event('response.content_part.done', item_id=msg_id, output_index=output_index, content_index=content_index, part={'type': 'output_text' if content_buf else 'reasoning_text', 'text': display_text})}\n\n"

            yield f"event: response.output_item.done\ndata: {_sse_event('response.output_item.done', output_index=output_index, item={'id': msg_id, 'type': 'message', 'role': 'assistant', 'status': 'completed', 'content': final_content})}\n\n"

            # ── Emit function_call output items ────────────────────────
            all_output_items = [
                {"id": msg_id, "type": "message", "role": "assistant", "status": "completed", "content": final_content}
            ]

            for tc_idx in sorted(tool_calls_by_index.keys()):
                tc = tool_calls_by_index[tc_idx]
                tc_id = tc["id"] or _make_id("call")
                tc_name = tc["name"] or ""
                tc_args = tc["arguments"] or "{}"
                tc_out_idx = output_index + tc_idx + 1

                yield f"event: response.output_item.added\ndata: {_sse_event('response.output_item.added', output_index=tc_out_idx, item={'id': tc_id, 'type': 'function_call', 'name': tc_name, 'call_id': tc_id, 'status': 'in_progress', 'arguments': ''})}\n\n"
                yield f"event: response.function_call_arguments.delta\ndata: {_sse_event('response.function_call_arguments.delta', item_id=tc_id, output_index=tc_out_idx, delta=tc_args)}\n\n"
                yield f"event: response.function_call_arguments.done\ndata: {_sse_event('response.function_call_arguments.done', item_id=tc_id, output_index=tc_out_idx, arguments=tc_args)}\n\n"
                yield f"event: response.output_item.done\ndata: {_sse_event('response.output_item.done', output_index=tc_out_idx, item={'id': tc_id, 'type': 'function_call', 'name': tc_name, 'call_id': tc_id, 'status': 'completed', 'arguments': tc_args})}\n\n"

                all_output_items.append({
                    "id": tc_id,
                    "type": "function_call",
                    "call_id": tc_id,
                    "name": tc_name,
                    "arguments": tc_args,
                    "status": "completed",
                })

            yield f"event: response.completed\ndata: {_sse_event('response.completed', response={'id': resp_id, 'object': 'response', 'created_at': int(time.time()), 'status': 'completed', 'model': model, 'output': all_output_items, 'output_text': display_text, 'usage': {'input_tokens': usage.get('prompt_tokens', 0), 'output_tokens': usage.get('completion_tokens', 0), 'total_tokens': usage.get('total_tokens', 0)}})}\n\n"
            yield "data: [DONE]\n\n"

            if reasoning_buf:
                _cache_reasoning(_get_session_id(body), reasoning_buf)

    except Exception as e:
        _record_error(500)
        _log_error(f"SSE stream error: {e}")
        yield f"data: {json.dumps({'type': 'error', 'error': {'message': str(e)}})}\n\n"


# ── Helpers ─────────────────────────────────────────────────────────────────

def _sse_event(event_type: str, **kwargs) -> str:
    payload = {"type": event_type}
    payload.update(kwargs)
    return json.dumps(payload, ensure_ascii=False)


# ── Shared WebSocket session handler (used by both FastAPI WS and CONNECT) ───

class _TunnelWsAdapter:
    """Adapter to make a raw asyncio stream pair behave like a FastAPI WebSocket."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self._reader = reader
        self._writer = writer
        self._buf = b""

    async def accept(self):
        """Perform WebSocket server handshake over the raw stream."""
        # Read the HTTP upgrade request
        request_data = await asyncio.wait_for(
            self._reader.readuntil(b'\r\n\r\n'), timeout=10
        )
        request_text = request_data.decode()
        lines = request_text.split('\r\n')
        if not lines or not lines[0].startswith('GET '):
            raise Exception(f"Expected WebSocket upgrade, got: {lines[0] if lines else 'empty'}")

        # Extract Sec-WebSocket-Key
        ws_key = None
        for line in lines[1:]:
            if line.lower().startswith('sec-websocket-key:'):
                ws_key = line.split(':', 1)[1].strip()
                break
        if not ws_key:
            raise Exception("No Sec-WebSocket-Key in upgrade request")

        # Compute accept key
        import base64
        GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
        digest = hashlib.sha1((ws_key + GUID).encode()).digest()
        accept = base64.b64encode(digest).decode()

        # Send upgrade response
        response = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n"
            "\r\n"
        )
        self._writer.write(response.encode())
        await self._writer.drain()

    async def receive_text(self) -> str:
        while True:
            if len(self._buf) < 2:
                chunk = await self._reader.read(4096)
                if not chunk:
                    raise WebSocketDisconnect()
                self._buf += chunk
                continue

            b0 = self._buf[0]
            opcode = b0 & 0x0F
            if opcode == 0x8:
                raise WebSocketDisconnect()
            if opcode == 0x9:
                b1 = self._buf[1]
                length = b1 & 0x7F
                header_len = 2
                if length == 126:
                    header_len = 4
                elif length == 127:
                    header_len = 10
                mask_flag = (b1 & 0x80) != 0
                if mask_flag:
                    header_len += 4
                if len(self._buf) < header_len + length:
                    chunk = await self._reader.read(4096)
                    self._buf += chunk
                    continue
                pong = bytearray([0x8A, b1 & 0x7F])
                pong += self._buf[2:2 + length]
                self._writer.write(bytes(pong))
                await self._writer.drain()
                self._buf = self._buf[header_len + length:]
                continue

            if opcode not in (0x1, 0x2):
                raise Exception(f"Unexpected WebSocket opcode: {opcode}")

            b1 = self._buf[1]
            masked = (b1 & 0x80) != 0
            length = b1 & 0x7F

            pos = 2
            if length == 126:
                if len(self._buf) < 4:
                    chunk = await self._reader.read(4096)
                    self._buf += chunk
                    continue
                length = int.from_bytes(self._buf[2:4], 'big')
                pos = 4
            elif length == 127:
                if len(self._buf) < 10:
                    chunk = await self._reader.read(4096)
                    self._buf += chunk
                    continue
                length = int.from_bytes(self._buf[2:10], 'big')
                pos = 10

            mask_key = b""
            if masked:
                if len(self._buf) < pos + 4:
                    chunk = await self._reader.read(4096)
                    self._buf += chunk
                    continue
                mask_key = self._buf[pos:pos + 4]
                pos += 4

            total_needed = pos + length
            if len(self._buf) < total_needed:
                chunk = await self._reader.read(4096)
                if not chunk:
                    raise WebSocketDisconnect()
                self._buf += chunk
                continue

            payload = bytearray(self._buf[pos:pos + length])
            if masked:
                for i in range(length):
                    payload[i] ^= mask_key[i % 4]

            self._buf = self._buf[total_needed:]
            return bytes(payload).decode('utf-8')

    async def send_json(self, data: dict):
        payload = json.dumps(data, ensure_ascii=False).encode('utf-8')
        frame = self._build_ws_frame(payload, opcode=0x1)
        self._writer.write(frame)
        await self._writer.drain()

    @staticmethod
    def _build_ws_frame(payload: bytes, opcode: int = 0x1) -> bytes:
        length = len(payload)
        header = bytearray()
        header.append(0x80 | opcode)
        if length < 126:
            header.append(length)
        elif length < 65536:
            header.append(126)
            header.extend(length.to_bytes(2, 'big'))
        else:
            header.append(127)
            header.extend(length.to_bytes(8, 'big'))
        return bytes(header) + payload

    async def close(self):
        try:
            frame = self._build_ws_frame(b"", opcode=0x8)
            self._writer.write(frame)
            await self._writer.drain()
        except Exception:
            pass
        try:
            self._writer.close()
        except Exception:
            pass


def _handle_codex_rpc(method: str, params: dict) -> dict | None:
    """Handle Codex internal RPC calls sent over WebSocket.
    Returns a JSON-serializable response dict, or None to ignore."""
    # Rate limits — return unlimited
    if method == "account/rateLimits/read":
        return {
            "method": "account/rateLimits/updated",
            "params": {
                "rateLimits": [],
                "rateLimitsByLimitId": {},
            },
        }
    # Config requirements
    if method == "config/requirements/read":
        return {
            "method": "config/requirements/updated",
            "params": {"requirements": []},
        }
    # Model provider capabilities
    if method == "modelProvider/capabilities/read":
        return {
            "method": "modelProvider/capabilities/updated",
            "params": {
                "providers": [{
                    "id": "openai",
                    "capabilities": {
                        "supports_tools": True,
                        "supports_images": True,
                        "supports_streaming": True,
                        "supports_reasoning": True,
                        "max_context_tokens": 131072,
                        "max_output_tokens": 16384,
                    },
                }],
            },
        }
    # Experimental features — return empty
    if method == "experimentalFeatures/list":
        return {
            "method": "experimentalFeatures/updated",
            "params": {"features": []},
        }
    # Account read — return a full account object
    if method == "account/read":
        return {
            "method": "account/updated",
            "params": {
                "account": {
                    "id": "proxy-user",
                    "email": "proxy@localhost",
                    "plan_type": "plus",
                    "entitled": True,
                },
                "entitlements": {"codex": True, "codex_plus": True},
            },
        }
    # Model list — return available models
    if method == "model/list":
        model_name = get_config("default_model", DEFAULT_MODEL)
        return {
            "method": "model/updated",
            "params": {
                "models": [
                    {
                        "id": model_name,
                        "name": model_name,
                        "capabilities": {
                            "supports_tools": True,
                            "supports_images": True,
                            "supports_streaming": True,
                            "supports_reasoning": True,
                        },
                    }
                ]
            },
        }
    # Account login — not needed
    if method.startswith("account/login"):
        return {
            "method": "account/login/completed",
            "params": {"status": "authenticated", "account": {"id": "proxy-user"}},
        }
    # Account updated — ignore
    if method == "account/updated":
        return None
    # MCP server / resource / skills — return empty lists
    if method.startswith("mcpServer/") or method.startswith("skills/") or method.startswith("device/"):
        return {"method": method.replace("read", "updated").replace("list", "updated"), "params": {}}
    # Catch-all for unknown RPC methods — return empty update
    if "/read" in method or "/list" in method:
        return {"method": method.replace("/read", "/updated").replace("/list", "/updated"), "params": {}}
    return None


async def _handle_ws_session(ws):
    """Shared WebSocket session logic — works with FastAPI WS and tunnel WS alike.
    Processes the first non-empty request then closes. Codex opens a new WS per turn,
    sending full conversation history each time."""
    while True:
        try:
            raw = await ws.receive_text()
        except WebSocketDisconnect:
            return
        except Exception as e:
            logger.error(f"WS receive error: {e}")
            return

        body = json.loads(raw) if raw else {}

        # Handle Codex internal RPC methods (account/read, config/read, etc.)
        # These are sent as {"method": "...", "params": {...}} over the WS.
        method = body.get("method", "")
        if method:
            rpc_response = _handle_codex_rpc(method, body.get("params", {}))
            if rpc_response is not None:
                await ws.send_json(rpc_response)
            if method.startswith("account/") or method.startswith("config/") or method.startswith("modelProvider/"):
                # RPC-only messages, continue reading
                logger.info(f"WS handled RPC: {method}")
                continue

        # Skip empty-input initialization requests — Codex sends these as a
        # "preview" but never displays the response. Save an API call.
        inp = body.get("input", "")
        if not inp or (isinstance(inp, list) and len(inp) == 0):
            prompt = body.get("prompt", "") or ""
            text_val = body.get("text", "")
            if not prompt and (not isinstance(text_val, str) or not text_val.strip()):
                logger.info("WS skip empty-input init request")
                await ws.send_json({
                    "type": "response.completed",
                    "response": {
                        "id": _make_id("resp"), "object": "response",
                        "created_at": int(time.time()), "status": "completed",
                        "model": map_model(body.get("model", DEFAULT_MODEL)),
                        "output": [], "output_text": "",
                        "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                    }
                })
                # Continue reading in case Codex sends the real request on the same WS
                continue

        # Process one real message then return — Codex opens a new WS per turn.
        # Each turn includes full conversation history, so one request per WS is correct.
        await _process_ws_request(ws, body)
        return


async def _process_ws_request(ws, body: dict):
    """Process a single Responses API request over WebSocket."""
    model = map_model(body.get("model", DEFAULT_MODEL))
    msg_id = _make_id("msg")
    resp_id = _make_id("resp")
    output_index = 0
    content_index = 0

    chat_request = responses_to_chat(body)
    chat_request["stream"] = True

    started = False
    sent_text_parts = False
    content_buf = ""
    reasoning_buf = ""
    usage = {}
    tool_calls_by_index: dict[int, dict] = {}

    client = await _get_http_client()
    async with client.stream("POST", _get_upstream(), json=chat_request, headers=_get_auth_headers()) as upstream:
        if upstream.status_code != 200:
            body_text = await upstream.aread()
            error_detail = body_text.decode()[:1000]
            logger.error(f"Upstream {upstream.status_code}: {error_detail}")
            logger.error(f"Chat request: {json.dumps(chat_request, ensure_ascii=False)[:2000]}")
            await ws.send_json({"type": "error", "error": {"message": error_detail, "code": upstream.status_code}})
            return

        async for line in upstream.aiter_lines():
            if not line.startswith("data: "):
                continue
            data_str = line[6:]
            if data_str == "[DONE]":
                break

            try:
                delta = json.loads(data_str)
            except json.JSONDecodeError:
                continue

            choices = delta.get("choices", [])
            if not choices:
                continue
            d = choices[0].get("delta", {})
            content_delta = d.get("content", "") or ""
            reasoning_delta = d.get("reasoning_content", "") or ""

            if delta.get("usage"):
                usage = delta["usage"]

            if not started:
                started = True
                await ws.send_json({
                    "type": "response.created",
                    "response": {
                        "id": resp_id, "object": "response",
                        "created_at": delta.get("created", int(time.time())),
                        "status": "in_progress", "model": model, "output": [],
                    }
                })
                await ws.send_json({
                    "type": "response.in_progress",
                    "response": {"id": resp_id, "object": "response", "status": "in_progress", "model": model}
                })
                await ws.send_json({
                    "type": "response.output_item.added",
                    "output_index": output_index,
                    "item": {"id": msg_id, "type": "message", "role": "assistant", "status": "in_progress", "content": []}
                })

            if reasoning_delta:
                if not reasoning_buf and not content_buf:
                    await ws.send_json({
                        "type": "response.content_part.added",
                        "item_id": msg_id, "output_index": output_index, "content_index": content_index,
                        "part": {"type": "reasoning_text", "text": "", "summary": []},
                    })
                    sent_text_parts = True
                reasoning_buf += reasoning_delta
                await ws.send_json({
                    "type": "response.reasoning_text.delta",
                    "item_id": msg_id, "output_index": output_index, "content_index": content_index,
                    "delta": reasoning_delta,
                })

            if content_delta:
                if not content_buf and not reasoning_buf and not sent_text_parts:
                    await ws.send_json({
                        "type": "response.content_part.added",
                        "item_id": msg_id, "output_index": output_index, "content_index": content_index,
                        "part": {"type": "output_text", "text": ""},
                    })
                    sent_text_parts = True
                elif reasoning_buf and not content_buf:
                    content_index += 1
                    await ws.send_json({
                        "type": "response.content_part.added",
                        "item_id": msg_id, "output_index": output_index, "content_index": content_index,
                        "part": {"type": "output_text", "text": ""},
                    })
                content_buf += content_delta
                await ws.send_json({
                    "type": "response.output_text.delta",
                    "item_id": msg_id, "output_index": output_index, "content_index": content_index,
                    "delta": content_delta,
                })

            # Accumulate tool call deltas
            tc_deltas = d.get("tool_calls") or []
            for tc in tc_deltas:
                idx = tc.get("index", 0)
                if idx not in tool_calls_by_index:
                    tool_calls_by_index[idx] = {
                        "id": tc.get("id", ""),
                        "name": tc.get("function", {}).get("name", ""),
                        "arguments": "",
                    }
                cur = tool_calls_by_index[idx]
                if tc.get("id"):
                    cur["id"] = tc["id"]
                func = tc.get("function", {})
                if func.get("name"):
                    cur["name"] = func["name"]
                if func.get("arguments"):
                    cur["arguments"] += func["arguments"]

        # ── Emit text message output items ─────────────────────────────
        final_content = []
        display_text = content_buf or reasoning_buf
        if display_text:
            final_content.append({"type": "output_text", "text": display_text, "annotations": []})

        if sent_text_parts:
            await ws.send_json({
                "type": "response.content_part.done",
                "item_id": msg_id, "output_index": output_index, "content_index": content_index,
                "part": {"type": "output_text" if content_buf else "reasoning_text", "text": display_text},
            })

        item = {"id": msg_id, "type": "message", "role": "assistant", "status": "completed", "content": final_content}
        await ws.send_json({
            "type": "response.output_item.done",
            "output_index": output_index,
            "item": item,
        })

        # ── Emit function_call output items ────────────────────────────
        all_output_items = [
            {"id": msg_id, "type": "message", "role": "assistant", "status": "completed", "content": final_content}
        ]

        for tc_idx in sorted(tool_calls_by_index.keys()):
            tc = tool_calls_by_index[tc_idx]
            tc_id = tc["id"] or _make_id("call")
            tc_name = tc["name"] or ""
            tc_args = tc["arguments"] or "{}"
            tc_out_idx = output_index + tc_idx + 1

            await ws.send_json({
                "type": "response.output_item.added",
                "output_index": tc_out_idx,
                "item": {"id": tc_id, "type": "function_call", "name": tc_name, "call_id": tc_id, "status": "in_progress", "arguments": ""},
            })
            await ws.send_json({
                "type": "response.function_call_arguments.delta",
                "item_id": tc_id, "output_index": tc_out_idx, "delta": tc_args,
            })
            await ws.send_json({
                "type": "response.function_call_arguments.done",
                "item_id": tc_id, "output_index": tc_out_idx, "arguments": tc_args,
            })
            await ws.send_json({
                "type": "response.output_item.done",
                "output_index": tc_out_idx,
                "item": {"id": tc_id, "type": "function_call", "name": tc_name, "call_id": tc_id, "status": "completed", "arguments": tc_args},
            })

            all_output_items.append({
                "id": tc_id,
                "type": "function_call",
                "call_id": tc_id,
                "name": tc_name,
                "arguments": tc_args,
                "status": "completed",
            })

        await ws.send_json({
            "type": "response.completed",
            "response": {
                "id": resp_id, "object": "response", "created_at": int(time.time()),
                "status": "completed", "model": model,
                "output": all_output_items,
                "output_text": display_text,
                "usage": {
                    "input_tokens": usage.get("prompt_tokens", 0),
                    "output_tokens": usage.get("completion_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                },
            }
        })

        if reasoning_buf:
            _cache_reasoning(_get_session_id(body), reasoning_buf)
        logger.info(f"WS done: reasoning={len(reasoning_buf)}B, content={len(content_buf)}B, "
                    f"tool_calls={len(tool_calls_by_index)}, started={started}, sent_parts={sent_text_parts}")


# ── Models & health ─────────────────────────────────────────────────────────

@app.get("/v1/models")
@app.get("/models")
async def list_models():
    default_model = get_config("default_model", DEFAULT_MODEL)
    return JSONResponse({
        "object": "list",
        "data": [
            {"id": "gpt-5.5", "object": "model", "created": 1750000000, "owned_by": "system"},
            {"id": "gpt-5", "object": "model", "created": 1750000000, "owned_by": "system"},
            {"id": default_model, "object": "model", "created": 1750000000, "owned_by": "deepseek"},
            {"id": "deepseek-v4-flash", "object": "model", "created": 1750000000, "owned_by": "deepseek"},
        ],
    })


@app.get("/health")
async def health():
    try:
        client = await _get_http_client()
        r = await client.get(f"{_get_deepseek_base()}/v1/models", headers=_get_auth_headers())
        upstream = "ok" if r.status_code < 500 else "error"
    except Exception:
        upstream = "unreachable"

    redis_info = None
    if _redis_available:
        try:
            redis_info = {
                "status": "connected",
                "host": REDIS_HOST,
                "port": REDIS_PORT,
                "db": REDIS_DB,
                "keys": len(_redis.keys(f"{REDIS_KEY_PREFIX}*")),
            }
        except Exception:
            redis_info = {"status": "disconnected"}
    else:
        redis_info = {"status": "disabled", "fallback": "memory"}

    return {
        "status": "ok",
        "target": _get_upstream(),
        "upstream": upstream,
        "cache": {
            "backend": "redis" if _redis_available else "memory",
            "redis": redis_info,
        },
    }


# ── Admin API & Web UI ───────────────────────────────────────────────────────

admin_app = FastAPI(title="Proxy Admin")


@admin_app.get("/health")
async def admin_health():
    ds_ok = True
    try:
        client = await _get_http_client()
        r = await client.get(f"{DEEPSEEK_BASE}/v1/models", headers=_get_auth_headers(), timeout=5)
        ds_ok = r.status_code < 500
    except Exception:
        ds_ok = False
    return {
        "status": "ok",
        "deepseek": "connected" if ds_ok else "unreachable",
        "redis": "connected" if _redis_available else "unavailable",
    }


# ── Codex backend API stubs ────────────────────────────────────────────────
# Codex expects these endpoints during TUI bootstrap and normal operation.
# Without them, Codex shows "account/read failed" and other errors.

def _make_model_entry(slug, display_name, description, priority, speed_tiers=None,
                      reasoning_level="medium", reasoning_levels=None):
    """Build a Codex model catalog entry with all required fields."""
    if reasoning_levels is None:
        reasoning_levels = [
            {"effort": "low", "description": "Fast responses with lighter reasoning"},
            {"effort": "medium", "description": "Balances speed and reasoning depth"},
            {"effort": "high", "description": "Greater reasoning depth for complex problems"},
        ]
    return {
        "slug": slug,
        "display_name": display_name,
        "description": description,
        "default_reasoning_level": reasoning_level,
        "default_reasoning_summary": "none",
        "default_verbosity": "low",
        "supported_reasoning_levels": reasoning_levels,
        "support_verbosity": True,
        "supports_reasoning_summaries": True,
        "supports_image_detail_original": True,
        "supports_parallel_tool_calls": True,
        "supports_search_tool": True,
        "context_window": 272000,
        "max_context_window": 272000,
        "effective_context_window_percent": 95,
        "input_modalities": ["text", "image"],
        "shell_type": "shell_command",
        "visibility": "list",
        "supported_in_api": True,
        "priority": priority,
        "additional_speed_tiers": speed_tiers or [],
        "apply_patch_tool_type": "freeform",
        "web_search_tool_type": "text_and_image",
        "experimental_supported_tools": [],
        "truncation_policy": {"mode": "tokens", "limit": 10000},
        "upgrade": None,
        "availability_nux": None,
        "base_instructions": "You are Codex, a coding agent.",
        "model_messages": {
            "instructions_template": (
                "You are Codex, a coding agent. You and the user share one workspace, "
                "and your job is to collaborate with them until their goal is genuinely handled."
            ),
        },
    }


@app.get("/backend-api/codex/models")
@app.get("/backend-api/models")
async def codex_models(request: Request):
    """Return models in Codex catalog format with all required fields."""
    default_model = get_config("default_model", DEFAULT_MODEL)
    return JSONResponse({
        "models": [
            _make_model_entry("gpt-5.5", "GPT-5.5",
                              "Frontier model for complex coding, research, and real-world work.",
                              priority=0, speed_tiers=["fast"],
                              reasoning_level="medium"),
            _make_model_entry("gpt-5", "GPT-5",
                              "Fast model for everyday tasks",
                              priority=1, reasoning_level="low",
                              reasoning_levels=[
                                  {"effort": "low", "description": "Fast responses with lighter reasoning"},
                                  {"effort": "medium", "description": "Balances speed and reasoning depth"},
                              ]),
            _make_model_entry(default_model, default_model,
                              f"DeepSeek V4 Pro via JinDX proxy",
                              priority=2, reasoning_level="medium"),
        ],
        "default": "gpt-5.5",
    })


@app.post("/backend-api/codex/analytics-events/events")
@app.post("/backend-api/analytics-events/events")
async def codex_analytics():
    """Accept and discard Codex telemetry."""
    return JSONResponse({"status": "ok"})


@app.get("/backend-api/plugins/featured")
async def codex_plugins():
    """Return empty featured plugins list. Codex expects a JSON array."""
    return JSONResponse([])


@app.post("/backend-api/wham/apps")
async def codex_wham():
    return JSONResponse({"status": "ok"})


# Catch-all for other backend-api/codex paths to avoid 404 during bootstrap
@app.api_route("/backend-api/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def codex_backend_fallback(path: str):
    """Return empty success for unknown Codex backend endpoints."""
    logger.debug(f"Codex backend fallback: /backend-api/{path}")
    return JSONResponse({"status": "ok"})


ADMIN_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>JinDX</title>
<style>
:root { --bg: #0d1117; --fg: #c9d1d9; --border: #30363d; --accent: #58a6ff; --danger: #f85149; --green: #3fb950; --orange: #d2991d; --input-bg: #161b22; --card: #161b22; --muted: #8b949e; }
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font: 14px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; background: var(--bg); color: var(--fg); min-height: 100vh; }

/* ── Top bar ──────────────────────────────── */
#topbar { display: flex; justify-content: space-between; align-items: center; padding: 12px 24px; border-bottom: 1px solid var(--border); background: var(--card); position: sticky; top: 0; z-index: 10; }
#topbar h1 { font-size: 20px; color: var(--accent); display: flex; align-items: center; gap: 10px; }
#topbar h1 .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--green); }
#lang-btn { padding: 4px 14px; border: 1px solid var(--border); border-radius: 4px; background: var(--input-bg); color: var(--fg); cursor: pointer; font-size: 13px; }
#lang-btn:hover { border-color: var(--accent); }

/* ── Layout ───────────────────────────────── */
#main { display: flex; gap: 20px; padding: 20px 24px; max-width: 1400px; margin: 0 auto; }
#left { flex: 1; min-width: 0; }
#right { width: 420px; flex-shrink: 0; }

@media (max-width: 900px) {
  #main { flex-direction: column; }
  #right { width: 100%; }
}

/* ── Cards ─────────────────────────────────── */
.card { background: var(--card); border: 1px solid var(--border); border-radius: 6px; padding: 16px; margin-bottom: 16px; }
.card h2 { font-size: 15px; margin-bottom: 14px; padding-bottom: 8px; border-bottom: 1px solid var(--border); color: var(--accent); display: flex; align-items: center; gap: 8px; }
.card h2 .icon { font-size: 16px; }

/* ── Stat grid ─────────────────────────────── */
.stat-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.stat-item { background: var(--bg); border: 1px solid var(--border); border-radius: 4px; padding: 12px; text-align: center; }
.stat-value { font-size: 24px; font-weight: 700; color: var(--accent); }
.stat-value.green { color: var(--green); }
.stat-value.orange { color: var(--orange); }
.stat-value.danger { color: var(--danger); }
.stat-label { font-size: 11px; color: var(--muted); margin-top: 2px; }

/* ── Log list ──────────────────────────────── */
#log-list { max-height: 200px; overflow-y: auto; font-size: 12px; font-family: monospace; }
#log-list .log-entry { padding: 4px 8px; border-bottom: 1px solid var(--border); color: var(--muted); }
#log-list .log-entry .log-time { color: var(--accent); margin-right: 8px; }

/* ── Form controls ─────────────────────────── */
.row { display: flex; gap: 12px; align-items: center; margin-bottom: 10px; flex-wrap: wrap; }
.row label { min-width: 150px; font-weight: 500; font-size: 13px; }
.row input, .row select, .row textarea { flex: 1; min-width: 180px; background: var(--input-bg); border: 1px solid var(--border); border-radius: 4px; color: var(--fg); padding: 6px 10px; font-size: 13px; }
.row textarea { min-height: 56px; font-family: monospace; }
.row input[type="checkbox"] { flex: 0; min-width: 0; accent-color: var(--accent); width: 16px; height: 16px; }
.row input:focus, .row select:focus, .row textarea:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 2px rgba(88,166,255,0.2); }
.btn-row { display: flex; gap: 10px; margin-top: 16px; }
.btn { padding: 8px 20px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; font-weight: 600; cursor: pointer; transition: all 0.15s; }
.btn-primary { background: #238636; color: #fff; border-color: #238636; }
.btn-primary:hover { background: #2ea043; }
.btn-secondary { background: var(--input-bg); color: var(--fg); }
.btn-secondary:hover { background: #30363d; }
.model-row { display: flex; gap: 8px; align-items: center; margin-bottom: 6px; }
.model-row input { flex: 1; }
.model-row button { background: none; border: 1px solid var(--border); border-radius: 4px; color: var(--danger); cursor: pointer; padding: 4px 10px; font-size: 12px; }
#add-model { margin-top: 6px; font-size: 12px; background: none; border: 1px dashed var(--border); border-radius: 4px; color: var(--accent); cursor: pointer; padding: 4px 12px; }

/* ── Toast ──────────────────────────────────── */
#toast { position: fixed; top: 16px; right: 16px; padding: 10px 18px; border-radius: 6px; font-size: 13px; font-weight: 500; opacity: 0; transition: opacity 0.25s; z-index: 999; pointer-events: none; }
#toast.show { opacity: 1; }
#toast.ok { background: #238636; color: #fff; }
#toast.err { background: var(--danger); color: #fff; }

/* ── Section toggle ────────────────────────── */
.section-toggle { cursor: pointer; user-select: none; }
.section-toggle:hover { color: #fff; }
.section-body { display: block; }
.section-body.collapsed { display: none; }

/* ── Status dot ────────────────────────────── */
.status-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }
.status-dot.up { background: var(--green); }
.status-dot.down { background: var(--danger); }
</style>
</head>
<body>

<div id="topbar">
  <h1><span class="dot" id="status-dot"></span><span data-i18n-zh="JinDX 代理管理" data-i18n-en="JinDX Proxy Manager">JinDX 代理管理</span></h1>
  <button id="lang-btn" onclick="toggleLang()" data-i18n-zh="English" data-i18n-en="中文">English</button>
</div>

<div id="toast"></div>

<div id="main">

<!-- ===== LEFT: Config panels ===== -->
<div id="left">

  <div class="card">
    <h2 class="section-toggle" onclick="toggleSection(this)"><span class="icon">&#128268;</span> <span data-i18n-zh="上游连接" data-i18n-en="Upstream API">上游连接</span></h2>
    <div class="section-body">
      <div class="row"><label>API Key</label><input id="deepseek_key" type="password" placeholder="sk-..." autocomplete="off"></div>
      <div class="row"><label>Base URL</label><input id="deepseek_base" type="text" placeholder="https://api.deepseek.com"></div>
      <div class="row"><label data-i18n-zh="默认模型" data-i18n-en="Default Model">默认模型</label><input id="default_model" type="text" placeholder="deepseek-v4-pro"></div>
    </div>
  </div>

  <div class="card">
    <h2 class="section-toggle" onclick="toggleSection(this)"><span class="icon">&#9881;</span> <span data-i18n-zh="模型映射" data-i18n-en="Model Mapping">模型映射</span></h2>
    <div class="section-body">
      <div id="model-rows"></div>
      <button id="add-model" onclick="addModelRow('','')">+ <span data-i18n-zh="添加映射" data-i18n-en="Add Mapping">添加映射</span></button>
    </div>
  </div>

  <div class="card">
    <h2 class="section-toggle" onclick="toggleSection(this)"><span class="icon">&#9881;</span> <span data-i18n-zh="生成参数" data-i18n-en="Generation Defaults">生成参数</span></h2>
    <div class="section-body">
      <div class="row"><label data-i18n-zh="推理强度" data-i18n-en="Reasoning Effort">推理强度</label><select id="reasoning_effort"><option value="" data-i18n-zh="(由 DeepSeek 决定)" data-i18n-en="(let DeepSeek decide)">(由 DeepSeek 决定)</option><option value="min">min</option><option value="low">low</option><option value="medium">medium</option><option value="high">high</option><option value="max">max</option></select></div>
      <div class="row"><label data-i18n-zh="上下文窗口" data-i18n-en="Context Window">上下文窗口</label><input id="max_position_embeddings" type="number" min="1024" max="10000000" step="1024"></div>
      <div class="row"><label data-i18n-zh="最大输出 Tokens" data-i18n-en="Max Output Tokens">最大输出 Tokens</label><input id="max_output_tokens" type="number" min="1" max="131072"></div>
      <div class="row"><label data-i18n-zh="温度" data-i18n-en="Temperature">温度</label><input id="temperature" type="number" step="0.01" min="0" max="2" placeholder="(unset)"></div>
      <div class="row"><label data-i18n-zh="Top P" data-i18n-en="Top P">Top P</label><input id="top_p" type="number" step="0.01" min="0" max="1" placeholder="(unset)"></div>
    </div>
  </div>

  <div class="card">
    <h2 class="section-toggle" onclick="toggleSection(this)"><span class="icon">&#127760;</span> <span data-i18n-zh="网页抓取" data-i18n-en="Web Fetch">网页抓取</span></h2>
    <div class="section-body">
      <div class="row"><label data-i18n-zh="最大 URL 数" data-i18n-en="Max URLs">最大 URL 数</label><input id="web_fetch_max_urls" type="number" min="0" max="50"></div>
      <div class="row"><label data-i18n-zh="超时 (秒)" data-i18n-en="Timeout (seconds)">超时 (秒)</label><input id="web_fetch_timeout" type="number" min="1" max="120"></div>
      <div class="row"><label data-i18n-zh="最大响应体 (字节)" data-i18n-en="Max Body (bytes)">最大响应体 (字节)</label><input id="web_fetch_max_body" type="number" min="1000" max="1000000"></div>
    </div>
  </div>

  <div class="card">
    <h2 class="section-toggle" onclick="toggleSection(this)"><span class="icon">&#128190;</span> <span data-i18n-zh="推理缓存" data-i18n-en="Reasoning Cache">推理缓存</span></h2>
    <div class="section-body">
      <div class="row"><label data-i18n-zh="启用缓存" data-i18n-en="Enable Cache">启用缓存</label><input id="enable_reasoning_cache" type="checkbox"></div>
      <div class="row"><label>Cache TTL (s)</label><input id="reasoning_cache_ttl" type="number" min="30" max="86400"></div>
    </div>
  </div>

  <div class="btn-row">
    <button class="btn btn-primary" onclick="saveConfig()"><span data-i18n-zh="保存配置" data-i18n-en="Save">保存配置</span></button>
    <button class="btn btn-secondary" onclick="loadConfig()"><span data-i18n-zh="重新加载" data-i18n-en="Reload">重新加载</span></button>
  </div>

</div>

<!-- ===== RIGHT: Stats + Status ===== -->
<div id="right">

  <div class="card">
    <h2><span class="icon">&#128200;</span> <span data-i18n-zh="实时统计" data-i18n-en="Live Stats">实时统计</span></h2>
    <div class="stat-grid">
      <div class="stat-item"><div class="stat-value" id="stat-uptime">--</div><div class="stat-label" data-i18n-zh="运行时间" data-i18n-en="Uptime">运行时间</div></div>
      <div class="stat-item"><div class="stat-value" id="stat-requests">--</div><div class="stat-label" data-i18n-zh="总请求数" data-i18n-en="Requests">总请求数</div></div>
      <div class="stat-item"><div class="stat-value" id="stat-streams">0</div><div class="stat-label" data-i18n-zh="活跃流" data-i18n-en="Active Streams">活跃流</div></div>
      <div class="stat-item"><div class="stat-value" id="stat-error-rate">--</div><div class="stat-label" data-i18n-zh="错误率" data-i18n-en="Error Rate">错误率</div></div>
      <div class="stat-item"><div class="stat-value green" id="stat-cache-hit">--</div><div class="stat-label" data-i18n-zh="缓存命中率" data-i18n-en="Cache Hit">缓存命中率</div></div>
      <div class="stat-item"><div class="stat-value" id="stat-sessions">--</div><div class="stat-label" data-i18n-zh="活跃会话" data-i18n-en="Sessions">活跃会话</div></div>
    </div>
  </div>

  <div class="card">
    <h2><span class="icon">&#9888;</span> <span data-i18n-zh="上游错误" data-i18n-en="Upstream Errors">上游错误</span></h2>
    <div id="upstream-errors" style="font-size:12px;color:var(--muted);max-height:150px;overflow-y:auto;">--</div>
  </div>

  <div class="card">
    <h2><span class="icon">&#128220;</span> <span data-i18n-zh="最近日志" data-i18n-en="Recent Logs">最近日志</span></h2>
    <div id="log-list"><span style="color:var(--muted)">--</span></div>
  </div>

  <div class="card">
    <h2><span class="icon">&#128225;</span> <span data-i18n-zh="系统状态" data-i18n-en="System Status">系统状态</span></h2>
    <div style="font-size:13px;">
      <div style="margin-bottom:6px;"><span class="status-dot" id="ds-status-dot"></span><span data-i18n-zh="DeepSeek API：" data-i18n-en="DeepSeek API: ">DeepSeek API：</span><span id="ds-status">--</span></div>
      <div style="margin-bottom:6px;"><span class="status-dot" id="redis-status-dot"></span><span>Redis：</span><span id="redis-status">--</span></div>
    </div>
  </div>

</div>
</div>

<script>
// ── i18n ────────────────────────────────────────────────────────────────────
const LANG_KEY = 'jindx_lang';
let currentLang = localStorage.getItem(LANG_KEY) || 'zh';

function toggleLang() {
  currentLang = currentLang === 'zh' ? 'en' : 'zh';
  localStorage.setItem(LANG_KEY, currentLang);
  applyLang();
}

function applyLang() {
  document.documentElement.lang = currentLang === 'zh' ? 'zh-CN' : 'en';
  document.querySelectorAll('[data-i18n-zh]').forEach(el => {
    const text = currentLang === 'zh' ? el.getAttribute('data-i18n-zh') : el.getAttribute('data-i18n-en');
    if (text) el.textContent = text;
  });
  // Update option texts
  document.querySelectorAll('[data-i18n-zh] option').forEach(opt => {
    // Options inherit from parent select handling
  });
  document.getElementById('lang-btn').textContent = currentLang === 'zh' ? 'English' : '中文';
  // Re-render stats & logs with current lang
  refreshStats();
  refreshSessions();
  refreshLogs();
}

function t(zh, en) { return currentLang === 'zh' ? zh : en; }

// ── Section toggle ──────────────────────────────────────────────────────────
function toggleSection(header) {
  const body = header.nextElementSibling;
  body.classList.toggle('collapsed');
}

// ── Toast ───────────────────────────────────────────────────────────────────
function toast(msg, ok) {
  const t = document.getElementById('toast');
  t.textContent = msg; t.className = (ok ? 'ok' : 'err') + ' show';
  setTimeout(() => t.classList.remove('show'), 2200);
}

// ── Model mapping ───────────────────────────────────────────────────────────
function addModelRow(k, v) {
  const div = document.createElement('div'); div.className = 'model-row';
  const ki = document.createElement('input'); ki.placeholder = 'OpenAI model (e.g. gpt-5.5)'; ki.value = k || '';
  const vi = document.createElement('input'); vi.placeholder = 'DeepSeek model (e.g. deepseek-v4-pro)'; vi.value = v || '';
  const del = document.createElement('button'); del.textContent = 'X'; del.onclick = () => div.remove();
  div.append(ki, vi, del);
  document.getElementById('model-rows').appendChild(div);
}
function getModelMapping() {
  const map = {};
  document.querySelectorAll('.model-row').forEach(r => {
    const inputs = r.querySelectorAll('input');
    if (inputs[0].value.trim() && inputs[1].value.trim()) map[inputs[0].value.trim()] = inputs[1].value.trim();
  });
  return map;
}
function setModelMapping(map) {
  document.getElementById('model-rows').innerHTML = '';
  if (map && Object.keys(map).length) Object.entries(map).forEach(([k,v]) => addModelRow(k,v));
}

// ── Config load / save ──────────────────────────────────────────────────────
async function loadConfig() {
  try {
    const r = await fetch('/config'); const cfg = await r.json();
    document.getElementById('deepseek_key').value = cfg.deepseek_key || '';
    document.getElementById('deepseek_base').value = cfg.deepseek_base || '';
    document.getElementById('default_model').value = cfg.default_model || '';
    setModelMapping(cfg.model_mapping);
    document.getElementById('reasoning_effort').value = cfg.reasoning_effort || '';
    document.getElementById('max_position_embeddings').value = cfg.max_position_embeddings || 1000000;
    document.getElementById('max_output_tokens').value = cfg.max_output_tokens || '';
    document.getElementById('temperature').value = cfg.temperature != null ? cfg.temperature : '';
    document.getElementById('top_p').value = cfg.top_p != null ? cfg.top_p : '';
    document.getElementById('web_fetch_max_urls').value = cfg.web_fetch_max_urls || '';
    document.getElementById('web_fetch_timeout').value = cfg.web_fetch_timeout || '';
    document.getElementById('web_fetch_max_body').value = cfg.web_fetch_max_body || '';
    document.getElementById('enable_reasoning_cache').checked = cfg.enable_reasoning_cache;
    document.getElementById('reasoning_cache_ttl').value = cfg.reasoning_cache_ttl || '';
    toast(t('配置已加载','Config loaded'), true);
  } catch(e) { toast(t('加载失败','Load failed') + ': ' + e, false); }
}

async function saveConfig() {
  const cfg = {
    deepseek_key: document.getElementById('deepseek_key').value.trim(),
    deepseek_base: document.getElementById('deepseek_base').value.trim(),
    default_model: document.getElementById('default_model').value.trim(),
    model_mapping: getModelMapping(),
    reasoning_effort: document.getElementById('reasoning_effort').value || null,
    max_position_embeddings: parseInt(document.getElementById('max_position_embeddings').value) || 1000000,
    max_output_tokens: parseInt(document.getElementById('max_output_tokens').value) || 16384,
    temperature: document.getElementById('temperature').value ? parseFloat(document.getElementById('temperature').value) : null,
    top_p: document.getElementById('top_p').value ? parseFloat(document.getElementById('top_p').value) : null,
    web_fetch_max_urls: parseInt(document.getElementById('web_fetch_max_urls').value),
    web_fetch_timeout: parseInt(document.getElementById('web_fetch_timeout').value),
    web_fetch_max_body: parseInt(document.getElementById('web_fetch_max_body').value),
    enable_reasoning_cache: document.getElementById('enable_reasoning_cache').checked,
    reasoning_cache_ttl: parseInt(document.getElementById('reasoning_cache_ttl').value),
  };
  try {
    const r = await fetch('/config', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(cfg) });
    if (r.ok) { toast(t('已保存并生效','Saved & applied'), true); loadConfig(); }
    else { const e = await r.json(); toast(t('保存失败','Save failed') + ': ' + (e.detail || r.status), false); }
  } catch(e) { toast(t('保存失败','Save failed') + ': ' + e, false); }
}

// ── Stats refresh ───────────────────────────────────────────────────────────
function fmtUptime(sec) {
  if (sec < 60) return sec + 's';
  if (sec < 3600) return Math.floor(sec/60) + 'm';
  if (sec < 86400) return Math.floor(sec/3600) + 'h ' + Math.floor((sec%3600)/60) + 'm';
  return Math.floor(sec/86400) + 'd ' + Math.floor((sec%86400)/3600) + 'h';
}

async function refreshStats() {
  try {
    const r = await fetch('/stats');
    const s = await r.json();
    document.getElementById('stat-uptime').textContent = fmtUptime(s.uptime);
    document.getElementById('stat-requests').textContent = s.total_requests;
    document.getElementById('stat-streams').textContent = s.active_streams;
    const erEl = document.getElementById('stat-error-rate');
    erEl.textContent = s.error_rate + '%';
    erEl.className = 'stat-value' + (s.error_rate > 10 ? ' danger' : s.error_rate > 3 ? ' orange' : '');
    const chEl = document.getElementById('stat-cache-hit');
    chEl.textContent = s.cache_hit_rate + '%';
    chEl.className = 'stat-value' + (s.cache_hit_rate >= 70 ? ' green' : '');

    // Upstream errors
    const ue = document.getElementById('upstream-errors');
    if (s.top_upstream_errors && s.top_upstream_errors.length) {
      ue.innerHTML = s.top_upstream_errors.map(e =>
        '<div style="margin-bottom:4px;"><span style="color:var(--danger)">' + e.count + 'x</span> ' + escHtml(e.msg.substring(0,100)) + '</div>'
      ).join('');
    } else {
      ue.textContent = t('无','None');
    }
  } catch(e) { /* silent */ }
}

async function refreshSessions() {
  try {
    const r = await fetch('/sessions');
    const s = await r.json();
    document.getElementById('stat-sessions').textContent = s.memory_sessions + s.redis_sessions;
  } catch(e) { /* silent */ }
}

async function refreshLogs() {
  try {
    const r = await fetch('/logs?limit=20');
    const data = await r.json();
    const list = document.getElementById('log-list');
    if (data.logs && data.logs.length) {
      list.innerHTML = data.logs.map(l =>
        '<div class="log-entry"><span class="log-time">' + new Date(l.ts*1000).toLocaleTimeString() + '</span>' + escHtml(l.msg) + '</div>'
      ).join('');
    } else {
      list.innerHTML = '<span style="color:var(--muted)">' + t('暂无错误','No errors') + '</span>';
    }
  } catch(e) { /* silent */ }
}

function escHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

// ── System status ───────────────────────────────────────────────────────────
async function checkStatus() {
  try {
    const r = await fetch('/health');
    if (r.ok) {
      const s = await r.json();
      document.getElementById('ds-status').textContent = s.deepseek || 'OK';
      document.getElementById('ds-status-dot').className = 'status-dot up';
      document.getElementById('redis-status').textContent = s.redis || 'OK';
      document.getElementById('redis-status-dot').className = 'status-dot up';
      document.getElementById('status-dot').className = 'dot';
    }
  } catch(e) {
    document.getElementById('ds-status').textContent = '--';
    document.getElementById('ds-status-dot').className = 'status-dot down';
    document.getElementById('redis-status').textContent = '--';
    document.getElementById('redis-status-dot').className = 'status-dot down';
    document.getElementById('status-dot').className = 'dot';
    document.getElementById('status-dot').style.background = 'var(--danger)';
  }
}

// ── Init ────────────────────────────────────────────────────────────────────
function init() {
  applyLang();
  loadConfig();
  refreshStats();
  refreshSessions();
  refreshLogs();
  checkStatus();
  setInterval(refreshStats, 5000);
  setInterval(refreshSessions, 30000);
  setInterval(refreshLogs, 15000);
  setInterval(checkStatus, 30000);
}

init();
</script>
</body>
</html>"""


@admin_app.get("/", response_class=HTMLResponse)
async def admin_page():
    return ADMIN_HTML


@admin_app.get("/config")
async def admin_get_config():
    return JSONResponse(content=_runtime_config)


@admin_app.post("/config")
async def admin_set_config(request: Request):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    allowed_keys = {
        "deepseek_key", "deepseek_base", "default_model",
        "model_mapping", "reasoning_effort", "max_position_embeddings", "max_output_tokens", "temperature",
        "top_p", "tool_use_enforcement", "tool_use_prompt",
        "web_fetch_max_urls", "web_fetch_timeout", "web_fetch_max_body",
        "enable_reasoning_cache", "reasoning_cache_ttl",
    }
    for key in body:
        if key in allowed_keys:
            _runtime_config[key] = body[key]

    _save_config(_runtime_config)
    logger.info(f"Config updated via admin API: {json.dumps(body, ensure_ascii=False)[:500]}")
    return JSONResponse(content={"status": "ok", "config": _runtime_config})


@admin_app.get("/stats")
async def admin_stats():
    """Return request statistics for the dashboard."""
    with _stats_lock:
        uptime = int(time.time() - _stats["start_time"])
        total = _stats["total_requests"]
        errors = sum(_stats["errors_by_code"].values())
        error_rate = round(errors / max(total, 1) * 100, 1)
        cache_total = _stats["cache_hits"] + _stats["cache_misses"]
        cache_hit_rate = round(_stats["cache_hits"] / max(cache_total, 1) * 100, 1)
        top_errors = sorted(_stats["errors_by_code"].items(), key=lambda x: x[1], reverse=True)[:10]
        top_upstream = sorted(_stats["upstream_errors"].items(), key=lambda x: x[1], reverse=True)[:5]
        return {
            "uptime": uptime,
            "total_requests": total,
            "active_streams": _stats["active_streams"],
            "errors_by_code": dict(top_errors),
            "error_rate": error_rate,
            "cache_hits": _stats["cache_hits"],
            "cache_misses": _stats["cache_misses"],
            "cache_hit_rate": cache_hit_rate,
            "top_upstream_errors": [{"msg": k, "count": v} for k, v in top_upstream],
        }


@admin_app.get("/sessions")
async def admin_sessions():
    """Return session / cache pool info."""
    with _cache_lock:
        memory_sessions = len(_reasoning_cache)
    redis_sessions = 0
    if _redis_available:
        try:
            keys = _redis.keys(f"{REDIS_KEY_PREFIX}*")
            redis_sessions = len(keys) if keys else 0
        except Exception:
            pass
    return {
        "memory_sessions": memory_sessions,
        "redis_sessions": redis_sessions,
        "cache_max": REASONING_CACHE_MAX,
        "cache_ttl": REASONING_CACHE_TTL,
    }


@admin_app.get("/logs")
async def admin_logs(limit: int = 50):
    """Return recent error logs from the ring buffer."""
    recent = _log_buffer[-limit:]
    recent.reverse()
    return {"logs": recent}


# ── TLS cert management ─────────────────────────────────────────────────────

def _ensure_certs():
    """Generate a self-signed certificate for TLS termination if not present."""
    if CERT_FILE.exists() and KEY_FILE.exists():
        return
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    from subprocess import run
    run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048",
        "-keyout", str(KEY_FILE), "-out", str(CERT_FILE),
        "-days", "3650", "-nodes",
        "-subj", "/CN=localhost",
        "-addext", "subjectAltName=DNS:localhost,DNS:api.openai.com",
        "-addext", "basicConstraints=CA:FALSE",
        "-addext", "keyUsage=digitalSignature,keyEncipherment",
        "-addext", "extendedKeyUsage=serverAuth",
    ], check=True, capture_output=True)
    logger.info(f"Generated self-signed TLS cert: {CERT_FILE}")


# ── CONNECT tunnel server ───────────────────────────────────────────────────

async def _run_connect_server():
    if CONNECT_PORT == 0:
        logger.info("CONNECT tunnel DISABLED (CONNECT_PORT=0)")
        return
    """Run a raw TCP server that handles HTTP CONNECT + TLS termination,
    then transparently proxies the plain stream to the local HTTP/WS server."""
    _ensure_certs()

    async def pipe(src_reader: asyncio.StreamReader, dst_writer: asyncio.StreamWriter, label: str):
        """Bidirectional pipe between two streams."""
        try:
            while True:
                data = await src_reader.read(65536)
                if not data:
                    break
                dst_writer.write(data)
                await dst_writer.drain()
        except (ConnectionError, asyncio.TimeoutError, asyncio.IncompleteReadError):
            pass
        except Exception as e:
            logger.debug(f"Pipe {label} error: {e}")
        finally:
            try:
                dst_writer.close()
            except Exception:
                pass

    async def handle_connect(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        peer = writer.get_extra_info('peername')
        try:
            # 1. Read HTTP CONNECT request
            data = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), timeout=10)
            first_line = data.split(b'\r\n')[0].decode()

            if not first_line.startswith('CONNECT '):
                writer.write(b'HTTP/1.1 405 Method Not Allowed\r\n\r\n')
                await writer.drain()
                writer.close()
                return

            # 2. Accept the tunnel
            writer.write(b'HTTP/1.1 200 Connection Established\r\n\r\n')
            await writer.drain()

            # 3. Upgrade to TLS
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(str(CERT_FILE), str(KEY_FILE))

            loop = asyncio.get_event_loop()
            transport = writer.transport

            tls_reader = asyncio.StreamReader()
            protocol = asyncio.StreamReaderProtocol(tls_reader)

            tls_transport = await loop.start_tls(
                transport=transport,
                protocol=protocol,
                sslcontext=ctx,
                server_side=True,
                ssl_handshake_timeout=10,
            )
            tls_writer = asyncio.StreamWriter(tls_transport, protocol, tls_reader, loop)

            # 4. Connect to local HTTP/WS server and transparently proxy
            backend_reader, backend_writer = await asyncio.open_connection('127.0.0.1', PROXY_PORT)

            await asyncio.gather(
                pipe(tls_reader, backend_writer, "client->backend"),
                pipe(backend_reader, tls_writer, "backend->client"),
            )

        except (ConnectionError, asyncio.TimeoutError):
            pass
        except ssl.SSLError as e:
            logger.warning(f"TLS handshake failed from {peer}: {e}")
        except Exception as e:
            logger.error(f"CONNECT tunnel error from {peer}: {e}")
        finally:
            try:
                writer.close()
            except Exception:
                pass

    server = await asyncio.start_server(handle_connect, '127.0.0.1', CONNECT_PORT)
    logger.info(f"CONNECT+TLS tunnel server listening on 127.0.0.1:{CONNECT_PORT}")

    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    import uvicorn

    _ensure_certs()

    async def _serve_all():
        # Run CONNECT tunnel, HTTP, TLS, and admin servers in one event loop
        connect_task = asyncio.create_task(_run_connect_server())

        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_ctx.load_cert_chain(str(CERT_FILE), str(KEY_FILE))

        http_config = uvicorn.Config(app, host="0.0.0.0", port=PROXY_PORT)
        tls_config = uvicorn.Config(app, host="0.0.0.0", port=TLS_PORT,
                                     ssl_certfile=str(CERT_FILE), ssl_keyfile=str(KEY_FILE))
        admin_config = uvicorn.Config(admin_app, host="0.0.0.0", port=ADMIN_PORT)

        http_server = uvicorn.Server(http_config)
        tls_server = uvicorn.Server(tls_config)
        admin_server = uvicorn.Server(admin_config)

        logger.info(f"Starting HTTP/WS proxy on 0.0.0.0:{PROXY_PORT}")
        logger.info(f"Starting direct TLS proxy on 0.0.0.0:{TLS_PORT}")
        logger.info(f"Starting admin UI on 0.0.0.0:{ADMIN_PORT}")

        await asyncio.gather(
            connect_task,
            http_server.serve(),
            tls_server.serve(),
            admin_server.serve(),
        )

    asyncio.run(_serve_all())