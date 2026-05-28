"""runner 集成测试：mock upstream / lancedb / refiner，验证全路径行为。

覆盖场景：

1. 上游空 → 整轮 no-op，不调任何 upsert / tombstone
2. 上游异常 → aborted，不调任何 upsert
3. 新 case + refine 成功 → inserted_refined += 1
4. 新 case + refine 失败 → inserted_raw_fallback += 1
5. 库内 refined 同 hash → skipped += 1
6. 库内 raw_fallback + 本轮 refine 成功 → overwritten_to_refined += 1，doc_id 被复用
7. 库内 raw_fallback + 本轮 refine 失败 → bumped_attempts += 1
8. 同 question 新版进来 → tombstoned 旧版（refined）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from case_refinery.config import Settings, set_settings
from case_refinery.pipeline import refiner, runner
from case_refinery.pipeline.lancedb_client import (
    ExistingDoc,
    ExistingIndex,
    LanceDBV2Client,
)


@dataclass
class FakeLanceClient:
    """记录所有写操作的 fake，供 runner 集成测试用。"""

    existing: ExistingIndex = field(default_factory=ExistingIndex)
    upserts: list[tuple[str, dict, str]] = field(default_factory=list)  # (kh, doc, mode)
    tombstoned: list[ExistingDoc] = field(default_factory=list)

    async def list_existing(self, kh_code: str) -> ExistingIndex:  # noqa: ARG002
        return self.existing

    async def upsert_one(
        self, kh_code: str, document: dict, *, mode: str = "append"
    ) -> dict:
        self.upserts.append((kh_code, document, mode))
        return {"written": 1}

    async def tombstone_docs(
        self, kh_code: str, targets: list[ExistingDoc]  # noqa: ARG002
    ) -> int:
        self.tombstoned.extend(targets)
        return len(targets)

    async def aclose(self) -> None:
        return


@pytest.fixture(autouse=True)
def _settings_for_tests() -> None:
    set_settings(Settings(
        upstream_base_url="http://stub",
        upstream_list_all_path="/stub-all",
        upstream_list_path="/stub",
        lancedb_base_url="http://stub-lance",
        lancedb_api_key="",
        kh_codes=["KH_TEST"],
        schedule_cron_hour=0,
        schedule_cron_minute=0,
        schedule_interval_hours=0,
        schedule_interval_seconds=0,
        schedule_enabled=False,
        llm_vendor="servyou",
        llm_model="deepseek-v3.2-1163259bcc6c",
        refine_max_attempts=5,
    ))


def _make_case(q: str = "Q1", expert_revised: bool = False) -> dict[str, str]:
    oa = "原始回答 oa"
    ac = "专家修订回答 ac" if expert_revised else oa
    ot = "原始推理 ot"
    th = "专家修订推理 th" if expert_revised else ot
    return {
        "questionContent": q,
        "originalAnswer": oa,
        "originalThinking": ot,
        "answerContent": ac,
        "thinking": th,
    }


def _patch_fetch_cases(monkeypatch: pytest.MonkeyPatch, cases: list[dict] | None,
                       raise_exc: BaseException | None = None) -> None:
    async def _fake(kh_code: str, **kwargs):  # noqa: ARG001
        if raise_exc is not None:
            raise raise_exc
        return cases or []
    monkeypatch.setattr(runner, "fetch_cases", _fake)


def _patch_fetch_all_kh_codes(
    monkeypatch: pytest.MonkeyPatch, kh_codes: list[str] | None
) -> None:
    async def _fake(**kwargs):  # noqa: ARG001
        return kh_codes or []
    monkeypatch.setattr(runner, "fetch_all_kh_codes", _fake)


def _patch_refine(
    monkeypatch: pytest.MonkeyPatch,
    *,
    ok: bool,
    text: str = "## 业务场景\nXX\n## 关键判定要素\nXX\n## 适用政策/原则\nXX\n## 结论与处理建议\nXX",
) -> None:
    async def _fake(case, polarity, **kwargs):  # noqa: ARG001
        if ok:
            return refiner.RefineResult(ok=True, refined_knowledge=text)
        return refiner.RefineResult(ok=False, error="stub_failure")
    monkeypatch.setattr(refiner, "refine", _fake)


# ---------- 1: 上游空 ----------

@pytest.mark.asyncio
async def test_upstream_empty_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_fetch_cases(monkeypatch, [])
    cli = FakeLanceClient()

    summary = await runner.run_once("KH_TEST", lancedb_client=cli)  # type: ignore[arg-type]

    assert summary.aborted == "upstream_empty"
    assert summary.upstream_fetched == 0
    assert cli.upserts == []
    assert cli.tombstoned == []


# ---------- 2: 上游异常 ----------

@pytest.mark.asyncio
async def test_upstream_error_aborts_run(monkeypatch: pytest.MonkeyPatch) -> None:
    from case_refinery.pipeline.upstream import UpstreamError
    _patch_fetch_cases(monkeypatch, None, raise_exc=UpstreamError("boom"))
    cli = FakeLanceClient()

    summary = await runner.run_once("KH_TEST", lancedb_client=cli)  # type: ignore[arg-type]

    assert summary.aborted.startswith("upstream_error")
    assert cli.upserts == []
    assert cli.tombstoned == []


# ---------- 3: 新 case + refine 成功 ----------

@pytest.mark.asyncio
async def test_new_case_refine_success(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_fetch_cases(monkeypatch, [_make_case()])
    _patch_refine(monkeypatch, ok=True)
    cli = FakeLanceClient()

    summary = await runner.run_once("KH_TEST", lancedb_client=cli)  # type: ignore[arg-type]

    assert summary.inserted_refined == 1
    assert summary.inserted_raw_fallback == 0
    assert len(cli.upserts) == 1
    _, doc, mode = cli.upserts[0]
    assert mode == "append"
    md = doc["metadata"]
    assert isinstance(doc["document_id"], int)
    assert doc["metadata"]["refine_status"] == "refined"
    assert doc["metadata"]["case_polarity"] == "positive"
    assert doc["metadata"]["refined_knowledge"]
    assert doc["metadata"]["case_uuid"]


# ---------- 4: 新 case + refine 失败 ----------

@pytest.mark.asyncio
async def test_new_case_refine_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_fetch_cases(monkeypatch, [_make_case(expert_revised=True)])
    _patch_refine(monkeypatch, ok=False)
    cli = FakeLanceClient()

    summary = await runner.run_once("KH_TEST", lancedb_client=cli)  # type: ignore[arg-type]

    assert summary.inserted_raw_fallback == 1
    assert summary.inserted_refined == 0
    assert len(cli.upserts) == 1
    _, doc, mode = cli.upserts[0]
    assert mode == "append"
    md = doc["metadata"]
    assert md["refine_status"] == "raw_fallback"
    assert md["case_polarity"] == "negative"
    assert md["refined_knowledge"] == ""


# ---------- 5: 库内 refined 同 hash → skip ----------

@pytest.mark.asyncio
async def test_existing_refined_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    from case_refinery.pipeline import classifier

    case = _make_case()
    rh = classifier.record_hash(case)
    qh = classifier.question_hash(case)

    _patch_fetch_cases(monkeypatch, [case])
    # 不应被调用，但还是 patch 一下避免真发请求
    _patch_refine(monkeypatch, ok=True)

    cli = FakeLanceClient(existing=ExistingIndex(
        by_record_hash={rh: ExistingDoc(
            doc_id="doc-existing", record_hash=rh, question_hash=qh,
            refine_status="refined", refine_attempts=1, tombstoned=False,
        )},
        by_question_hash={qh: [ExistingDoc(
            doc_id="doc-existing", record_hash=rh, question_hash=qh,
            refine_status="refined", refine_attempts=1, tombstoned=False,
        )]},
    ))

    summary = await runner.run_once("KH_TEST", lancedb_client=cli)  # type: ignore[arg-type]

    assert summary.skipped == 1
    assert summary.inserted_refined == 0
    assert cli.upserts == []


# ---------- 6: 库内 raw_fallback + 本轮 refine 成功 → overwrite ----------

@pytest.mark.asyncio
async def test_raw_fallback_overwrite_success(monkeypatch: pytest.MonkeyPatch) -> None:
    from case_refinery.pipeline import classifier

    case = _make_case()
    rh = classifier.record_hash(case)
    qh = classifier.question_hash(case)

    _patch_fetch_cases(monkeypatch, [case])
    _patch_refine(monkeypatch, ok=True)

    cli = FakeLanceClient(existing=ExistingIndex(
        by_record_hash={rh: ExistingDoc(
            doc_id="9001", record_hash=rh, question_hash=qh,
            refine_status="raw_fallback", refine_attempts=2, tombstoned=False,
        )},
        by_question_hash={qh: [ExistingDoc(
            doc_id="9001", record_hash=rh, question_hash=qh,
            refine_status="raw_fallback", refine_attempts=2, tombstoned=False,
        )]},
    ))

    summary = await runner.run_once("KH_TEST", lancedb_client=cli)  # type: ignore[arg-type]

    assert summary.overwritten_to_refined == 1
    assert summary.bumped_attempts == 0
    assert len(cli.upserts) == 1
    _, doc, mode = cli.upserts[0]
    assert mode == "merge_by_chunk_id"
    assert doc["document_id"] == 9001
    md = doc["metadata"]
    assert md["refine_status"] == "refined"
    assert md["refine_attempts"] == 3


# ---------- 7: 库内 raw_fallback + 本轮 refine 失败 → bump ----------

@pytest.mark.asyncio
async def test_raw_fallback_bump_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    from case_refinery.pipeline import classifier

    case = _make_case()
    rh = classifier.record_hash(case)
    qh = classifier.question_hash(case)

    _patch_fetch_cases(monkeypatch, [case])
    _patch_refine(monkeypatch, ok=False)

    cli = FakeLanceClient(existing=ExistingIndex(
        by_record_hash={rh: ExistingDoc(
            doc_id="9002", record_hash=rh, question_hash=qh,
            refine_status="raw_fallback", refine_attempts=1, tombstoned=False,
        )},
        by_question_hash={qh: [ExistingDoc(
            doc_id="9002", record_hash=rh, question_hash=qh,
            refine_status="raw_fallback", refine_attempts=1, tombstoned=False,
        )]},
    ))

    summary = await runner.run_once("KH_TEST", lancedb_client=cli)  # type: ignore[arg-type]

    assert summary.bumped_attempts == 1
    assert summary.overwritten_to_refined == 0
    assert len(cli.upserts) == 1
    _, doc, mode = cli.upserts[0]
    assert mode == "merge_by_chunk_id"
    assert doc["document_id"] == 9002
    md = doc["metadata"]
    assert md["refine_status"] == "raw_fallback"
    assert md["refine_attempts"] == 2


# ---------- 8: 同 question 新版本进来 → tombstone 旧版本 ----------

@pytest.mark.asyncio
async def test_same_question_revision_tombstones_old_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from case_refinery.pipeline import classifier

    # 旧版本 doc
    old_case = _make_case()
    old_rh = classifier.record_hash(old_case)
    qh = classifier.question_hash(old_case)
    old_doc = ExistingDoc(
        doc_id="doc-old",
        record_hash=old_rh,
        question_hash=qh,
        refine_status="refined",
        refine_attempts=1,
        tombstoned=False,
    )

    # 新版本 case：同 question 但专家改过 → record_hash 不同
    new_case = _make_case(expert_revised=True)
    assert classifier.question_hash(new_case) == qh
    assert classifier.record_hash(new_case) != old_rh

    _patch_fetch_cases(monkeypatch, [new_case])
    _patch_refine(monkeypatch, ok=True)

    cli = FakeLanceClient(existing=ExistingIndex(
        by_record_hash={old_rh: old_doc},
        by_question_hash={qh: [old_doc]},
    ))

    summary = await runner.run_once("KH_TEST", lancedb_client=cli)  # type: ignore[arg-type]

    assert summary.inserted_refined == 1
    assert summary.tombstoned == 1
    assert len(cli.tombstoned) == 1
    assert cli.tombstoned[0].doc_id == "doc-old"


@pytest.mark.asyncio
async def test_run_all_uses_list_all_kh(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_fetch_all_kh_codes(monkeypatch, ["KH_A", "KH_B"])

    async def _fake_run_once(kh_code: str, **kwargs):  # noqa: ARG001
        return runner.RunSummary(
            kh_code=kh_code,
            started_at_ms=1,
            finished_at_ms=2,
            upstream_fetched=0,
        )

    monkeypatch.setattr(runner, "run_once", _fake_run_once)

    class DummyCli:
        def __init__(self, settings):  # noqa: ARG002
            pass

        async def aclose(self) -> None:
            return

    monkeypatch.setattr(runner, "LanceDBV2Client", DummyCli)

    summaries = await runner.run_all()
    assert [s.kh_code for s in summaries] == ["KH_A", "KH_B"]


@pytest.mark.asyncio
async def test_run_all_when_list_all_fails_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from case_refinery.pipeline.upstream import UpstreamError

    async def _fake(**kwargs):  # noqa: ARG001
        raise UpstreamError("boom")

    monkeypatch.setattr(runner, "fetch_all_kh_codes", _fake)
    summaries = await runner.run_all()
    assert summaries == []
