"""SWOT / 수익모델 / 리스크 Agent 스키마 검증 테스트 (LLM 호출 없음)."""
from __future__ import annotations

import json

from app.agents import swot, business_model, risk


def test_swot_validate_normalizes():
    fb = swot._dummy({})
    out = swot._validate({"strengths": ["A", "", 3], "weaknesses": "x", "opportunities": ["O"]}, fb)
    assert out["strengths"] == ["A"]          # 빈/비문자열 제거
    assert out["weaknesses"] == []            # 리스트 아님 → []
    assert out["opportunities"] == ["O"]
    assert out["threats"] == []               # 누락 → []
    assert "[더미]" not in json.dumps(out, ensure_ascii=False)
    assert swot._validate("깨짐", fb) == fb


def test_business_model_validate_normalizes():
    fb = business_model._dummy({})
    out = business_model._validate(
        {"revenue_streams": ["구독", ""], "pricing": 42, "cost_structure": ["인프라"], "key_metrics": []}, fb)
    assert out["revenue_streams"] == ["구독"]
    assert out["pricing"] == ""               # 타입오류 → ""
    assert out["cost_structure"] == ["인프라"]
    assert out["key_metrics"] == []
    assert business_model._validate(None, fb) == fb


def test_risk_validate_clamps_levels():
    fb = risk._dummy({})
    out = risk._validate({"risks": [
        {"category": "기술", "description": "모델 정확도", "likelihood": "높음", "impact": "상", "mitigation": "개선"},
        "무시",
    ]}, fb)
    assert len(out["risks"]) == 1
    rr = out["risks"][0]
    assert rr["likelihood"] == "중"           # 잘못된 등급 → 기본 '중'
    assert rr["impact"] == "상"               # 유효 등급 유지
    assert rr["category"] == "기술"
    # risks가 비면 fallback
    assert risk._validate({"risks": []}, fb) == fb
