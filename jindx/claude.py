"""Anthropic Messages API ↔ DeepSeek Chat Completions 协议翻译。

让 Claude Code 能通过 JinDX 代理使用 DeepSeek。
请求方向：Anthropic Messages → DeepSeek Chat Completions
响应方向：DeepSeek Chat Completions → Anthropic Messages（含 SSE 流式）
"""

import json
import logging
import time
import uuid

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .config import config
from .cache import get_cached_reasoning, cache_reasoning, get_session_id
from .routes import get_http_client
from .stats import record_request, record_error, record_upstream_error, log_error

logger = logging.getLogger(__name__)

MAX_TOKENS_DEFAULT = 16384
MAX_POS_DEFAULT = 1000000


def _make_claude_id(prefix: str = "msg") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


def _cfg(key, default=None):
    """读取 Claude 专用配置项。"""
    return config.get(f"claude_{key}", config.get(key, default))


# ═══════════════════════════════════════════════════════════
# 请求转换：Anthropic Messages → DeepSeek Chat Completions
# ═══════════════════════════════════════════════════════════

def _anthropic_content_to_chat_message(role: str, content) -> list:
    """将 Anthropic content block 转为 DeepSeek Chat 格式。

    返回 list[dict] — 一个 Anthropic user 消息中的多个 tool_result 会被拆成多条 tool 消息。
    cc-switch 风格：
    - thinking block → reasoning_content
    - redacted_thinking block → 跳过
    - signature 字段自动忽略
    - 多个 tool_result 各自独立成 tool 消息（修复 insufficient tool messages 400 错误）
    - tool 消息发出后 text 才发出（DeepSeek 要求 tool 紧跟 assistant）
    """
    if isinstance(content, str):
        return [{"role": role, "content": content}]

    results = []
    text_parts = []
    thinking_parts = []
    tool_calls = []

    for part in content:
        tp = part.get("type", "")
        if tp == "text":
            text_parts.append(part.get("text", ""))
        elif tp == "thinking":
            thinking_text = part.get("thinking", "")
            if thinking_text:
                thinking_parts.append(thinking_text)
        elif tp == "redacted_thinking":
            pass
        elif tp == "tool_use":
            tool_calls.append({
                "id": part.get("id", _make_claude_id("call")),
                "type": "function",
                "function": {"name": part.get("name", ""),
                             "arguments": json.dumps(part.get("input", {}), ensure_ascii=False)},
            })
        elif tp == "tool_result":
            # 每个 tool_result 独立成一条 tool 消息（先于 text 发出）
            tool_msg = {"role": "tool",
                        "tool_call_id": part.get("tool_use_id", ""),
                        "content": ""}
            inner = part.get("content", "")
            if isinstance(inner, list):
                tool_texts = []
                for c in inner:
                    if isinstance(c, dict):
                        if c.get("type") == "text":
                            tool_texts.append(c.get("text", ""))
                    else:
                        tool_texts.append(str(c))
                tool_msg["content"] = "".join(tool_texts)
            elif isinstance(inner, str):
                tool_msg["content"] = inner
            else:
                tool_msg["content"] = json.dumps(inner, ensure_ascii=False)
            results.append(tool_msg)
        # 忽略其他未知类型

    # 所有非 tool_result 内容累积为最后一条消息
    final = {"role": role, "content": "".join(text_parts)}
    if tool_calls:
        final["content"] = final.get("content") or ""
        final["tool_calls"] = tool_calls
    if thinking_parts and role == "assistant":
        final["reasoning_content"] = "\n".join(thinking_parts)

    # cc-switch 风格：tool 消息在前，text 消息在后
    results.append(final)
    return results


def _anthropic_tools_to_chat(tools: list) -> list:
    result = []
    for tool in tools:
        schema = tool.get("input_schema", {})
        params = {"type": schema.get("type", "object")}
        for key in ("properties", "required", "additionalProperties", "enum",
                     "oneOf", "anyOf", "allOf", "items", "minItems", "maxItems",
                     "minProperties", "maxProperties", "uniqueItems"):
            if key in schema:
                params[key] = schema[key]
        result.append({
            "type": "function",
            "function": {"name": tool.get("name", ""),
                         "description": tool.get("description", ""),
                         "parameters": params},
        })
    return result


# ── 全局推理缓存桥接 ───────────────────────────────────────
# Claude Code 没有 Codex 的 session ID 机制，用前几条消息的哈希做会话标识


def _claude_session_key(messages: list) -> str:
    """从消息列表衍生出稳定的会话标识。"""
    # 取前几条 user/assistant 消息（跳过 system prompt 变化）
    short = [m for m in messages[:6] if m.get("role") in ("user", "assistant")]
    if not short:
        return "claude_default"
    seed = json.dumps(short, ensure_ascii=False, sort_keys=True)[:200]
    return f"claude_{hash(seed) & 0x7FFFFFFF:08x}"


def anthropic_to_chat(request_body: dict) -> dict:
    messages = []

    system_text = request_body.get("system", "")
    if system_text:
        if isinstance(system_text, list):
            system_text = "".join(c.get("text", "") if isinstance(c, dict) else str(c)
                                  for c in system_text)
        messages.append({"role": "system", "content": system_text})

    for msg in request_body.get("messages", []):
        role = msg.get("role", "user")
        converted = _anthropic_content_to_chat_message(role, msg.get("content", ""))
        messages.extend(converted)

    # ── 推理缓存注入 ──
    # 仅在 thinking 启用时才注入 reasoning_content，否则保持消息原样以
    # 最大化 DeepSeek prompt cache 命中率。
    session_id = _claude_session_key(messages)
    thinking_enabled = _cfg("deepseek_thinking_enabled", False)

    if thinking_enabled:
        cached = get_cached_reasoning("claude", session_id)

        # 第一层：本会话缓存注入
        if cached:
            idx = 0
            for msg in messages:
                if msg.get("role") == "assistant":
                    if idx < len(cached):
                        msg["reasoning_content"] = cached[idx]
                        idx += 1
            if idx > 0:
                logger.info(f"Claude injected {idx} cached reasoning entries (session {session_id})")

        # 第二层：全局最近缓存回退
        if not cached:
            cached_global = get_cached_reasoning("claude", "recent")
            if cached_global:
                for msg in messages:
                    if msg.get("role") == "assistant" and not msg.get("reasoning_content"):
                        msg["reasoning_content"] = cached_global[0]
                        break

        # 第三层：填充缺失的 reasoning_content
        from .protocol import _ensure_assistant_reasoning
        all_cached = cached if cached else get_cached_reasoning("claude", "recent")
        _ensure_assistant_reasoning(messages, all_cached)

    chat = {
        "model": _cfg("default_model", "deepseek-v4-pro"),
        "messages": messages,
        "stream": request_body.get("stream", False),
    }

    max_tokens = request_body.get("max_tokens", 0) or _cfg("max_output_tokens", MAX_TOKENS_DEFAULT)
    if max_tokens:
        chat["max_tokens"] = max_tokens
    if (ctx := _cfg("max_position_embeddings", MAX_POS_DEFAULT)):
        chat["max_position_embeddings"] = ctx

    if (temp := request_body.get("temperature")) is not None:
        chat["temperature"] = temp
    elif (cfg_temp := _cfg("temperature")) is not None:
        chat["temperature"] = cfg_temp
    if (top_p := request_body.get("top_p")) is not None:
        chat["top_p"] = top_p
    elif (cfg_top_p := _cfg("top_p")) is not None:
        chat["top_p"] = cfg_top_p

    # thinking 控制：默认关闭（避免 reasoning_content 400 错误），
    # 开启后配合推理缓存实现跨轮思考连续性。
    if not thinking_enabled:
        chat["thinking"] = {"type": "disabled"}
    tools = request_body.get("tools") or []
    if tools:
        chat["tools"] = _anthropic_tools_to_chat(tools)
        chat["tool_choice"] = "auto"

    return chat, session_id


# ═══════════════════════════════════════════════════════════
# 响应转换：DeepSeek Chat Completions → Anthropic Messages
# ═══════════════════════════════════════════════════════════

_FINISH_MAP = {
    "stop": "end_turn", "length": "max_tokens",
    "tool_calls": "tool_use", "content_filter": "end_turn",
}

def _ds_finish_to_claude(fs): return _FINISH_MAP.get(fs or "", "end_turn")

def _ensure_tool_use_id(tid):
    if tid and tid.startswith("toolu_"): return tid
    return _make_claude_id("toolu")


def chat_to_anthropic(chat_response: dict, upstream_model: str) -> dict:
    choices = chat_response.get("choices", [])
    msg_id = _make_claude_id()
    blocks = []
    has_tc = False
    reasoning_text = ""

    if choices:
        ch = choices[0]
        finish = ch.get("finish_reason", "stop")
        msg = ch.get("message", {})
        ds_text = msg.get("content", "") or ""
        reasoning_text = msg.get("reasoning_content", "") or ""

        strip = _cfg("strip_thinking", True)
        if reasoning_text and not strip:
            blocks.append({"type": "text", "text": reasoning_text})
        if ds_text:
            blocks.append({"type": "text", "text": ds_text})

        for tc in msg.get("tool_calls") or []:
            has_tc = True
            func = tc.get("function", {})
            try:
                inp = json.loads(func.get("arguments", "{}"))
            except json.JSONDecodeError:
                inp = {"_raw": func.get("arguments", "")}
            blocks.append({"type": "tool_use", "id": _ensure_tool_use_id(tc.get("id", "")),
                           "name": func.get("name", ""), "input": inp})
    else:
        finish = "stop"

    if not blocks:
        blocks = [{"type": "text", "text": ""}]

    usage = chat_response.get("usage", {})
    return {
        "id": msg_id, "type": "message", "role": "assistant", "model": upstream_model,
        "content": blocks,
        "stop_reason": "tool_use" if has_tc else _ds_finish_to_claude(finish),
        "stop_sequence": None,
        "usage": {"input_tokens": usage.get("prompt_tokens", 0),
                   "output_tokens": usage.get("completion_tokens", 0)},
    }, reasoning_text


# ═══════════════════════════════════════════════════════════
# 流式响应：DeepSeek SSE → Anthropic SSE
# ═══════════════════════════════════════════════════════════

async def _stream_anthropic_from_chat(chat_request: dict, session_id: str,
                                       upstream_url: str, auth_headers: dict,
                                       client: httpx.AsyncClient):
    msg_id = _make_claude_id()
    upstream_model = chat_request.get("model", "deepseek-v4-pro")
    started = False
    total_usage = {}
    finish_reason = "end_turn"
    strip = _cfg("strip_thinking", True)
    pending_tc: dict[int, dict] = {}
    reasoning_buf = ""

    def _emit(ev, d): return f"event: {ev}\ndata: {json.dumps(d)}\n\n"

    try:
        async with client.stream("POST", upstream_url, json=chat_request, headers=auth_headers) as up:
            if up.status_code != 200:
                body_str = (await up.aread()).decode()[:2000]
                record_error(up.status_code)
                record_upstream_error(body_str)
                log_error(f"Claude stream {up.status_code}: {body_str}")
                yield _emit("error", {"type": "error",
                    "error": {"type": "api_error", "message": body_str}})
                return

            async for line in up.aiter_lines():
                if not line.startswith("data: "): continue
                data_str = line[6:]
                if data_str == "[DONE]": break

                try: delta = json.loads(data_str)
                except json.JSONDecodeError: continue

                if delta.get("usage"): total_usage = delta["usage"]
                choices = delta.get("choices", [])
                if not choices: continue

                d = choices[0].get("delta", {})
                content_delta = d.get("content", "") or ""
                reasoning_delta = d.get("reasoning_content", "") or ""
                if choices[0].get("finish_reason"):
                    finish_reason = choices[0]["finish_reason"]

                if not started:
                    started = True
                    yield _emit("message_start", {"type": "message_start",
                        "message": {"id": msg_id, "type": "message", "role": "assistant",
                                     "model": upstream_model, "content": [],
                                     "usage": {"input_tokens": 0}}})
                    yield _emit("content_block_start", {"type": "content_block_start",
                        "index": 0, "content_block": {"type": "text", "text": ""}})

                if reasoning_delta:
                    reasoning_buf += reasoning_delta
                    if not strip:
                        yield _emit("content_block_delta", {"type": "content_block_delta",
                            "index": 0, "delta": {"type": "text_delta", "text": reasoning_delta}})

                if content_delta:
                    yield _emit("content_block_delta", {"type": "content_block_delta",
                        "index": 0, "delta": {"type": "text_delta", "text": content_delta}})

                for tc in d.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    if idx not in pending_tc:
                        pending_tc[idx] = {"id": tc.get("id", ""), "name": "", "arguments": ""}
                    cur = pending_tc[idx]
                    if tc.get("id"): cur["id"] = tc["id"]
                    func = tc.get("function", {})
                    if func.get("name"): cur["name"] = func["name"]
                    if func.get("arguments"): cur["arguments"] += func["arguments"]

            yield _emit("content_block_stop", {"type": "content_block_stop", "index": 0})

            has_tc = len(pending_tc) > 0
            bi = 1
            for ti in sorted(pending_tc.keys()):
                tc = pending_tc[ti]
                tcid = _ensure_tool_use_id(tc.get("id", ""))
                targs = tc.get("arguments", "{}")
                yield _emit("content_block_start", {"type": "content_block_start",
                    "index": bi, "content_block": {"type": "tool_use", "id": tcid,
                        "name": tc.get("name", ""), "input": {}}})
                if targs:
                    yield _emit("content_block_delta", {"type": "content_block_delta",
                        "index": bi, "delta": {"type": "input_json_delta", "partial_json": targs}})
                yield _emit("content_block_stop", {"type": "content_block_stop", "index": bi})
                bi += 1

            stop_reason = "tool_use" if has_tc else _ds_finish_to_claude(finish_reason)
            yield _emit("message_delta", {"type": "message_delta",
                "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                "usage": {"output_tokens": total_usage.get("completion_tokens", 0)}})
            yield _emit("message_stop", {"type": "message_stop"})

            # ── 缓存推理内容 ──
            if reasoning_buf:
                cache_reasoning("claude", session_id, reasoning_buf[:8000])
                cache_reasoning("claude", "recent", reasoning_buf[:8000])
                logger.info(f"Claude cached reasoning for {session_id} ({len(reasoning_buf)} chars)")

    except (httpx.TimeoutException, httpx.ConnectError) as e:
        record_error(500)
        yield _emit("error", {"type": "error",
            "error": {"type": "api_error", "message": str(e)}})


def _get_upstream() -> str:
    return f"{_cfg('deepseek_base', 'https://api.deepseek.com')}/v1/chat/completions"

def _get_auth_headers() -> dict:
    key = _cfg("deepseek_key", "") or config.get("deepseek_key", "")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


# ═══════════════════════════════════════════════════════════
# 路由处理函数
# ═══════════════════════════════════════════════════════════

async def claude_messages(request: Request):
    record_request()
    try: body = await request.json()
    except json.JSONDecodeError: raise HTTPException(status_code=400, detail="Invalid JSON body")

    stream = body.get("stream", False)
    chat_request, session_id = anthropic_to_chat(body)

    if stream:
        return StreamingResponse(
            _stream_anthropic_from_chat(chat_request, session_id,
                                         _get_upstream(), _get_auth_headers(),
                                         await get_http_client()),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"})

    client = await get_http_client()
    try:
        resp = await client.post(_get_upstream(), json=chat_request, headers=_get_auth_headers())
    except httpx.TimeoutException: raise HTTPException(status_code=504, detail="Upstream timeout")
    except httpx.ConnectError as e: raise HTTPException(status_code=502, detail=str(e))

    if resp.status_code != 200:
        record_error(resp.status_code)
        record_upstream_error(resp.text[:2000])
        log_error(f"Claude upstream {resp.status_code}: {resp.text[:500]}")
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    chat_data = resp.json()
    claude_response, reasoning_text = chat_to_anthropic(chat_data, chat_request.get("model", "deepseek-v4-pro"))

    # 缓存推理内容
    if reasoning_text:
        cache_reasoning("claude", session_id, reasoning_text[:8000])
        cache_reasoning("claude", "recent", reasoning_text[:8000])

    return JSONResponse(content=claude_response)


async def claude_models():
    return JSONResponse({
        "data": [{"id": _cfg("default_model", "deepseek-v4-pro"), "object": "model",
                   "created": 1750000000, "owned_by": "deepseek"}],
    })
