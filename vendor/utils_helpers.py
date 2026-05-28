"""retry 装饰器。

复制自 page-know-how/utils/helpers.py（仅保留 retry 实现），不引入主仓任何依赖。
"""

from __future__ import annotations

import functools
import logging
import time

logger = logging.getLogger(__name__)


def retry(max_retries: int = 3, sleep_seconds: float = 5.0):
    """重试装饰器，在函数抛出异常时自动重试。

    与主仓 ``utils.helpers.retry`` 行为完全一致：
    - 抛错时记一条 warning 然后 ``time.sleep`` 等待
    - 全部失败后向上抛最后一次的异常
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception: Exception | None = None
            for attempt in range(1, max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:  # noqa: BLE001
                    last_exception = e
                    logger.warning(
                        "[retry] %s 第%d次调用失败: %s",
                        func.__name__, attempt, e,
                    )
                    if attempt < max_retries:
                        time.sleep(sleep_seconds)
            assert last_exception is not None
            raise last_exception

        return wrapper

    return decorator
