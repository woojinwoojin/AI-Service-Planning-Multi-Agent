"""Risk Agent — Research·PESTEL 근거로 리스크 유형별 도출 + 대응책.

기획서의 '위험요인 및 대응방안' 섹션을 이 결과로 강화한다.
"""
from __future__ import annotations

import json

from app.prompts.templates import RISK_SYSTEM
from app.schemas.state import ProjectState
from app.services import llm

_LEVELS = {"상", "중", "하"}


def _level(v) -> str:
    return v if isinstance(v, str) and v in _LEVELS else "중"


def _validate(result: dict, fallback: dict) -> dict:
    if not isinstance(result, dict):
        return dict(fallback)
    raw = result.get("risks")
    risks = []
    if isinstance(raw, list):
        for r in raw:
            if not isinstance(r, dict):
                continue
            risks.append({
                "category": r.get("category") if isinstance(r.get("category"), str) else "",
                "description": r.get("description") if isinstance(r.get("description"), str) else "",
                "likelihood": _level(r.get("likelihood")),
                "impact": _level(r.get("impact")),
                "mitigation": r.get("mitigation") if isinstance(r.get("mitigation"), str) else "",
            })
    return {"risks": risks} if risks else dict(fallback)


def _dummy(_: dict) -> dict:
    return {"risks": [
        {"category": "시장", "description": "[더미] 초기 사용자 확보 난이도",
         "likelihood": "중", "impact": "상", "mitigation": "[더미] 니치 타깃 집중"},
    ]}


def risk(state: ProjectState) -> dict:
    research = state.get("research_result", {})
    pestel = state.get("pestel_result", {})
    fallback = _dummy(research)
    user = (
        "아래 결과를 근거로 리스크 분석을 수행하세요.\n"
        f"[시장조사]\n{json.dumps(research, ensure_ascii=False)}\n"
        f"[PESTEL]\n{json.dumps(pestel, ensure_ascii=False)}"
    )
    status: dict = {}
    raw = llm.complete_json(RISK_SYSTEM, user, fallback=fallback,
                            model=state.get("model", ""), status=status)
    result = _validate(raw, fallback)
    mode = llm.mode_label(status, state.get("model", ""))
    logs = state.get("logs", []) + [f"[risk] 리스크 분석 완료 ({mode}, {len(result['risks'])}건)"]
    return {"risk_result": result, "logs": logs}
