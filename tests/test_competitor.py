"""Competitor Agent 스키마 검증 테스트 (LLM/검색 호출 없음)."""
from __future__ import annotations

import json

from app.agents import competitor


def test_validate_normalizes_competitors():
    fb = competitor._dummy({"competitors": ["A"]})
    raw = {
        "competitors": [
            {"name": "잡코리아", "description": "채용 정보", "strengths": ["인지도", "", 3], "weaknesses": ["특화 부족"]},
            "문자열(무시)",
            {"name": 42},  # name 타입오류 → ""
        ],
        "positioning": "니치 특화",
        "differentiation": ["타깃 특화", "", "편의성"],
    }
    out = competitor._validate(raw, fb)
    assert len(out["competitors"]) == 2                     # 비-dict 항목 제외
    first = out["competitors"][0]
    assert first["name"] == "잡코리아"
    assert first["strengths"] == ["인지도"]                 # 빈/비문자열 제거
    assert out["competitors"][1]["name"] == ""              # 타입오류 → 빈값
    assert out["positioning"] == "니치 특화"
    assert out["differentiation"] == ["타깃 특화", "편의성"]
    assert "[더미]" not in json.dumps(out, ensure_ascii=False)


def test_validate_non_dict_falls_back():
    fb = competitor._dummy({})
    assert competitor._validate("깨짐", fb) == fb


def test_competitor_saves_search_sources(monkeypatch):
    """외부 리뷰 P0-3: Competitor 가 쓴 실제 검색 출처를 competitor_sources 로 보존한다."""
    monkeypatch.setattr(competitor.llm, "is_dummy", lambda: False)
    monkeypatch.setattr(competitor.search, "web_search",
                        lambda q, **k: [{"title": "경쟁 비교", "url": "https://cmp.io/x", "content": "내용"}])
    monkeypatch.setattr(competitor.llm, "complete_json",
                        lambda *a, **k: {"competitors": [], "positioning": "P", "differentiation": ["d"]})
    out = competitor.competitor({"structured_input": {"project_name": "P"},
                                 "research_result": {}, "logs": []})
    assert out["competitor_sources"][0]["url"] == "https://cmp.io/x"   # 검색 출처 보존
    assert "웹검색 1건" in out["logs"][-1]
