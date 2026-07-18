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
from app.services import llm, search

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


def _build_query(si: dict) -> str:
    """검색 쿼리: 프로젝트명 + 주요 키워드 조합."""
    parts = [si.get("project_name", "")] + list(si.get("keywords", []) or [])
    return " ".join(p for p in parts if p).strip() + " 시장 동향 경쟁 서비스"


def _format_hits(hits: list[dict]) -> str:
    lines = []
    for i, h in enumerate(hits, 1):
        snippet = h["content"][:300]
        lines.append(f"[{i}] {h['title']}\n{snippet}\n출처: {h['url']}")
    return "\n\n".join(lines)


def _merge_sources(llm_sources: list, hits: list[dict]) -> list:
    """실제 검색 출처(제목 — URL)를 sources 앞쪽에 보장하고, LLM이 적은 것과 병합."""
    real = [f"{h['title']} — {h['url']}" if h["title"] else h["url"] for h in hits]
    seen, merged = set(), []
    for s in real + [str(x) for x in llm_sources]:
        if s and s not in seen:
            seen.add(s)
            merged.append(s)
    return merged


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

    # 웹 검색으로 근거 확보 (키 없으면 빈 결과 → LLM 지식 기반으로 진행)
    hits = search.web_search(_build_query(si)) if not llm.is_dummy() else []

    user = "다음 사업 아이디어를 조사하세요.\n" f"{json.dumps(si, ensure_ascii=False, indent=2)}"
    if hits:
        user += (
            "\n\n아래는 실제 웹 검색 결과입니다. 이 내용을 1차 근거로 삼아 조사하고, "
            "sources 에는 실제 참고한 출처 URL을 포함하세요.\n\n" + _format_hits(hits)
        )

    status: dict = {}
    raw = llm.complete_json(RESEARCH_SYSTEM, user, fallback=fallback,
                            model=state.get("model", ""), status=status)
    result = _validate(raw, fallback)

    # 실제 검색 출처를 sources(인용)로 보장
    if hits:
        result["sources"] = _merge_sources(result.get("sources", []), hits)

    mode = llm.mode_label(status, state.get("model", ""))
    src = f"웹검색 {len(hits)}건" if hits else ("검색 비활성" if not search.search_enabled() else "검색 결과 없음")
    logs = state.get("logs", []) + [f"[research] 시장조사 완료 ({mode}, {src})"]
    return {"research_result": result, "logs": logs}
