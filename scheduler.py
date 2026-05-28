"""APScheduler 作业注册与运行态管理。

设计点：

- ``AsyncIOScheduler``：与 FastAPI 的事件循环共用，避免新建线程池
- ``max_instances=1`` + ``coalesce=True``：同一作业重入时合并，前一轮没跑完时
  下一次到点直接丢弃，绝不并行（防止两轮任务同时改库）
- 单 :data:`_LATEST_SUMMARIES` 字典缓存上轮结果，``/status`` 接口直接读
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .config import Settings, get_settings
from .pipeline.runner import RunSummary, run_all, run_once

logger = logging.getLogger(__name__)


# 进程级别的运行状态缓存：``kh_code -> last RunSummary dict``
_LATEST_SUMMARIES: dict[str, dict[str, Any]] = {}
# 调度自身的最近状态
_SCHEDULER_STATE: dict[str, Any] = {
    "started": False,
    "last_run_started_ms": 0,
    "last_run_finished_ms": 0,
    "interval_hours": 0,
    "interval_seconds": 0,
    "cron_hour": 0,
    "cron_minute": 0,
    "interval_label": "",
    "kh_codes": [],
}

# 保护并发触发（手动 + 定时）不互相踩
_RUN_LOCK = asyncio.Lock()


async def _scheduled_tick() -> None:
    """APScheduler 触发的作业入口；遍历所有 khCode。"""
    async with _RUN_LOCK:
        s = get_settings()
        import time
        _SCHEDULER_STATE["last_run_started_ms"] = int(time.time() * 1000)
        logger.info(
            "[scheduler] tick start"
        )
        summaries = await run_all(settings=s)
        for summary in summaries:
            _LATEST_SUMMARIES[summary.kh_code] = summary.as_dict()
        _SCHEDULER_STATE["last_run_finished_ms"] = int(time.time() * 1000)
        logger.info(
            "[scheduler] tick done: %d kh_codes processed",
            len(summaries),
        )


async def trigger_kh_code(kh_code: str) -> RunSummary:
    """手动触发单个 khCode（与定时任务共享 ``_RUN_LOCK``，互斥）。"""
    s = get_settings()
    async with _RUN_LOCK:
        summary = await run_once(kh_code, settings=s)
        _LATEST_SUMMARIES[kh_code] = summary.as_dict()
        return summary


async def trigger_all() -> list[RunSummary]:
    """手动触发全部 khCode。"""
    s = get_settings()
    async with _RUN_LOCK:
        import time
        _SCHEDULER_STATE["last_run_started_ms"] = int(time.time() * 1000)
        summaries = await run_all(settings=s)
        for summary in summaries:
            _LATEST_SUMMARIES[summary.kh_code] = summary.as_dict()
        _SCHEDULER_STATE["last_run_finished_ms"] = int(time.time() * 1000)
        return summaries


def build_scheduler(settings: Settings | None = None) -> AsyncIOScheduler:
    s = settings or get_settings()
    sched = AsyncIOScheduler()
    if s.schedule_enabled:
        if s.schedule_interval_seconds > 0:
            sched.add_job(
                _scheduled_tick,
                trigger="interval",
                seconds=s.schedule_interval_seconds,
                id="case_refinery_tick",
                max_instances=1,
                coalesce=True,
            )
        elif s.schedule_interval_hours > 0:
            sched.add_job(
                _scheduled_tick,
                trigger="interval",
                hours=s.schedule_interval_hours,
                id="case_refinery_tick",
                max_instances=1,
                coalesce=True,
            )
        else:
            sched.add_job(
                _scheduled_tick,
                trigger="cron",
                hour=s.schedule_cron_hour,
                minute=s.schedule_cron_minute,
                id="case_refinery_tick",
                max_instances=1,
                coalesce=True,
            )
    return sched


def mark_scheduler_started(settings: Settings | None = None) -> None:
    s = settings or get_settings()
    _SCHEDULER_STATE["started"] = True
    _SCHEDULER_STATE["interval_hours"] = s.schedule_interval_hours
    _SCHEDULER_STATE["interval_seconds"] = s.schedule_interval_seconds
    _SCHEDULER_STATE["cron_hour"] = s.schedule_cron_hour
    _SCHEDULER_STATE["cron_minute"] = s.schedule_cron_minute
    _SCHEDULER_STATE["interval_label"] = s.schedule_interval_label()
    _SCHEDULER_STATE["kh_codes"] = list(s.kh_codes)


def status_snapshot() -> dict[str, Any]:
    """供 /status 接口序列化输出。"""
    return {
        "scheduler": dict(_SCHEDULER_STATE),
        "latest_summaries": dict(_LATEST_SUMMARIES),
    }
