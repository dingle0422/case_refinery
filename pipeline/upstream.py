"""上游 case 回流接口客户端。

接口形态（示例）::

    POST http://10.199.0.40:8080/kg-platform/api/kh/listCorpusByPolicyId
    Body: {"khCode": "<kh_code_prefix>"}  或  {"policyId": "<full_policy_id>"}

``CASE_REFINERY_KH_CODES`` 配置项通常放 **完整 policyId**；其中 khCode 为其前缀
（首个 ``_`` 之前）。默认 ``upstream_kh_field=auto`` 时会：

1. 先以 ``khCode``（前缀）请求（兼容上游切换后）
2. 若 HTTP/业务失败，再以 ``policyId``（完整 id）重试（兼容当前线上）

返回体（success 时）::

    {
        "success": true,
        "code": "OK",
        "message": "成功",
        "data": [
            {
                "questionContent":  "...",
                "originalAnswer":   "...",
                "originalThinking": "...",
                "answerContent":    "...",
                "thinking":         "..."
            },
            ...
        ]
    }

异常策略：
- 所有候选 payload 均失败 → 抛 :class:`UpstreamError`
- success=true 但 data=[] → 返回空 list（runner 据此 no-op，绝不触发任何删除）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from ..config import Settings, get_settings

logger = logging.getLogger(__name__)


# 上游 5 字段保持原始驼峰命名，跨模块以 dict 形式传递；用 TypeAlias 让 classifier /
# refiner / dedupe 的类型签名更清晰，不引入额外运行期对象。
CaseDict = dict[str, Any]


class UpstreamError(RuntimeError):
    """上游接口错误（网络 / HTTP / 业务 success=false / 解析失败）。"""


@dataclass(frozen=True)
class RawCase:
    """与上游 5 字段同名的轻量结构。

    保留 dict 兼容性：传入 :func:`classifier.classify` 等下游函数时仍以 dict 形态使用，
    本 dataclass 仅作类型标注、不强制构造。
    """

    question_content: str
    original_answer: str
    original_thinking: str
    answer_content: str
    thinking: str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RawCase":
        return cls(
            question_content=str(d.get("questionContent") or ""),
            original_answer=str(d.get("originalAnswer") or ""),
            original_thinking=str(d.get("originalThinking") or ""),
            answer_content=str(d.get("answerContent") or ""),
            thinking=str(d.get("thinking") or ""),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "questionContent": self.question_content,
            "originalAnswer": self.original_answer,
            "originalThinking": self.original_thinking,
            "answerContent": self.answer_content,
            "thinking": self.thinking,
        }


def build_upstream_payloads(
    identifier: str,
    *,
    settings: Settings | None = None,
) -> list[tuple[str, dict[str, str]]]:
    """构造按优先级排列的上游请求体列表。

    返回 ``[(label, payload), ...]``，``label`` 形如 ``khCode=KHxxx`` 便于日志。
    """
    s = settings or get_settings()
    mode = (s.upstream_kh_field or "auto").strip().lower()
    prefix = Settings.kh_code_prefix(identifier)

    if mode == "khcode":
        return [("khCode", {"khCode": prefix})]
    if mode == "policyid":
        return [("policyId", {"policyId": identifier})]

    # auto：先 khCode 前缀，再 policyId 全量（两者值不同时才发两次）
    payloads: list[tuple[str, dict[str, str]]] = [
        ("khCode", {"khCode": prefix}),
    ]
    if prefix != identifier:
        payloads.append(("policyId", {"policyId": identifier}))
    else:
        # 短 id（无 ``_`` 后缀）时 policyId 字段值与 khCode 相同，补一次 policyId 重试
        payloads.append(("policyId", {"policyId": identifier}))
    return payloads


def _parse_upstream_response(
    identifier: str,
    obj: Any,
    *,
    field_label: str,
) -> list[dict[str, Any]] | None:
    """解析上游 JSON。success=true 返回 data list；success=false 返回 None（可换 payload）。"""

    if not isinstance(obj, dict):
        raise UpstreamError(
            f"{identifier} 上游响应顶层非 dict ({field_label}): "
            f"{type(obj).__name__}"
        )

    if not obj.get("success"):
        logger.info(
            "[upstream] %s %s 请求 success=false: code=%s message=%r",
            identifier, field_label, obj.get("code"), obj.get("message"),
        )
        return None

    data = obj.get("data")
    if data is None:
        return []
    if not isinstance(data, list):
        raise UpstreamError(
            f"{identifier} 上游 data 非 list ({field_label}): {type(data).__name__}"
        )

    cleaned: list[dict[str, Any]] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            logger.warning(
                "[upstream] %s data[%d] 非 dict 已跳过 (%s): %r",
                identifier, i, field_label, item,
            )
            continue
        cleaned.append(item)
    return cleaned


async def fetch_cases(
    kh_code: str,
    *,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """按 policyId / khCode 标识拉一批 case。

    ``kh_code`` 参数名沿用调度侧命名，实际可传完整 policyId。
    返回上游 ``data`` 数组；失败抛 :class:`UpstreamError`，空数据返回 ``[]``。
    """

    s = settings or get_settings()
    url = s.upstream_base_url.rstrip("/") + s.upstream_list_path
    payloads = build_upstream_payloads(kh_code, settings=s)

    own_client = client is None
    cli = client or httpx.AsyncClient(timeout=s.upstream_timeout_s)
    last_biz_error = ""

    try:
        for field_label, payload in payloads:
            label = f"{field_label}={payload[field_label]!r}"
            try:
                resp = await cli.post(
                    url, json=payload, headers={"Content-Type": "application/json"}
                )
            except httpx.HTTPError as e:
                raise UpstreamError(f"{kh_code} 上游网络异常 ({label}): {e}") from e

            if resp.status_code >= 400:
                last_biz_error = (
                    f"{label} HTTP {resp.status_code}: {resp.text[:300]!r}"
                )
                logger.warning("[upstream] %s %s", kh_code, last_biz_error)
                continue

            try:
                obj = resp.json()
            except Exception as e:  # noqa: BLE001
                raise UpstreamError(
                    f"{kh_code} 上游响应非 JSON ({label}): {e}; "
                    f"body={resp.text[:300]!r}"
                ) from e

            try:
                cleaned = _parse_upstream_response(kh_code, obj, field_label=label)
            except UpstreamError:
                raise
            if cleaned is None:
                last_biz_error = (
                    f"{label} success=false: code={obj.get('code')} "
                    f"message={obj.get('message')!r}"
                )
                continue

            logger.info(
                "[upstream] %s 通过 %s 拉到 %d 条 case",
                kh_code, label, len(cleaned),
            )
            return cleaned

        raise UpstreamError(
            f"{kh_code} 上游全部 payload 均失败: {last_biz_error or 'unknown'}"
        )
    finally:
        if own_client:
            await cli.aclose()
