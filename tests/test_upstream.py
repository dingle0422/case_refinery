"""upstream 接口测试：listAllKh + listCorpusByKhCode。"""

from __future__ import annotations

import json

import pytest

from case_refinery.config import Settings, set_settings
from case_refinery.pipeline.upstream import UpstreamError, fetch_all_kh_codes, fetch_cases


@pytest.fixture(autouse=True)
def _settings() -> None:
    set_settings(Settings(
        upstream_base_url="http://stub-upstream",
        upstream_list_all_path="/api/kh/listAllKh",
        upstream_list_path="/api/kh/listCorpusByKhCode",
        upstream_timeout_s=5.0,
    ))


@pytest.mark.asyncio
async def test_fetch_all_kh_codes_success() -> None:
    class FakeResp:
        def __init__(self, status_code: int, body: dict):
            self.status_code = status_code
            self._body = body
            self.text = json.dumps(body)

        def json(self) -> dict:
            return self._body

    async def fake_post(url, json=None, headers=None):  # noqa: ARG001
        return FakeResp(200, {
            "success": True,
            "code": "OK",
            "message": "成功",
            "data": [
                {"code": "KH001"},
                {"code": "KH002"},
                {"code": "KH001"},
                {"id": 3},
            ],
        })

    class FakeClient:
        post = staticmethod(fake_post)

    result = await fetch_all_kh_codes(client=FakeClient())  # type: ignore[arg-type]
    assert result == ["KH001", "KH002"]


@pytest.mark.asyncio
async def test_fetch_cases_success_and_only_required_fields() -> None:
    class FakeResp:
        def __init__(self, status_code: int, body: dict):
            self.status_code = status_code
            self._body = body
            self.text = json.dumps(body)

        def json(self) -> dict:
            return self._body

    async def fake_post(url, json=None, headers=None):  # noqa: ARG001
        assert json == {"khCode": "KH001"}
        return FakeResp(200, {
            "success": True,
            "code": "OK",
            "message": "成功",
            "data": [{
                "questionContent": "Q1",
                "originalAnswer": "OA1",
                "originalThinking": "OT1",
                "answerContent": "AC1",
                "thinking": "TH1",
                "extraField": "ignored",
            }],
        })

    class FakeClient:
        post = staticmethod(fake_post)

    result = await fetch_cases("KH001", client=FakeClient())  # type: ignore[arg-type]
    assert result == [{
        "questionContent": "Q1",
        "originalAnswer": "OA1",
        "originalThinking": "OT1",
        "answerContent": "AC1",
        "thinking": "TH1",
    }]


@pytest.mark.asyncio
async def test_fetch_all_kh_codes_fail_raises() -> None:
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

    with pytest.raises(UpstreamError, match="success=false"):
        await fetch_all_kh_codes(client=FakeClient())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_fetch_cases_http_error_raises() -> None:
    class FakeResp:
        status_code = 500
        text = "server error"

        @staticmethod
        def json() -> dict:
            return {}

    class FakeClient:
        @staticmethod
        async def post(url, json=None, headers=None):  # noqa: ARG001
            return FakeResp()

    with pytest.raises(UpstreamError, match="HTTP 500"):
        await fetch_cases("KH001", client=FakeClient())  # type: ignore[arg-type]
