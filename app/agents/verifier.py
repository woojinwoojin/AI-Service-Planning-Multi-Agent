"""Source Verification Agent — 기획서 주장을 수집 근거와 대조 검증.

파이프라인 맨 끝(최종본 확정 후)에서 동작한다. 최종 기획서의 사실성 주장을 뽑아
Research가 모은 근거(시장조사 결과·출처)와 대조하고, 지지되지 않는 주장을 표면화한다.
원래 12-Agent 비전의 '출처 검증'에 해당하며, Multi-Agent의 근거성을 마지막에 점검한다.
"""
from __future__ import annotations

import json

from app.prompts.templates import VERIFY_SYSTEM
from app.schemas.state import ProjectState
from app.services import llm

_STATUS = {"supported", "unsupported", "uncertain"}


def _validate(result: dict, fallback: dict) -> dict:
    if not isinstance(result, dict):
        return dict(fallback)
    raw = result.get("claims")
    claims = []
    if isinstance(raw, list):
        for c in raw:
            if not isinstance(c, dict):
                continue
            claim = c.get("claim") if isinstance(c.get("claim"), str) else ""
            if not claim.strip():
                continue
            status = c.get("status") if c.get("status") in _STATUS else "uncertain"
            basis = c.get("basis") if isinstance(c.get("basis"), str) else ""
            claims.append({"claim": claim.strip(), "status": status, "basis": basis})
    if not claims:
        return dict(fallback)
    supported = sum(1 for c in claims if c["status"] == "supported")
    total = len(claims)
    return {
        "claims": claims,
        "supported": supported,
        "total": total,
        "support_rate": round(supported / total, 2) if total else 0.0,
        "unsupported": [c["claim"] for c in claims if c["status"] == "unsupported"],
    }


def _dummy(_: str) -> dict:
    claims = [
        {"claim": "[더미] 시장이 성장 중이다", "status": "uncertain", "basis": "[더미] 근거 불충분"},
    ]
    return {"claims": claims, "supported": 0, "total": 1, "support_rate": 0.0, "unsupported": []}


def verify(state: ProjectState) -> dict:
    draft = state.get("final_draft", "") or state.get("draft", "")
    research = state.get("research_result", {})
    fallback = _dummy(draft)

    user = (
        "아래 기획서의 사실성 주장을 근거와 대조해 검증하세요.\n"
        f"[기획서]\n{draft}\n\n"
        f"[근거: 시장조사 결과]\n{json.dumps(research, ensure_ascii=False)}"
    )
    status: dict = {}
    raw = llm.complete_json(VERIFY_SYSTEM, user, fallback=fallback,
                            model=state.get("model", ""), status=status)
    result = _validate(raw, fallback)

    mode = llm.mode_label(status, state.get("model", ""))
    logs = state.get("logs", []) + [
        f"[verify] 출처 검증 완료 ({mode}, 지지 {result['supported']}/{result['total']})"
    ]
    return {"verification_result": result, "logs": logs}
