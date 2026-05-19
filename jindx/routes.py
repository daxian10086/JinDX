"""HTTP、SSE 和 WebSocket API 路由。"""

import asyncio
import json
import logging
import time

import httpx
from fastapi import HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse

from .config import config, DEFAULT_MODEL
from .stats import (
    record_request, record_codex_request, record_claude_request, record_error, record_upstream_error, log_error,
    increment_active_streams, decrement_active_streams,
)
from .protocol import (
    map_model, responses_to_chat, chat_to_responses, sse_event, _make_id,
)
from .codex import handle_codex_rpc
from .cache import cache_reasoning, get_session_id

logger = logging.getLogger(__name__)

# ── 共享 HTTP 客户端池 ──────────────────────────────────────────────

_http_client: httpx.AsyncClient | None = None
_http_client_lock = asyncio.Lock()


async def get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        async with _http_client_lock:
            # 双重检查：其他协程可能已创建
            if _http_client is not None and not _http_client.is_closed:
                return _http_client
            _http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(300.0),
                trust_env=False,
                limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
            )
    return _http_client


def _get_auth_headers() -> dict:
    return {
        "Authorization": f"Bearer {config.get('deepseek_key', '')}",
        "Content-Type": "application/json",
    }


def _get_client_auth_headers(request: Request) -> dict:
    """透传请求头：优先用客户端自带的 API Key，没有则用代理的 Key。"""
    headers = {"Content-Type": "application/json"}
    client_auth = request.headers.get("Authorization", "")
    if client_auth:
        headers["Authorization"] = client_auth
    else:
        headers["Authorization"] = f"Bearer {config.get('deepseek_key', '')}"
    return headers


def _get_upstream() -> str:
    return f"{config.get('deepseek_base', 'https://api.deepseek.com')}/v1/chat/completions"


def _maybe_map_model(name: str) -> str:
    """仅映射已知的非 DeepSeek 模型名（如 gpt-*），DeepSeek 原生名不变。"""
    if not name:
        return config.get("default_model", DEFAULT_MODEL)
    low = name.lower()
    if "deepseek" in low:
        return name  # DeepSeek 原生模型，不做替换
    mapping = config.get("model_mapping", {})
    if name in mapping:
        return mapping[name]
    if low in mapping:
        return mapping[low]
    return name  # 未知模型，保留原样


# ── Chat Completions 透传（透明通道，不做协议翻译）──────────────────

async def chat_completions(request: Request):
    record_codex_request()
    try:
        body = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    body["model"] = _maybe_map_model(body.get("model", ""))
    auth_headers = _get_client_auth_headers(request)
    stream = body.get("stream", False)

    if stream:
        return StreamingResponse(
            _stream_chat(body, auth_headers),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    client = await get_http_client()
    try:
        resp = await client.post(_get_upstream(), json=body, headers=auth_headers)
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Upstream timeout")
    except httpx.ConnectError as e:
        raise HTTPException(status_code=502, detail=str(e))

    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return JSONResponse(content=resp.json())


async def _stream_chat(body: dict, auth_headers: dict):
    body["model"] = _maybe_map_model(body.get("model", ""))
    client = await get_http_client()
    try:
        async with client.stream("POST", _get_upstream(), json=body, headers=auth_headers) as resp:
            if resp.status_code != 200:
                body_text = await resp.aread()
                body_str = body_text.decode()[:2000]
                logger.error(f"DeepSeek chat/stream {resp.status_code}: {body_str}")
                raise HTTPException(status_code=resp.status_code, detail=body_str)
            async for line in resp.aiter_lines():
                yield line + "\n"
    except (httpx.TimeoutException, httpx.ConnectError) as e:
        logger.error(f"Chat stream error: {e}")
        yield f"data: {json.dumps({'error': {'message': str(e), 'type': 'upstream_error'}})}\n\n"


# ── HTTP Responses 端点 ─────────────────────────────────────────────

async def responses_http(request: Request):
    record_codex_request()
    try:
        body = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    stream = body.get("stream", False)
    model = map_model(body.get("model", DEFAULT_MODEL))

    # 跳过空输入 — DeepSeek 在空用户消息上会挂起
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
    client = await get_http_client()
    try:
        resp = await client.post(_get_upstream(), json=chat_request, headers=_get_auth_headers())
        if resp.status_code != 200:
            record_error(resp.status_code)
            record_upstream_error(resp.text[:2000])
            log_error(f"DeepSeek non-stream {resp.status_code}: {resp.text[:200]}")
            logger.error(f"DeepSeek non-stream {resp.status_code}: {resp.text[:2000]}")
            logger.error(f"Chat request: {json.dumps(chat_request, ensure_ascii=False)[:3000]}")
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        chat_data = resp.json()
        reasoning = chat_data.get("choices", [{}])[0].get("message", {}).get("reasoning_content", "")
        if reasoning:
            cache_reasoning("codex", get_session_id(body), reasoning)
        return JSONResponse(content=chat_to_responses(chat_data, model))
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Upstream timeout")
    except httpx.ConnectError as e:
        raise HTTPException(status_code=502, detail=str(e))


# ── SSE 流式 ────────────────────────────────────────────────────────

async def _stream_responses_sse(body: dict):
    increment_active_streams()
    try:
        async for event in _stream_responses_sse_inner(body):
            yield event
    except GeneratorExit:
        raise
    except Exception as e:
        logger.exception(f"SSE stream unhandled error: {e}")
        yield f"data: {json.dumps({'type': 'error', 'error': {'message': str(e)}})}\n\n"
    finally:
        decrement_active_streams()


async def _stream_responses_sse_inner(body: dict):
    model = map_model(body.get("model", DEFAULT_MODEL))
    resp_id = _make_id("resp")
    msg_id = _make_id("msg")
    output_index = 0
    content_index = 0
    sent_text_parts = False
    usage = {}
    tool_calls_by_index: dict[int, dict] = {}

    # 立即发送初始事件，防止长上下文/大推理场景下
    # responses_to_chat() 和上游连接建立前 Codex 判定超时断连。
    yield f"event: response.created\ndata: {sse_event('response.created', response={'id': resp_id, 'object': 'response', 'created_at': int(time.time()), 'status': 'in_progress', 'model': model, 'output': []})}\n\n"
    yield f"event: response.in_progress\ndata: {sse_event('response.in_progress', response={'id': resp_id, 'object': 'response', 'status': 'in_progress', 'model': model})}\n\n"
    yield f"event: response.output_item.added\ndata: {sse_event('response.output_item.added', output_index=output_index, item={'id': msg_id, 'type': 'message', 'role': 'assistant', 'status': 'in_progress', 'content': []})}\n\n"

    chat_request = responses_to_chat(body)
    chat_request["stream"] = True

    client = await get_http_client()
    try:
        async with client.stream("POST", _get_upstream(), json=chat_request, headers=_get_auth_headers()) as upstream:
            if upstream.status_code != 200:
                body_text = await upstream.aread()
                body_str = body_text.decode()[:2000]
                record_error(upstream.status_code)
                record_upstream_error(body_str)
                log_error(f"DeepSeek {upstream.status_code}: {body_str}")
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

                if reasoning_delta:
                    if not reasoning_buf and not content_buf:
                        yield f"event: response.content_part.added\ndata: {sse_event('response.content_part.added', item_id=msg_id, output_index=output_index, content_index=content_index, part={'type': 'reasoning_text', 'text': '', 'summary': []})}\n\n"
                        sent_text_parts = True
                    reasoning_buf += reasoning_delta
                    yield f"event: response.reasoning_text.delta\ndata: {sse_event('response.reasoning_text.delta', item_id=msg_id, output_index=output_index, content_index=content_index, delta=reasoning_delta)}\n\n"

                if content_delta:
                    if not content_buf and not reasoning_buf and not sent_text_parts:
                        yield f"event: response.content_part.added\ndata: {sse_event('response.content_part.added', item_id=msg_id, output_index=output_index, content_index=content_index, part={'type': 'output_text', 'text': ''})}\n\n"
                        sent_text_parts = True
                    elif reasoning_buf and not content_buf:
                        content_index += 1
                        yield f"event: response.content_part.added\ndata: {sse_event('response.content_part.added', item_id=msg_id, output_index=output_index, content_index=content_index, part={'type': 'output_text', 'text': ''})}\n\n"
                    content_buf += content_delta
                    yield f"event: response.output_text.delta\ndata: {sse_event('response.output_text.delta', item_id=msg_id, output_index=output_index, content_index=content_index, delta=content_delta)}\n\n"

                # 累积 tool call deltas
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

            # ── 发送文本消息 output items ──────────────────────
            final_content = []
            display_text = content_buf or reasoning_buf
            if display_text:
                final_content.append({"type": "output_text", "text": display_text, "annotations": []})

            if sent_text_parts:
                yield f"event: response.content_part.done\ndata: {sse_event('response.content_part.done', item_id=msg_id, output_index=output_index, content_index=content_index, part={'type': 'output_text' if content_buf else 'reasoning_text', 'text': display_text})}\n\n"

            yield f"event: response.output_item.done\ndata: {sse_event('response.output_item.done', output_index=output_index, item={'id': msg_id, 'type': 'message', 'role': 'assistant', 'status': 'completed', 'content': final_content})}\n\n"

            # ── 发送 function_call output items ──────────────────
            all_output_items = [
                {"id": msg_id, "type": "message", "role": "assistant", "status": "completed", "content": final_content}
            ]

            for tc_idx in sorted(tool_calls_by_index.keys()):
                tc = tool_calls_by_index[tc_idx]
                tc_id = tc["id"] or _make_id("call")
                tc_name = tc["name"] or ""
                tc_args = tc["arguments"] or "{}"
                tc_out_idx = output_index + tc_idx + 1

                yield f"event: response.output_item.added\ndata: {sse_event('response.output_item.added', output_index=tc_out_idx, item={'id': tc_id, 'type': 'function_call', 'name': tc_name, 'call_id': tc_id, 'status': 'in_progress', 'arguments': ''})}\n\n"
                yield f"event: response.function_call_arguments.delta\ndata: {sse_event('response.function_call_arguments.delta', item_id=tc_id, output_index=tc_out_idx, delta=tc_args)}\n\n"
                yield f"event: response.function_call_arguments.done\ndata: {sse_event('response.function_call_arguments.done', item_id=tc_id, output_index=tc_out_idx, arguments=tc_args)}\n\n"
                yield f"event: response.output_item.done\ndata: {sse_event('response.output_item.done', output_index=tc_out_idx, item={'id': tc_id, 'type': 'function_call', 'name': tc_name, 'call_id': tc_id, 'status': 'completed', 'arguments': tc_args})}\n\n"

                all_output_items.append({
                    "id": tc_id,
                    "type": "function_call",
                    "call_id": tc_id,
                    "name": tc_name,
                    "arguments": tc_args,
                    "status": "completed",
                })

            yield f"event: response.completed\ndata: {sse_event('response.completed', response={'id': resp_id, 'object': 'response', 'created_at': int(time.time()), 'status': 'completed', 'model': model, 'output': all_output_items, 'output_text': display_text, 'usage': {'input_tokens': usage.get('prompt_tokens', 0), 'output_tokens': usage.get('completion_tokens', 0), 'total_tokens': usage.get('total_tokens', 0)}})}\n\n"
            yield "data: [DONE]\n\n"

            if reasoning_buf:
                cache_reasoning("codex", get_session_id(body), reasoning_buf)
            return

    except GeneratorExit:
        raise
    except httpx.ReadError as e:
        record_error(500)
        logger.warning(f"SSE stream read error at eof (likely upstream closed early): {e}")
        yield f"data: {json.dumps({'type': 'error', 'error': {'message': 'Upstream closed connection early', 'code': 500}})}\n\n"
        return
    except (httpx.TimeoutException, httpx.ConnectError) as e:
        record_error(500)
        log_error(f"SSE stream error: {e}")
        yield f"data: {json.dumps({'type': 'error', 'error': {'message': str(e)}})}\n\n"
        return
    except Exception as e:
        record_error(500)
        logger.exception(f"SSE stream unexpected error: {e}")
        return



# ── WebSocket 处理 ─────────────────────────────────────────────────

async def handle_ws_session(ws: WebSocket):
    """WebSocket
    """
    record_codex_request()
    while True:
        try:
            raw = await ws.receive_text()
        except WebSocketDisconnect:
            return
        except Exception as e:
            logger.error(f"WS receive error: {e}")
            return

        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            body = {}

        # 处理 Codex 内部 RPC 方法
        method = body.get("method", "")
        if method:
            rpc_response = handle_codex_rpc(method, body.get("params", {}))
            if rpc_response is not None:
                await ws.send_json(rpc_response)
            if method.startswith("account/") or method.startswith("config/") or method.startswith("modelProvider/"):
                logger.info(f"WS handled RPC: {method}")
                continue

        # 跳过空输入初始化请求
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
                continue

        await _process_ws_request(ws, body)
        return


async def _process_ws_request(ws: WebSocket, body: dict):
    """处理单个 Responses API 请求的 WebSocket 流式响应。"""
    model = map_model(body.get("model", DEFAULT_MODEL))
    msg_id = _make_id("msg")
    resp_id = _make_id("resp")
    output_index = 0
    content_index = 0

    # 在等待上游响应之前立即发送初始事件，防止长上下文场景下
    # WebSocket 静默时间过长被 Codex 判定为断连。
    # 必须放在 responses_to_chat() 之前，因为该函数涉及 URL 预取、
    # 消息重排等阻塞操作，大上下文下可能耗时 200ms+。
    await ws.send_json({
        "type": "response.created",
        "response": {
            "id": resp_id, "object": "response",
            "created_at": int(time.time()),
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

    chat_request = responses_to_chat(body)
    chat_request["stream"] = True

    sent_text_parts = False
    content_buf = ""
    reasoning_buf = ""
    usage = {}
    tool_calls_by_index: dict[int, dict] = {}

    client = await get_http_client()
    try:
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

                # 累积 tool call deltas
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

            # ── 发送文本消息 output items ─────────────────────
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

            # ── 发送 function_call output items ──────────────
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
                cache_reasoning("codex", get_session_id(body), reasoning_buf)
            logger.info(f"WS done: reasoning={len(reasoning_buf)}B, content={len(content_buf)}B, "
                        f"tool_calls={len(tool_calls_by_index)}, sent_parts={sent_text_parts}")

    except (httpx.TimeoutException, httpx.ConnectError) as e:
        logger.error(f"WS stream error: {e}")
        await ws.send_json({"type": "error", "error": {"message": str(e)}})


# ── 模型列表 & 健康检查 ──────────────────────────────────────────

async def list_models():
    default_model = config.get("default_model", DEFAULT_MODEL)
    return JSONResponse({
        "object": "list",
        "data": [
            {"id": "gpt-5.5", "object": "model", "created": 1750000000, "owned_by": "system"},
            {"id": "gpt-5", "object": "model", "created": 1750000000, "owned_by": "system"},
            {"id": default_model, "object": "model", "created": 1750000000, "owned_by": "deepseek"},
            {"id": "deepseek-v4-flash", "object": "model", "created": 1750000000, "owned_by": "deepseek"},
        ],
    })


async def health():
    try:
        client = await get_http_client()
        r = await client.get(f"{config.get('deepseek_base', 'https://api.deepseek.com')}/v1/models", headers=_get_auth_headers())
        upstream = "ok" if r.status_code < 500 else "error"
    except (httpx.TimeoutException, httpx.ConnectError):
        upstream = "unreachable"

    return {
        "status": "ok",
        "target": _get_upstream(),
        "upstream": upstream,
        "cache": {
            "backend": "file",
        },
    }

# ── Compact（对话压缩）端点 ─────────────────────────────────────────


def _to_content_parts(text):
    return [{"type": "input_text", "text": text}]

def _normalize_items(items):
    """Convert items with plain-string content to content-part arrays."""
    result = []
    for item in items:
        if not isinstance(item, dict):
            result.append(item)
            continue
        item = dict(item)
        cnt = item.get("content")
        if isinstance(cnt, str):
            item["content"] = _to_content_parts(cnt)
        result.append(item)
    return result

def _format_conversation_for_compact(inp: list) -> str:
    """将 input 列表格式化为可读对话文本用于摘要。"""
    lines = []
    for item in inp:
        if isinstance(item, dict):
            role = item.get("role", "")
            itype = item.get("type", "")
            content = item.get("content", "")

            if itype == "function_call":
                name = item.get("name", "")
                args = item.get("arguments", "")
                args_short = args[:200] + "..." if len(args) > 200 else args
                lines.append(f"[工具调用] {name}({args_short})")
            elif itype == "function_call_output":
                call_id = item.get("call_id", "")
                output = item.get("output", "")
                output_short = str(output)[:300] + "..." if len(str(output)) > 300 else str(output)
                lines.append(f"[工具结果 {call_id}] {output_short}")
            elif role or itype == "message":
                role_label = {"user": "用户", "assistant": "助手", "developer": "系统", "system": "系统", "tool": "工具"}.get(role, role)
                if isinstance(content, list):
                    text_parts = []
                    for part in content:
                        if isinstance(part, dict):
                            pt = part.get("type", "")
                            if pt in ("output_text", "input_text", "text"):
                                t = part.get("text", "")
                                if len(t) > 500:
                                    t = t[:500] + "..."
                                text_parts.append(t)
                            elif pt == "reasoning_text":
                                t = part.get("text", "")
                                if len(t) > 200:
                                    t = t[:200] + "..."
                                text_parts.append(f"[思考: {t}]")
                    content = "\n".join(text_parts)
                elif isinstance(content, str) and len(content) > 800:
                    content = content[:800] + "..."
                lines.append(f"[{role_label}] {content}")
            # 跳过其他类型
    return "\n\n".join(lines)

async def responses_compact(request: Request):
    """处理 OpenAI /v1/responses/compact 请求。

    Codex CLI 在上下文接近窗口上限时调用此端点，要求压缩/摘要历史对话。
    将 OpenAI compact 请求转为 DeepSeek 摘要请求，返回压缩后的 input。
    """
    try:
        body = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # 从请求中提取 input 和 instructions
    inp = body.get("input", [])
    instructions = body.get("instructions", body.get("system_message", ""))
    model_name = map_model(body.get("model", DEFAULT_MODEL))

    # 构建摘要请求：把历史对话发送给 DeepSeek 做压缩
    summary_messages = []
    if instructions:
        summary_messages.append({"role": "system", "content": instructions})

    # 将 input 列表转为消息格式用于摘要
    conv_text = _format_conversation_for_compact(inp)
    summary_messages.append({
        "role": "user",
        "content": (
            "请对以下对话历史进行压缩摘要。保留所有关键信息、决策和代码变更，"
            "但去除冗余的中间步骤和重复内容。用中文输出摘要：\n\n"
            + conv_text
        ),
    })

    chat_request = {
        "model": model_name,
        "messages": summary_messages,
        "stream": False,
    }
    if (cfg_tokens := config.get("max_output_tokens")):
        chat_request["max_tokens"] = cfg_tokens

    client = await get_http_client()
    try:
        resp = await client.post(_get_upstream(), json=chat_request, headers=_get_auth_headers())
        if resp.status_code != 200:
            logger.error(f"Compact summary failed: {resp.status_code} {resp.text[:500]}")
            # 降级：返回原始 input 只保留最后几条
            fallback = inp[-20:] if len(inp) > 20 else inp
            return JSONResponse({
                "output": _normalize_items(fallback),
                "compacted_input": _normalize_items(fallback),
            })
        chat_data = resp.json()
        summary_text = chat_data.get("choices", [{}])[0].get("message", {}).get("content", "")

        # 帮助函数：将 content 转为 input_text 数组格式（OpenAI Responses API 要求）
        # 构造压缩后的 input：instructions + 摘要 + 最近几轮原始消息保持上下文
        compacted = []
        # 保留 instructions
        if instructions:
            compacted.append({
                "type": "message",
                "role": "developer",
                "content": _to_content_parts(instructions),
            })
        # 添加摘要作为上下文
        compacted.append({
            "type": "message",
            "role": "developer",
            "content": _to_content_parts(f"[对话历史摘要]\n{summary_text}"),
        })
        # 保留最后 6 条原始消息（约 3 轮对话）以保证近期上下文不丢失
        keep_tail = min(6, len(inp))
        if keep_tail > 0:
            compacted.extend(_normalize_items(inp[-keep_tail:]))

        logger.info(f"Compact done: {len(inp)} items -> {len(compacted)} items, summary {len(summary_text)} chars")
        return JSONResponse({
            "output": compacted, "compacted_input": compacted,
        })
    except (httpx.TimeoutException, httpx.ConnectError) as e:
        logger.error(f"Compact upstream error: {e}")
        fallback = inp[-20:] if len(inp) > 20 else inp
        return JSONResponse({
            "output": _normalize_items(fallback),
            "compacted_input": _normalize_items(fallback),
        })


