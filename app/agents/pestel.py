"""PESTEL Agent — Research 결과만 근거로 6개 요인 분석.

- 실제 모드: LLM이 시장조사 결과만 근거로 PESTEL 6요인을 분석한다.
- 더미 모드: 골격 데이터를 반환해 파이프라인이 관통되게 한다.

Research Agent와 동일하게, LLM 응답이 부분적으로만 올바르더라도 _validate()로
스키마(6요인 × 4키)를 강제하여 다음 Agent(Draft)가 항상 온전한 구조를 받게 한다.
"""
from __future__ import annotations

import json

from app.prompts.templates import PESTEL_SYSTEM
from app.schemas.state import ProjectState
from app.services import llm

_FACTORS = ["Political", "Economic", "Social", "Technological", "Environmental", "Legal"]
_SUBKEYS = ["content", "opportunity", "threat", "response"]


def _validate(result: dict, fallback: dict) -> dict:
    """LLM 출력을 PESTEL 스키마(6요인 × 4키)로 정규화한다.

    - 응답이 dict가 아니면(파싱 실패 등) fallback 전체를 쓴다.
    - 요인/하위키가 누락·타입오류·빈값이면 그 자리만 중립 빈값("")으로 채운다.
      (fallback의 더미 문구가 실제 산출물에 새어들지 않게 하기 위함)
    """
    if not isinstance(result, dict):
        return {f: dict(fallback[f]) for f in _FACTORS}
    out: dict = {}
    for factor in _FACTORS:
        block = result.get(factor)
        block = block if isinstance(block, dict) else {}
        out[factor] = {}
        for sub in _SUBKEYS:
            value = block.get(sub)
            out[factor][sub] = value if isinstance(value, str) and value.strip() else ""
    return out


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
        "이 결과에 없는 새로운 사실은 지어내지 마세요.\n"
        f"{json.dumps(research, ensure_ascii=False, indent=2)}"
    )
    raw = llm.complete_json(PESTEL_SYSTEM, user, fallback=fallback, model=state.get("model", ""))
    result = _validate(raw, fallback)

    mode = "더미" if llm.is_dummy() else f"실제 LLM·{llm.resolve_model(state.get('model', ''))}"
    logs = state.get("logs", []) + [f"[pestel] PESTEL 분석 완료 ({mode})"]
    return {"pestel_result": result, "logs": logs}
