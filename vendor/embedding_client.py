"""Embedding 客户端（OpenAI-compatible embeddings endpoint）。"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from .utils_helpers import retry

logger = logging.getLogger(__name__)


def _build_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _extract_vector(resp: Any) -> list[float]:
    if not isinstance(resp, dict):
        raise ValueError("embedding 响应不是 JSON object")

    # OpenAI-compatible: {"data":[{"embedding":[...]}]}
    data = resp.get("data")
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict) and isinstance(first.get("embedding"), list):
            return [float(x) for x in first["embedding"]]

    # 兼容少数网关：{"embedding":[...]} / {"vector":[...]}
    if isinstance(resp.get("embedding"), list):
        return [float(x) for x in resp["embedding"]]
    if isinstance(resp.get("vector"), list):
        return [float(x) for x in resp["vector"]]

    raise ValueError("embedding 响应缺少向量字段")


@retry(max_retries=3, sleep_seconds=5.0)
def embed(
    text: str,
    *,
    base_url: str,
    path: str = "/embeddings",
    model: str = "qwen3-embedding",
    api_key: str = "",
    timeout_s: float = 10.0,
) -> list[float]:
    """对单段文本做 embedding，返回向量。"""
    content = (text or "").strip()
    if not content:
        raise ValueError("embedding 输入文本为空")

    url = _build_url(base_url, path)
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = api_key

    payload = {
        "model": model,
        "input": content,
    }
    logger.debug("Embedding 请求 [%s]: %s...", model, content[:100])

    resp = httpx.post(
        url,
        data=json.dumps(payload),
        headers=headers,
        timeout=httpx.Timeout(timeout_s, connect=10.0),
    )
    resp.raise_for_status()

    body = resp.json()
    vector = _extract_vector(body)
    if not vector:
        raise ValueError("embedding 响应向量为空")
    logger.debug("Embedding 响应维度: %d", len(vector))
    return vector

