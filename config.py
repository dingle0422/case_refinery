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
    upstream_list_path: str = _env_str(
        "CASE_REFINERY_UPSTREAM_LIST_PATH",
        "/api/kh/listCorpusByPolicyId",
    )
    # 上游 list 接口入参策略：
    # - ``auto``（默认）：先 ``khCode``（policyId 前缀），失败再 ``policyId``（完整 id）
    # - ``khCode`` / ``policyId``：强制只用单一字段（调试或上游已完全切换时用）
    upstream_kh_field: str = _env_str(
        "CASE_REFINERY_UPSTREAM_KH_FIELD",
        "auto",
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
        "CASE_REFINERY_LANCEDB_TIMEOUT_S", 30.0
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

    # --- 业务 ---
    refine_max_attempts: int = _env_int(
        "CASE_REFINERY_REFINE_MAX_ATTEMPTS", 5
    )

    # --- 日志 ---
    log_level: str = _env_str("CASE_REFINERY_LOG_LEVEL", "INFO")

    def collection_id(self, kh_code: str) -> str:
        return f"{self.lancedb_collection_prefix}{kh_code}"

    @staticmethod
    def kh_code_prefix(identifier: str) -> str:
        """从 policyId 派生 khCode：取首个 ``_`` 之前的前缀。

        ``CASE_REFINERY_KH_CODES`` 里通常放完整 policyId（如
        ``KH1493204307733168128_20260519101916``），其 khCode 前缀为
        ``KH1493204307733168128``。若无 ``_`` 则原样返回（已是 khCode 短 id）。
        """
        if "_" in identifier:
            return identifier.split("_", 1)[0]
        return identifier


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
