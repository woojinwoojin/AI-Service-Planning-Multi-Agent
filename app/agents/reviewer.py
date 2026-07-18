"""Reviewer Agent — 5항목 100점 평가 + 개선지시 (7일 차 구현 예정)."""
from __future__ import annotations

from app.prompts.templates import REVIEWER_SYSTEM
from app.schemas.state import ProjectState
from app.services import llm

SECTION_KEYS = [
    "problem_clarity", "market_validity", "solution_specificity",
    "differentiation", "feasibility",
]


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


def reviewer(state: ProjectState) -> dict:
    draft = state.get("draft", "")
    fallback = _dummy(draft)

    user = f"아래 기획서 초안을 평가 기준에 따라 심사하세요.\n\n{draft}"
    result = llm.complete_json(REVIEWER_SYSTEM, user, fallback=fallback, model=state.get("model", ""))

    score = result.get("total_score", "?")
    logs = state.get("logs", []) + [f"[reviewer] 평가 완료 (총점={score})"]
    return {"review_result": result, "logs": logs}
