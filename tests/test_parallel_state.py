"""병렬 State 안전성 (PR-2): logs reducer + '자기 로그만 반환' 계약.

병렬 노드가 동시에 logs 를 갱신해도 유실·중복·덮어쓰기가 없어야 한다.
LLM 호출 없이(더미/함수 mock) 검증한다.
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.agents import research
from app.graph.workflow import apply_node_update, run_workflow, run_workflow_stream
from app.schemas.state import ProjectState


def test_agent_returns_only_its_own_log(monkeypatch):
    """각 Agent 는 기존 로그 전체가 아니라 '자기 새 로그만' 반환한다(reducer 전제)."""
    monkeypatch.setattr(research.llm, "is_dummy", lambda: True)
    out = research.research({"structured_input": {"project_name": "P"}, "logs": ["기존-로그"]})
    assert "기존-로그" not in out["logs"]                     # 이전 로그를 다시 싣지 않음
    assert len(out["logs"]) == 1 and out["logs"][0].startswith("[research]")


def test_logs_reducer_merges_parallel_branches():
    """ProjectState.logs 는 reducer 필드 → 병렬 분기의 로그가 모두 이어붙는다(덮어쓰기 없음)."""
    g = StateGraph(ProjectState)
    g.add_node("a", lambda s: {"logs": ["A"]})
    g.add_node("b", lambda s: {"logs": ["B"]})
    g.add_node("c", lambda s: {"logs": ["C"]})
    g.add_node("join", lambda s: {"logs": ["J"]})
    for n in ("a", "b", "c"):
        g.add_edge(START, n)
        g.add_edge(n, "join")
    g.add_edge("join", END)
    out = g.compile().invoke({"logs": []})
    assert set(out["logs"]) == {"A", "B", "C", "J"}          # 병렬 3분기 모두 생존
    assert out["logs"][-1] == "J"                             # join 은 분기 이후


def test_apply_node_update_accumulates_logs_out_of_graph():
    """그래프 밖(/revise·rerun_finalizers)에서는 apply_node_update 가 logs 를 누적한다."""
    state: ProjectState = {"logs": ["x"]}
    apply_node_update(state, {"logs": ["y"], "draft": "D"})
    apply_node_update(state, {"logs": ["z"]})
    assert state["logs"] == ["x", "y", "z"]                   # 누적(덮어쓰기 아님)
    assert state["draft"] == "D"                              # 다른 필드는 정상 갱신
    # logs 없는 업데이트는 기존 로그 보존
    apply_node_update(state, {"final_draft": "F"})
    assert state["logs"] == ["x", "y", "z"] and state["final_draft"] == "F"


def _markers(logs: list[str]) -> list[str]:
    return [ln.split("]")[0] + "]" for ln in logs if ln.startswith("[")]


def test_full_run_logs_complete_and_no_duplication(monkeypatch):
    """더미 전체 실행: 모든 노드 로그가 유실·중복 없이 누적된다(reducer 이중적용 회귀 방지)."""
    from app.services import llm
    monkeypatch.setattr(llm, "is_dummy", lambda: True)        # 실제 LLM 호출 없이(무료·빠름)
    state = run_workflow({"project_name": "로그", "problem": "P"})
    logs = state["logs"]
    assert len(logs) == len(set(logs))                        # 중복 없음(=reducer 이중적용 아님)
    joined = "\n".join(logs)
    for marker in ["[preprocess]", "[research]", "[draft_writer]", "[verify]"]:
        assert marker in joined                               # 앞·중간·끝 노드 로그 존재
    assert _markers(logs).count("[preprocess]") == 1          # 정확히 한 번


def test_stream_final_state_has_complete_logs(monkeypatch):
    """스트리밍 경로도 최종 state 로그가 전 노드를 포함한다(updates 모드 누적 병합)."""
    from app.services import llm
    monkeypatch.setattr(llm, "is_dummy", lambda: True)        # 실제 LLM 호출 없이(무료·빠름)
    done = None
    for ev in run_workflow_stream({"project_name": "스트림로그", "problem": "P"}):
        if ev["type"] == "done":
            done = ev["state"]
    assert done is not None
    joined = "\n".join(done["logs"])
    assert "[preprocess]" in joined and "[verify]" in joined
    assert len(done["logs"]) == len(set(done["logs"]))        # 중복 없음
