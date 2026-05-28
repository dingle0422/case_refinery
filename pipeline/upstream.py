"""上游 case 回流接口客户端。

当前固定链路：

1. ``POST /api/kh/listAllKh``（无入参）拉取全部 ``khCode``
2. 对每个 ``khCode`` 调 ``POST /api/kh/listCorpusByKhCode``（入参 ``{"khCode": ...}``）
   拉取 case 数据

``listCorpusByKhCode`` 返回体中，当前仅消费以下 5 个字段：

- ``questionContent``
- ``originalAnswer``
- ``originalThinking``
- ``answerContent``
- ``thinking``
"""

from __future__ import annotations

import logging
from typing import Any, Dict

import httpx

from ..config import Settings, get_settings

logger = logging.getLogger(__name__)


# 上游 5 字段保持原始驼峰命名，跨模块以 dict 形式传递；用 TypeAlias 让 classifier /
# refiner / dedupe 的类型签名更清晰，不引入额外运行期对象。
CaseDict = Dict[str, Any]


class UpstreamError(RuntimeError):
    """上游接口错误（网络 / HTTP / 业务 success=false / 解析失败）。"""


def _parse_success_list_response(request_label: str, obj: Any) -> list[Any]:
    """解析通用 ``{success, data}`` 响应，返回 ``data`` 列表。"""
    if not isinstance(obj, dict):
        raise UpstreamError(
            f"{request_label} 上游响应顶层非 dict: {type(obj).__name__}"
        )
    if not obj.get("success"):
        raise UpstreamError(
            f"{request_label} success=false: "
            f"code={obj.get('code')} message={obj.get('message')!r}"
        )
    data = obj.get("data")
    if data is None:
        return []
    if not isinstance(data, list):
        raise UpstreamError(
            f"{request_label} 上游 data 非 list: {type(data).__name__}"
        )
    return data


def _normalize_case_item(item: dict[str, Any]) -> dict[str, str]:
    """提取并规范下游依赖的 5 个必要字段。"""
    return {
        "questionContent": str(item.get("questionContent") or ""),
        "originalAnswer": str(item.get("originalAnswer") or ""),
        "originalThinking": str(item.get("originalThinking") or ""),
        "answerContent": str(item.get("answerContent") or ""),
        "thinking": str(item.get("thinking") or ""),
    }


async def fetch_all_kh_codes(
    *,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[str]:
    """调用 ``/api/kh/listAllKh`` 拉取全部 khCode。"""

    s = settings or get_settings()
    url = s.upstream_base_url.rstrip("/") + s.upstream_list_all_path

    own_client = client is None
    cli = client or httpx.AsyncClient(timeout=s.upstream_timeout_s)
    try:
        try:
            resp = await cli.post(
                url, json={}, headers={"Content-Type": "application/json"}
            )
        except httpx.HTTPError as e:
            raise UpstreamError(f"listAllKh 上游网络异常: {e}") from e

        if resp.status_code >= 400:
            raise UpstreamError(
                f"listAllKh HTTP {resp.status_code}: {resp.text[:300]!r}"
            )

        try:
            obj = resp.json()
        except Exception as e:  # noqa: BLE001
            raise UpstreamError(
                f"listAllKh 上游响应非 JSON: {e}; body={resp.text[:300]!r}"
            ) from e

        data = _parse_success_list_response("listAllKh", obj)
        kh_codes: list[str] = []
        seen: set[str] = set()
        for i, item in enumerate(data):
            if not isinstance(item, dict):
                logger.warning("[upstream] listAllKh data[%d] 非 dict，已跳过: %r", i, item)
                continue
            code = str(item.get("code") or "").strip()
            if not code:
                logger.warning("[upstream] listAllKh data[%d] 缺少 code，已跳过: %r", i, item)
                continue
            if code in seen:
                continue
            seen.add(code)
            kh_codes.append(code)

        logger.info("[upstream] listAllKh 拉到 %d 个 khCode", len(kh_codes))
        return kh_codes
    finally:
        if own_client:
            await cli.aclose()


async def fetch_cases(
    kh_code: str,
    *,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """按 ``khCode`` 调 ``/api/kh/listCorpusByKhCode`` 拉取一批 case。"""

    s = settings or get_settings()
    url = s.upstream_base_url.rstrip("/") + s.upstream_list_path

    own_client = client is None
    cli = client or httpx.AsyncClient(timeout=s.upstream_timeout_s)
    try:
        try:
            resp = await cli.post(
                url,
                json={"khCode": kh_code},
                headers={"Content-Type": "application/json"},
            )
        except httpx.HTTPError as e:
            raise UpstreamError(f"{kh_code} 上游网络异常: {e}") from e

        if resp.status_code >= 400:
            raise UpstreamError(
                f"{kh_code} HTTP {resp.status_code}: {resp.text[:300]!r}"
            )

        try:
            obj = resp.json()
        except Exception as e:  # noqa: BLE001
            raise UpstreamError(
                f"{kh_code} 上游响应非 JSON: {e}; body={resp.text[:300]!r}"
            ) from e

        data = _parse_success_list_response(f"listCorpusByKhCode[{kh_code}]", obj)
        cleaned: list[dict[str, Any]] = []
        for i, item in enumerate(data):
            if not isinstance(item, dict):
                logger.warning(
                    "[upstream] %s data[%d] 非 dict 已跳过: %r",
                    kh_code, i, item,
                )
                continue
            cleaned.append(_normalize_case_item(item))

        logger.info(
            "[upstream] %s 通过 khCode 拉到 %d 条 case",
            kh_code, len(cleaned),
        )
        return cleaned
    finally:
        if own_client:
            await cli.aclose()
