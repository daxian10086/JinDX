"""运行时配置管理，线程安全。"""

import json
import os
import logging
from pathlib import Path
from threading import Lock

logger = logging.getLogger(__name__)


# ── 环境变量默认值（集中定义，与原始 proxy.py 完全一致）──────────────

DEEPSEEK_BASE = os.environ.get("DEEPSEEK_BASE", "https://api.deepseek.com")
DEEPSEEK_KEY = os.environ.get("DEEPSEEK_KEY", "sk-your-deepseek-api-key")
PROXY_PORT = int(os.environ.get("PROXY_PORT", "8080"))
CONNECT_PORT = int(os.environ.get("CONNECT_PORT", "8443"))
DEFAULT_MODEL = os.environ.get("DEFAULT_MODEL", "deepseek-v4-pro")
TLS_PORT = int(os.environ.get("TLS_PORT", "8444"))
ADMIN_PORT = int(os.environ.get("ADMIN_PORT", "8090"))
REASONING_CACHE_MAX = int(os.environ.get("REASONING_CACHE_MAX", "10"))
REASONING_CACHE_TTL = int(os.environ.get("REASONING_CACHE_TTL", "600"))
DEFAULT_REASONING_EFFORT = os.environ.get("DEFAULT_REASONING_EFFORT", None)
MAX_POSITION_EMBEDDINGS = int(os.environ.get("MAX_POSITION_EMBEDDINGS", "1000000"))

TOOL_USE_ENFORCEMENT = os.environ.get(
    "TOOL_USE_ENFORCEMENT",
    "You MUST use the provided tools to accomplish the user's task. "
    "Never respond with just text explaining what you would do — actually call the tools. "
    "If tools are available, use them to take real actions: run commands, read/write files, search the web. "
    "Do NOT ask the user for confirmation before using tools. Just do it.",
)

CONFIG_FILE = Path(os.environ.get("PROXY_CONFIG_FILE", "/home/wdmms123/.config/proxy-config.json"))


# ── Redis 配置 ─────────────────────────────────────────────────────────

REDIS_HOST = os.environ.get("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
REDIS_DB = int(os.environ.get("REDIS_DB", "0"))
REDIS_KEY_PREFIX = "reasoning:"


# ── TLS 证书路径 ───────────────────────────────────────────────────────

CERT_DIR = Path(__file__).parent.parent / "certs"
CERT_FILE = CERT_DIR / "tls.crt"
KEY_FILE = CERT_DIR / "tls.key"


# ── 默认配置 ──────────────────────────────────────────────────────────

_DEFAULT_CONFIG = {
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
    "claude_deepseek_key": "",
    "claude_deepseek_base": "https://api.deepseek.com",
    "claude_default_model": "deepseek-v4-pro",
    "claude_reasoning_effort": None,
    "claude_max_position_embeddings": 1000000,
    "claude_max_output_tokens": 16384,
    "claude_temperature": None,
    "claude_top_p": None,
    "claude_strip_thinking": True,
    "claude_skip_dangerous_mode": True,
}


class RuntimeConfig:
    """线程安全的运行时配置，替代全局 _runtime_config dict。"""

    def __init__(self):
        self._lock = Lock()
        self._config: dict = {}
        self._load()

    # ── 私有方法 ──────────────────────────────────────────────────────

    def _load(self):
        """从文件加载配置，合并默认值。"""
        cfg = dict(_DEFAULT_CONFIG)
        try:
            if CONFIG_FILE.exists():
                saved = json.loads(CONFIG_FILE.read_text())
                cfg.update(saved)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load config: {e}")
        self._config = cfg

    def _save(self) -> None:
        """持久化当前配置到文件。"""
        try:
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(json.dumps(self._config, indent=2, ensure_ascii=False))
        except OSError as e:
            logger.error(f"Failed to save config: {e}")

    # ── 公共接口 ──────────────────────────────────────────────────────

    def get(self, key: str, default=None):
        """读取配置项，线程安全。"""
        with self._lock:
            return self._config.get(key, default)

    def update(self, updates: dict) -> None:
        """批量更新配置项（复制-修改-替换，非原地修改），线程安全。"""
        allowed = {
            "deepseek_key", "deepseek_base", "default_model",
            "model_mapping", "reasoning_effort", "max_position_embeddings",
            "max_output_tokens", "temperature", "top_p",
            "tool_use_enforcement", "tool_use_prompt",
            "web_fetch_max_urls", "web_fetch_timeout", "web_fetch_max_body",
            "enable_reasoning_cache", "reasoning_cache_ttl",
            "claude_deepseek_key", "claude_deepseek_base", "claude_default_model",
            "claude_reasoning_effort", "claude_max_position_embeddings",
            "claude_max_output_tokens", "claude_temperature", "claude_top_p",
            "claude_strip_thinking", "claude_skip_dangerous_mode",
        }
        with self._lock:
            new_cfg = dict(self._config)
            for key, val in updates.items():
                if key in allowed:
                    new_cfg[key] = val
            self._config = new_cfg
        self._save()
        logger.info(f"Config updated: {json.dumps({k: v for k, v in updates.items() if k in allowed}, ensure_ascii=False)[:500]}")

    def reload(self) -> None:
        """重新从文件加载配置。"""
        self._load()

    @property
    def config_dict(self) -> dict:
        """返回只读快照（用于管理 API 序列化）。"""
        with self._lock:
            return dict(self._config)


# ── 全局单例 ─────────────────────────────────────────────────────────

config = RuntimeConfig()
