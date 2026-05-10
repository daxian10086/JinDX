"""统计计数和错误日志缓冲区，线程安全。"""

import time
from threading import Lock

# ── 统计数据 ──────────────────────────────────────────────────────────

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

# ── 错误日志环形缓冲区 ──────────────────────────────────────────────

_log_buffer: list[dict] = []  # each entry: {"ts": float, "msg": str}
_MAX_LOG_BUFFER = 200


def record_request():
    with _stats_lock:
        _stats["total_requests"] += 1


def record_error(code: int):
    with _stats_lock:
        key = str(code)
        _stats["errors_by_code"][key] = _stats["errors_by_code"].get(key, 0) + 1


def record_upstream_error(msg: str):
    with _stats_lock:
        short = msg[:120]
        _stats["upstream_errors"][short] = _stats["upstream_errors"].get(short, 0) + 1


def record_cache(hit: bool):
    with _stats_lock:
        if hit:
            _stats["cache_hits"] += 1
        else:
            _stats["cache_misses"] += 1


def log_error(msg: str):
    """追加到内存环形缓冲区，供仪表盘日志查看器使用。"""
    entry = {"ts": time.time(), "msg": msg[:500]}
    _log_buffer.append(entry)
    if len(_log_buffer) > _MAX_LOG_BUFFER:
        del _log_buffer[:len(_log_buffer) - _MAX_LOG_BUFFER]


def get_stats() -> dict:
    """获取统计快照（用于管理 API）。"""
    with _stats_lock:
        total = _stats["total_requests"]
        errors = sum(_stats["errors_by_code"].values())
        error_rate = round(errors / max(total, 1) * 100, 1)
        cache_total = _stats["cache_hits"] + _stats["cache_misses"]
        cache_hit_rate = round(_stats["cache_hits"] / max(cache_total, 1) * 100, 1)
        top_errors = sorted(_stats["errors_by_code"].items(), key=lambda x: x[1], reverse=True)[:10]
        top_upstream = sorted(_stats["upstream_errors"].items(), key=lambda x: x[1], reverse=True)[:5]
        return {
            "uptime": int(time.time() - _stats["start_time"]),
            "total_requests": total,
            "active_streams": _stats["active_streams"],
            "errors_by_code": dict(top_errors),
            "error_rate": error_rate,
            "cache_hits": _stats["cache_hits"],
            "cache_misses": _stats["cache_misses"],
            "cache_hit_rate": cache_hit_rate,
            "top_upstream_errors": [{"msg": k, "count": v} for k, v in top_upstream],
        }


def get_logs(limit: int = 50) -> list[dict]:
    """获取最近 N 条错误日志。"""
    recent = _log_buffer[-limit:]
    recent.reverse()
    return recent


def increment_active_streams():
    with _stats_lock:
        _stats["active_streams"] += 1


def decrement_active_streams():
    with _stats_lock:
        _stats["active_streams"] -= 1
