"""웹 검색 통합(Research grounding + 출처 인용) 테스트 — 실제 Tavily 호출 없음."""
from __future__ import annotations

from app.agents import research
from app.services import search


def test_search_disabled_returns_empty(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    assert search.search_enabled() is False
    assert search.web_search("아무 쿼리") == []


def test_build_query_uses_name_and_keywords():
    q = research._build_query({"project_name": "AI 진로 설계", "keywords": ["진로", "대학생"]})
    assert "AI 진로 설계" in q and "진로" in q and "대학생" in q


def test_merge_sources_puts_real_urls_first_and_dedups():
    hits = [{"title": "시장 보고서", "url": "https://ex.com/a"},
            {"title": "", "url": "https://ex.com/b"}]
    merged = research._merge_sources(["https://ex.com/a", "LLM이 적은 근거"], hits)
    assert merged[0] == "시장 보고서 — https://ex.com/a"   # 실제 출처 우선
    assert "https://ex.com/b" in merged                     # 제목 없으면 URL만
    assert "LLM이 적은 근거" in merged                       # LLM 근거도 병합
    assert len(merged) == len(set(merged))                   # 중복 제거


def test_research_injects_real_sources(monkeypatch):
    monkeypatch.setattr(research.llm, "is_dummy", lambda: False)
    monkeypatch.setattr(research.search, "web_search",
                        lambda q, **k: [{"title": "동향", "url": "https://src.io/x", "content": "내용"}])
    monkeypatch.setattr(research.llm, "complete_json",
                        lambda *a, **k: {"market_overview": "개요", "industry_trends": ["t"],
                                         "customer_needs": ["n"], "competitors": ["c"],
                                         "opportunities": ["o"], "risks": ["r"], "sources": []})
    out = research.research({"structured_input": {"project_name": "P", "keywords": ["k"]}, "logs": []})
    srcs = out["research_result"]["sources"]
    assert any("https://src.io/x" in s for s in srcs)         # 실제 검색 URL이 인용됨
    assert "웹검색 1건" in out["logs"][-1]                    # 로그에 검색 사용 표기
