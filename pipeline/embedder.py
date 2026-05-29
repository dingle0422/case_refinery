"""文本 embedding 封装。

在异步链路里通过 ``asyncio.to_thread`` 调同步 embedding 客户端，避免阻塞事件循环。
"""

from __future__ import annotations

import asyncio
import logging

from ..config import Settings, get_settings
from ..vendor.embedding_client import embed as embedding_embed

logger = logging.getLogger(__name__)


class EmbedError(RuntimeError):
    """Embedding 调用失败。"""


async def embed_question_content(
    content: str,
    *,
    settings: Settings | None = None,
) -> list[float]:
    s = settings or get_settings()
    try:
        return await asyncio.to_thread(
            embedding_embed,
            content,
            base_url=s.embedding_base_url,
            path=s.embedding_path,
            model=s.embedding_model,
            api_key=s.embedding_api_key,
            timeout_s=s.embedding_timeout_sec,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[embedder] embedding 失败: %s", e)
        raise EmbedError(f"embedding_failed: {e}") from e

