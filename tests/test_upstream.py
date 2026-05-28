"""upstream 入参兼容：khCode 前缀 + policyId 回退。"""

from __future__ import annotations

import json

import httpx
import pytest

from case_refinery.config import Settings, set_settings
from case_refinery.pipeline.upstream import (
    UpstreamError,
    build_upstream_payloads,
    fetch_cases,
)


@pytest.fixture(autouse=True)
def _settings() -> None:
    set_settings(Settings(
        upstream_base_url="http://stub-upstream",
        upstream_list_path="/api/kh/listCorpusByPolicyId",
        upstream_kh_field="auto",
        upstream_timeout_s=5.0,
    ))


def test_kh_code_prefix_from_policy_id() -> None:
    pid = "KH1493204307733168128_20260519101916"
    assert Settings.kh_code_prefix(pid) == "KH1493204307733168128"
    assert Settings.kh_code_prefix("KH123") == "KH123"


def test_build_payloads_auto_order() -> None:
    pid = "KH1493204307733168128_20260519101916"
    payloads = build_upstream_payloads(pid)
    assert len(payloads) == 2
    assert payloads[0] == ("khCode", {"khCode": "KH1493204307733168128"})
    assert payloads[1] == ("policyId", {"policyId": pid})


def test_build_payloads_force_policy_id() -> None:
    set_settings(Settings(
        upstream_base_url="http://stub",
        upstream_list_path="/x",
        upstream_kh_field="policyId",
    ))
    pid = "KH1493204307733168128_20260519101916"
    assert build_upstream_payloads(pid) == [
        ("policyId", {"policyId": pid}),
    ]


@pytest.mark.asyncio
async def test_fetch_cases_fallback_to_policy_id() -> None:
    pid = "KH1493204307733168128_20260519101916"
    case = {"questionContent": "Q1", "originalAnswer": "a", "originalThinking": "t",
            "answerContent": "a", "thinking": "t"}

    class FakeResp:
        def __init__(self, status_code: int, body: dict):
            self.status_code = status_code
            self._body = body
            self.text = json.dumps(body)

        def json(self) -> dict:
            return self._body

    async def fake_post(url, json=None, headers=None):  # noqa: ARG001
        if json.get("khCode"):
            return FakeResp(200, {
                "success": False,
                "code": "DKY-00000",
                "message": "请输入policyId",
                "data": None,
            })
        if json.get("policyId") == pid:
            return FakeResp(200, {
                "success": True,
                "code": "OK",
                "message": "成功",
                "data": [case],
            })
        raise AssertionError(f"unexpected payload: {json}")

    class FakeClient:
        post = staticmethod(fake_post)

    result = await fetch_cases(pid, client=FakeClient())  # type: ignore[arg-type]
    assert len(result) == 1
    assert result[0]["questionContent"] == "Q1"


@pytest.mark.asyncio
async def test_fetch_cases_all_fail_raises() -> None:
    class FakeResp:
        status_code = 200
        text = "{}"

        @staticmethod
        def json() -> dict:
            return {"success": False, "code": "X", "message": "fail", "data": None}

    class FakeClient:
        @staticmethod
        async def post(url, json=None, headers=None):  # noqa: ARG001
            return FakeResp()

    with pytest.raises(UpstreamError, match="全部 payload 均失败"):
        await fetch_cases(
            "KH1493204307733168128_20260519101916",
            client=FakeClient(),  # type: ignore[arg-type]
        )
