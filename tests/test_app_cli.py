"""app CLI 参数覆盖测试。"""

from __future__ import annotations

import os
from contextlib import contextmanager

import pytest

from case_refinery import app
from case_refinery.config import Settings, get_settings, set_settings


@contextmanager
def _preserve_env(key: str):
    old = os.getenv(key)
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = old


def test_apply_cli_overrides_schedule_interval_seconds() -> None:
    with _preserve_env("CASE_REFINERY_SCHEDULE_INTERVAL_SECONDS"):
        set_settings(Settings())
        app._apply_cli_overrides(["--schedule_interval_seconds", "10"])
        assert os.getenv("CASE_REFINERY_SCHEDULE_INTERVAL_SECONDS") == "10"
        assert get_settings().schedule_interval_seconds == 10


def test_apply_cli_overrides_reject_negative() -> None:
    with _preserve_env("CASE_REFINERY_SCHEDULE_INTERVAL_SECONDS"):
        set_settings(Settings())
        with pytest.raises(SystemExit):
            app._apply_cli_overrides(["--schedule_interval_seconds", "-1"])
