"""正/负样本 refine prompt 模板。

设计原则：

- **positive**（专家未修改）：把"问题 + 已被认可的答案"提炼为 *场景化财税案例知识*，
  服务于召回阶段当作"权威案例片段"注入推理。
- **negative**（专家修改过）：基于"原始回答 vs 修正后回答"的差异，提炼"正向推导"
  形态的案例知识，**不要暴露"原回答错了"的负面措辞**，重点写"正确的推导路径 +
  关键判定差异点"。

输出格式：要求 LLM 严格输出一段 Markdown 文本（不要 JSON / 不要思考标签），
runner / refiner 拿到后会做最小化清洗（去掉 ``<think>...</think>``、首尾空白）。
若清洗后为空 / 全是噪声，refiner 视为失败，进入 raw_fallback 分支。
"""

from __future__ import annotations

from typing import Any


_POSITIVE_SYSTEM = """你是资深财税案例知识工程师，专门把已被业务专家审核的高质量"问答 case"提炼为可在后续推理中复用的"场景化财税案例知识"。

输出要求：
1. 严格用中文 Markdown，不要输出任何 <think>...</think> 标签、不要 JSON、不要多余的开场白，总长度控制在500字左右。
2. 必须按下列三段式结构组织（每段都必填，标题用四级标题"#### "）：
   #### 关键业务特征
   #### 关键判定要素
   #### 结论与处理建议
3. 关键业务特征：要做适度脱敏抽象（如"某企业"），但保留行业、业务环节、纳税人身份等会决定结论的关键业务特征属性。
4. 关键判定要素：以条目形式列出做出本结论必须依据的事实点（如"是否取得合规扣税凭证"、"销售形态：批发/零售/直销"）。
5. 结论与处理建议：先给一句话核心结论，再写具体的会计/税务处理动作（若答案中有明确建议，则直接引用；否则省略建议）。
6. 不要复述原问题；不要添加"以上回答仅供参考"等免责语；不要使用"用户"、"提问者"等称谓。
"""


_POSITIVE_USER_TEMPLATE = """以下 case 已经过业务专家审核，请按系统提示中的三段式结构，提炼成"场景化财税案例知识"。

【问题】
{question}

【已审核答案】
{answer}

【支撑推理（仅供参考，可酌情提炼为"关键判定要素"的依据，不必逐句引用）】
{thinking}
"""


_NEGATIVE_SYSTEM = """你是资深财税案例知识工程师，专门把"业务专家已经修订过的问答 case"提炼为可在后续推理中复用的案例知识。

输入会包含：原问题、**原始（错误）回答** 与 **修订后（正确）回答**（以及各自推理内容）。修订后回答代表专家认可的正确推理和结论。你的任务是基于这两版回答的差异，重构一段**提示易错点的防坑指南**。

输出要求：
1. 严格用中文 Markdown，不要输出任何 <think>...</think> 标签、不要 JSON、不要多余的开场白，总长度控制在500字左右。
2. 必须按下列三段式结构组织（每段都必填，标题用四级标题"#### "）：
   #### 关键业务特征
   #### 关键判定要素
   #### 结论与易错点警告
3. 关键业务特征：要做适度脱敏抽象（如"某企业"），但保留行业、业务环节、纳税人身份等会决定结论的关键业务特征属性。
4. 关键判定要素：请以条目形式列出**修订后**推理、回答内容中的**判定逻辑和事实依据**，且必须注释**原始**推理、回答内容在每个条目上的**差异点**。
5. 结论与易错点警告：先给一句话核心结论，再基于'关键判定要素'内容以条目形式列出**易错点警告**。
6. 不要复述原问题；不要添加"以上回答仅供参考"等免责语；不要使用"用户"、"提问者"等称谓。
"""


_NEGATIVE_USER_TEMPLATE = """以下 case 经业务专家修订过，请按系统提示中的四段式结构，基于"原始回答"与"修订后回答"的差异，提炼成"正向推导案例知识"。

【问题】
{question}

【原始（错误）回答】
{original_answer}

【原始回答推理】
{original_thinking}

【修订后（正确）回答】
{revised_answer}

【修订后回答推理】
{revised_thinking}
"""


def render_positive(case: dict[str, Any]) -> tuple[str, str]:
    """返回 (system, user)。"""
    return (
        _POSITIVE_SYSTEM,
        _POSITIVE_USER_TEMPLATE.format(
            question=case.get("questionContent") or "",
            answer=case.get("answerContent") or "",
            thinking=case.get("thinking") or "",
        ),
    )


def render_negative(case: dict[str, Any]) -> tuple[str, str]:
    """返回 (system, user)。"""
    return (
        _NEGATIVE_SYSTEM,
        _NEGATIVE_USER_TEMPLATE.format(
            question=case.get("questionContent") or "",
            original_answer=case.get("originalAnswer") or "",
            original_thinking=case.get("originalThinking") or "",
            revised_answer=case.get("answerContent") or "",
            revised_thinking=case.get("thinking") or "",
        ),
    )
