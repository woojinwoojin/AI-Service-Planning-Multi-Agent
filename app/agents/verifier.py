"""근거 일치성 검증 Agent — 기획서 주장이 앞 단계 조사 결과와 일치하는지 검토.

파이프라인 맨 끝(최종본 확정 후)에서 동작한다. 최종 기획서의 사실성 주장을 뽑아
Research가 모은 근거(시장조사 결과·출처)와 대조하고, 지지되지 않는 주장을 표면화한다.

주의(정직성): 이 Agent는 URL 원문에 접속해 재확인하지 '않는다'. 어디까지나 '앞 단계에서
수집된 조사 결과 텍스트'와 기획서 주장의 근거 일치성을 검토하는 것이지, 엄밀한 '출처 검증'
(URL 접속 → 원문 추출 → 주장 대조)은 아니다. 원래 12-Agent 비전의 '출처 검증' 자리에
해당하지만 명칭을 구현 수준에 맞게 정직하게 조정했다.
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
        # 검증 범위 명시: 수집된 검색 요약 근거와의 일치 여부일 뿐, URL 원문 사실 검증이 아니다.
        "verification_scope": "search_snippet_only",
    }


def _dummy(_: str) -> dict:
    claims = [
        {"claim": "[더미] 시장이 성장 중이다", "status": "uncertain", "basis": "[더미] 근거 불충분"},
    ]
    return {"claims": claims, "supported": 0, "total": 1, "support_rate": 0.0,
            "unsupported": [], "verification_scope": "search_snippet_only"}


def verify(state: ProjectState) -> dict:
    draft = state.get("final_draft", "") or state.get("draft", "")
    research = state.get("research_result", {})
    competitor = state.get("competitor_result", {})
    # 경쟁사 분석이 쓴 실제 검색 출처(제목·요약)도 근거에 포함한다 — 차별성/경쟁 관련 주장이
    # 근거 없이 unsupported 로 몰리지 않도록(외부 리뷰 P0-3: verifier 가 경쟁 근거를 못 보던 문제).
    comp_sources = state.get("competitor_sources", []) or []
    comp_evidence = [{"title": s.get("title", ""), "snippet": s.get("snippet", "")}
                     for s in comp_sources if isinstance(s, dict)]
    fallback = _dummy(draft)

    user = (
        "아래 기획서의 사실성 주장을 근거와 대조해 검증하세요.\n"
        f"[기획서]\n{draft}\n\n"
        f"[근거: 시장조사 결과]\n{json.dumps(research, ensure_ascii=False)}\n\n"
        f"[근거: 경쟁사 분석]\n{json.dumps(competitor, ensure_ascii=False)}\n\n"
        f"[근거: 경쟁사 검색 요약]\n{json.dumps(comp_evidence, ensure_ascii=False)}"
    )
    status: dict = {}
    raw = llm.complete_json(VERIFY_SYSTEM, user, fallback=fallback,
                            model=state.get("model", ""), status=status)
    result = _validate(raw, fallback)

    mode = llm.mode_label(status, state.get("model", ""))
    logs = [
        f"[verify] 근거 일치성 검증 완료 ({mode}, 근거 확인 {result['supported']}/{result['total']}, 검색 요약 기준)"
    ]
    return {"verification_result": result, "logs": logs}
