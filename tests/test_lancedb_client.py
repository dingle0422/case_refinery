"""lancedb_client 重试与容错测试。"""

from __future__ import annotations

import httpx
import pytest

from case_refinery.config import Settings
from case_refinery.pipeline import lancedb_client
from case_refinery.pipeline.lancedb_client import LanceDBError, LanceDBV2Client


class _SequencedClient:
    def __init__(self, steps: list[httpx.Response | Exception]) -> None:
        self._steps = list(steps)
        self.calls = 0

    async def request(self, method: str, path: str, **kwargs):  # noqa: ARG002
        step = self._steps[self.calls]
        self.calls += 1
        if isinstance(step, Exception):
            raise step
        return step


def _resp(status_code: int, *, body: dict | None = None) -> httpx.Response:
    req = httpx.Request("GET", "http://stub/v2/test")
    if body is None:
        return httpx.Response(status_code, request=req, text="")
    return httpx.Response(status_code, request=req, json=body)


@pytest.mark.asyncio
async def test_request_retries_on_timeout_then_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(lancedb_client.asyncio, "sleep", _fake_sleep)

    fake = _SequencedClient([
        httpx.ReadTimeout("timeout"),
        _resp(200, body={"ok": True}),
    ])
    cli = LanceDBV2Client(
        settings=Settings(
            lancedb_base_url="http://stub",
            lancedb_timeout_s=1.0,
            lancedb_max_retries=2,
            lancedb_retry_backoff_s=0.2,
            lancedb_retry_backoff_max_s=1.0,
        ),
        client=fake,  # type: ignore[arg-type]
    )

    out = await cli._request("GET", "/v2/capabilities")

    assert out == {"ok": True}
    assert fake.calls == 2
    assert sleeps == [0.2]


@pytest.mark.asyncio
async def test_request_retries_on_5xx_then_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(lancedb_client.asyncio, "sleep", _fake_sleep)

    fake = _SequencedClient([
        _resp(500, body={"error": "server"}),
        _resp(500, body={"error": "server"}),
    ])
    cli = LanceDBV2Client(
        settings=Settings(
            lancedb_base_url="http://stub",
            lancedb_timeout_s=1.0,
            lancedb_max_retries=1,
            lancedb_retry_backoff_s=0.3,
            lancedb_retry_backoff_max_s=1.0,
        ),
        client=fake,  # type: ignore[arg-type]
    )

    with pytest.raises(LanceDBError, match="-> 500"):
        await cli._request("POST", "/v2/collections/case_x/documents:upsert", json={})
    assert fake.calls == 2
    assert sleeps == [0.3]


@pytest.mark.asyncio
async def test_request_does_not_retry_on_4xx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(lancedb_client.asyncio, "sleep", _fake_sleep)

    fake = _SequencedClient([
        _resp(422, body={"error": "bad request"}),
        _resp(200, body={"ok": True}),
    ])
    cli = LanceDBV2Client(
        settings=Settings(
            lancedb_base_url="http://stub",
            lancedb_timeout_s=1.0,
            lancedb_max_retries=3,
            lancedb_retry_backoff_s=0.1,
            lancedb_retry_backoff_max_s=1.0,
        ),
        client=fake,  # type: ignore[arg-type]
    )

    with pytest.raises(LanceDBError, match="-> 422"):
        await cli._request("POST", "/v2/collections/case_x/documents:upsert", json={})
    assert fake.calls == 1
    assert sleeps == []
