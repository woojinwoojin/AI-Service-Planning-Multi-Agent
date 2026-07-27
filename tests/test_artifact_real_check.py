"""Artifact 실 LLM 검증 하네스의 자기검증 (로드맵 2-2 PR 5f) — 실 LLM·검색 호출 없음.

이 모듈은 **판정을 내리는 도구**다. 도구가 조용히 항상 ok 를 뱉으면 리포트의 ✅ 는 아무
의미가 없다. 그래서 통과 경로만 보지 않고 **일부러 어긋뜨렸을 때 잡는지**를 함께 고정한다
(PR 3 의 `check_parity` 테스트와 같은 태도).
"""
from __future__ import annotations

import pytest

from app.schemas import artifact
from app.services import artifact_real_check as arc
from app.services import llm

_FULL = {"market_overview": "시장 개요", "competitors": ["A사", "B사"],
         "industry_trends": ["t1"], "customer_needs": ["n1"],
         "opportunities": ["o1"], "risks": ["r1"]}


@pytest.fixture
def legacy_env(monkeypatch):
    """monkeypatch 가 원래 값을 기억하게 해 둔다 — 하네스가 env 를 직접 바꾸기 때문."""
    monkeypatch.setenv(artifact.READ_MODE_ENV, artifact.READ_LEGACY)
    monkeypatch.setattr(llm, "is_dummy", lambda: True)


def _consistent_state() -> dict:
    """평면 키와 Artifact 내용이 **같은** 정상 State(실제 실행이 남기는 모양)."""
    results = {
        "research_analysis": _FULL,
        "competitor_analysis": {"competitors": [{"name": "A사"}], "positioning": "p",
                                "differentiation": ["d"]},
        "customer_analysis": {"personas": [{"name": "김철수"}]},
        # PESTEL 은 요인별 dict 다(초안 조립기가 .get 으로 읽는다) — 실제 형태를 그대로 쓴다.
        "pestel_analysis": {"정치": {"content": "규제", "opportunity": "지원",
                                    "threat": "규제 강화", "response": "대응"}},
        "swot_analysis": {"strengths": ["s"]},
        "business_model_analysis": {"revenue_streams": ["r"]},
        "risk_analysis": {"risks": [{"name": "위험"}]},
    }
    state: dict = {"structured_input": {"project_name": "P"}, "final_draft": "# 문서\n본문",
                   "artifacts": [artifact.make_artifact(t, c) for t, c in results.items()]}
    for t, c in results.items():
        state[artifact.SPEC_BY_TYPE[t]["legacy_key"]] = c
    return state


# ---- prompt_parity ----

def test_prompt_parity_passes_on_consistent_state(legacy_env):
    """평면 키와 Artifact 가 같으면 세 모드의 프롬프트가 같아야 한다."""
    rep = arc.prompt_parity(_consistent_state())
    assert rep["ok"] and rep["mismatched"] == [] and rep["empty"] == []
    assert rep["checked"] == len(arc.CONSUMERS)


def test_prompt_parity_catches_divergent_read(legacy_env):
    """**공허하지 않은가** — Artifact 만 다른 값으로 바꾸면 반드시 잡아야 한다.

    잡지 못하면 리포트의 '프롬프트 동일성 ✅' 는 아무것도 보증하지 않는다.
    """
    state = _consistent_state()
    state["artifacts"] = [artifact.make_artifact("research_analysis", {"market_overview": "다른 값"})
                          if a["artifact_type"] == "research_analysis" else a
                          for a in state["artifacts"]]
    rep = arc.prompt_parity(state)
    assert not rep["ok"]
    # research 를 읽는 소비자가 전부 걸려야 한다(하나만 걸리면 범위가 좁다는 뜻).
    consumers = {m["consumer"] for m in rep["mismatched"]}
    assert {"competitor", "customer", "pestel", "swot", "business_model", "risk"} <= consumers


def test_prompt_parity_reports_consumers_with_no_prompt(legacy_env, monkeypatch):
    """프롬프트가 하나도 안 잡힌 소비자는 **검증한 게 아니다** — ok 로 세지 않는다."""
    monkeypatch.setitem(arc.CONSUMERS, "아무것도안함", lambda state: {})
    rep = arc.prompt_parity(_consistent_state())
    assert not rep["ok"] and rep["empty"] == ["아무것도안함"]


def test_prompt_parity_makes_no_llm_or_search_calls(legacy_env, monkeypatch):
    """비용 0 이어야 한다 — 이 검사는 '추가 비용 없이' 돌린다는 게 설계 전제다."""
    def boom(*a, **k):
        raise AssertionError("호출되면 안 된다")

    monkeypatch.setattr(llm, "_get_model", boom)
    monkeypatch.setattr("app.services.search._client", boom, raising=False)
    arc.prompt_parity(_consistent_state())


def test_capture_restores_patched_functions(legacy_env):
    """가로채기가 끝나면 원래 함수로 돌아와야 한다(다음 실행이 오염되면 안 된다)."""
    from app.agents import draft_writer
    from app.services import search

    before = (llm.complete_json, llm.complete_text, draft_writer._generate, search.web_search)
    arc.prompt_parity(_consistent_state())
    assert (llm.complete_json, llm.complete_text,
            draft_writer._generate, search.web_search) == before


def test_artifact_only_missing_dependency_is_recorded(legacy_env):
    """`artifact_only` 에서 의존이 없으면 사유가 비교 대상에 남는다(조용히 빈 목록 아님)."""
    state = {"structured_input": {"project_name": "P"}, "research_result": {"x": 1}}
    seen = arc._capture_prompts(arc.CONSUMERS["competitor"], state, artifact.READ_ARTIFACT_ONLY)
    assert len(seen) == 1 and "ArtifactUnavailable" in seen[0]


# ---- 지표 추출·집계 ----

def test_run_metrics_survives_a_sparse_state():
    """지표 추출이 옛/부분 State 에서도 죽지 않아야 한다(리포트가 통째로 날아가지 않게)."""
    row = arc._run_metrics({}, artifact.READ_LEGACY, "t1")
    assert row["topic"] == "t1" and row["read_mode"] == artifact.READ_LEGACY
    assert row["failed_nodes"] == [] and row["parity_ok"] is None
    assert row["sections_present"] == 0


def test_merge_counts_sums_and_sorts():
    assert arc._merge_counts([{"missing": 1}, {"missing": 2, "empty": 1}, {}]) == \
           {"empty": 1, "missing": 3}


def test_summarize_groups_by_mode_and_flags_regressions():
    rows = [
        {"topic": "a", "read_mode": artifact.READ_LEGACY, "run_status": "success",
         "failed_nodes": [], "fallback_nodes": [], "parity_ok": True, "parity_reasons": [],
         "artifact_statuses": {"complete": 7}, "reads_total": 20, "reads_from_artifact": 0,
         "fallbacks": 0, "fallback_reasons": {}, "shadow_fallbacks": 1,
         "shadow_reasons": {"empty": 1}, "sections_complete": True, "empty_sections": 0,
         "unique_source_urls": 9, "fact_support_rate": 0.9, "evidence_link_rate": 1.0,
         "total_score": 80, "calls": 13, "fallback_calls": 0, "est_cost_usd": 0.01,
         "wall_time_ms": 100.0},
        {"topic": "a", "read_mode": artifact.READ_PREFER_ARTIFACT, "run_status": "degraded",
         "failed_nodes": ["swot"], "fallback_nodes": [], "parity_ok": False,
         "parity_reasons": ["content_mismatch"], "artifact_statuses": {"complete": 6, "failed": 1},
         "reads_total": 20, "reads_from_artifact": 19, "fallbacks": 1,
         "fallback_reasons": {"empty": 1}, "shadow_fallbacks": 0, "shadow_reasons": {},
         "sections_complete": False, "empty_sections": 2, "unique_source_urls": 8,
         "fact_support_rate": 0.8, "evidence_link_rate": 0.9, "total_score": 70,
         "calls": 13, "fallback_calls": 2, "est_cost_usd": 0.02, "wall_time_ms": 120.0},
    ]
    rep = arc.summarize(rows, {"a": {"ok": True, "checked": 8, "mismatched": [], "empty": []}})
    legacy = rep["by_mode"][artifact.READ_LEGACY]
    prefer = rep["by_mode"][artifact.READ_PREFER_ARTIFACT]
    assert legacy["parity_ok_all"] is True and legacy["shadow_reasons"] == {"empty": 1}
    # 회귀는 감추지 않고 그대로 드러나야 한다.
    assert prefer["parity_ok_all"] is False and prefer["parity_reasons"] == ["content_mismatch"]
    assert prefer["failed_nodes_total"] == 1 and prefer["sections_complete_all"] is False
    assert prefer["fallback_calls_total"] == 2
    assert rep["total_cost_usd"] == 0.03 and rep["runs"] == 2
    assert rep["by_mode"][artifact.READ_ARTIFACT_ONLY] == {}      # 안 돈 모드는 빈 칸


def test_summary_lines_surface_prompt_parity_failures():
    rep = arc.summarize([], {"a": {"ok": False, "checked": 8,
                                   "mismatched": [{"consumer": "swot"}], "empty": []}})
    joined = "\n".join(arc.summary_lines(rep))
    assert "불일치 1건" in joined and "\"a\"" in joined
