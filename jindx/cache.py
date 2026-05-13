"""推理缓存：本地文件持久化 + 内存加速。

Session 隔离：
  - Codex 与 Claude 的推理缓存完全隔离（按 source 参数区分）
  - 文件路径：{cache_dir}/{source}_{session_id}.json
  - 内存缓存 key 格式：{source}:{session_id}
"""

import asyncio
import hashlib
import json
import logging
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from threading import Lock
from typing import Literal

from .config import config, REASONING_CACHE_MAX
from .stats import record_cache

logger = logging.getLogger(__name__)

Source = Literal["codex", "claude"]


# ── 本地文件缓存 ────────────────────────────────────────────────────

def _file_cache_dir() -> Path:
    from .config import CONFIG_FILE
    return CONFIG_FILE.parent / "reasoning_cache"


def _file_cache_path(source: Source, session_id: str) -> Path:
    return _file_cache_dir() / f"{source}_{session_id}.json"


def _cache_file_get(source: Source, session_id: str, ttl: int) -> list[str]:
    path = _file_cache_path(source, session_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        entries = data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []

    now = time.time()
    valid = [e for e in entries if now - e.get("ts", 0) < ttl]
    if valid:
        path.write_text(json.dumps(valid, ensure_ascii=False))
        return [e["text"] for e in valid]
    else:
        path.unlink(missing_ok=True)
        return []


def _cache_file_set(source: Source, session_id: str, reasoning_text: str, ttl: int):
    path = _file_cache_path(source, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = []
    if path.exists():
        try:
            data = json.loads(path.read_text())
            entries = data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            entries = []

    entry = {"text": reasoning_text, "ts": time.time()}
    entries.append(entry)
    while len(entries) > REASONING_CACHE_MAX:
        entries.pop(0)

    path.write_text(json.dumps(entries, ensure_ascii=False))


# ── 内存缓存 ────────────────────────────────────────────────────────

_reasoning_cache: dict[str, list[dict]] = OrderedDict()
_cache_lock = Lock()


def _cache_memory_get(full_key: str, ttl: int) -> list[str]:
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


def _cache_memory_set(full_key: str, reasoning_text: str):
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
    with _cache_lock:
        return len(_reasoning_cache)


def _cleanup_expired_memory_entries(ttl: int):
    with _cache_lock:
        now = time.time()
        expired = [
            k for k, entries in _reasoning_cache.items()
            if all(now - e["ts"] >= ttl for e in entries)
        ]
        for k in expired:
            _reasoning_cache.pop(k, None)
        if expired:
            logger.debug(f"Cleaned {len(expired)} expired memory cache entries")


async def memory_cache_cleanup_loop(interval: int = 300):
    while True:
        await asyncio.sleep(interval)
        cache_ttl = config.get("reasoning_cache_ttl", 600)
        _cleanup_expired_memory_entries(cache_ttl)


# ── 内部 key 构建 ─────────────────────────────────────────────────

def _full_key(source: Source, session_id: str) -> str:
    return f"{source}:{session_id}"


# ── 公开 API ────────────────────────────────────────────────────────

def get_session_id(data: dict) -> str:
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

    # ?? add randomness to avoid session collision ??
    inst_hash = hashlib.sha256(instructions.encode()).hexdigest()[:8]
    seed = f"{inst_hash}||{first_user_msg}||{uuid.uuid4().hex[:8]}"[:1000]
    return hashlib.sha256(seed.encode()).hexdigest()[:16]


def get_cached_reasoning(source: Source, session_id: str) -> list[str]:
    if not config.get("enable_reasoning_cache", True):
        return []
    cache_ttl = config.get("reasoning_cache_ttl", 600)

    # 文件优先
    try:
        result = _cache_file_get(source, session_id, cache_ttl)
        record_cache(bool(result))
        if result:
            return result
    except Exception:
        pass

    # 内存兜底
    result = _cache_memory_get(_full_key(source, session_id), cache_ttl)
    record_cache(bool(result))
    return result


def cache_reasoning(source: Source, session_id: str, reasoning_text: str):
    if not reasoning_text or not reasoning_text.strip():
        return
    if not config.get("enable_reasoning_cache", True):
        return

    cache_ttl = config.get("reasoning_cache_ttl", 600)

    # 文件持久化
    try:
        _cache_file_set(source, session_id, reasoning_text, cache_ttl)
    except Exception:
        pass

    # 同步写到内存加速读取
    _cache_memory_set(_full_key(source, session_id), reasoning_text)


def is_redis_available() -> bool:
    return False


def get_redis_info() -> dict:
    return {"status": "disabled", "fallback": "file"}


def get_redis_session_count() -> int:
    return 0


def get_cache_size_info() -> dict:
    """获取缓存大小信息（用于管理面板显示）。"""
    import os as _os
    cache_dir = _file_cache_dir()
    file_count = 0
    total_size = 0
    if cache_dir.exists():
        for f in cache_dir.iterdir():
            if f.suffix == ".json":
                file_count += 1
                try:
                    total_size += f.stat().st_size
                except OSError:
                    pass

    with _cache_lock:
        mem_count = len(_reasoning_cache)

    def _fmt(size: int) -> str:
        if size < 1024:
            return f"{size}B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f}KB"
        else:
            return f"{size / 1024 / 1024:.1f}MB"

    return {
        "file_count": file_count,
        "file_size": total_size,
        "file_size_str": _fmt(total_size),
        "memory_count": mem_count,
    }


def clear_cache(source: str = "") -> int:
    """清理推理缓存。source 为空清全部，否则仅清 "codex" 或 "claude"。返回删除的文件数。"""
    cache_dir = _file_cache_dir()
    deleted = 0
    if cache_dir.exists():
        for f in cache_dir.iterdir():
            if f.suffix != ".json":
                continue
            if source and not f.stem.startswith(f"{source}_"):
                continue
            try:
                f.unlink()
                deleted += 1
            except OSError:
                pass

    # 清理内存缓存
    with _cache_lock:
        if source:
            prefix = f"{source}:"
            keys_to_del = [k for k in _reasoning_cache if k.startswith(prefix)]
            for k in keys_to_del:
                del _reasoning_cache[k]
        else:
            _reasoning_cache.clear()

    logger.info(f"Cache cleared: {deleted} files deleted, source={source or 'all'}")
    return deleted
