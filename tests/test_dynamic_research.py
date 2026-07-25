"""제한된 동적 실행(로드맵 2-5) 테스트 — 실제 LLM·실제 검색 호출 없음.

핵심 계약:
  1) 트리거는 Research 가 스스로 보고한 근거 공백뿐 — 보고가 없으면 검색·LLM 호출 0회.
  2) 상한을 넘지 않는다(검색 DYNAMIC_MAX_GAP_SEARCHES · LLM 1회).
  3) 예산 상한·검색 비활성·더미 모드에서는 생략하고 '왜 안 했는지'를 남긴다.
  4) 실패·빈 결과여도 기존 조사 결과를 훼손하지 않고 관통한다.
"""
from __future__ import annotations

import pytest

from app.agents import research
from app.services import budget, llm, search, usage


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("DYNAMIC_MAX_GAP_SEARCHES", raising=False)
    budget.reset()
    yield


@pytest.fixture
def real_mode(monkeypatch):
    """실제 모드 + 검색 활성으로 간주(외부 호출은 각 테스트가 monkeypatch 로 대체)."""
    monkeypatch.setattr(llm, "is_dummy", lambda: False)
    monkeypatch.setattr(search, "search_enabled", lambda: True)
    return monkeypatch


def _state(gaps, **over):
    state = {
        "evidence_gaps": gaps,
        "research_result": {"industry_trends": ["기존 트렌드"], "customer_needs": [],
                            "opportunities": [], "risks": [], "sources": ["기존 출처"],
                            "source_objects": [{"url": "https://known", "title": "기존"}]},
    }
    state.update(over)
    return state


def _hit(url, title="제목", content="검색 요약문"):
    return {"url": url, "title": title, "content": content}


# ── 1) 트리거: 보고된 공백만 ────────────────────────────────────────────────────

def test_no_gap_reported_means_no_calls(real_mode):
    """공백 보고가 없으면 추가 검색·LLM 호출을 전혀 하지 않는다(비용 0)."""
    calls = {"search": 0, "llm": 0}
    real_mode.setattr(search, "web_search", lambda *a, **k: calls.__setitem__("search", calls["search"] + 1))
    real_mode.setattr(llm, "complete_json", lambda *a, **k: calls.__setitem__("llm", calls["llm"] + 1))

    out = research.research_gap(_state([]))
    assert calls == {"search": 0, "llm": 0}
    assert out["dynamic_research"]["skip_reason"] == "근거 공백 보고 없음"
    assert "research_result" not in out                     # 기존 결과를 건드리지 않는다
    assert "생략" in out["logs"][0]


def test_research_reports_gaps_into_separate_state_key(monkeypatch):
    """Research 응답의 evidence_gaps 는 research_result 가 아니라 state 별도 키로 나간다.

    (research_result 에 남기면 Draft·분석 프롬프트에 '근거 부족' 메타가 섞여 문서에 새어든다.)
    """
    monkeypatch.setattr(llm, "is_dummy", lambda: False)
    monkeypatch.setattr(search, "web_search", lambda *a, **k: [])
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: {
        "market_overview": "개요", "industry_trends": ["t"], "customer_needs": ["n"],
        "competitors": ["c"], "opportunities": ["o"], "risks": ["r"], "sources": ["s"],
        "evidence_gaps": [{"topic": "국내 시장 규모", "query": "국내 시장 규모 2026"},
                          {"topic": "규제", "query": "관련 규제"},
                          {"topic": "세번째", "query": "초과분"}],
    })
    monkeypatch.setattr(llm, "mode_label", lambda *a, **k: "테스트")

    out = research.research({"structured_input": {"project_name": "P"}})
    assert "evidence_gaps" not in out["research_result"]      # 조사 결과에는 없다
    assert [g["topic"] for g in out["evidence_gaps"]] == ["국내 시장 규모", "규제"]  # 최대 2건
    assert "근거공백 2건 보고" in out["logs"][0]              # 로그에 정직하게 표면화


def test_normalize_gaps_accepts_strings_and_dedupes():
    gaps = research._normalize_gaps(["시장 규모", {"topic": "규제", "query": "규제 검색"},
                                     "시장 규모", {"nope": 1}, 3])
    assert gaps == [{"topic": "시장 규모", "query": "시장 규모"},
                    {"topic": "규제", "query": "규제 검색"}]


# ── 2) 상한 ─────────────────────────────────────────────────────────────────────

def test_search_count_is_capped_by_env(real_mode):
    """보고가 여러 건이어도 DYNAMIC_MAX_GAP_SEARCHES 를 넘겨 검색하지 않는다."""
    real_mode.setenv("DYNAMIC_MAX_GAP_SEARCHES", "1")
    queries = []

    def fake_search(q, **k):
        queries.append(q)
        return [_hit(f"https://new/{len(queries)}")]

    real_mode.setattr(search, "web_search", fake_search)
    real_mode.setattr(llm, "complete_json", lambda *a, **k: {})
    real_mode.setattr(llm, "mode_label", lambda *a, **k: "테스트")

    gaps = [{"topic": "A", "query": "qa"}, {"topic": "B", "query": "qb"}]
    out = research.research_gap(_state(gaps))
    assert queries == ["qa"]                                  # 1회만
    assert len(out["dynamic_research"]["searches"]) == 1


def test_llm_augmentation_is_single_call(real_mode):
    """새 근거가 여러 건이어도 보강 LLM 호출은 1회다."""
    llm_calls = {"n": 0}

    def fake_json(*a, **k):
        llm_calls["n"] += 1
        return {"industry_trends": ["새 트렌드"], "risks": ["새 리스크"]}

    real_mode.setattr(search, "web_search", lambda q, **k: [_hit("https://a"), _hit("https://b")])
    real_mode.setattr(llm, "complete_json", fake_json)
    real_mode.setattr(llm, "mode_label", lambda *a, **k: "테스트")

    out = research.research_gap(_state([{"topic": "A", "query": "qa"}, {"topic": "B", "query": "qb"}]))
    assert llm_calls["n"] == 1
    assert out["dynamic_research"]["applied"] is True


def test_env_zero_disables_feature(real_mode):
    real_mode.setenv("DYNAMIC_MAX_GAP_SEARCHES", "0")
    real_mode.setattr(search, "web_search", lambda *a, **k: pytest.fail("검색해서는 안 된다"))
    out = research.research_gap(_state([{"topic": "A", "query": "qa"}]))
    assert "비활성" in out["dynamic_research"]["skip_reason"]


# ── 3) 생략 사유 ────────────────────────────────────────────────────────────────

def test_skipped_when_budget_exhausted(real_mode):
    """예산 상한에 도달했으면 추가 조사를 하지 않고 사유를 남긴다(예산 정책과 연계)."""
    usage.start()
    budget.start({"max_llm_calls": 1, "max_cost_usd": None, "max_wall_ms": None})
    usage.record("gpt-4o-mini", 10, 10, 1.0, False)            # 상한 도달
    real_mode.setattr(search, "web_search", lambda *a, **k: pytest.fail("검색해서는 안 된다"))

    out = research.research_gap(_state([{"topic": "A", "query": "qa"}]))
    assert out["dynamic_research"]["skip_reason"] == "예산 상한 도달"
    assert budget.status()["enforced"] is True                 # 예산이 막았음이 표면화


def test_skipped_when_search_disabled(monkeypatch):
    monkeypatch.setattr(llm, "is_dummy", lambda: False)
    monkeypatch.setattr(search, "search_enabled", lambda: False)
    out = research.research_gap(_state([{"topic": "A", "query": "qa"}]))
    assert out["dynamic_research"]["skip_reason"] == "검색 비활성"


def test_skipped_in_dummy_mode(monkeypatch):
    monkeypatch.setattr(llm, "is_dummy", lambda: True)
    monkeypatch.setattr(search, "search_enabled", lambda: True)
    out = research.research_gap(_state([{"topic": "A", "query": "qa"}]))
    assert out["dynamic_research"]["skip_reason"] == "더미 모드"


# ── 4) 결과 반영·관통 ───────────────────────────────────────────────────────────

def test_new_evidence_merged_into_result_and_registry(real_mode):
    """새 출처는 sources·source_objects·근거 레지스트리에, 새 내용은 조사 결과에 덧붙는다."""
    real_mode.setattr(search, "web_search", lambda q, **k: [_hit("https://new", "새 보고서")])
    real_mode.setattr(llm, "complete_json", lambda *a, **k: {
        "industry_trends": ["새 트렌드", "기존 트렌드"],       # 중복은 제외돼야 한다
        "opportunities": ["새 기회"]})
    real_mode.setattr(llm, "mode_label", lambda *a, **k: "테스트")

    out = research.research_gap(_state([{"topic": "시장 규모", "query": "시장 규모"}]))
    rr = out["research_result"]
    assert rr["industry_trends"] == ["기존 트렌드", "새 트렌드"]   # 덧붙이기(중복 제거)
    assert rr["opportunities"] == ["새 기회"]
    assert any("https://new" in s for s in rr["sources"])          # 표시용 출처
    assert [o["url"] for o in rr["source_objects"]] == ["https://known", "https://new"]
    assert [e["url"] for e in out["evidence_registry"]] == ["https://new"]
    assert out["evidence_registry"][0]["source_agents"] == ["research_gap"]   # 출처 주체 구분
    meta = out["dynamic_research"]
    assert meta["reported"] == 1 and meta["new_sources"] == 1
    assert meta["added_findings"] == 2 and meta["applied"] is True


def test_already_known_url_is_not_counted_as_new(real_mode):
    """이미 확보한 URL 만 나오면 '새 근거 없음'으로 끝난다(중복 근거로 부풀리지 않음)."""
    real_mode.setattr(search, "web_search", lambda q, **k: [_hit("https://known")])
    real_mode.setattr(llm, "complete_json", lambda *a, **k: pytest.fail("LLM 호출 불필요"))

    out = research.research_gap(_state([{"topic": "A", "query": "qa"}]))
    assert out["dynamic_research"]["skip_reason"] == "새 근거 없음"
    assert out["dynamic_research"]["new_sources"] == 0
    assert "research_result" not in out                        # 기존 결과 보존


def test_llm_failure_keeps_original_findings(real_mode):
    """보강 LLM 이 실패(fallback {})해도 조사 결과는 훼손되지 않고 새 출처만 반영된다."""
    real_mode.setattr(search, "web_search", lambda q, **k: [_hit("https://new")])
    real_mode.setattr(llm, "complete_json", lambda *a, **k: {})   # fallback
    real_mode.setattr(llm, "mode_label", lambda *a, **k: "fallback·형식")

    out = research.research_gap(_state([{"topic": "A", "query": "qa"}]))
    assert out["research_result"]["industry_trends"] == ["기존 트렌드"]   # 원본 유지
    assert out["dynamic_research"]["applied"] is False
    assert out["dynamic_research"]["new_sources"] == 1                   # 근거는 남는다
    assert "fallback" in out["logs"][0]                                  # 정직하게 표면화


# ── 워크플로 배선 ───────────────────────────────────────────────────────────────

def test_node_is_wired_in_both_graphs():
    """직렬·병렬 두 그래프 모두 research → research_gap → (다음) 경로를 갖는다."""
    from app.graph.workflow import PARALLEL_GRAPH, SERIAL_GRAPH

    for graph in (SERIAL_GRAPH, PARALLEL_GRAPH):
        assert "research_gap" in graph.get_graph().nodes


def test_dummy_run_reports_skip_and_persists(tmp_path, monkeypatch):
    """더미 관통 실행에서도 dynamic_research 가 state 에 남아 '무엇을 안 했는지'가 보인다."""
    from app.graph.workflow import run_workflow

    monkeypatch.setattr(llm, "is_dummy", lambda: True)
    state = run_workflow({"project_name": "동적", "problem": "P"})
    assert state["evidence_gaps"] == []
    assert state["dynamic_research"]["skip_reason"] == "근거 공백 보고 없음"
    assert state["dynamic_research"]["new_sources"] == 0
