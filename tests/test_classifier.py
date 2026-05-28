"""classifier 单元测试：polarity 判定 + hash 稳定性。"""

from __future__ import annotations

from case_refinery.pipeline import classifier


_BASE_CASE = {
    "questionContent": "问题A",
    "originalAnswer": "原始回答",
    "originalThinking": "原始推理",
    "answerContent": "原始回答",
    "thinking": "原始推理",
}


def test_classify_positive_when_both_unchanged() -> None:
    assert classifier.classify(_BASE_CASE) == "positive"
    assert classifier.is_expert_revised(_BASE_CASE) is False


def test_classify_negative_when_answer_changed() -> None:
    c = dict(_BASE_CASE, answerContent="专家改后的回答")
    assert classifier.classify(c) == "negative"
    assert classifier.is_expert_revised(c) is True


def test_classify_negative_when_only_thinking_changed() -> None:
    c = dict(_BASE_CASE, thinking="专家改后的推理")
    assert classifier.classify(c) == "negative"


def test_record_hash_stable_across_field_order() -> None:
    """dict 序列化顺序不影响 record_hash。"""
    a = {
        "questionContent": "Q",
        "originalAnswer": "OA",
        "originalThinking": "OT",
        "answerContent": "AC",
        "thinking": "TH",
    }
    b = {
        "thinking": "TH",
        "answerContent": "AC",
        "originalThinking": "OT",
        "originalAnswer": "OA",
        "questionContent": "Q",
    }
    assert classifier.record_hash(a) == classifier.record_hash(b)


def test_record_hash_changes_when_any_field_changes() -> None:
    base_h = classifier.record_hash(_BASE_CASE)
    for k in ("questionContent", "originalAnswer", "originalThinking",
              "answerContent", "thinking"):
        mutated = dict(_BASE_CASE, **{k: _BASE_CASE[k] + "_x"})
        assert classifier.record_hash(mutated) != base_h, k


def test_question_hash_only_depends_on_question() -> None:
    h0 = classifier.question_hash(_BASE_CASE)
    c = dict(_BASE_CASE, answerContent="something else", thinking="x")
    assert classifier.question_hash(c) == h0
    c2 = dict(_BASE_CASE, questionContent="another question")
    assert classifier.question_hash(c2) != h0
