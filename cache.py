"""推理缓存：Redis 优先，内存兜底，支持自动重连。

Session 隔离：
  - Codex 与 Claude 的推理缓存完全隔离（按 source 参数区分）
  - Redis key 格式：reasoning:{source}:{session_id}
  - 内存缓存 key 格式：{source}:{session_id}
"""

import asyncio
import hashlib
import json
import logging
import time
from collections import OrderedDict
from threading import Lock
from typing import Literal, Optional

import redis

from .config import config
from .config import REDIS_HOST, REDIS_PORT, REDIS_DB, REASONING_CACHE_MAX, REDIS_KEY_PREFIX
from .stats import record_cache

logger = logging.getLogger(__name__)

Source = Literal["codex", "claude"]

# ── Redis 连接（延迟初始化）─────────────────────────────────────────

_redis: Optional[redis.Redis] = None
_redis_available = False
_redis_lock = Lock()


def _connect_redis() -> Optional[redis.Redis]:
    """尝试连接 Redis，失败返回 None。"""
    try:
        r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, decode_responses=True)
        r.ping()
        logger.info(f"Redis connected at {REDIS_HOST}:{REDIS_PORT}")
        return r
    except (redis.ConnectionError, redis.TimeoutError, OSError) as e:
        logger.warning(f"Redis unavailable ({e}), falling back to in-memory cache")
        return None


def _get_redis() -> Optional[redis.Redis]:
    """获取当前 Redis 连接。"""
    global _redis, _redis_available
    with _redis_lock:
        if _redis is None and _redis_available is False:
            _redis = _connect_redis()
            _redis_available = _redis is not None
        return _redis


def is_redis_available() -> bool:
    """Redis 当前是否可用。"""
    return _redis_available


def get_redis_info() -> dict:
    """获取 Redis 状态信息（用于管理 API）。"""
    if not _redis_available:
        return {"status": "disabled", "fallback": "memory"}
    r = _get_redis()
    if r is None:
        return {"status": "disconnected"}
    try:
        keys = r.keys(f"{REDIS_KEY_PREFIX}*")
        return {
            "status": "connected",
            "host": REDIS_HOST,
            "port": REDIS_PORT,
            "db": REDIS_DB,
            "keys": len(keys) if keys else 0,
        }
    except (redis.ConnectionError, redis.TimeoutError):
        return {"status": "disconnected"}


# ── 内部 key 构建 ─────────────────────────────────────────────────

def _full_key(source: Source, session_id: str) -> str:
    """构建内存缓存 key：{source}:{session_id}。"""
    return f"{source}:{session_id}"


def _serialize_key(source: Source, session_id: str) -> str:
    """构建 Redis key：reasoning:{source}:{session_id}。"""
    return f"{REDIS_KEY_PREFIX}{source}:{session_id}"


# ── 内存缓存 ────────────────────────────────────────────────────────

_reasoning_cache: dict[str, list[dict]] = OrderedDict()
_cache_lock = Lock()


def _cache_memory_get(full_key: str, ttl: int) -> list[str]:
    """从内存缓存读取推理文本。"""
    with _cache_lock:
        entries = _reasoning_cache.get(full_key, [])
        now = time.time()
        valid = [e for e in entries if now - e["ts"] < ttl]
        if valid:
            _reasoning_cache[full_key] = valid
            return [e["text"] for e in valid]
        else:
            _reasoning_cache.pop(full_key, None)
            return []


def _cache_memory_set(full_key: str, reasoning_text: str, ttl: int):
    """写入推理文本到内存缓存。"""
    entry = {"text": reasoning_text, "ts": time.time()}
    with _cache_lock:
        if full_key not in _reasoning_cache:
            _reasoning_cache[full_key] = []
        entries = _reasoning_cache[full_key]
        entries.append(entry)
        while len(entries) > REASONING_CACHE_MAX:
            entries.pop(0)
        while len(_reasoning_cache) > 1000:
            _reasoning_cache.popitem(last=False)


def get_memory_sessions_count() -> int:
    """返回内存缓存中的会话数。"""
    with _cache_lock:
        return len(_reasoning_cache)


# ── Redis 缓存操作 ──────────────────────────────────────────────────

def _cache_redis_get(source: Source, session_id: str, ttl: int) -> list[str]:
    """从 Redis 读取推理文本。"""
    r = _get_redis()
    if r is None:
        return []
    try:
        rkey = _serialize_key(source, session_id)
        raw = r.get(rkey)
        if raw:
            entries = json.loads(raw)
            now = time.time()
            valid = [e for e in entries if now - e["ts"] < ttl]
            if valid:
                r.set(rkey, json.dumps(valid, ensure_ascii=False), ex=ttl)
                return [e["text"] for e in valid]
            else:
                r.delete(rkey)
        return []
    except (redis.ConnectionError, redis.TimeoutError, json.JSONDecodeError) as e:
        logger.warning(f"Redis read error: {e}")
        return []


def _cache_redis_set(source: Source, session_id: str, reasoning_text: str, ttl: int):
    """写入推理文本到 Redis。"""
    r = _get_redis()
    if r is None:
        return
    try:
        rkey = _serialize_key(source, session_id)
        raw = r.get(rkey)
        entries = json.loads(raw) if raw else []
        entry = {"text": reasoning_text, "ts": time.time()}
        entries.append(entry)
        while len(entries) > REASONING_CACHE_MAX:
            entries.pop(0)
        r.set(rkey, json.dumps(entries, ensure_ascii=False), ex=ttl)
        logger.info(f"Redis cached reasoning for {rkey} ({len(entries)} entries)")
    except (redis.ConnectionError, redis.TimeoutError, json.JSONDecodeError) as e:
        logger.warning(f"Redis write error, falling back to memory: {e}")
        _cache_memory_set(_full_key(source, session_id), reasoning_text, ttl)


# ── 公开 API ────────────────────────────────────────────────────────

def get_session_id(data: dict) -> str:
    """从请求中提取稳定的会话 ID。

    优先级：Codex prompt_cache_key > 显式 ID > 第一条用户消息哈希。
    """
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

    inst_hash = hashlib.sha256(instructions.encode()).hexdigest()[:8]
    seed = f"{inst_hash}||{first_user_msg}"[:1000]
    return hashlib.sha256(seed.encode()).hexdigest()[:16]


def get_cached_reasoning(source: Source, session_id: str) -> list[str]:
    """获取会话的缓存推理内容，Redis 优先，内存兜底。

    参数：
        source: "codex" 或 "claude"，用于隔离两个来源的缓存
        session_id: 会话标识
    """
    if not config.get("enable_reasoning_cache", True):
        return []
    cache_ttl = config.get("reasoning_cache_ttl", 600)

    # Redis 优先
    if _redis_available:
        try:
            result = _cache_redis_get(source, session_id, cache_ttl)
            record_cache(bool(result))
            if result:
                return result
        except Exception:
            pass  # 降级到内存

    # 内存兜底
    result = _cache_memory_get(_full_key(source, session_id), cache_ttl)
    record_cache(bool(result))
    return result


def cache_reasoning(source: Source, session_id: str, reasoning_text: str):
    """缓存会话的推理文本，Redis 优先，内存兜底。

    参数：
        source: "codex" 或 "claude"，用于隔离两个来源的缓存
        session_id: 会话标识
        reasoning_text: 推理文本
    """
    if not reasoning_text or not reasoning_text.strip():
        return
    if not config.get("enable_reasoning_cache", True):
        return

    cache_ttl = config.get("reasoning_cache_ttl", 600)

    if _redis_available:
        try:
            _cache_redis_set(source, session_id, reasoning_text, cache_ttl)
            return
        except Exception:
            pass  # 降级到内存

    _cache_memory_set(_full_key(source, session_id), reasoning_text, cache_ttl)
    logger.info(f"Memory cached reasoning for {source}:{session_id}")


def get_redis_session_count() -> int:
    """获取 Redis 中的会话数。"""
    if not _redis_available:
        return 0
    r = _get_redis()
    if r is None:
        return 0
    try:
        keys = r.keys(f"{REDIS_KEY_PREFIX}*")
        return len(keys) if keys else 0
    except (redis.ConnectionError, redis.TimeoutError):
        return 0


# ── Redis 自动重连 ──────────────────────────────────────────────────

async def redis_health_check_loop(interval: int = 60):
    """后台定期检查 Redis 健康状态并在恢复时重连。"""
    global _redis, _redis_available
    while True:
        await asyncio.sleep(interval)
        if not _redis_available:
            with _redis_lock:
                if not _redis_available:  # 双重检查
                    new_r = _connect_redis()
                    if new_r is not None:
                        _redis = new_r
                        _redis_available = True
                        logger.info("Redis reconnected via health check")
