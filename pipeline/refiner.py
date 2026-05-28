"""LLM refine 调用 + 输出清洗。

异常策略（与 dedupe 决策表对齐）：

- **成功**：返回 ``RefineResult(ok=True, refined_knowledge=<cleaned text>)``，
  调用方据此写入 ``refine_status="refined"``。
- **失败**：返回 ``RefineResult(ok=False, error=<reason>)``，调用方据此写入
  ``refine_status="raw_fallback"`` 或 ``refine_attempts += 1``。
- 失败原因覆盖：LLM 抛错 / 输出为空 / 清洗后剩余字符过少。

为了在 FastAPI 异步上下文里复用同步的 ``vendor.llm_client.chat``，本模块通过
``asyncio.to_thread`` 把同步调用放进线程池，避免阻塞事件循环。
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Literal

from ..config import Settings, get_settings
from ..vendor.llm_client import chat as llm_chat
from . import prompts
from .upstream import CaseDict

logger = logging.getLogger(__name__)


Polarity = Literal["positive", "negative"]


@dataclass(frozen=True)
class RefineResult:
    ok: bool
    refined_knowledge: str = ""
    error: str = ""


# 清洗后的有效文本最少字符数；过短视为无意义产物（如 LLM 只输出"无法回答"等）。
_MIN_VALID_LEN = 60

_THINK_BLOCK_RE = re.compile(
    r"<think[^>]*>.*?</think>", re.DOTALL | re.IGNORECASE
)


def _clean_llm_output(text: str) -> str:
    """剥离 <think>...</think> 块、首尾空白。

    与主仓 ``utils.helpers.split_think_block`` 的差异：这里我们不需要单独保留 think
    段，refine 产物只关心正文。
    """
    if not text:
        return ""
    cleaned = _THINK_BLOCK_RE.sub("", text)
    return cleaned.strip()


async def refine(
    case: CaseDict,
    polarity: Polarity,
    *,
    settings: Settings | None = None,
) -> RefineResult:
    """对单条 case 跑一次 refine。"""

    s = settings or get_settings()

    if polarity == "positive":
        system, user = prompts.render_positive(case)
    else:
        system, user = prompts.render_negative(case)

    try:
        raw = await asyncio.to_thread(
            llm_chat,
            user,
            s.llm_vendor,
            s.llm_model,
            system,
            s.llm_enable_thinking,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[refiner] LLM 调用失败 polarity=%s: %s", polarity, e)
        return RefineResult(ok=False, error=f"llm_call_failed: {e}")

    cleaned = _clean_llm_output(raw)
    if len(cleaned) < _MIN_VALID_LEN:
        logger.warning(
            "[refiner] LLM 输出过短（%d 字符），视为失败 polarity=%s raw=%r",
            len(cleaned), polarity, raw[:200],
        )
        return RefineResult(
            ok=False, error=f"output_too_short: len={len(cleaned)}"
        )

    return RefineResult(ok=True, refined_knowledge=cleaned)
