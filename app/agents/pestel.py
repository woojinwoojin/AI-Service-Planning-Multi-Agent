"""PESTEL Agent — Research 결과만 근거로 6개 요인 분석 (5일 차 구현 예정)."""
from __future__ import annotations

import json

from app.prompts.templates import PESTEL_SYSTEM
from app.schemas.state import ProjectState
from app.services import llm

_FACTORS = ["Political", "Economic", "Social", "Technological", "Environmental", "Legal"]


def _dummy(research: dict) -> dict:
    return {
        factor: {
            "content": f"[더미] {factor} 관점의 주요 내용",
            "opportunity": f"[더미] {factor} 기회 요인",
            "threat": f"[더미] {factor} 위협 요인",
            "response": f"[더미] {factor} 대응 방향",
        }
        for factor in _FACTORS
    }


def pestel(state: ProjectState) -> dict:
    research = state.get("research_result", {})
    fallback = _dummy(research)

    user = (
        "아래 시장조사 결과만을 근거로 PESTEL 분석을 수행하세요.\n"
        f"{json.dumps(research, ensure_ascii=False, indent=2)}"
    )
    result = llm.complete_json(PESTEL_SYSTEM, user, fallback=fallback, model=state.get("model", ""))

    logs = state.get("logs", []) + ["[pestel] PESTEL 분석 완료"]
    return {"pestel_result": result, "logs": logs}
