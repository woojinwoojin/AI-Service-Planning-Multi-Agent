"""Customer Problem Agent — 타깃 사용자의 페르소나·Pain Point·니즈·JTBD 심화 분석.

Research의 customer_needs와 아이디어를 근거로 고객 이해를 깊게 만든다.
기획서의 '문제 정의'·'목표 사용자' 섹션이 이 결과를 근거로 강화된다.
"""
from __future__ import annotations

import json

from app.prompts.templates import CUSTOMER_SYSTEM
from app.schemas.state import ProjectState
from app.services import llm

_LIST_KEYS = ["pain_points", "needs", "jobs_to_be_done"]


def _validate(result: dict, fallback: dict) -> dict:
    if not isinstance(result, dict):
        return dict(fallback)
    out: dict = {}
    persona = result.get("target_persona")
    out["target_persona"] = persona if isinstance(persona, str) else ""
    for k in _LIST_KEYS:
        v = result.get(k)
        out[k] = [s.strip() for s in v if isinstance(s, str) and s.strip()] if isinstance(v, list) else []
    # 내용이 전혀 없으면 fallback
    if not out["target_persona"] and not any(out[k] for k in _LIST_KEYS):
        return dict(fallback)
    return out


def _dummy(si: dict) -> dict:
    user = si.get("target_user", "사용자")
    return {
        "target_persona": f"[더미] {user} 대표 페르소나",
        "pain_points": ["[더미] 핵심 불편"],
        "needs": ["[더미] 주요 니즈"],
        "jobs_to_be_done": ["[더미] 해결 과업"],
    }


def customer(state: ProjectState) -> dict:
    research = state.get("research_result", {})
    si = state.get("structured_input", {})
    fallback = _dummy(si)
    user = (
        "아래 정보를 근거로 고객 문제를 심화 분석하세요.\n"
        f"[아이디어]\n{json.dumps(si, ensure_ascii=False)}\n"
        f"[시장조사]\n{json.dumps(research, ensure_ascii=False)}"
    )
    status: dict = {}
    raw = llm.complete_json(CUSTOMER_SYSTEM, user, fallback=fallback,
                            model=state.get("model", ""), status=status)
    result = _validate(raw, fallback)
    mode = llm.mode_label(status, state.get("model", ""))
    logs = [
        f"[customer] 고객 문제 분석 완료 ({mode}, pain {len(result['pain_points'])}건)"
    ]
    return {"customer_result": result, "logs": logs}
