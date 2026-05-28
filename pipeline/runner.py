"""单轮 case ingestion 编排。

调度链路：

1. :func:`upstream.fetch_cases` 拉一批 case
2. :meth:`LanceDBV2Client.list_existing` 取库内现状
3. 对每条 case：
   - :func:`classifier.classify` + :func:`classifier.record_hash` + :func:`classifier.question_hash`
   - :func:`dedupe.decide` 决策（是否需要 refine、成功/失败动作、tombstone 目标）
   - 若 ``need_refine=True``：:func:`refiner.refine` 调 LLM
   - 根据 refine 结果与决策，构造目标 document 并调用 :meth:`LanceDBV2Client.upsert_one`
   - 入库成功后软删同 question_hash 旧版本（:meth:`LanceDBV2Client.tombstone_docs`）

错误隔离：任何单条 case 的异常都被吃掉、计入 ``errors``，不影响后续 case；上游
``fetch_cases`` 抛 :class:`UpstreamError` 时整轮 abort，**不做任何删除/写入**——
保证上游接口故障时库存不被错误污染。
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings, get_settings
from . import classifier, dedupe, refiner
from .lancedb_client import ExistingDoc, ExistingIndex, LanceDBError, LanceDBV2Client
from .upstream import CaseDict, UpstreamError, fetch_all_kh_codes, fetch_cases

logger = logging.getLogger(__name__)


@dataclass
class RunSummary:
    kh_code: str
    started_at_ms: int
    finished_at_ms: int = 0
    upstream_fetched: int = 0
    inserted_refined: int = 0
    inserted_raw_fallback: int = 0
    overwritten_to_refined: int = 0
    bumped_attempts: int = 0
    skipped: int = 0
    tombstoned: int = 0
    errors: list[str] = field(default_factory=list)
    aborted: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "kh_code": self.kh_code,
            "started_at_ms": self.started_at_ms,
            "finished_at_ms": self.finished_at_ms,
            "elapsed_ms": (
                self.finished_at_ms - self.started_at_ms
                if self.finished_at_ms else 0
            ),
            "upstream_fetched": self.upstream_fetched,
            "inserted_refined": self.inserted_refined,
            "inserted_raw_fallback": self.inserted_raw_fallback,
            "overwritten_to_refined": self.overwritten_to_refined,
            "bumped_attempts": self.bumped_attempts,
            "skipped": self.skipped,
            "tombstoned": self.tombstoned,
            "errors": list(self.errors),
            "aborted": self.aborted,
        }


def _now_ms() -> int:
    return int(time.time() * 1000)


def _int_from_hash(text: str) -> int:
    """把任意字符串映射为 LanceDB 要求的正整数 document_id。"""
    digest = hashlib.sha256(text.encode()).digest()
    n = int.from_bytes(digest[:8], "big") & 0x7FFFFFFFFFFFFFFF
    return n if n > 0 else 1


def _resolve_document_id(doc_id: str | int | None, *, record_hash: str) -> int:
    """解析 LanceDB ``document_id``（int）。

    - 新文档：用 ``record_hash`` 稳定派生（同内容重试 id 一致）
    - 覆盖 / bump：复用库内已有 id（ExistingDoc.doc_id，可为 int 或数字字符串）
    """
    if doc_id is None:
        return _int_from_hash(record_hash)
    if isinstance(doc_id, int):
        return doc_id
    s = str(doc_id)
    if s.isdigit():
        return int(s)
    return _int_from_hash(s)


def _build_insert_document(
    case: CaseDict,
    *,
    kh_code: str,
    polarity: str,
    record_hash: str,
    question_hash: str,
    refined_knowledge: str,
    refine_status: str,
    refine_attempts: int,
    refined_at_ms: int,
    doc_id: str | int | None = None,
    case_uuid: str | None = None,
) -> dict:
    """构造 v2 GenericDocumentInput dict。"""
    return {
        "document_id": _resolve_document_id(doc_id, record_hash=record_hash),
        "content": case.get("questionContent") or "",
        "content_tokenized": "",   # 服务端 fallback 简单分词
        "vector": [],              # 服务端按 content 自动 embed
        "metadata": {
            "kh_code": kh_code,
            "source": "case_refinery",
            "case_uuid": case_uuid or uuid.uuid4().hex,

            # 原始 5 字段
            "question_content": case.get("questionContent") or "",
            "original_answer": case.get("originalAnswer") or "",
            "original_thinking": case.get("originalThinking") or "",
            "answer_content": case.get("answerContent") or "",
            "thinking": case.get("thinking") or "",

            # refine 产物
            "refined_knowledge": refined_knowledge,
            "refine_status": refine_status,         # "refined" | "raw_fallback"
            "refine_attempts": int(refine_attempts),
            "refined_at": int(refined_at_ms),

            # 标签
            "case_polarity": polarity,              # "positive" | "negative"
            "expert_revised": polarity == "negative",

            # 去重 / 同 question 覆盖
            "record_hash": record_hash,
            "question_hash": question_hash,

            # 调度
            "ingest_ts": _now_ms(),
            "schema_version": 1,
            "tombstoned": False,
        },
    }


def _build_bump_document(
    existing_doc: ExistingDoc,
    *,
    kh_code: str,
) -> dict:
    """raw_fallback 上限内但本轮 refine 又失败时，只更新 attempts + 时间戳。

    与 ``tombstone_docs`` 同样依赖 ``mode=merge_by_chunk_id``。content / vector 留空，
    与 :meth:`LanceDBV2Client.tombstone_docs` 中的注释一致——若 v2 后续证实是「整行
    替换」，需要切到「拉 content 后回写」方案。
    """
    return {
        "document_id": _resolve_document_id(
            existing_doc.doc_id, record_hash=existing_doc.record_hash
        ),
        "content": "",
        "content_tokenized": "",
        "vector": [],
        "metadata": {
            "kh_code": kh_code,
            "source": "case_refinery",
            "record_hash": existing_doc.record_hash,
            "question_hash": existing_doc.question_hash,
            "refine_status": "raw_fallback",
            "refine_attempts": existing_doc.refine_attempts + 1,
            "refined_at": 0,
            "ingest_ts": _now_ms(),
            "schema_version": 1,
            "tombstoned": False,
        },
    }


async def run_once(
    kh_code: str,
    *,
    settings: Settings | None = None,
    lancedb_client: LanceDBV2Client | None = None,
) -> RunSummary:
    """对单个 khCode 跑一轮 ingestion。"""

    s = settings or get_settings()
    summary = RunSummary(kh_code=kh_code, started_at_ms=_now_ms())

    own_client = lancedb_client is None
    cli = lancedb_client or LanceDBV2Client(settings=s)

    try:
        try:
            cases = await fetch_cases(kh_code, settings=s)
        except UpstreamError as e:
            summary.aborted = f"upstream_error: {e}"
            logger.warning("[runner] %s 上游失败，整轮 abort: %s", kh_code, e)
            return summary

        summary.upstream_fetched = len(cases)
        if not cases:
            summary.aborted = "upstream_empty"
            logger.info(
                "[runner] %s 上游 0 条 case，整轮 no-op（不触发删除）", kh_code
            )
            return summary

        try:
            existing = await cli.list_existing(kh_code)
        except LanceDBError as e:
            summary.aborted = f"lancedb_list_failed: {e}"
            logger.warning("[runner] %s lancedb 列表失败，整轮 abort: %s", kh_code, e)
            return summary

        # 处理每条 case
        for raw in cases:
            try:
                await _process_one_case(
                    raw,
                    kh_code=kh_code,
                    existing=existing,
                    summary=summary,
                    settings=s,
                    cli=cli,
                )
            except Exception as e:  # noqa: BLE001
                msg = f"case_failed: {type(e).__name__}: {e}"
                summary.errors.append(msg)
                logger.exception("[runner] %s 单 case 处理异常: %s", kh_code, e)

        return summary
    finally:
        summary.finished_at_ms = _now_ms()
        if own_client:
            await cli.aclose()


async def _process_one_case(
    raw: CaseDict,
    *,
    kh_code: str,
    existing: ExistingIndex,
    summary: RunSummary,
    settings: Settings,
    cli: LanceDBV2Client,
) -> None:
    polarity = classifier.classify(raw)
    rh = classifier.record_hash(raw)
    qh = classifier.question_hash(raw)

    decision = dedupe.decide(
        rh, qh, existing,
        refine_max_attempts=settings.refine_max_attempts,
    )

    logger.debug(
        "[runner] %s decision: polarity=%s rh=%s reason=%s need_refine=%s "
        "on_success=%s on_failure=%s tomb=%d",
        kh_code, polarity, rh[:8], decision.reason,
        decision.need_refine, decision.on_refine_success,
        decision.on_refine_failure, len(decision.tombstone_doc_ids),
    )

    # 决策为 skip：直接 short-circuit
    if not decision.need_refine and decision.on_refine_success == "skip":
        summary.skipped += 1
        return

    # 调 LLM
    if decision.need_refine:
        result = await refiner.refine(raw, polarity, settings=settings)
    else:
        # 极少：need_refine=False 但还需要做动作的分支当前不存在；保留兜底
        result = refiner.RefineResult(ok=False, error="no_refine_needed_but_action_required")

    chosen_action = (
        decision.on_refine_success if result.ok else decision.on_refine_failure
    )

    if chosen_action == "skip":
        summary.skipped += 1
        return

    if chosen_action == "insert":
        if result.ok:
            doc = _build_insert_document(
                raw, kh_code=kh_code, polarity=polarity,
                record_hash=rh, question_hash=qh,
                refined_knowledge=result.refined_knowledge,
                refine_status="refined",
                refine_attempts=1,
                refined_at_ms=_now_ms(),
            )
            await cli.upsert_one(kh_code, doc, mode="append")
            summary.inserted_refined += 1
        else:
            doc = _build_insert_document(
                raw, kh_code=kh_code, polarity=polarity,
                record_hash=rh, question_hash=qh,
                refined_knowledge="",
                refine_status="raw_fallback",
                refine_attempts=1,
                refined_at_ms=0,
            )
            await cli.upsert_one(kh_code, doc, mode="append")
            summary.inserted_raw_fallback += 1
            logger.info(
                "[runner] %s refine 失败 fallback 入库: %s",
                kh_code, result.error,
            )

    elif chosen_action == "overwrite_raw_fallback":
        assert decision.existing_doc_id is not None
        doc = _build_insert_document(
            raw, kh_code=kh_code, polarity=polarity,
            record_hash=rh, question_hash=qh,
            refined_knowledge=result.refined_knowledge,
            refine_status="refined",
            refine_attempts=decision.existing_refine_attempts + 1,
            refined_at_ms=_now_ms(),
            doc_id=decision.existing_doc_id,
        )
        await cli.upsert_one(kh_code, doc, mode="merge_by_chunk_id")
        summary.overwritten_to_refined += 1

    elif chosen_action == "bump_attempts":
        assert decision.existing_doc_id is not None
        # bump 不需要重 refine 时使用 existing_doc 信息构造 doc；这里我们已经
        # 拥有 record_hash / question_hash（来自本轮 case），但 ExistingDoc 也有
        # 等价值，二者一致。
        from .lancedb_client import ExistingDoc as _ED
        bump_target = _ED(
            doc_id=decision.existing_doc_id,
            record_hash=rh,
            question_hash=qh,
            refine_status="raw_fallback",
            refine_attempts=decision.existing_refine_attempts,
            tombstoned=False,
        )
        doc = _build_bump_document(bump_target, kh_code=kh_code)
        await cli.upsert_one(kh_code, doc, mode="merge_by_chunk_id")
        summary.bumped_attempts += 1
        logger.info(
            "[runner] %s bump_attempts -> %d: %s",
            kh_code, decision.existing_refine_attempts + 1, result.error,
        )

    else:  # pragma: no cover
        raise RuntimeError(f"unknown action: {chosen_action}")

    # 入库成功后软删同 question 旧版（仅在 insert / overwrite 路径会带 victims）
    if decision.tombstone_doc_ids:
        # 把要 tombstone 的 doc_id 还原成 ExistingDoc（从 existing 索引里拿原始信息）
        victims_by_id: dict[str, ExistingDoc] = {}
        for doc_list in existing.by_question_hash.values():
            for ed in doc_list:
                victims_by_id[ed.doc_id] = ed
        targets = [
            victims_by_id[d] for d in decision.tombstone_doc_ids
            if d in victims_by_id
        ]
        if targets:
            n = await cli.tombstone_docs(kh_code, targets)
            summary.tombstoned += n


async def run_all(
    *,
    settings: Settings | None = None,
) -> list[RunSummary]:
    """先拉 ``listAllKh``，再遍历所有 khCode。"""
    s = settings or get_settings()
    try:
        kh_codes = await fetch_all_kh_codes(settings=s)
    except UpstreamError as e:
        logger.warning("[runner] 拉取 khCode 列表失败，整轮 abort: %s", e)
        return []

    if not kh_codes:
        logger.info("[runner] listAllKh 返回空，无任何 khCode 可调度")
        return []

    cli = LanceDBV2Client(settings=s)
    summaries: list[RunSummary] = []
    try:
        for kh in kh_codes:
            summaries.append(
                await run_once(kh, settings=s, lancedb_client=cli)
            )
    finally:
        await cli.aclose()
    return summaries
