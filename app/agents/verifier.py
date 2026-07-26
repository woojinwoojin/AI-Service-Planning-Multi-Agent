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
from app.schemas import artifact
from app.schemas.state import ProjectState
from app.services import evidence, llm


def _content(state: ProjectState, artifact_type: str) -> dict:
    """Agent 산출물을 읽는 단일 창구(로드맵 2-2 PR 5). ARTIFACT_READ_MODE 를 따른다."""
    legacy_key = artifact.SPEC_BY_TYPE[artifact_type]["legacy_key"]
    data = artifact.get_artifact_content(state, artifact_type, legacy_key)
    return data if isinstance(data, dict) else {}

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
    """LLM 이 인용한 evidence_ids 를 정규화한다 — 문자열만·중복 제거·'알려진 id'만 남김.

    valid_ids 는 근거 레지스트리에 실제로 존재하는 id 집합이다. 레지스트리가 없으면 빈 집합이며,
    이때는 모든 id 가 제거된다 — 알려진 근거가 없는데 LLM 이 `ev999` 같은 id 를 지어내 붙이면
    '근거에 연결됨'으로 집계돼 근거 연결률이 부풀려지기 때문이다(외부 리뷰 3차 B-1).
    `None` 도 '알려진 id 없음'으로 취급한다(과거의 '전부 허용' 의미를 폐기).
    """
    allowed = valid_ids or set()
    out: list[str] = []
    for x in raw or []:
        x = x.strip() if isinstance(x, str) else ""
        if not x or x in out:
            continue
        if x not in allowed:
            continue
        out.append(x)
    return out


def _validate(result: dict, fallback: dict, valid_ids: set | None = None,
              require_evidence_link: bool = False, evidence_available: bool = True) -> dict:
    """LLM 판정을 스키마·검증 규칙에 맞게 정규화한다.

    근거 관련 인자는 서로 독립이다(B-1: 예전엔 `valid_ids is None` 하나가 두 뜻을 겸했다).
      valid_ids            — 레지스트리에 실제 존재하는 evidence_id (없으면 전부 제거)
      require_evidence_link— supported 사실 주장에 evidence_id 연결을 요구할지(레지스트리가 있을 때)
      evidence_available   — 검증에 쓸 근거 텍스트가 하나라도 있었는지(없으면 supported 인정 불가)
    """
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
            if ctype == "fact" and status == "supported":
                # 근거 레지스트리가 있는데 특정 evidence_id 를 지목하지 못한 'supported' 는 자기확인일
                # 수 있다(LLM 이 근거 없이 지지 판정) → uncertain 으로 강등한다(외부 리뷰 P1-4).
                if require_evidence_link and not eids:
                    status = "uncertain"
                # 검증 근거 텍스트 자체가 없었으면(레지스트리·검색 스니펫 모두 없음) 지지 판정의
                # 근거가 없다 → uncertain. 앞 단계 LLM 산출물(research/competitor)은 2차 생성물이라
                # 검증 근거로 쓰지 않는다(B-2 자기확인 차단).
                elif not evidence_available:
                    status = "uncertain"
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
    # 분석 문맥(검증 근거가 아니다 — 근거는 아래 Evidence Registry 스니펫뿐, 외부 리뷰 B-2).
    research = _content(state, "research_analysis")
    competitor = _content(state, "competitor_analysis")
    fallback = _dummy(draft)

    # 통합 근거 레지스트리를 evidence_id 와 함께 제시 → LLM 이 주장별로 어떤 근거가 뒷받침하는지
    # 지목(인용)하게 한다(2-1b: 주장-근거 연결). 레지스트리가 없으면(옛 프로젝트/재작성) 기존
    # 경쟁사 검색 출처로 fallback 하되 evidence_id 연결은 생략한다(회귀 없이 동작 보장).
    registry = evidence.normalize(state.get("evidence_registry", []) or [])
    if registry:
        evidence_block = evidence.for_prompt(registry)
        valid_ids: set = {e["evidence_id"] for e in registry}
    else:
        comp_sources = state.get("competitor_sources", []) or []
        evidence_block = "\n".join(
            f"- {s.get('title', '')}: {s.get('snippet', '')}"
            for s in comp_sources if isinstance(s, dict)
        )
        valid_ids = set()   # 알려진 id 가 없으므로 LLM 이 지어낸 id 는 모두 제거(B-1)

    # 검증 근거는 '외부에서 수집한 것'(레지스트리·검색 스니펫)만이다. research/competitor 는
    # 앞 단계 LLM 이 만든 2차 생성물이라 근거로 쓰면 자기확인이 되므로, 아래에서 '참고 문맥'으로만
    # 넘긴다(B-2). 근거가 하나도 없으면 supported 를 인정하지 않는다(_validate 가 강등).
    evidence_available = bool(evidence_block.strip())
    user = (
        "아래 기획서의 사실성 주장을 '검증 근거'와 대조해 검증하세요.\n"
        f"[기획서]\n{draft}\n\n"
        "[검증 근거 — 판정은 오직 이 목록만을 기준으로 합니다. 각 주장을 뒷받침하는 출처의 "
        "evidence_id 를 evidence_ids 에 적으세요]\n"
        f"{evidence_block or '(수집된 근거 없음 — 사실 주장을 supported 로 판정할 수 없습니다)'}\n\n"
        "[참고 문맥 — 앞 단계 Agent 가 생성한 분석 결과입니다. 주장의 의미를 이해하는 데만 쓰고 "
        "판정 근거로는 쓰지 마세요(검증 대상과 같은 LLM 이 만든 2차 생성물입니다)]\n"
        f"- 시장조사 분석: {json.dumps(research, ensure_ascii=False)}\n"
        f"- 경쟁사 분석: {json.dumps(competitor, ensure_ascii=False)}"
    )
    status: dict = {}
    raw = llm.complete_json(VERIFY_SYSTEM, user, fallback=fallback,
                            model=state.get("model", ""), status=status)
    result = _validate(raw, fallback, valid_ids,
                       require_evidence_link=bool(registry),
                       evidence_available=evidence_available)

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
        valid_ids: set = {e["evidence_id"] for e in reg}
    else:
        block, valid_ids = "", set()
    user = (
        "아래 '단일 주장' 하나만 검증하세요. claims 에는 이 주장 1개만 담습니다.\n"
        f"[주장]\n{claim}\n\n"
        "[검증 근거 — 판정은 오직 이 목록만을 기준으로 합니다. 이 주장을 뒷받침하는 출처의 "
        "evidence_id 를 evidence_ids 에 적으세요]\n"
        f"{block or '(수집된 근거 없음 — supported 로 판정할 수 없습니다)'}"
    )
    fb = _dummy(claim)
    raw = llm.complete_json(VERIFY_SYSTEM, user, fallback=fb, model=model)
    return _validate(raw, fb, valid_ids,
                     require_evidence_link=bool(reg),
                     evidence_available=bool(block.strip()))["claims"][0]
