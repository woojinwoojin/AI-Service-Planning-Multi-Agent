"""Agent별 Dual Write 1묶음: Research·Competitor (로드맵 2-2 PR 4) — 실 LLM·검색 호출 없음.

Dual Write 는 Agent 가 평면 결과와 Artifact 를 **함께** 반환하는 단계다. 소비자는 아직
평면 키를 읽으므로 동작 변화가 없어야 하고, 대신 다음이 지켜져야 한다:
  1) 옮긴 Agent 의 Artifact 는 source=agent, 나머지 5개는 legacy_derived
  2) 같은 Artifact 가 두 번 방출돼도(research → research_gap) 중복되지 않고 마지막이 남는다
  3) evidence_ids·status 는 Agent 가 알 수 없으므로 finalize 에서 확정된다
  4) 병렬에서도 유실·중복이 없다
"""
from __future__ import annotations

import pytest

from app.agents import competitor, research
from app.graph import workflow
from app.graph.workflow import run_workflow
from app.schemas import artifact
from app.schemas.state import ProjectState
from app.services import llm, search

DUAL_WRITTEN = {"research_analysis", "competitor_analysis"}


def _dummy(monkeypatch):
    monkeypatch.setattr(llm, "is_dummy", lambda: True)


def _by_type(state) -> dict:
    return {a["artifact_type"]: a for a in state["artifacts"]}


# ---- 1) 누가 썼는지 ----

@pytest.mark.parametrize("mode", ["serial", "parallel"])
def test_dual_written_agents_are_marked_agent_source(monkeypatch, mode):
    _dummy(monkeypatch)
    state = run_workflow({"project_name": "출처", "problem": "P"}, workflow_mode=mode)
    arts = _by_type(state)
    assert len(state["artifacts"]) == 7
    for t, a in arts.items():
        expected = artifact.SOURCE_AGENT if t in DUAL_WRITTEN else artifact.SOURCE_LEGACY
        assert a["metadata"]["source"] == expected, t


def test_dual_write_content_equals_legacy_key(monkeypatch):
    """Dual Write 의 성공 기준 — Agent 가 쓴 봉투 내용이 평면 결과와 같아야 한다."""
    _dummy(monkeypatch)
    state = run_workflow({"project_name": "일치", "problem": "P"})
    arts = _by_type(state)
    assert arts["research_analysis"]["content"] == state["research_result"]
    assert arts["competitor_analysis"]["content"] == state["competitor_result"]
    assert state["artifact_parity"]["ok"]


def test_agents_emit_artifact_in_node_return(monkeypatch):
    """노드 반환값 자체에 artifacts 가 들어있는지(그래프 배선과 무관하게)."""
    _dummy(monkeypatch)
    st: ProjectState = {"structured_input": {"project_name": "P", "keywords": ["k"]}}
    out = research.research(st)
    assert [a["artifact_id"] for a in out["artifacts"]] == ["artifact-research"]
    assert out["artifacts"][0]["content"] == out["research_result"]

    st2: ProjectState = {"structured_input": {"project_name": "P"}, "research_result": {}}
    out2 = competitor.competitor(st2)
    assert [a["artifact_id"] for a in out2["artifacts"]] == ["artifact-competitor"]
    assert out2["artifacts"][0]["content"] == out2["competitor_result"]


# ---- 2) reducer: 중복 없이 마지막이 남는가 ----

def test_merge_artifacts_last_wins_without_duplicates():
    a1 = artifact.make_artifact("research_analysis", {"v": 1})
    a2 = artifact.make_artifact("research_analysis", {"v": 2})
    out = artifact.merge_artifacts([a1], [a2])
    assert len(out) == 1 and out[0]["content"] == {"v": 2}


def test_merge_artifacts_order_is_deterministic():
    """병렬 도착 순서가 달라도 결과 순서가 같아야 한다."""
    comp = artifact.make_artifact("competitor_analysis", {})
    res = artifact.make_artifact("research_analysis", {})
    assert ([a["artifact_id"] for a in artifact.merge_artifacts([comp], [res])]
            == [a["artifact_id"] for a in artifact.merge_artifacts([res], [comp])]
            == ["artifact-research", "artifact-competitor"])


def test_merge_artifacts_keeps_unknown_ids_at_end():
    ghost = {"artifact_id": "artifact-ghost", "artifact_type": "ghost"}
    res = artifact.make_artifact("research_analysis", {})
    out = artifact.merge_artifacts([ghost], [res])
    assert [a["artifact_id"] for a in out] == ["artifact-research", "artifact-ghost"]


def test_merge_artifacts_handles_empty_and_junk():
    res = artifact.make_artifact("research_analysis", {})
    assert artifact.merge_artifacts([], []) == []
    assert artifact.merge_artifacts(None, [res]) == [res]
    assert artifact.merge_artifacts(["문자열"], [res]) == [res]   # dict 아닌 항목 무시


def test_state_reducer_is_merge_not_add():
    """operator.add 였다면 research → research_gap 재방출 시 중복된다."""
    from typing import get_args, get_type_hints

    # state.py 는 `from __future__ import annotations` 라 __annotations__ 가 문자열이다.
    hints = get_type_hints(ProjectState, include_extras=True)
    assert artifact.merge_artifacts in get_args(hints["artifacts"])


def test_apply_node_update_merges_artifacts_by_id(monkeypatch):
    """그래프 밖(/revise 경로)에서도 concat 이 아니라 id 병합이어야 한다."""
    _dummy(monkeypatch)
    state: ProjectState = {"artifacts": [artifact.make_artifact("research_analysis", {"v": 1})]}
    workflow.apply_node_update(
        state, {"artifacts": [artifact.make_artifact("research_analysis", {"v": 2})]})
    assert len(state["artifacts"]) == 1 and state["artifacts"][0]["content"] == {"v": 2}


# ---- 3) research_gap 재방출 (2-5 경로) ----

def test_research_gap_reemits_updated_artifact(monkeypatch):
    """research_gap 이 research_result 를 갱신하면 Artifact 도 갱신본이어야 한다."""
    monkeypatch.setattr(llm, "is_dummy", lambda: False)
    monkeypatch.setattr(search, "search_enabled", lambda: True)
    monkeypatch.setattr(search, "web_search",
                        lambda q, **k: [{"url": "https://new", "title": "새", "content": "요약"}])
    monkeypatch.setattr(search, "build_source_objects",
                        lambda hits: [{"url": "https://new", "title": "새", "snippet": "요약"}])
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: {"opportunities": ["새 기회"]})
    monkeypatch.setattr(llm, "mode_label", lambda *a, **k: "테스트")

    state: ProjectState = {
        "evidence_gaps": [{"topic": "시장 규모", "query": "시장 규모"}],
        "research_result": {"opportunities": [], "sources": [], "source_objects": []},
    }
    out = research.research_gap(state)
    assert out["dynamic_research"]["applied"] is True
    emitted = out["artifacts"][0]
    assert emitted["artifact_id"] == "artifact-research"
    assert emitted["content"] == out["research_result"]      # 보강본과 동일
    assert emitted["content"]["opportunities"] == ["새 기회"]


def test_research_then_gap_leaves_single_research_artifact():
    """두 번 방출돼도 최종은 1개이고 마지막(보강본)이 남는다."""
    before = artifact.make_artifact("research_analysis", {"opportunities": []})
    after = artifact.make_artifact("research_analysis", {"opportunities": ["새 기회"]})
    merged = artifact.merge_artifacts([before], [after])
    assert len(merged) == 1
    assert merged[0]["content"]["opportunities"] == ["새 기회"]


# ---- 4) finalize 가 evidence_ids·status 를 확정하는가 ----

def test_make_artifact_leaves_evidence_and_status_for_finalize():
    a = artifact.make_artifact("research_analysis", {"x": 1})
    assert a["evidence_ids"] == []                  # normalize 전이라 알 수 없다
    assert a["status"] == artifact.STATUS_COMPLETE  # 실행 결말은 finalize 가 정한다
    assert a["metadata"]["source"] == artifact.SOURCE_AGENT


def test_reconcile_fills_evidence_ids_for_agent_written():
    """Agent 가 빈 evidence_ids 로 써도 finalize 에서 레지스트리 기준으로 채워져야 한다.

    이 재확정이 없으면 Dual Write 로 옮긴 Agent 만 근거 연결이 비는 회귀가 생긴다.
    """
    state = {
        "research_result": {"m": 1},
        "artifacts": [artifact.make_artifact("research_analysis", {"m": 1})],
        "evidence_registry": [
            {"evidence_id": "ev1", "url": "https://a", "source_agents": ["research"]},
            {"evidence_id": "ev2", "url": "https://b", "source_agents": ["research_gap"]},
        ],
    }
    out = {a["artifact_type"]: a for a in artifact.reconcile(state)}
    assert out["research_analysis"]["evidence_ids"] == ["ev1", "ev2"]
    assert out["research_analysis"]["metadata"]["source"] == artifact.SOURCE_AGENT


def test_reconcile_fixes_status_for_agent_written():
    """Agent 는 자기가 fallback 이었는지 모른다 — finalize 가 로그 판정으로 고쳐야 한다."""
    state = {
        "research_result": {"m": 1},
        "artifacts": [artifact.make_artifact("research_analysis", {"m": 1})],
        "fallback_nodes": ["research"],
    }
    out = {a["artifact_type"]: a for a in artifact.reconcile(state)}
    assert out["research_analysis"]["status"] == artifact.STATUS_FALLBACK


def test_reconcile_leaves_unknown_artifacts_untouched():
    ghost = {"artifact_id": "artifact-ghost", "artifact_type": "ghost", "content": {"x": 1}}
    out = artifact.reconcile({"artifacts": [ghost]})
    assert any(a is ghost for a in out)       # 손대지 않는다(정합성 검사가 잡는다)


def test_reconcile_safe_on_invalid_state():
    assert artifact.reconcile(None) == []


# ---- 5) 회귀: 실행 결과·API 무변경 ----

@pytest.mark.parametrize("mode", ["serial", "parallel"])
def test_run_still_completes_normally(monkeypatch, mode):
    _dummy(monkeypatch)
    state = run_workflow({"project_name": "회귀", "problem": "P"}, workflow_mode=mode)
    assert state["final_draft"] and state["run_status"] in ("success", "degraded")
    assert state["research_result"] and state["competitor_result"]   # 평면 키 그대로


def test_api_response_still_has_no_artifacts_field():
    """소비자 전환은 PR 5 — 지금은 API 응답이 바뀌면 안 된다."""
    from app.schemas.state import RunResult

    assert "artifacts" not in RunResult.model_fields
    assert "artifact_parity" not in RunResult.model_fields
