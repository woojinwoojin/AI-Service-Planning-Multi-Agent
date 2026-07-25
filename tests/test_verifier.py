"""근거 일치성 검증 Agent 스키마 검증 테스트 (LLM 호출 없음)."""
from __future__ import annotations

import json

from app.agents import verifier


def test_validate_computes_support_rate_and_unsupported():
    fb = verifier._dummy("draft")
    raw = {"claims": [
        {"claim": "시장이 연 20% 성장", "status": "supported", "basis": "보고서 인용"},
        {"claim": "경쟁자가 없다", "status": "unsupported", "basis": "근거 없음"},
        {"claim": "효과가 크다", "status": "weird", "basis": ""},      # 잘못된 status → uncertain
        {"claim": "", "status": "supported"},                          # 빈 claim → 제외
        "무시",
    ]}
    out = verifier._validate(raw, fb)
    assert out["total"] == 3
    assert out["supported"] == 1
    assert out["support_rate"] == round(1 / 3, 2)
    assert out["unsupported"] == ["경쟁자가 없다"]
    assert out["claims"][2]["status"] == "uncertain"                   # 잘못된 등급 정규화
    assert "[더미]" not in json.dumps(out, ensure_ascii=False)


def test_validate_non_dict_and_empty_fall_back():
    fb = verifier._dummy("draft")
    assert verifier._validate("깨짐", fb) == fb
    assert verifier._validate({"claims": []}, fb) == fb


def test_verify_system_is_named_honestly():
    """item 5: 명칭이 구현 수준과 일치(근거 일치성 검증, URL 접속 아님)."""
    from app.prompts import templates
    assert "근거 일치성 검증" in templates.VERIFY_SYSTEM
    assert "접속하지 않" in templates.VERIFY_SYSTEM


def test_verify_log_uses_honest_label(monkeypatch):
    monkeypatch.setattr(verifier.llm, "is_dummy", lambda: True)
    out = verifier.verify({"final_draft": "본문", "research_result": {}, "logs": []})
    assert "근거 일치성 검증 완료" in out["logs"][-1]
    assert "출처 검증" not in out["logs"][-1]


def test_validate_sets_verification_scope():
    """PR-C: 검증 결과에 범위(검색 요약 기준)가 항상 명시된다."""
    fb = verifier._dummy("draft")
    assert fb["verification_scope"] == "search_snippet_only"          # fallback 도 명시
    raw = {"claims": [{"claim": "x", "status": "supported", "basis": "b"}]}
    out = verifier._validate(raw, fb)
    assert out["verification_scope"] == "search_snippet_only"         # 정상 경로도 명시


def test_verify_prompt_guards_against_type_and_falsehood():
    """PR-C: 프롬프트가 (1)출처 유형으로 판정 금지 (2)미확인≠거짓 가드레일을 담는다."""
    from app.prompts import templates
    assert "유형만으로 지지/불일치를 판정하지" in templates.VERIFY_SYSTEM  # §7-2 가드레일
    assert "거짓/틀림" in templates.VERIFY_SYSTEM                        # unsupported ≠ 거짓
    assert "모르면 판단 불가" in templates.VERIFY_SYSTEM                 # 확신 없으면 uncertain


# ── 로드맵 Tier 2: 주장 유형 분류 + 근거 상태 분리 ──────────────────────────

def test_non_fact_claims_are_not_applicable():
    """추론·제안 주장은 근거 검증 대상이 아니라 status=not_applicable 로 강제된다(사실만 검증)."""
    fb = verifier._dummy("d")
    raw = {"claims": [
        {"claim": "시장 규모는 1조원이다", "claim_type": "fact", "status": "supported"},
        {"claim": "따라서 성장할 것이다", "claim_type": "inference", "status": "supported"},  # 추론
        {"claim": "본 서비스는 추천을 제공한다", "claim_type": "proposal", "status": "supported"},  # 제안
    ]}
    out = verifier._validate(raw, fb)
    types = {c["claim"]: (c["claim_type"], c["status"]) for c in out["claims"]}
    assert types["시장 규모는 1조원이다"] == ("fact", "supported")
    assert types["따라서 성장할 것이다"][1] == "not_applicable"       # 추론 → 검증 대상 아님
    assert types["본 서비스는 추천을 제공한다"][1] == "not_applicable"  # 제안 → 검증 대상 아님
    # 사실 주장만 검증률 집계 대상
    assert out["fact_total"] == 1 and out["fact_supported"] == 1
    assert out["fact_support_rate"] == 1.0
    assert out["claim_type_counts"] == {"fact": 1, "inference": 1, "proposal": 1}


def test_contradicted_separated_from_unsupported():
    """'반대 근거(contradicted)'와 '근거 미확인(unsupported)'을 분리해 표면화한다(Tier 2)."""
    fb = verifier._dummy("d")
    raw = {"claims": [
        {"claim": "사용자가 증가한다", "claim_type": "fact", "status": "contradicted", "basis": "근거는 감소"},
        {"claim": "경쟁자가 없다", "claim_type": "fact", "status": "unsupported"},
    ]}
    out = verifier._validate(raw, fb)
    assert out["contradicted"] == ["사용자가 증가한다"]
    assert out["unsupported"] == ["경쟁자가 없다"]                     # 미확인과 분리
    assert out["fact_supported"] == 0 and out["fact_total"] == 2


def test_evidence_link_rate_over_fact_claims():
    """근거 연결률(완료 게이트)은 '사실 주장 중 evidence_id 가 붙은 비율'로 계산한다."""
    fb = verifier._dummy("d")
    valid = {"ev1", "ev2"}
    raw = {"claims": [
        {"claim": "A", "claim_type": "fact", "status": "supported", "evidence_ids": ["ev1"]},
        {"claim": "B", "claim_type": "fact", "status": "supported", "evidence_ids": []},
        {"claim": "C", "claim_type": "proposal", "status": "supported", "evidence_ids": ["ev2"]},  # 비-사실
    ]}
    out = verifier._validate(raw, fb, valid)
    assert out["fact_total"] == 2
    assert out["evidence_link_rate"] == 0.5                          # 사실 2건 중 1건 연결


def test_invalid_claim_type_defaults_to_fact():
    fb = verifier._dummy("d")
    raw = {"claims": [{"claim": "X", "claim_type": "이상값", "status": "supported"}]}
    out = verifier._validate(raw, fb)
    assert out["claims"][0]["claim_type"] == "fact"                  # 이상 유형 → 보수적으로 fact


def test_dummy_has_tier2_fields():
    d = verifier._dummy("draft")
    for key in ("claim_type_counts", "fact_total", "fact_support_rate",
                "evidence_link_rate", "contradicted"):
        assert key in d
    assert d["claims"][0]["claim_type"] == "fact"


# ── 외부 리뷰 3차 B-1·B-2: 가짜 evidence_id 차단 · 2차 생성물 자기확인 분리 ──────

def test_invented_evidence_ids_dropped_without_registry():
    """B-1: 레지스트리가 없으면 LLM 이 지어낸 id(ev999)는 전부 제거된다(근거 연결률 부풀림 방지)."""
    fb = verifier._dummy("d")
    raw = {"claims": [
        {"claim": "A", "claim_type": "fact", "status": "supported", "evidence_ids": ["ev999"]},
    ]}
    out = verifier._validate(raw, fb)                  # valid_ids 없음 = 알려진 근거 없음
    assert out["claims"][0]["evidence_ids"] == []
    assert out["evidence_linked"] == 0
    assert out["evidence_link_rate"] == 0.0


def test_clean_evidence_ids_treats_none_as_empty():
    """B-1: valid_ids=None 은 '전부 허용'이 아니라 '알려진 id 없음'이다."""
    assert verifier._clean_evidence_ids(["ev1", "ev2"], None) == []
    assert verifier._clean_evidence_ids(["ev1", "ev2"], set()) == []
    assert verifier._clean_evidence_ids(["ev1", "zz"], {"ev1"}) == ["ev1"]


def test_supported_downgraded_when_registry_but_no_link():
    """레지스트리가 있는데 근거를 지목하지 못한 supported 는 uncertain 으로 강등(P1-4)."""
    fb = verifier._dummy("d")
    raw = {"claims": [{"claim": "A", "claim_type": "fact", "status": "supported", "evidence_ids": []}]}
    out = verifier._validate(raw, fb, {"ev1"}, require_evidence_link=True)
    assert out["claims"][0]["status"] == "uncertain"


def test_supported_downgraded_when_no_evidence_at_all():
    """B-2: 검증 근거 텍스트가 아예 없으면 supported 를 인정하지 않는다(자기확인 차단)."""
    fb = verifier._dummy("d")
    raw = {"claims": [{"claim": "A", "claim_type": "fact", "status": "supported"}]}
    out = verifier._validate(raw, fb, set(), evidence_available=False)
    assert out["claims"][0]["status"] == "uncertain"
    assert out["fact_supported"] == 0


def test_snippet_fallback_allows_supported_without_ids():
    """레지스트리는 없지만 검색 스니펫이 있으면(옛 경로) supported 를 유지한다 — 회귀 방지."""
    fb = verifier._dummy("d")
    raw = {"claims": [{"claim": "A", "claim_type": "fact", "status": "supported"}]}
    out = verifier._validate(raw, fb, set(), require_evidence_link=False, evidence_available=True)
    assert out["claims"][0]["status"] == "supported"


def test_verify_prompt_separates_context_from_evidence(monkeypatch):
    """B-2: verify 프롬프트가 research/competitor 를 '참고 문맥'으로만 넘기고 근거와 분리한다."""
    captured = {}

    def fake(system, user, **kw):
        captured["user"] = user
        return {"claims": [{"claim": "A", "claim_type": "fact", "status": "supported"}]}

    monkeypatch.setattr(verifier.llm, "complete_json", fake)
    monkeypatch.setattr(verifier.llm, "mode_label", lambda *a, **k: "테스트")
    out = verifier.verify({
        "final_draft": "본문", "research_result": {"market_size": "1조"},
        "competitor_result": {"note": "경쟁"}, "logs": [],
    })
    user = captured["user"]
    assert "[검증 근거" in user and "[참고 문맥" in user
    # 2차 생성물은 참고 문맥 뒤에만 등장해야 한다(근거 블록에 섞이지 않음).
    assert user.index("[참고 문맥") < user.index("1조")
    assert "수집된 근거 없음" in user                       # 근거가 없다는 사실을 명시
    # 근거가 하나도 없었으므로 supported 는 인정되지 않는다.
    assert out["verification_result"]["claims"][0]["status"] == "uncertain"


def test_verify_uses_registry_ids_and_requires_link(monkeypatch):
    """레지스트리가 있으면 유효 id 만 통과하고, 연결 없는 supported 는 강등된다."""
    from app.services import evidence

    reg = evidence.entries_from("research", "q", [
        {"url": "https://a", "title": "t", "snippet": "s", "source_type": "news"}])

    monkeypatch.setattr(verifier.llm, "complete_json", lambda *a, **k: {"claims": [
        {"claim": "A", "claim_type": "fact", "status": "supported", "evidence_ids": ["ev1"]},
        {"claim": "B", "claim_type": "fact", "status": "supported", "evidence_ids": ["ev404"]},
    ]})
    monkeypatch.setattr(verifier.llm, "mode_label", lambda *a, **k: "테스트")
    res = verifier.verify({"final_draft": "본문", "evidence_registry": reg, "logs": []})["verification_result"]
    by_claim = {c["claim"]: c for c in res["claims"]}
    assert by_claim["A"]["status"] == "supported" and by_claim["A"]["evidence_ids"] == ["ev1"]
    assert by_claim["B"]["evidence_ids"] == []            # 지어낸 id 제거
    assert by_claim["B"]["status"] == "uncertain"         # 연결 없는 supported 강등


def test_verify_prompt_forbids_self_confirmation():
    """프롬프트가 '2차 생성물을 근거로 쓰지 말라'는 가드레일을 담는다."""
    from app.prompts import templates
    assert "자기확인" in templates.VERIFY_SYSTEM
    assert "[검증 근거]" in templates.VERIFY_SYSTEM


def test_verify_prompt_has_tier2_classification():
    from app.prompts import templates
    assert "claim_type" in templates.VERIFY_SYSTEM
    assert "inference" in templates.VERIFY_SYSTEM and "proposal" in templates.VERIFY_SYSTEM
    assert "contradicted" in templates.VERIFY_SYSTEM                 # 반대 근거 구분
    assert "사실 주장" in templates.VERIFY_SYSTEM
