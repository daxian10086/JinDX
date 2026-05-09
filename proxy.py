"""
Chat Completions + Responses API proxy for DeepSeek V4 (and compatible models).
- /v1/chat/completions -> passthrough
- /v1/responses -> convert to Chat Completions -> DeepSeek -> convert back
- Supports HTTP (SSE) and WebSocket streaming
- Supports HTTP CONNECT tunnel (for codex CLI https_proxy) with TLS termination
- Reasoning cache: stores previous thinking, injects into next request
"""

import asyncio
import logging
import os
import ssl
from urllib.parse import urlparse

import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect

from jindx.config import config, PROXY_PORT, CONNECT_PORT, TLS_PORT, ADMIN_PORT
from jindx.tunnel import ensure_certs, _run_connect_server, CERT_FILE, KEY_FILE
from jindx.admin import admin_app

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Chat-Responses-WebSocket Proxy")


# ── 转发代理路径中间件 ──────────────────────────────────────────
# cc-switch 将此服务器视为 HTTP 转发代理：它将完整目标 URL 作为请求路径
# 发送 URL 编码形式（例如 POST http%3A//127.0.0.1%3A8080/v1/chat/completions）。
# 此中间件检测这些路径，解码并重写 ASGI scope，以便 FastAPI 路由到正常处理器。

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


# ── 导入路由处理函数 ────────────────────────────────────────────

from jindx.routes import (
    chat_completions, responses_http, list_models, health,
    handle_ws_session,
)
from jindx.codex import (
    codex_models, codex_analytics, codex_plugins, codex_wham,
    codex_backend_fallback,
)


# ── WebSocket 端点包装 ──────────────────────────────────────────

async def _make_ws_endpoint(ws: WebSocket):
    await ws.accept()
    try:
        await handle_ws_session(ws)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        try:
            await ws.close()
        except Exception:
            pass


# ── 注册路由 ────────────────────────────────────────────────────

# Chat Completions 透传
app.post("/v1/chat/completions")(chat_completions)
app.post("/chat/completions")(chat_completions)

# Responses HTTP
app.post("/v1/responses")(responses_http)
app.post("/responses")(responses_http)
app.post("/backend-api/codex/responses")(responses_http)

# Responses WebSocket
app.websocket("/v1/responses")(_make_ws_endpoint)
app.websocket("/responses")(_make_ws_endpoint)
app.websocket("/backend-api/codex/responses")(_make_ws_endpoint)

# Models & health
app.get("/v1/models")(list_models)
app.get("/models")(list_models)
app.get("/health")(health)

# Codex backend API
app.get("/backend-api/codex/models")(codex_models)
app.get("/backend-api/models")(codex_models)
app.post("/backend-api/codex/analytics-events/events")(codex_analytics)
app.post("/backend-api/analytics-events/events")(codex_analytics)
app.get("/backend-api/plugins/featured")(codex_plugins)
app.post("/backend-api/wham/apps")(codex_wham)
app.api_route("/backend-api/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])(codex_backend_fallback)


# ── 启动 ────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Windows: asyncio.start_server + loop.start_tls 需要 SelectorEventLoop
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    ensure_certs()

    async def _serve_all():
        # 启动 Redis 健康检查后台任务
        from jindx.cache import redis_health_check_loop
        asyncio.create_task(redis_health_check_loop())

        connect_task = asyncio.create_task(_run_connect_server())

        http_config = uvicorn.Config(app, host="0.0.0.0", port=PROXY_PORT)
        tls_config = uvicorn.Config(
            app, host="0.0.0.0", port=TLS_PORT,
            ssl_certfile=str(CERT_FILE), ssl_keyfile=str(KEY_FILE),
        )
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
