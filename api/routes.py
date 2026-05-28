"""FastAPI 路由。

约定：
- ``GET /healthz``                  存活探针（k8s liveness 用）
- ``GET /status``                   调度器 / 上次运行结果快照
- ``POST /trigger``                 手动触发全部 khCode（同步等待结果返回）
- ``POST /trigger/{kh_code}``       手动触发单个 khCode

手动触发接口当前实现是 **同步等待结果** —— case 量不大、refine 是慢操作，但前端
调试时通常希望直接看到 summary。若后续接入运维平台需要"立即返回 + 异步执行"，
再加 ``BackgroundTasks`` 改造。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from ..config import get_settings
from ..scheduler import status_snapshot, trigger_all, trigger_kh_code

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


@router.get("/status")
async def status() -> dict:
    s = get_settings()
    return {
        "service": "case_refinery",
        "config": {
            "kh_codes": list(s.kh_codes),
            "schedule_enabled": s.schedule_enabled,
            "schedule_interval_hours": s.schedule_interval_hours,
            "schedule_interval_seconds": s.schedule_interval_seconds,
            "schedule_cron_hour": s.schedule_cron_hour,
            "schedule_cron_minute": s.schedule_cron_minute,
            "schedule_interval_label": s.schedule_interval_label(),
            "upstream_base_url": s.upstream_base_url,
            "upstream_list_path": s.upstream_list_path,
            "upstream_kh_field": s.upstream_kh_field,
            "lancedb_base_url": s.lancedb_base_url,
            "lancedb_collection_prefix": s.lancedb_collection_prefix,
            "llm_vendor": s.llm_vendor,
            "llm_model": s.llm_model,
            "refine_max_attempts": s.refine_max_attempts,
        },
        **status_snapshot(),
    }


@router.post("/trigger")
async def trigger_all_route() -> dict:
    s = get_settings()
    if not s.kh_codes:
        raise HTTPException(
            status_code=400,
            detail="CASE_REFINERY_KH_CODES 未配置，无可触发的 khCode",
        )
    summaries = await trigger_all()
    return {"summaries": [x.as_dict() for x in summaries]}


@router.post("/trigger/{kh_code}")
async def trigger_one_route(kh_code: str) -> dict:
    if not kh_code or not kh_code.strip():
        raise HTTPException(status_code=400, detail="kh_code 不能为空")
    summary = await trigger_kh_code(kh_code.strip())
    return {"summary": summary.as_dict()}
