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
from .stats import record_request, record_error, record_upstream_error, log_error

logger = logging.getLogger(__name__)

MAX_TOKENS_DEFAULT = 16384
MAX_POS_DEFAULT = 1000000


def _make_claude_id(prefix: str = "msg") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


def _cfg(key, default=None):
    """读取 Claude 专用配置项。"""
    return config.get(f"claude_{key}", config.get(key, default))


# ═══════════════════════════════════════════════════════════════════
# 请求转换：Anthropic Messages → DeepSeek Chat Completions
# ═══════════════════════════════════════════════════════════════════

def _anthropic_text_to_chat_content(text: str) -> str:
    """Anthropic 的 text content 直接转为字符串。"""
    return text


def _anthropic_content_to_chat_message(role: str, content) -> dict:
    """将 Anthropic 消息的 content（字符串或数组）转为 DeepSeek 消息格式。"""
    if isinstance(content, str):
        return {"role": role, "content": content}

    result = {"role": role, "content": ""}
    text_parts = []
    tool_calls = []

    for part in content:
        tp = part.get("type", "")
        if tp == "text":
            text_parts.append(part.get("text", ""))
        elif tp == "tool_use":
            tool_calls.append({
                "id": part.get("id", _make_claude_id("call")),
                "type": "function",
                "function": {
                    "name": part.get("name", ""),
                    "arguments": json.dumps(part.get("input", {}), ensure_ascii=False),
                },
            })
        elif tp == "tool_result":
            result["role"] = "tool"
            result["tool_call_id"] = part.get("tool_use_id", "")
            inner = part.get("content", "")
            if isinstance(inner, list):
                result["content"] = "".join(
                    c.get("text", "") if isinstance(c, dict) else str(c)
                    for c in inner
                )
            elif isinstance(inner, str):
                result["content"] = inner
            else:
                result["content"] = json.dumps(inner, ensure_ascii=False)
            return result

    if text_parts:
        result["content"] = "".join(text_parts)
    if tool_calls:
        result["content"] = result.get("content") or ""
        result["tool_calls"] = tool_calls

    return result


def _anthropic_tools_to_chat(tools: list) -> list:
    """将 Anthropic tools 格式转为 DeepSeek tools 格式。"""
    result = []
    for tool in tools:
        schema = tool.get("input_schema")
        params = {}
        if schema:
            params["type"] = schema.get("type", "object")
            if "properties" in schema:
                params["properties"] = schema["properties"]
            if "required" in schema:
                params["required"] = schema["required"]
        result.append({
            "type": "function",
            "function": {
                "name": tool.get("name", ""),
                "description": tool.get("description", ""),
                "parameters": params,
            },
        })
    return result


def anthropic_to_chat(request_body: dict) -> dict:
    """将 Anthropic Messages API 请求体转为 DeepSeek Chat Completions 格式。"""
    messages = []

    # Claude 的 system 是顶级字段，转为 DeepSeek 的 system 消息
    system_text = request_body.get("system", "")
    if system_text:
        if isinstance(system_text, list):
            system_text = "".join(
                c.get("text", "") if isinstance(c, dict) else str(c)
                for c in system_text
            )
        messages.append({"role": "system", "content": system_text})

    # 转换 messages
    for msg in request_body.get("messages", []):
        role = msg.get("role", "user")
        content = msg.get("content", "")
        converted = _anthropic_content_to_chat_message(role, content)
        messages.append(converted)

    chat = {
        "model": _cfg("default_model", "deepseek-v4-pro"),
        "messages": messages,
        "stream": request_body.get("stream", False),
    }

    # 推断 token 限制
    max_tokens = request_body.get("max_tokens", 0)
    if not max_tokens:
        max_tokens = _cfg("max_output_tokens", MAX_TOKENS_DEFAULT)
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

    if (effort := _cfg("reasoning_effort")):
        chat["reasoning_effort"] = effort

    tools = request_body.get("tools") or []
    if tools:
        chat["tools"] = _anthropic_tools_to_chat(tools)
        chat["tool_choice"] = "auto"

    return chat


# ═══════════════════════════════════════════════════════════════════
# 响应转换：DeepSeek Chat Completions → Anthropic Messages
# ═══════════════════════════════════════════════════════════════════

_FINISH_MAP = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "content_filter": "end_turn",
}


def _ds_finish_to_claude(ds_finish: str) -> str:
    return _FINISH_MAP.get(ds_finish or "", "end_turn")


def chat_to_anthropic(chat_response: dict, upstream_model: str) -> dict:
    """将 DeepSeek Chat Completions 响应转为 Anthropic Messages 格式（非流式）。"""
    choices = chat_response.get("choices", [])
    msg_id = _make_claude_id()
    content_blocks = []

    if choices:
        choice = choices[0]
        finish = choice.get("finish_reason", "stop")
        message = choice.get("message", {})

        ds_content = message.get("content", "") or ""
        reasoning = message.get("reasoning_content", "") or ""

        # 如果配置了 strip_thinking，跳过推理文本
        strip = _cfg("strip_thinking", True)
        if reasoning and not strip:
            content_blocks.append({"type": "text", "text": reasoning})
        elif reasoning:
            # 跳过 — reasoning_content 不回显
            pass

        if ds_content:
            content_blocks.append({"type": "text", "text": ds_content})

        # 转换 tool_calls
        tool_calls = message.get("tool_calls") or []
        for tc in tool_calls:
            func = tc.get("function", {})
            try:
                inp = json.loads(func.get("arguments", "{}"))
            except json.JSONDecodeError:
                inp = {"_raw": func.get("arguments", "")}
            content_blocks.append({
                "type": "tool_use",
                "id": tc.get("id", _make_claude_id("toolu")),
                "name": func.get("name", ""),
                "input": inp,
            })
    else:
        finish = "stop"

    usage = chat_response.get("usage", {})
    return {
        "id": msg_id,
        "type": "message",
        "role": "assistant",
        "model": upstream_model,
        "content": content_blocks if content_blocks else [{"type": "text", "text": ""}],
        "stop_reason": _ds_finish_to_claude(finish),
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }


# ═══════════════════════════════════════════════════════════════════
# 流式响应：DeepSeek SSE → Anthropic SSE
# ═══════════════════════════════════════════════════════════════════

async def _stream_anthropic_from_chat(chat_request: dict, upstream_url: str, auth_headers: dict, client: httpx.AsyncClient):
    """将 DeepSeek SSE 流转为 Anthropic SSE 流。"""
    import asyncio

    msg_id = _make_claude_id()
    upstream_model = chat_request.get("model", "deepseek-v4-pro")
    started = False
    content_index = 0
    total_usage = {}
    finish_reason = "end_turn"
    strip = _cfg("strip_thinking", True)

    try:
        async with client.stream("POST", upstream_url, json=chat_request, headers=auth_headers) as upstream:
            if upstream.status_code != 200:
                body_text = await upstream.aread()
                body_str = body_text.decode()[:2000]
                record_error(upstream.status_code, body_str)
                log_error(f"Claude stream {upstream.status_code}: {body_str}")
                err = json.dumps({
                    "type": "error",
                    "error": {"type": "api_error", "message": body_str},
                })
                yield f"event: error\ndata: {err}\n\n"
                return

            pending_text = ""
            pending_tool_calls: dict[int, dict] = {}

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

                if delta.get("usage"):
                    total_usage = delta["usage"]

                choices = delta.get("choices", [])
                if not choices:
                    continue

                d = choices[0].get("delta", {})
                content_delta = d.get("content", "") or ""
                reasoning_delta = d.get("reasoning_content", "") or ""

                if choices[0].get("finish_reason"):
                    finish_reason = choices[0]["finish_reason"]

                if not started:
                    started = True
                    yield (
                        f"event: message_start\n"
                        f"data: {json.dumps({'type': 'message_start', 'message': {'id': msg_id, 'type': 'message', 'role': 'assistant', 'model': upstream_model, 'content': [], 'usage': {'input_tokens': 0}}})}\n\n"
                    )
                    yield (
                        f"event: content_block_start\n"
                        f"data: {json.dumps({'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}})}\n\n"
                    )

                # DeepSeek 先发 reasoning_content 再发 content
                if reasoning_delta:
                    if not strip:
                        pending_text += reasoning_delta
                        yield (
                            f"event: content_block_delta\n"
                            f"data: {json.dumps({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': reasoning_delta}})}\n\n"
                        )

                if content_delta:
                    pending_text += content_delta
                    yield (
                        f"event: content_block_delta\n"
                        f"data: {json.dumps({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': content_delta}})}\n\n"
                    )

                # 累积 tool call deltas
                tc_deltas = d.get("tool_calls") or []
                for tc in tc_deltas:
                    idx = tc.get("index", 0)
                    if idx not in pending_tool_calls:
                        pending_tool_calls[idx] = {"id": tc.get("id", ""), "name": "", "arguments": ""}
                    cur = pending_tool_calls[idx]
                    if tc.get("id"):
                        cur["id"] = tc["id"]
                    func = tc.get("function", {})
                    if func.get("name"):
                        cur["name"] = func["name"]
                    if func.get("arguments"):
                        cur["arguments"] += func["arguments"]

            # 发送 content_block_stop
            yield (
                f"event: content_block_stop\n"
                f"data: {json.dumps({'type': 'content_block_stop', 'index': 0})}\n\n"
            )

            # 发送 message_delta
            stop_reason = _ds_finish_to_claude(finish_reason)
            yield (
                f"event: message_delta\n"
                f"data: {json.dumps({'type': 'message_delta', 'delta': {'stop_reason': stop_reason, 'stop_sequence': None}, 'usage': {'output_tokens': total_usage.get('completion_tokens', 0)}})}\n\n"
            )

            # 发送 message_stop
            yield f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'})}\n\n"

    except (httpx.TimeoutException, httpx.ConnectError) as e:
        record_error(500)
        err = json.dumps({"type": "error", "error": {"type": "api_error", "message": str(e)}})
        yield f"event: error\ndata: {err}\n\n"


# ═══════════════════════════════════════════════════════════════════
# HTTP 客户端 & 上游 URL
# ═══════════════════════════════════════════════════════════════════

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


def _get_upstream() -> str:
    """Claude 专用上游：优先使用 claude_deepseek_base。"""
    base = _cfg("deepseek_base", "https://api.deepseek.com")
    return f"{base}/v1/chat/completions"


def _get_auth_headers() -> dict:
    key = _cfg("deepseek_key", "")
    if not key:
        key = config.get("deepseek_key", "")
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


# ═══════════════════════════════════════════════════════════════════
# 路由处理函数
# ═══════════════════════════════════════════════════════════════════

async def claude_messages(request: Request):
    """处理 Claude Code 的 POST /v1/messages 请求。"""
    record_request()
    try:
        body = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    stream = body.get("stream", False)

    chat_request = anthropic_to_chat(body)

    if stream:
        return StreamingResponse(
            _stream_anthropic_from_chat(
                chat_request,
                _get_upstream(),
                _get_auth_headers(),
                await _get_http_client(),
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    client = await _get_http_client()
    try:
        resp = await client.post(_get_upstream(), json=chat_request, headers=_get_auth_headers())
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Upstream timeout")
    except httpx.ConnectError as e:
        raise HTTPException(status_code=502, detail=str(e))

    if resp.status_code != 200:
        record_error(resp.status_code)
        record_upstream_error(resp.text[:2000])
        log_error(f"Claude upstream {resp.status_code}: {resp.text[:500]}")
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    chat_data = resp.json()
    claude_response = chat_to_anthropic(chat_data, chat_request.get("model", "deepseek-v4-pro"))
    return JSONResponse(content=claude_response)


async def claude_models():
    """Claude Code 模型列表端点。"""
    return JSONResponse({
        "data": [
            {"id": _cfg("default_model", "deepseek-v4-pro"), "object": "model", "created": 1750000000, "owned_by": "deepseek"},
        ],
    })
