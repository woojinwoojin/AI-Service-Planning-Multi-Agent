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

# 사실 주장의 근거 판정값(로드맵 Tier 2). contradicted(반대 근거)를 unsupported(근거 미확인)와 분리한다.
#   supported   = 수집된 검색 근거에서 확인됨
#   unsupported = 수집된 검색 근거에서 확인되지 않음(= 근거 미확인, '거짓'이 아님)
#   contradicted= 수집된 근거가 주장과 배치됨(반대 근거)
#   uncertain   = 근거가 불충분해 판단 불가
_STATUS = {"supported", "unsupported", "contradicted", "uncertain"}
# 주장 유형(로드맵 Tier 2). 근거 검증은 '사실 주장(fact)'만 대상으로 한다.
#   fact=검증 가능한 사실 주장, inference=분석적 추론, proposal=서비스 제안(둘 다 검증 대상 아님).
_CLAIM_TYPES = {"fact", "inference", "proposal"}
# 비-사실 주장(inference/proposal)에 강제하는 근거 상태 — 근거 검증 대상이 아님.
_NOT_APPLICABLE = "not_applicable"


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
            # 주장 유형 분류(Tier 2). 유형이 이상하면 보수적으로 fact 로 둔다(검증 대상에 포함).
            ctype = c.get("claim_type") if c.get("claim_type") in _CLAIM_TYPES else "fact"
            # 근거 검증은 사실 주장만. 추론/제안은 근거 상태를 not_applicable 로 강제(검증 대상 아님).
            if ctype == "fact":
                status = c.get("status") if c.get("status") in _STATUS else "uncertain"
            else:
                status = _NOT_APPLICABLE
            basis = c.get("basis") if isinstance(c.get("basis"), str) else ""
            eids = _clean_evidence_ids(c.get("evidence_ids"), valid_ids)
            # claim 에 실행 내 안정 id(c1, c2 …)를 부여 — 근거의 used_by_claims 역연결 키.
            claims.append({"id": f"c{len(claims) + 1}", "claim": claim.strip(),
                           "claim_type": ctype, "status": status,
                           "basis": basis, "evidence_ids": eids})
    if not claims:
        return dict(fallback)
    return _metrics(claims)


def _metrics(claims: list[dict]) -> dict:
    """검증 지표를 계산한다. 기존 필드(supported/total/support_rate/unsupported/evidence_linked)는
    하위호환으로 유지하고, Tier 2 지표(사실 주장 검증률·반대 근거 분리·근거 연결률)를 추가한다."""
    total = len(claims)
    supported = sum(1 for c in claims if c["status"] == "supported")
    facts = [c for c in claims if c["claim_type"] == "fact"]
    fact_total = len(facts)
    fact_supported = sum(1 for c in facts if c["status"] == "supported")
    fact_linked = sum(1 for c in facts if c["evidence_ids"])
    return {
        "claims": claims,
        "supported": supported,
        "total": total,
        "support_rate": round(supported / total, 2) if total else 0.0,
        # '근거 미확인'과 '반대 근거'를 분리해 표면화(Tier 2 요구).
        "unsupported": [c["claim"] for c in claims if c["status"] == "unsupported"],
        "contradicted": [c["claim"] for c in claims if c["status"] == "contradicted"],
        # 주장-근거 연결 지표(2-1b): evidence_id 로 특정 근거에 연결된 주장 수.
        "evidence_linked": sum(1 for c in claims if c["evidence_ids"]),
        # Tier 2 지표: 주장 유형 분포 + '사실 주장'에 한정한 검증률·근거 연결률(완료 게이트).
        "claim_type_counts": {t: sum(1 for c in claims if c["claim_type"] == t) for t in _CLAIM_TYPES},
        "fact_total": fact_total,
        "fact_supported": fact_supported,
        "fact_support_rate": round(fact_supported / fact_total, 2) if fact_total else 0.0,
        "evidence_link_rate": round(fact_linked / fact_total, 2) if fact_total else 0.0,
        # 검증 범위 명시: 수집된 검색 요약 근거와의 일치 여부일 뿐, URL 원문 사실 검증이 아니다.
        "verification_scope": "search_snippet_only",
    }


def _dummy(_: str) -> dict:
    claims = [
        {"id": "c1", "claim": "[더미] 시장이 성장 중이다", "claim_type": "fact",
         "status": "uncertain", "basis": "[더미] 근거 불충분", "evidence_ids": []},
    ]
    return _metrics(claims)


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
    contra = len(result.get("contradicted", []))
    logs = [
        f"[verify] 근거 일치성 검증 완료 ({mode}, 사실주장 확인 "
        f"{result.get('fact_supported', 0)}/{result.get('fact_total', 0)}, "
        f"반대근거 {contra}건, 근거연결 {result.get('evidence_linked', 0)}건, 검색 요약 기준)"
    ]
    return {"verification_result": result, "logs": logs}


def judge_claim(claim: str, evidence_registry: list | None = None, model: str = "") -> dict:
    """단일 주장 하나를 제공된 근거로 검증해 판정 dict 하나를 반환한다(Ground Truth 평가·재사용용).

    verify() 는 기획서에서 주장을 스스로 뽑아 일괄 판정하지만, 이 함수는 '이미 정해진 주장 1개'를
    통제된 근거와 대조한다 — 균형 GT 스모크셋으로 verifier 판정 품질을 측정할 때 쓴다. 같은
    VERIFY_SYSTEM 프롬프트·_validate 규칙을 재사용해 실제 프로덕션 판정 기준을 그대로 측정한다.
    반환은 claim dict {id, claim, claim_type, status, basis, evidence_ids}.
    """
    reg = evidence.normalize(evidence_registry or [])
    if reg:
        block = evidence.for_prompt(reg)
        valid_ids: set | None = {e["evidence_id"] for e in reg}
    else:
        block, valid_ids = "", None
    user = (
        "아래 '단일 주장' 하나만 검증하세요. claims 에는 이 주장 1개만 담습니다.\n"
        f"[주장]\n{claim}\n\n"
        "[근거 출처 목록 — 이 주장을 뒷받침하는 출처의 evidence_id 를 evidence_ids 에 적으세요]\n"
        f"{block}"
    )
    fb = _dummy(claim)
    raw = llm.complete_json(VERIFY_SYSTEM, user, fallback=fb, model=model)
    return _validate(raw, fb, valid_ids)["claims"][0]
