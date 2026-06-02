#!/usr/bin/env python3
"""测试 positive / negative refine prompt 的实际 LLM 生成效果，输出 Markdown 报告。"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from case_refinery.config import get_settings
from case_refinery.pipeline import prompts
from case_refinery.pipeline.refiner import _clean_llm_output
from case_refinery.vendor.llm_client import chat as llm_chat

# ---------------------------------------------------------------------------
# 测试样例：贴近真实财税问答场景
# ---------------------------------------------------------------------------

POSITIVE_CASE = {
    "questionContent": (
        "我公司是一般纳税人，从事农产品批发业务，从农户处收购自产农产品"
        "（未取得农产品收购发票），直接销售给下游超市。请问该笔销售"
        "能否按9%税率开具增值税专用发票？进项如何抵扣？"
    ),
    "answerContent": (
        "贵司为一般纳税人，从事农产品批发，从农户收购自产农产品后转售，"
        "属于购进农产品再销售。根据现行政策，购进用于销售的农产品，"
        "若未取得合规扣税凭证（如农产品收购发票或增值税专用发票），"
        "不得抵扣进项税额。销售环节应按13%税率（现行政策下农产品"
        "批发环节适用税率）开具增值税专用发票，不得按9%简易征收。"
        "建议：向农户代开或自行开具农产品收购发票，取得合规凭证后"
        "可按9%计算抵扣进项。"
    ),
    "thinking": (
        "1. 先判断纳税人身份：一般纳税人。\n"
        "2. 业务形态：收购自产农产品 → 批发销售，非自产。\n"
        "3. 关键凭证：未取得农产品收购发票，进项无合法扣税凭证。\n"
        "4. 销售税率：批发环节适用13%，非9%低税率场景。\n"
        "5. 处理建议：补开收购发票后可按9%计算抵扣。"
    ),
    "originalAnswer": (
        "贵司为一般纳税人，从事农产品批发，从农户收购自产农产品后转售，"
        "属于购进农产品再销售。根据现行政策，购进用于销售的农产品，"
        "若未取得合规扣税凭证（如农产品收购发票或增值税专用发票），"
        "不得抵扣进项税额。销售环节应按13%税率（现行政策下农产品"
        "批发环节适用税率）开具增值税专用发票，不得按9%简易征收。"
        "建议：向农户代开或自行开具农产品收购发票，取得合规凭证后"
        "可按9%计算抵扣进项。"
    ),
    "originalThinking": (
        "1. 先判断纳税人身份：一般纳税人。\n"
        "2. 业务形态：收购自产农产品 → 批发销售，非自产。\n"
        "3. 关键凭证：未取得农产品收购发票，进项无合法扣税凭证。\n"
        "4. 销售税率：批发环节适用13%，非9%低税率场景。\n"
        "5. 处理建议：补开收购发票后可按9%计算抵扣。"
    ),
}

NEGATIVE_CASE = {
    "questionContent": (
        "某制造业企业（一般纳税人）将自有厂房出租给关联公司，"
        "租金收入100万元/年，未单独核算。该租金收入应如何"
        "确认增值税应税行为及适用税率？"
    ),
    "originalAnswer": (
        "厂房出租属于不动产租赁，应适用9%税率，按100万元"
        "全额开具9%增值税专用发票，计入其他业务收入。"
    ),
    "originalThinking": (
        "不动产租赁 → 9%税率 → 全额开票。"
    ),
    "answerContent": (
        "厂房出租属于不动产经营租赁。若出租方在2016年4月30日前"
        "取得的不动产，可选择适用5%简易计税；2016年5月1日后"
        "取得的不动产，适用9%一般计税。本题未说明取得时间，"
        "需先核实不动产取得时点。若为一般计税，租金100万元"
        "按9%税率开具增值税专用发票，销项税额=100/(1+9%)*9%；"
        "若为简易计税，按5%征收率开具普通发票。建议单独核算"
        "租赁业务，避免与主业混合影响税负。"
    ),
    "thinking": (
        "1. 行为定性：不动产经营租赁。\n"
        "2. 关键判定：不动产取得时间（2016.4.30前后）决定计税方法。\n"
        "3. 一般计税9% vs 简易计税5% 二选一。\n"
        "4. 未说明取得时间 → 不能直接断定9%。\n"
        "5. 需单独核算，避免混合。"
    ),
}

OUTPUT_PATH = ROOT / "prompts_test_results.md"


def _run_prompt(label: str, system: str, user: str, settings) -> dict:
    print(f"[{label}] 调用 LLM ...")
    raw = llm_chat(
        user,
        settings.llm_vendor,
        settings.llm_model,
        system,
        settings.llm_enable_thinking,
    )
    cleaned = _clean_llm_output(raw)
    return {"raw": raw, "cleaned": cleaned, "ok": len(cleaned) >= 60}


def _section(title: str, body: str) -> str:
    return f"### {title}\n\n{body}\n"


def build_report(pos_result: dict, neg_result: dict, settings) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    pos_sys, pos_user = prompts.render_positive(POSITIVE_CASE)
    neg_sys, neg_user = prompts.render_negative(NEGATIVE_CASE)

    lines = [
        "# POS / NEG Prompt 生成测试报告",
        "",
        f"- 生成时间：{now}",
        f"- LLM vendor：`{settings.llm_vendor}`",
        f"- LLM model：`{settings.llm_model}`",
        f"- enable_thinking：`{settings.llm_enable_thinking}`",
        "",
        "---",
        "",
        "## 1. Positive（专家未修改）",
        "",
        _section("输入：问题", POSITIVE_CASE["questionContent"]),
        _section("输入：已审核答案", POSITIVE_CASE["answerContent"]),
        _section("输入：支撑推理", POSITIVE_CASE["thinking"]),
        _section("System Prompt", f"```\n{pos_sys.strip()}\n```"),
        _section("User Prompt", f"```\n{pos_user.strip()}\n```"),
        f"**生成状态**：{'✅ 成功' if pos_result['ok'] else '❌ 失败（输出过短）'}",
        "",
        _section("LLM 生成结果（清洗后）", pos_result["cleaned"] or "_(空)_"),
        "",
        "<details><summary>原始 LLM 输出</summary>",
        "",
        "```",
        pos_result["raw"] or "(空)",
        "```",
        "",
        "</details>",
        "",
        "---",
        "",
        "## 2. Negative（专家已修订）",
        "",
        _section("输入：问题", NEGATIVE_CASE["questionContent"]),
        _section("输入：原始（错误）回答", NEGATIVE_CASE["originalAnswer"]),
        _section("输入：原始回答推理", NEGATIVE_CASE["originalThinking"]),
        _section("输入：修订后（正确）回答", NEGATIVE_CASE["answerContent"]),
        _section("输入：修订后回答推理", NEGATIVE_CASE["thinking"]),
        _section("System Prompt", f"```\n{neg_sys.strip()}\n```"),
        _section("User Prompt", f"```\n{neg_user.strip()}\n```"),
        f"**生成状态**：{'✅ 成功' if neg_result['ok'] else '❌ 失败（输出过短）'}",
        "",
        _section("LLM 生成结果（清洗后）", neg_result["cleaned"] or "_(空)_"),
        "",
        "<details><summary>原始 LLM 输出</summary>",
        "",
        "```",
        neg_result["raw"] or "(空)",
        "```",
        "",
        "</details>",
        "",
        "---",
        "",
        "## 3. 结构校验",
        "",
    ]

    for label, text in [("Positive", pos_result["cleaned"]), ("Negative", neg_result["cleaned"])]:
        checks = {
            "#### 关键业务特征": "#### 关键业务特征" in text,
            "#### 关键判定要素": "#### 关键判定要素" in text,
            "结论段（第三节）": (
                "#### 结论与处理建议" in text or "#### 结论与易错点警告" in text
            ),
            "无 <think> 残留": "<think>" not in text.lower(),
            "长度 ≥ 60 字符": len(text) >= 60,
        }
        lines.append(f"### {label}")
        lines.append("")
        for name, passed in checks.items():
            mark = "✅" if passed else "❌"
            lines.append(f"- {mark} {name}")
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    settings = get_settings()
    pos_sys, pos_user = prompts.render_positive(POSITIVE_CASE)
    neg_sys, neg_user = prompts.render_negative(NEGATIVE_CASE)

    pos_result = _run_prompt("positive", pos_sys, pos_user, settings)
    neg_result = _run_prompt("negative", neg_sys, neg_user, settings)

    report = build_report(pos_result, neg_result, settings)
    OUTPUT_PATH.write_text(report, encoding="utf-8")
    print(f"报告已写入：{OUTPUT_PATH}")


if __name__ == "__main__":
    main()
