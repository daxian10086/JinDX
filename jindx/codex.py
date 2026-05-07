"""Codex CLI 适配：RPC 模拟、模型目录、分析桩。"""

import logging

from fastapi import Request
from fastapi.responses import JSONResponse

from .config import config, DEFAULT_MODEL

logger = logging.getLogger(__name__)


def handle_codex_rpc(method: str, params: dict) -> dict | None:
    """处理 Codex 通过 WebSocket 发送的内部 RPC 调用。"""
    # 速率限制 — 返回无限制
    if method == "account/rateLimits/read":
        return {
            "method": "account/rateLimits/updated",
            "params": {
                "rateLimits": [],
                "rateLimitsByLimitId": {},
            },
        }
    # 配置要求
    if method == "config/requirements/read":
        return {
            "method": "config/requirements/updated",
            "params": {"requirements": []},
        }
    # 模型提供商能力
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
    # 实验性功能 — 返回空
    if method == "experimentalFeatures/list":
        return {
            "method": "experimentalFeatures/updated",
            "params": {"features": []},
        }
    # 账户读取
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
    # 模型列表
    if method == "model/list":
        model_name = config.get("default_model", DEFAULT_MODEL)
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
    # 账户登录 — 不需要
    if method.startswith("account/login"):
        return {
            "method": "account/login/completed",
            "params": {"status": "authenticated", "account": {"id": "proxy-user"}},
        }
    # 账户已更新 — 忽略
    if method == "account/updated":
        return None
    # MCP server / resource / skills — 返回空列表
    if method.startswith("mcpServer/") or method.startswith("skills/") or method.startswith("device/"):
        return {"method": method.replace("read", "updated").replace("list", "updated"), "params": {}}
    # Catch-all for unknown RPC methods
    if "/read" in method or "/list" in method:
        return {"method": method.replace("/read", "/updated").replace("/list", "/updated"), "params": {}}
    return None


# ── Codex 模型目录入口 builder ────────────────────────────────────

def _make_model_entry(slug, display_name, description, priority, speed_tiers=None,
                      reasoning_level="medium", reasoning_levels=None):
    """构建 Codex 模型目录条目。"""
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


async def codex_models(request: Request):
    """返回 Codex 目录格式的模型列表。"""
    default_model = config.get("default_model", DEFAULT_MODEL)
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


async def codex_analytics():
    """接受并丢弃 Codex 遥测数据。"""
    return JSONResponse({"status": "ok"})


async def codex_plugins():
    """返回空推荐插件列表。"""
    return JSONResponse([])


async def codex_wham():
    return JSONResponse({"status": "ok"})


async def codex_backend_fallback(path: str):
    """未知 Codex 后端端点返回空成功。"""
    logger.debug(f"Codex backend fallback: /backend-api/{path}")
    return JSONResponse({"status": "ok"})
