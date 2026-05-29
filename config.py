"""case_refinery 全局配置，环境变量优先。

所有运行时可调项集中在此，便于本地起服务与容器化部署互不冲突。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_str(key: str, default: str) -> str:
    v = os.getenv(key)
    return v if v is not None else default


def _env_int(key: str, default: int) -> int:
    v = os.getenv(key)
    if v is None or v == "":
        return default
    try:
        return int(v)
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    v = os.getenv(key)
    if v is None or v == "":
        return default
    try:
        return float(v)
    except ValueError:
        return default


def _env_float_compat(keys: list[str], default: float) -> float:
    """按顺序读取多个浮点环境变量，返回第一个有效值。"""
    for key in keys:
        v = os.getenv(key)
        if v is None or v == "":
            continue
        try:
            return float(v)
        except ValueError:
            continue
    return default


def _env_bool(key: str, default: bool) -> bool:
    v = os.getenv(key)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def _env_csv(key: str, default: list[str] | None = None) -> list[str]:
    v = os.getenv(key)
    if v is None or v.strip() == "":
        return list(default or [])
    return [x.strip() for x in v.split(",") if x.strip()]


@dataclass(frozen=True)
class Settings:
    # --- 上游 case 接口 ---
    upstream_base_url: str = _env_str(
        "CASE_REFINERY_UPSTREAM_BASE_URL",
        "http://10.199.0.40:8080/kg-platform",
    )
    upstream_list_all_path: str = _env_str(
        "CASE_REFINERY_UPSTREAM_LIST_ALL_PATH",
        "/api/kh/listAllKh",
    )
    upstream_list_path: str = _env_str(
        "CASE_REFINERY_UPSTREAM_LIST_PATH",
        "/api/kh/listCorpusByKhCode",
    )
    upstream_timeout_s: float = _env_float(
        "CASE_REFINERY_UPSTREAM_TIMEOUT_S", 20.0
    )

    # --- LanceDB v2 ---
    lancedb_base_url: str = _env_str(
        "CASE_REFINERY_LANCEDB_BASE_URL",
        "http://mlp.paas.dc.servyou-it.com/kh-lancedb",
    )
    lancedb_api_key: str = _env_str("CASE_REFINERY_LANCEDB_API_KEY", "")
    lancedb_timeout_s: float = _env_float(
        "CASE_REFINERY_LANCEDB_TIMEOUT_S", 60.0
    )
    lancedb_max_retries: int = _env_int(
        "CASE_REFINERY_LANCEDB_MAX_RETRIES", 2
    )
    lancedb_retry_backoff_s: float = _env_float(
        "CASE_REFINERY_LANCEDB_RETRY_BACKOFF_S", 0.5
    )
    lancedb_retry_backoff_max_s: float = _env_float(
        "CASE_REFINERY_LANCEDB_RETRY_BACKOFF_MAX_S", 4.0
    )
    lancedb_collection_prefix: str = _env_str(
        "CASE_REFINERY_LANCEDB_COLLECTION_PREFIX", "case_"
    )

    # --- 调度 ---
    kh_codes: list[str] = field(
        default_factory=lambda: _env_csv("CASE_REFINERY_KH_CODES", [])
    )
    # --- 调度（默认 cron 每天 00:00） ---
    # 说明：
    # 1) 生产默认走 cron（每天固定时刻）
    # 2) 本地调试可设置 interval_seconds/interval_hours 覆盖 cron
    schedule_cron_hour: int = _env_int(
        "CASE_REFINERY_SCHEDULE_CRON_HOUR", 0
    )
    schedule_cron_minute: int = _env_int(
        "CASE_REFINERY_SCHEDULE_CRON_MINUTE", 0
    )
    schedule_interval_hours: int = _env_int(
        "CASE_REFINERY_SCHEDULE_INTERVAL_HOURS", 0
    )
    schedule_interval_seconds: int = _env_int(
        "CASE_REFINERY_SCHEDULE_INTERVAL_SECONDS", 0
    )
    schedule_enabled: bool = _env_bool(
        "CASE_REFINERY_SCHEDULE_ENABLED", True
    )

    def schedule_interval_label(self) -> str:
        if self.schedule_interval_seconds > 0:
            return f"every {self.schedule_interval_seconds}s"
        if self.schedule_interval_hours > 0:
            return f"every {self.schedule_interval_hours}h"
        return f"cron {self.schedule_cron_hour:02d}:{self.schedule_cron_minute:02d}"

    # --- LLM ---
    llm_vendor: str = _env_str("CASE_REFINERY_LLM_VENDOR", "servyou")
    llm_model: str = _env_str(
        "CASE_REFINERY_LLM_MODEL", "deepseek-v3.2-1163259bcc6c"
    )
    llm_enable_thinking: bool = _env_bool(
        "CASE_REFINERY_LLM_ENABLE_THINKING", False
    )

    # --- Embedding ---
    embedding_base_url: str = _env_str(
        "CASE_REFINERY_EMBEDDING_BASE_URL",
        "http://mlp.paas.dc.servyou-it.com/qwen3-embedding/v1",
    )
    embedding_path: str = _env_str(
        "CASE_REFINERY_EMBEDDING_PATH", "/embeddings"
    )
    embedding_model: str = _env_str(
        "CASE_REFINERY_EMBEDDING_MODEL", "qwen3-embedding"
    )
    embedding_api_key: str = _env_str(
        "CASE_REFINERY_EMBEDDING_API_KEY", ""
    )
    embedding_timeout_sec: float = _env_float_compat(
        [
            "CASE_REFINERY_EMBEDDING_TIMEOUT_SEC",
            "CASE_REFINERY_EMBEDDING_TIMEOUT_S",
        ],
        10.0,
    )

    # --- 业务 ---
    refine_max_attempts: int = _env_int(
        "CASE_REFINERY_REFINE_MAX_ATTEMPTS", 5
    )

    # --- 日志 ---
    log_level: str = _env_str("CASE_REFINERY_LOG_LEVEL", "INFO")

    def collection_id(self, kh_code: str) -> str:
        return f"{self.lancedb_collection_prefix}{kh_code}"

_settings: Settings | None = None


def get_settings() -> Settings:
    """单例。测试时可通过 set_settings 覆盖。"""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def set_settings(s: Settings) -> None:
    """测试场景显式注入配置。"""
    global _settings
    _settings = s
