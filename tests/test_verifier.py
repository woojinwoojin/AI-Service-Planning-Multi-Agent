"""Source Verification Agent 스키마 검증 테스트 (LLM 호출 없음)."""
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
