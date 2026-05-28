"""dedupe 决策表测试，覆盖 plan section 6 的 5 行。"""

from __future__ import annotations

from case_refinery.pipeline import dedupe
from case_refinery.pipeline.lancedb_client import ExistingDoc, ExistingIndex


def _index(*docs: ExistingDoc) -> ExistingIndex:
    idx = ExistingIndex()
    for d in docs:
        idx.by_record_hash[d.record_hash] = d
        idx.by_question_hash.setdefault(d.question_hash, []).append(d)
    return idx


def test_new_case_need_refine_and_insert_both_paths() -> None:
    d = dedupe.decide(
        record_hash="rh_new",
        question_hash="qh_new",
        existing=ExistingIndex(),
        refine_max_attempts=5,
    )
    assert d.need_refine is True
    assert d.on_refine_success == "insert"
    assert d.on_refine_failure == "insert"
    assert d.existing_doc_id is None
    assert d.tombstone_doc_ids == []
    assert d.reason == "new_case"


def test_hash_match_refined_is_skipped() -> None:
    existed = ExistingDoc(
        doc_id="doc-1",
        record_hash="rh_1",
        question_hash="qh_1",
        refine_status="refined",
        refine_attempts=2,
        tombstoned=False,
    )
    d = dedupe.decide(
        record_hash="rh_1",
        question_hash="qh_1",
        existing=_index(existed),
        refine_max_attempts=5,
    )
    assert d.need_refine is False
    assert d.on_refine_success == "skip"
    assert d.on_refine_failure == "skip"
    assert d.existing_doc_id == "doc-1"
    assert d.reason == "hash_match_refined"


def test_raw_fallback_within_limit_triggers_overwrite_or_bump() -> None:
    existed = ExistingDoc(
        doc_id="doc-1",
        record_hash="rh_1",
        question_hash="qh_1",
        refine_status="raw_fallback",
        refine_attempts=2,
        tombstoned=False,
    )
    d = dedupe.decide(
        record_hash="rh_1",
        question_hash="qh_1",
        existing=_index(existed),
        refine_max_attempts=5,
    )
    assert d.need_refine is True
    assert d.on_refine_success == "overwrite_raw_fallback"
    assert d.on_refine_failure == "bump_attempts"
    assert d.existing_doc_id == "doc-1"
    assert d.existing_refine_attempts == 2
    assert d.tombstone_doc_ids == []  # 同 record_hash 不算 victim


def test_raw_fallback_at_max_attempts_is_skipped() -> None:
    existed = ExistingDoc(
        doc_id="doc-1",
        record_hash="rh_1",
        question_hash="qh_1",
        refine_status="raw_fallback",
        refine_attempts=5,
        tombstoned=False,
    )
    d = dedupe.decide(
        record_hash="rh_1",
        question_hash="qh_1",
        existing=_index(existed),
        refine_max_attempts=5,
    )
    assert d.need_refine is False
    assert d.on_refine_success == "skip"
    assert d.on_refine_failure == "skip"
    assert "max_attempts" in d.reason


def test_same_question_other_record_hash_listed_as_tombstone_victim() -> None:
    """同 question_hash 但不同 record_hash 的旧版应进入 tombstone_doc_ids。"""
    sibling = ExistingDoc(
        doc_id="doc-sibling",
        record_hash="rh_old_version",
        question_hash="qh_same",
        refine_status="refined",
        refine_attempts=1,
        tombstoned=False,
    )
    idx = _index(sibling)

    d = dedupe.decide(
        record_hash="rh_new_version",
        question_hash="qh_same",
        existing=idx,
        refine_max_attempts=5,
    )
    assert d.need_refine is True
    assert d.on_refine_success == "insert"
    assert d.tombstone_doc_ids == ["doc-sibling"]


def test_overwrite_raw_fallback_does_not_tombstone_self() -> None:
    """overwrite_raw_fallback 时被复用的 doc_id 不能出现在 victims。"""
    me = ExistingDoc(
        doc_id="doc-me",
        record_hash="rh_x",
        question_hash="qh_x",
        refine_status="raw_fallback",
        refine_attempts=1,
        tombstoned=False,
    )
    sibling = ExistingDoc(
        doc_id="doc-sibling",
        record_hash="rh_y_other",
        question_hash="qh_x",
        refine_status="refined",
        refine_attempts=1,
        tombstoned=False,
    )
    idx = _index(me, sibling)

    d = dedupe.decide(
        record_hash="rh_x",
        question_hash="qh_x",
        existing=idx,
        refine_max_attempts=5,
    )
    assert d.on_refine_success == "overwrite_raw_fallback"
    assert d.existing_doc_id == "doc-me"
    assert "doc-me" not in d.tombstone_doc_ids
    assert d.tombstone_doc_ids == ["doc-sibling"]
