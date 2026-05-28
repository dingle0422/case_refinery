"""case 极性判定 + 哈希计算。

判定规则（与用户对齐）：

- 当 ``originalAnswer == answerContent`` **且** ``originalThinking == thinking`` 时，
  视为 ``positive``（专家未修改）
- 任意一项不等 -> ``negative``（专家修改过）

哈希：

- ``record_hash``：5 字段 sorted-JSON 的 sha256，用于"完全相同 case"去重。
- ``question_hash``：仅 ``questionContent`` 的 sha256，用于"同问题反复修订"覆盖。
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from .upstream import CaseDict

Polarity = Literal["positive", "negative"]


def classify(case: CaseDict) -> Polarity:
    """positive: 5 字段中"原始 vs 修正后"两类完全一致；否则 negative。"""
    if (
        case["originalAnswer"] == case["answerContent"]
        and case["originalThinking"] == case["thinking"]
    ):
        return "positive"
    return "negative"


def is_expert_revised(case: CaseDict) -> bool:
    return classify(case) == "negative"


def record_hash(case: CaseDict) -> str:
    """5 字段 sha256。

    使用 ``sort_keys=True`` + ``ensure_ascii=False`` 让同样内容跨进程/跨语言产物一致。
    """
    payload = json.dumps(
        {
            "q":  case["questionContent"],
            "oa": case["originalAnswer"],
            "ot": case["originalThinking"],
            "ac": case["answerContent"],
            "th": case["thinking"],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def question_hash(case: CaseDict) -> str:
    """``questionContent`` sha256，避免长 question 在 where 过滤时的字符串比对开销。"""
    return hashlib.sha256(
        (case["questionContent"] or "").encode("utf-8")
    ).hexdigest()
