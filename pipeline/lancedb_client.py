"""LanceDB v2 HTTP 客户端。

只实现本服务需要的几个端点（参考主仓 docs/lancedb_v2_api.md）：

- ``GET /v2/capabilities``                                    启动自检
- ``GET /v2/collections/{cid}/meta``                          可选：dim / 索引状态
- ``GET /v2/collections/{cid}/documents``                     列出文档（用于 dedupe 构 index）
- ``POST /v2/collections/{cid}/documents:upsert``             写入 / merge

向量策略：
- 客户端 **不计算** embedding，``vector=[]`` 留空发出
- LanceDB v2 服务端 fallback 用 ``content`` 自动 embed（见 v2 文档 L98）

删除策略（MVP，等服务端补单文档 DELETE 接口前的过渡）：
- 同 doc_id + ``mode="merge_by_chunk_id"`` 改写 ``metadata.tombstoned=true``
- 主仓读侧统一加 ``where md_tombstoned_xxxxxxxx = false`` 过滤
- :meth:`LanceDBV2Client.list_existing` 也会跳过 tombstoned 文档
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..config import Settings, get_settings

logger = logging.getLogger(__name__)


def _coerce_lance_document_id(doc_id: str | int) -> int:
    if isinstance(doc_id, int):
        return doc_id
    s = str(doc_id)
    if s.isdigit():
        return int(s)
    import hashlib
    digest = hashlib.sha256(s.encode()).digest()
    n = int.from_bytes(digest[:8], "big") & 0x7FFFFFFFFFFFFFFF
    return n if n > 0 else 1


class LanceDBError(RuntimeError):
    """LanceDB HTTP 调用失败（网络 / 4xx / 5xx / 解析）。"""


@dataclass
class ExistingDoc:
    """库内已有 document 在 dedupe 阶段需要参考的子集字段（来自 metadata）。"""

    doc_id: str
    record_hash: str
    question_hash: str
    refine_status: str           # "refined" | "raw_fallback"
    refine_attempts: int
    tombstoned: bool


@dataclass
class ExistingIndex:
    """list_existing 的产物：以 record_hash / question_hash 为 key 的两路索引。

    - ``by_record_hash``：``record_hash`` -> ExistingDoc（已剔除 tombstoned）
    - ``by_question_hash``：``question_hash`` -> list[ExistingDoc]（已剔除 tombstoned）

    用于 dedupe.decide 单次决策时 O(1) 查询。
    """

    by_record_hash: dict[str, ExistingDoc] = field(default_factory=dict)
    by_question_hash: dict[str, list[ExistingDoc]] = field(default_factory=dict)

    def total(self) -> int:
        return len(self.by_record_hash)


class LanceDBV2Client:
    """单进程内可复用一个实例。"""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._s = settings or get_settings()
        self._own_client = client is None
        headers = {"Content-Type": "application/json"}
        if self._s.lancedb_api_key:
            headers["X-API-Key"] = self._s.lancedb_api_key
        self._client = client or httpx.AsyncClient(
            base_url=self._s.lancedb_base_url.rstrip("/"),
            timeout=self._s.lancedb_timeout_s,
            headers=headers,
        )

    async def aclose(self) -> None:
        if self._own_client:
            await self._client.aclose()

    # ------------------------------------------------------------------ 基础调用

    async def _request(self, method: str, path: str, **kwargs) -> Any:
        try:
            resp = await self._client.request(method, path, **kwargs)
        except httpx.HTTPError as e:
            raise LanceDBError(f"{method} {path} 网络异常: {e}") from e

        if resp.status_code >= 400:
            raise LanceDBError(
                f"{method} {path} -> {resp.status_code}: {resp.text[:300]!r}"
            )
        if not resp.content:
            return None
        try:
            return resp.json()
        except Exception as e:  # noqa: BLE001
            raise LanceDBError(
                f"{method} {path} 响应非 JSON: {e}; body={resp.text[:300]!r}"
            ) from e

    # ------------------------------------------------------------------ 能力

    async def capabilities(self) -> dict:
        return await self._request("GET", "/v2/capabilities")

    async def get_collection_meta(self, collection_id: str) -> dict | None:
        """集合不存在时返回 None（v2 spec 是 404）。"""
        try:
            return await self._request(
                "GET", f"/v2/collections/{collection_id}/meta"
            )
        except LanceDBError as e:
            if "404" in str(e):
                return None
            raise

    # ------------------------------------------------------------------ 读

    async def list_existing(self, kh_code: str) -> ExistingIndex:
        """拉某 khCode 集合下全部文档的轻量索引（不含 content）。

        集合不存在 → 空索引（首次运行场景）。
        """

        collection_id = self._s.collection_id(kh_code)
        # 集合不存在直接返回空索引，不抛错
        meta = await self.get_collection_meta(collection_id)
        if meta is None:
            logger.info(
                "[lancedb] collection %s 不存在，视为空索引", collection_id
            )
            return ExistingIndex()

        params = {
            "include_content": "false",
        }
        data = await self._request(
            "GET",
            f"/v2/collections/{collection_id}/documents",
            params=params,
        )
        docs = (data or {}).get("documents") or []

        index = ExistingIndex()
        skipped_tombstoned = 0
        skipped_malformed = 0
        for d in docs:
            if not isinstance(d, dict):
                skipped_malformed += 1
                continue
            md = d.get("metadata") or {}
            rh = md.get("record_hash")
            qh = md.get("question_hash")
            if not rh or not qh:
                skipped_malformed += 1
                continue
            tombstoned = bool(md.get("tombstoned", False))
            if tombstoned:
                skipped_tombstoned += 1
                continue

            ed = ExistingDoc(
                doc_id=str(d.get("document_id") or ""),
                record_hash=str(rh),
                question_hash=str(qh),
                refine_status=str(md.get("refine_status") or "raw_fallback"),
                refine_attempts=int(md.get("refine_attempts") or 0),
                tombstoned=False,
            )
            if not ed.doc_id:
                skipped_malformed += 1
                continue

            # 同 record_hash 重复（理论上不该出现，保留最后一条）：
            index.by_record_hash[ed.record_hash] = ed
            index.by_question_hash.setdefault(ed.question_hash, []).append(ed)

        logger.info(
            "[lancedb] %s 索引构建：total=%d, tombstoned_skip=%d, malformed_skip=%d",
            collection_id, index.total(), skipped_tombstoned, skipped_malformed,
        )
        return index

    # ------------------------------------------------------------------ 写

    async def upsert_one(
        self,
        kh_code: str,
        document: dict,
        *,
        mode: str = "append",
    ) -> dict:
        """upsert 单条文档。``mode``: ``append`` 新增 / ``merge_by_chunk_id`` 覆盖。

        注意：documents 数组不能为空（v2 422），所以批量场景需上层自己 batch。
        """
        doc_id = document.get("document_id")
        if doc_id is None:
            raise LanceDBError("document_id 必填")

        collection_id = self._s.collection_id(kh_code)
        body = {
            "documents": [document],
            "mode": mode,
        }
        # 不传 expected_dim，让服务端按 content 自动 embed
        return await self._request(
            "POST",
            f"/v2/collections/{collection_id}/documents:upsert",
            json=body,
        )

    async def tombstone_docs(
        self,
        kh_code: str,
        targets: list[ExistingDoc],
    ) -> int:
        """把目标 doc 全部 merge 写为 ``tombstoned=true``。

        因为 v2 当前只有 ``DELETE /v2/collections/{cid}``（整集合），单文档删除尚未
        提供，所以这里用 merge_by_chunk_id 把 metadata 改写。读侧需统一过滤
        ``md_tombstoned_xxx = false``。

        失败返回当前已完成数；调用方可根据返回值判断是否需要在 metadata 标
        ``tombstone_failed`` 之类，本 MVP 暂时只记 warning 并继续。
        """
        if not targets:
            return 0

        ok = 0
        for t in targets:
            doc = {
                "document_id": _coerce_lance_document_id(t.doc_id),
                # content / vector / content_tokenized 必须带上，否则 merge 之后这些
                # 字段会被空值覆盖。为了避免在 list_existing 阶段把 content 也拉下来，
                # 这里直接传空串/空数组——服务端 fallback 行为是「保留原向量、用空
                # content 重算分词」。如果 v2 实际是「整行替换」，会在生产端先冒
                # 烟，再决定是否切换到拉 content 后回写的方案。
                "content": "",
                "content_tokenized": "",
                "vector": [],
                "metadata": {
                    # 仅承诺 tombstoned 标记真实可信，其余字段保留 placeholder
                    "tombstoned": True,
                    "tombstoned_at": _now_ms(),
                    "kh_code": kh_code,
                    # 把已知的 hash 也带上便于审计追溯
                    "record_hash": t.record_hash,
                    "question_hash": t.question_hash,
                    "refine_status": t.refine_status,
                    "refine_attempts": t.refine_attempts,
                    "schema_version": 1,
                    "source": "case_refinery",
                },
            }
            try:
                await self.upsert_one(kh_code, doc, mode="merge_by_chunk_id")
                ok += 1
            except LanceDBError as e:
                logger.warning(
                    "[lancedb] tombstone 失败 doc_id=%s: %s", t.doc_id, e
                )
        logger.info(
            "[lancedb] tombstone %d/%d on collection=%s",
            ok, len(targets), self._s.collection_id(kh_code),
        )
        return ok


def _now_ms() -> int:
    import time

    return int(time.time() * 1000)
