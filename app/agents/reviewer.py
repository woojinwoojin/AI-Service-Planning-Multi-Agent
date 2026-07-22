"""Reviewer Agent — 5항목 100점 평가 + 개선지시.

- 실제 모드: LLM이 초안을 5개 항목(각 0~20점)으로 평가하고 개선지시를 낸다.
- 더미 모드: 골격 평가를 반환한다.

Research/PESTEL과 동일하게 _validate()로 스키마를 강제한다. 특히 total_score는
LLM 값에 의존하지 않고 section_scores 합으로 재계산해, 워크플로의 재작성 판단
(_needs_revision, PASS_SCORE)이 항상 정합적인 총점 위에서 동작하도록 한다.
"""
from __future__ import annotations

from app.prompts.templates import REVIEWER_SYSTEM
from app.schemas.state import ProjectState
from app.services import llm

SECTION_KEYS = [
    "problem_clarity", "market_validity", "solution_specificity",
    "differentiation", "feasibility",
]
_LIST_KEYS = ["strengths", "weaknesses", "unsupported_claims", "revision_instructions"]
_SECTION_MAX = 20


def _clamp_score(value) -> int:
    """세부 점수를 0~20 정수로 정규화. 숫자가 아니면 0."""
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        return 0
    return max(0, min(_SECTION_MAX, n))


def _validate(result: dict, fallback: dict) -> dict:
    """평가 결과를 스키마에 맞게 정규화한다.

    - dict가 아니면 fallback 전체 사용.
    - section_scores 5개는 0~20 정수로 clamp, total_score는 그 합으로 재계산.
    - 리스트 4종은 비어있지 않은 문자열만 남기고, 없으면 빈 리스트.
    """
    if not isinstance(result, dict):
        return dict(fallback)
    raw_scores = result.get("section_scores")
    raw_scores = raw_scores if isinstance(raw_scores, dict) else {}
    section_scores = {k: _clamp_score(raw_scores.get(k)) for k in SECTION_KEYS}
    out: dict = {
        "total_score": sum(section_scores.values()),
        "section_scores": section_scores,
    }
    for key in _LIST_KEYS:
        value = result.get(key)
        if isinstance(value, list):
            out[key] = [s.strip() for s in value if isinstance(s, str) and s.strip()]
        else:
            out[key] = []
    return out


def _dummy(draft: str) -> dict:
    section_scores = {k: 15 for k in SECTION_KEYS}
    return {
        "total_score": sum(section_scores.values()),
        "strengths": ["[더미] 구조가 서식을 잘 따름"],
        "weaknesses": ["[더미] 시장분석에 구체적 수치 부족"],
        "unsupported_claims": ["[더미] 출처 없는 시장 성장 주장"],
        "revision_instructions": [
            "[더미] 시장분석에 출처 기반 근거를 보강할 것",
            "[더미] 차별성 섹션을 경쟁 대비로 구체화할 것",
        ],
        "section_scores": section_scores,
    }


def _review(draft: str, model: str) -> tuple[dict, dict]:
    """기획서 하나를 5항목으로 평가해 (검증된 결과, status)를 반환. 초안/최종본 평가 공통."""
    fallback = _dummy(draft)
    status: dict = {}
    raw = llm.complete_json(
        REVIEWER_SYSTEM, f"아래 기획서를 평가 기준에 따라 심사하세요.\n\n{draft}",
        fallback=fallback, model=model, status=status,
    )
    return _validate(raw, fallback), status


def reviewer(state: ProjectState) -> dict:
    """초안 평가. review_result(재작성 판단용)와 initial_review_result(기록용)에 저장."""
    result, status = _review(state.get("draft", ""), state.get("model", ""))
    mode = llm.mode_label(status, state.get("model", ""))
    logs = [
        f"[reviewer] 초안 평가 완료 (총점={result['total_score']}, {mode})"
    ]
    return {"review_result": result, "initial_review_result": result, "logs": logs}


def final_reviewer(state: ProjectState) -> dict:
    """최종본(재작성·일관성 편집 후) 재평가.

    UI·이력에 표시되는 점수가 '실제로 보여주는 최종 문서'와 일치하도록, 초안 점수와
    별개로 최종본을 다시 채점해 final_review_result에 저장한다. 초안 대비 변화(Δ)도 로그에 남긴다.
    """
    result, status = _review(state.get("final_draft", "") or state.get("draft", ""), state.get("model", ""))
    initial = state.get("initial_review_result") or state.get("review_result") or {}
    before = initial.get("total_score")
    delta = f", Δ{result['total_score'] - before:+d} vs 초안" if isinstance(before, int) else ""
    mode = llm.mode_label(status, state.get("model", ""))
    logs = [
        f"[final_reviewer] 최종본 재평가 완료 (총점={result['total_score']}{delta}, {mode})"
    ]
    return {"final_review_result": result, "logs": logs}
