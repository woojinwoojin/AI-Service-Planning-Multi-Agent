"""Research Agent — 시장·산업 조사.

- 실제 모드: LLM을 호출해 시장조사 JSON을 생성한다.
- 더미 모드: 입력값을 반영한 골격 데이터를 반환하여 파이프라인이 관통되게 한다.

LLM이 유효한 JSON을 돌려주더라도 키가 누락되거나 타입이 어긋날 수 있으므로,
_validate()로 스키마(7개 키)를 강제하고 부족한 부분은 fallback으로 보완한다.
"""
from __future__ import annotations

import json

from app.prompts.templates import RESEARCH_SYSTEM
from app.schemas.state import ProjectState
from app.services import llm

# 출력 스키마: (키, 기대 타입). market_overview 만 문자열, 나머지는 리스트.
_SCHEMA: dict[str, type] = {
    "market_overview": str,
    "industry_trends": list,
    "customer_needs": list,
    "competitors": list,
    "opportunities": list,
    "risks": list,
    "sources": list,
}


def _validate(result: dict, fallback: dict) -> dict:
    """LLM 출력을 스키마에 맞게 정규화한다.

    - 응답 자체가 dict가 아니면(파싱 완전 실패 등) fallback 전체를 쓴다.
    - dict이지만 일부 키가 누락/타입오류/빈값이면, 그 키만 '중립 빈값'(""/[])으로
      채운다. 실제 응답에 fallback의 더미 문구('[더미]...')가 새어들지 않게 하기 위함.
    이렇게 하면 다음 Agent(PESTEL/Draft)는 항상 7개 키를 온전한 타입으로 받는다.
    """
    if not isinstance(result, dict):
        return dict(fallback)
    out: dict = {}
    for key, expected in _SCHEMA.items():
        value = result.get(key)
        if isinstance(value, expected) and value:
            out[key] = value
        else:
            out[key] = expected()  # str() -> "", list() -> []
    return out


def _dummy(si: dict) -> dict:
    name = si.get("project_name", "서비스")
    return {
        "market_overview": f"[더미] '{name}' 관련 시장은 성장 중이며 수요가 확대되고 있음.",
        "industry_trends": ["[더미] AI 도입 가속화", "[더미] 개인화 서비스 수요 증가"],
        "customer_needs": [f"[더미] {si.get('target_user', '사용자')}의 편의성 요구"],
        "competitors": ["[더미] 기존 대체 서비스 A", "[더미] 범용 도구 B"],
        "opportunities": ["[더미] 특정 니치 시장 선점 가능"],
        "risks": ["[더미] 초기 사용자 확보 난이도"],
        "sources": ["[더미] 사전 수집 참고자료 1", "[더미] 사전 수집 참고자료 2"],
    }


def research(state: ProjectState) -> dict:
    si = state.get("structured_input", {})
    fallback = _dummy(si)

    user = (
        "다음 사업 아이디어를 조사하세요.\n"
        f"{json.dumps(si, ensure_ascii=False, indent=2)}"
    )
    raw = llm.complete_json(RESEARCH_SYSTEM, user, fallback=fallback, model=state.get("model", ""))
    result = _validate(raw, fallback)

    mode = "더미" if llm.is_dummy() else f"실제 LLM·{llm.resolve_model(state.get('model', ''))}"
    logs = state.get("logs", []) + [f"[research] 시장조사 완료 ({mode})"]
    return {"research_result": result, "logs": logs}
