"""Customer Problem Agent 스키마 검증 테스트 (LLM 호출 없음)."""
from __future__ import annotations

import json

from app.agents import customer


def test_validate_normalizes():
    fb = customer._dummy({"target_user": "U"})
    out = customer._validate(
        {"target_persona": "30대 자영업자", "pain_points": ["재고 부담", "", 3],
         "needs": "리스트아님", "jobs_to_be_done": ["발주 자동화"]}, fb)
    assert out["target_persona"] == "30대 자영업자"
    assert out["pain_points"] == ["재고 부담"]       # 빈/비문자열 제거
    assert out["needs"] == []                         # 리스트 아님 → []
    assert out["jobs_to_be_done"] == ["발주 자동화"]
    assert "[더미]" not in json.dumps(out, ensure_ascii=False)


def test_validate_empty_and_non_dict_fall_back():
    fb = customer._dummy({})
    assert customer._validate("깨짐", fb) == fb
    # 내용이 전혀 없으면 fallback
    assert customer._validate({"target_persona": "", "pain_points": [], "needs": [], "jobs_to_be_done": []}, fb) == fb
