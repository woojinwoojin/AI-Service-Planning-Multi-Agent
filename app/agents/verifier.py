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
from app.services import evidence, llm

_STATUS = {"supported", "unsupported", "uncertain"}


def _clean_evidence_ids(raw, valid_ids: set | None) -> list[str]:
    """LLM 이 인용한 evidence_ids 를 정규화한다 — 문자열만·중복 제거·알려진 id 만 남김.

    valid_ids 가 주어지면(레지스트리 존재) 그 안의 id 만 통과시켜, LLM 이 지어낸 id 를 걸러낸다.
    """
    out: list[str] = []
    for x in raw or []:
        x = x.strip() if isinstance(x, str) else ""
        if not x or x in out:
            continue
        if valid_ids is not None and x not in valid_ids:
            continue
        out.append(x)
    return out


def _validate(result: dict, fallback: dict, valid_ids: set | None = None) -> dict:
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
            eids = _clean_evidence_ids(c.get("evidence_ids"), valid_ids)
            # claim 에 실행 내 안정 id(c1, c2 …)를 부여 — 근거의 used_by_claims 역연결 키.
            claims.append({"id": f"c{len(claims) + 1}", "claim": claim.strip(),
                           "status": status, "basis": basis, "evidence_ids": eids})
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
        # 주장-근거 연결 지표(2-1b): evidence_id 로 특정 근거에 연결된 주장 수.
        "evidence_linked": sum(1 for c in claims if c["evidence_ids"]),
        # 검증 범위 명시: 수집된 검색 요약 근거와의 일치 여부일 뿐, URL 원문 사실 검증이 아니다.
        "verification_scope": "search_snippet_only",
    }


def _dummy(_: str) -> dict:
    claims = [
        {"id": "c1", "claim": "[더미] 시장이 성장 중이다", "status": "uncertain",
         "basis": "[더미] 근거 불충분", "evidence_ids": []},
    ]
    return {"claims": claims, "supported": 0, "total": 1, "support_rate": 0.0,
            "unsupported": [], "evidence_linked": 0, "verification_scope": "search_snippet_only"}


def verify(state: ProjectState) -> dict:
    draft = state.get("final_draft", "") or state.get("draft", "")
    research = state.get("research_result", {})
    competitor = state.get("competitor_result", {})
    fallback = _dummy(draft)

    # 통합 근거 레지스트리를 evidence_id 와 함께 제시 → LLM 이 주장별로 어떤 근거가 뒷받침하는지
    # 지목(인용)하게 한다(2-1b: 주장-근거 연결). 레지스트리가 없으면(옛 프로젝트/재작성) 기존
    # 경쟁사 검색 출처로 fallback 하되 evidence_id 연결은 생략한다(회귀 없이 동작 보장).
    registry = evidence.normalize(state.get("evidence_registry", []) or [])
    if registry:
        evidence_block = evidence.for_prompt(registry)
        valid_ids: set | None = {e["evidence_id"] for e in registry}
    else:
        comp_sources = state.get("competitor_sources", []) or []
        evidence_block = "\n".join(
            f"- {s.get('title', '')}: {s.get('snippet', '')}"
            for s in comp_sources if isinstance(s, dict)
        )
        valid_ids = None

    user = (
        "아래 기획서의 사실성 주장을 근거와 대조해 검증하세요.\n"
        f"[기획서]\n{draft}\n\n"
        f"[근거: 시장조사 결과]\n{json.dumps(research, ensure_ascii=False)}\n\n"
        f"[근거: 경쟁사 분석]\n{json.dumps(competitor, ensure_ascii=False)}\n\n"
        "[근거 출처 목록 — 각 주장을 뒷받침하는 출처의 evidence_id 를 evidence_ids 에 적으세요]\n"
        f"{evidence_block}"
    )
    status: dict = {}
    raw = llm.complete_json(VERIFY_SYSTEM, user, fallback=fallback,
                            model=state.get("model", ""), status=status)
    result = _validate(raw, fallback, valid_ids)

    mode = llm.mode_label(status, state.get("model", ""))
    logs = [
        f"[verify] 근거 일치성 검증 완료 ({mode}, 근거 확인 {result['supported']}/{result['total']}, "
        f"근거연결 {result.get('evidence_linked', 0)}건, 검색 요약 기준)"
    ]
    return {"verification_result": result, "logs": logs}
