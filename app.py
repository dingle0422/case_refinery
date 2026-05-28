"""FastAPI 服务入口。

启动方式：

.. code-block:: bash

    cd case_refinery
    uvicorn app:app --host 0.0.0.0 --port 8090

或包形式（从仓库根目录）::

    uvicorn case_refinery.app:app --host 0.0.0.0 --port 8090

lifespan 负责：

1. 启动时配置日志、构建并启动 APScheduler、做一次 LanceDB ``capabilities`` 自检
2. 关停时优雅 shutdown APScheduler，释放底层 httpx 连接池（由各 client 自己 close）
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI

# 支持 `python app.py` 直跑：补齐包上下文，保证相对导入可用。
if __name__ == "__main__" and (__package__ is None or __package__ == ""):
    _package_dir = os.path.dirname(os.path.abspath(__file__))
    _parent_dir = os.path.dirname(_package_dir)
    _package_name = os.path.basename(_package_dir)
    if _parent_dir not in sys.path:
        sys.path.insert(0, _parent_dir)
    __package__ = _package_name

from .api.routes import router as api_router
from .config import get_settings
from .pipeline.lancedb_client import LanceDBError, LanceDBV2Client
from .scheduler import build_scheduler, mark_scheduler_started


def _setup_logging() -> None:
    s = get_settings()
    logging.basicConfig(
        level=getattr(logging, s.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _setup_logging()
    s = get_settings()
    logger.info(
        "[case_refinery] startup: kh_codes=%s interval=%s enabled=%s",
        s.kh_codes, s.schedule_interval_label(), s.schedule_enabled,
    )

    # LanceDB 能力自检（失败不阻断启动，只记 warning；调度起来后真正写入时会再失败）
    try:
        cli = LanceDBV2Client(settings=s)
        try:
            caps = await cli.capabilities()
            logger.info("[case_refinery] lancedb capabilities: %s", caps)
        finally:
            await cli.aclose()
    except LanceDBError as e:
        logger.warning("[case_refinery] lancedb 自检失败（不阻断启动）: %s", e)
    except Exception as e:  # noqa: BLE001
        logger.warning("[case_refinery] lancedb 自检异常（不阻断启动）: %s", e)

    sched = build_scheduler(s)
    sched.start()
    mark_scheduler_started(s)
    app.state.scheduler = sched
    logger.info("[case_refinery] scheduler started")

    try:
        yield
    finally:
        logger.info("[case_refinery] shutting down scheduler")
        sched.shutdown(wait=False)


app = FastAPI(title="case_refinery", version="0.1.0", lifespan=lifespan)
app.include_router(api_router)


def main() -> None:
    """支持直接运行 `python app.py` 启动服务。"""
    import uvicorn

    host = os.getenv("CASE_REFINERY_HOST", "0.0.0.0")
    port = int(os.getenv("CASE_REFINERY_PORT", "5000"))
    reload_enabled = os.getenv("CASE_REFINERY_RELOAD", "0").lower() in {"1", "true", "yes"}

    if reload_enabled:
        uvicorn.run(f"{__package__}.app:app", host=host, port=port, reload=True)
    else:
        uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
