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
