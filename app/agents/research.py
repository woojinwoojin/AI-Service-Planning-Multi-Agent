"""Research Agent — 시장·산업 조사 (4일 차 구현 예정, 현재는 더미 fallback).

더미 모드에서는 입력값을 반영한 골격 데이터를 반환하여 파이프라인이 관통되게 한다.
"""
from __future__ import annotations

import json

from app.prompts.templates import RESEARCH_SYSTEM
from app.schemas.state import ProjectState
from app.services import llm


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
    result = llm.complete_json(RESEARCH_SYSTEM, user, fallback=fallback)

    logs = state.get("logs", []) + ["[research] 시장조사 완료"]
    return {"research_result": result, "logs": logs}
