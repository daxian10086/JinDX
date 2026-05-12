"""OpenAI Responses API ↔ DeepSeek Chat Completions 协议翻译。

纯函数层，无副作用。所有配置通过参数传入。
"""

import json
import logging
import time
import uuid

from .config import config, DEFAULT_MODEL, DEFAULT_REASONING_EFFORT
from .cache import get_cached_reasoning, get_session_id
from .web_fetch import has_urls_in_messages, prefetch_urls_into_messages

logger = logging.getLogger(__name__)


def _make_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


def _normalize_role(role: str) -> str:
    """映射 OpenAI 特有角色到 DeepSeek 接受的角色。"""
    if role == "developer":
        return "system"
    return role


def _fix_tool_message_ordering(messages: list) -> list:
    """确保每个 assistant tool_calls 消息后跟随匹配的 tool 消息。

    DeepSeek 要求：assistant(tool_calls=[A,B]) -> tool(A) -> tool(B)。
    Codex 可能以稍不同的顺序发送条目，这里做重排。
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
            # 孤立 tool 消息 — 附加到最后一个包含 tool_calls 的 assistant
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
    """当 thinking 启用时，确保所有 assistant 消息包含 reasoning_content 字段。

    仅在存在缓存数据时才填充 reasoning_content。无缓存数据时跳过填充，
    避免给消息增加 reasoning_content="" 空字段破坏 DeepSeek prompt cache。
    """
    if not cached_reasoning:
        return

    cache_idx = 0
    cache_used = set()

    turn_start = 0
    for i, msg in enumerate(messages):
        if msg.get("role") == "assistant":
            if msg.get("reasoning_content"):
                continue
            # 在同回合的前一个 assistant 消息中查找 reasoning
            for j in range(i - 1, turn_start - 1, -1):
                mj = messages[j]
                if mj.get("role") == "assistant" and mj.get("reasoning_content"):
                    msg["reasoning_content"] = mj["reasoning_content"]
                    break
            # 如果仍然缺失，尝试使用缓存
            if not msg.get("reasoning_content"):
                while cache_idx < len(cached_reasoning):
                    if cache_idx not in cache_used:
                        msg["reasoning_content"] = cached_reasoning[cache_idx]
                        cache_used.add(cache_idx)
                        break
                    cache_idx += 1
        elif msg.get("role") not in ("tool",):
            turn_start = i + 1


# ── 请求转换：Responses → Chat Completions ─────────────────────────

def _extract_message_items(data: dict) -> list:
    """从 Responses API 请求体中提取 DeepSeek 格式的消息列表。"""
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


def _convert_tools(tools: list) -> list:
    """将 OpenAI 格式的 tools 转换为 DeepSeek 格式。"""
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


def map_model(name: str) -> str:
    """将请求中的模型名映射到 DeepSeek 模型名。"""
    if not name:
        return config.get("default_model", DEFAULT_MODEL)
    low = name.lower()
    if "deepseek" in low:
        return name
    mapping = config.get("model_mapping", {})
    if name in mapping:
        return mapping[name]
    if low in mapping:
        return mapping[low]
    return config.get("default_model", DEFAULT_MODEL)


def responses_to_chat(data: dict, prefetch_urls: bool = True) -> dict:
    """将 OpenAI Responses API 请求体转换为 DeepSeek Chat Completions 格式。

    参数：
        prefetch_urls: 是否同步预取 URL。streaming 路径应传 False 以在线程池中执行。
    """
    messages = _extract_message_items(data)

    if not messages:
        prompt = data.get("prompt", "")
        if prompt:
            messages = [{"role": "user", "content": prompt}]

    if not messages:
        messages = [{"role": "user", "content": ""}]

    messages = _fix_tool_message_ordering(messages)

    session_id = get_session_id(data)
    cached_reasoning = get_cached_reasoning("codex", session_id)
    if cached_reasoning:
        reasoning_idx = 0
        for msg in messages:
            if msg.get("role") == "assistant":
                if reasoning_idx < len(cached_reasoning):
                    msg["reasoning_content"] = cached_reasoning[reasoning_idx]
                reasoning_idx += 1
        logger.info(f"Attached reasoning_content to assistant messages (used {min(reasoning_idx, len(cached_reasoning))} entries)")

    _ensure_assistant_reasoning(messages, cached_reasoning)

    # tool-use 提示仅首轮注入（避免每轮修改 system 消息破坏 prompt cache）
    tools = data.get("tools")
    has_history = any(m.get("role") in ("assistant", "tool") for m in messages)
    if tools and not has_history and config.get("tool_use_enforcement", True):
        prompt = config.get("tool_use_prompt", "")
        if prompt and messages and messages[0].get("role") == "system":
            if prompt not in messages[0]["content"]:
                messages[0]["content"] = prompt + "\n\n" + messages[0]["content"]
        elif prompt:
            messages.insert(0, {"role": "system", "content": prompt})
        logger.info("Injected tool-use enforcement prompt (first turn)")

    tools = data.get("tools") or []
    if prefetch_urls and not has_history and has_urls_in_messages(messages):
        prefetch_urls_into_messages(messages)
        logger.info("Pre-fetched URLs into message context (first turn)")

    chat = {
        "model": map_model(data.get("model", DEFAULT_MODEL)),
        "messages": messages,
        "stream": data.get("stream", False),
    }

    if data.get("max_output_tokens"):
        chat["max_tokens"] = data["max_output_tokens"]
    elif (cfg_tokens := config.get("max_output_tokens")):
        chat["max_tokens"] = cfg_tokens
    if (cfg_ctx := config.get("max_position_embeddings")):
        chat["max_position_embeddings"] = cfg_ctx
    if data.get("temperature") is not None:
        chat["temperature"] = data["temperature"]
    elif (cfg_temp := config.get("temperature")) is not None:
        chat["temperature"] = cfg_temp
    if data.get("top_p") is not None:
        chat["top_p"] = data["top_p"]
    elif (cfg_top_p := config.get("top_p")) is not None:
        chat["top_p"] = cfg_top_p
    if data.get("stop"):
        chat["stop"] = data["stop"]

    for key in ("max_tokens", "frequency_penalty", "presence_penalty", "max_completion_tokens"):
        val = data.get(key)
        if val is not None and key not in chat:
            if key in ("max_tokens", "max_completion_tokens"):
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
    if "reasoning_effort" not in chat and (cfg_re := config.get("reasoning_effort")):
        chat["reasoning_effort"] = cfg_re

    if tools:
        tool_names = [t.get("function", t).get("name", t.get("type", "?")) for t in tools]
        logger.info(f"Tool names ({len(tools)}): {tool_names}")
        converted = _convert_tools(tools)
        logger.info(f"Converted tool names ({len(converted)}): {[c.get('function',{}).get('name','?') for c in converted]}")
        chat["tools"] = converted
        chat["tool_choice"] = data.get("tool_choice", "auto")

    # thinking 默认关闭，避免 reasoning_content 注入后 DeepSeek 要求
    # 后续请求也带回 reasoning_content 导致 400 错误。
    # 同时去掉 reasoning_effort，因为 thinking=disabled 与 reasoning_effort 冲突。
    if "thinking" not in chat:
        chat["thinking"] = {"type": "disabled"}
        chat.pop("reasoning_effort", None)

    return chat


# ── 响应转换：Chat Completions → Responses ─────────────────────────

def chat_to_responses(chat_data: dict, model: str) -> dict:
    """将 DeepSeek Chat Completions 响应转换为 OpenAI Responses 格式。"""
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


def sse_event(event_type: str, **kwargs) -> str:
    """构建 SSE 格式的事件 JSON。"""
    payload = {"type": event_type}
    payload.update(kwargs)
    return json.dumps(payload, ensure_ascii=False)
