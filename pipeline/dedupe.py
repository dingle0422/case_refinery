"""去重 + 覆盖决策表。

输入：

- 本轮一条 case 的 ``record_hash`` / ``question_hash``
- :class:`lancedb_client.ExistingIndex`（库内现状，已剔除 tombstoned）
- ``refine_max_attempts``（raw_fallback 累计尝试上限）

输出 :class:`Decision`：

- ``need_refine``：是否需要调 LLM（dedupe 阶段已经能判断的，不烧 token 跳过）
- ``on_refine_success`` / ``on_refine_failure``：runner 在拿到 refine 结果后据此分支
- ``existing_doc_id``：复用的 doc_id（``overwrite_raw_fallback`` / ``bump_attempts`` 用）
- ``existing_refine_attempts``：bump 用
- ``tombstone_doc_ids``：本次入库成功后，同 question_hash 旧版要软删的 doc_id 列表
- ``reason``：日志用
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .lancedb_client import ExistingIndex


Action = Literal[
    "skip",                  # 不写任何东西
    "insert",                # 新 doc 插入（refined / raw_fallback 都用此）
    "overwrite_raw_fallback",  # 同 doc_id merge，把 raw_fallback 升级成 refined
    "bump_attempts",         # 同 doc_id merge，只把 refine_attempts +1
]


@dataclass
class Decision:
    need_refine: bool
    on_refine_success: Action
    on_refine_failure: Action
    existing_doc_id: str | None = None
    existing_refine_attempts: int = 0
    tombstone_doc_ids: list[str] = field(default_factory=list)
    reason: str = ""


def _collect_tombstone_victims(
    question_hash: str,
    record_hash: str,
    existing: ExistingIndex,
    *,
    keep_doc_id: str | None = None,
) -> list[str]:
    """同 question_hash 但 record_hash 不同的旧版本 doc_id 列表（待软删）。

    ``keep_doc_id``：若本次是 overwrite_raw_fallback，被复用的 doc_id 不能被 tombstone
    （否则刚 merge 写完就被自己改成 tombstoned=true）。当前 dedupe 的 overwrite_raw_fallback
    场景下，复用的就是同 record_hash 的旧 doc，本来就不在差集里——但显式排除一道保险。
    """
    victims: list[str] = []
    for ed in existing.by_question_hash.get(question_hash, []):
        if ed.record_hash == record_hash:
            continue
        if keep_doc_id and ed.doc_id == keep_doc_id:
            continue
        victims.append(ed.doc_id)
    return victims


def decide(
    record_hash: str,
    question_hash: str,
    existing: ExistingIndex,
    *,
    refine_max_attempts: int,
) -> Decision:
    """根据 plan section 6 的决策表生成 :class:`Decision`。"""

    existing_doc = existing.by_record_hash.get(record_hash)

    if existing_doc is None:
        # 新 case：无论 refine 成败都要写入；同 question_hash 旧版本要清理。
        return Decision(
            need_refine=True,
            on_refine_success="insert",
            on_refine_failure="insert",
            tombstone_doc_ids=_collect_tombstone_victims(
                question_hash, record_hash, existing
            ),
            reason="new_case",
        )

    if existing_doc.refine_status == "refined":
        # 真去重：库内已是 refined 版本，本轮直接 skip。
        return Decision(
            need_refine=False,
            on_refine_success="skip",
            on_refine_failure="skip",
            existing_doc_id=existing_doc.doc_id,
            existing_refine_attempts=existing_doc.refine_attempts,
            reason="hash_match_refined",
        )

    # 库内是 raw_fallback：要不要重试 refine 取决于累计 attempts 是否超阈值
    if existing_doc.refine_attempts >= refine_max_attempts:
        return Decision(
            need_refine=False,
            on_refine_success="skip",
            on_refine_failure="skip",
            existing_doc_id=existing_doc.doc_id,
            existing_refine_attempts=existing_doc.refine_attempts,
            reason=f"raw_fallback_max_attempts({existing_doc.refine_attempts})",
        )

    return Decision(
        need_refine=True,
        on_refine_success="overwrite_raw_fallback",
        on_refine_failure="bump_attempts",
        existing_doc_id=existing_doc.doc_id,
        existing_refine_attempts=existing_doc.refine_attempts,
        tombstone_doc_ids=_collect_tombstone_victims(
            question_hash, record_hash, existing,
            keep_doc_id=existing_doc.doc_id,
        ),
        reason=f"raw_fallback_retry(attempt={existing_doc.refine_attempts + 1})",
    )
