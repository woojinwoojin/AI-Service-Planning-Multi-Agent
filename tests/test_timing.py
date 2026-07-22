"""단계별 실행시간 계측 (PR-6) 테스트 — 측정만(최적화 아님). 실제 API 호출 없음.

병목 '위치'를 신뢰성 있게 측정하는지 검증한다: 노드별 event, 병렬 겹침(node duration 합 >
analysis_block wall), 단계 합이 실행 구간의 대부분을 설명(coverage), 스키마 일관성.
"""
from __future__ import annotations

import time

from app.graph.workflow import run_workflow
from app.services import timing

_CORE_NODES = {"preprocess", "research", "competitor", "customer", "pestel", "swot",
               "business_model", "risk", "draft", "reviewer", "polish",
               "final_reviewer", "verify"}


def _inject_latency(monkeypatch, seconds=0.3, with_search=False):
    """실제 LLM 모드 + 고정 지연 주입(무료). 검색은 네트워크 없이 빈 결과."""
    from app.services import llm, search
    monkeypatch.setattr(llm, "is_dummy", lambda: False)
    monkeypatch.setattr(llm, "_get_model", lambda model="": object())
    if not with_search:
        monkeypatch.setattr(search, "web_search", lambda q, **k: [])

    class _Resp:
        content = "{}"
        usage_metadata = {"input_tokens": 10, "output_tokens": 5}

    def _slow(chat, s, u, attempts=2):
        time.sleep(seconds)
        return _Resp()

    monkeypatch.setattr(llm, "_invoke_with_retry", _slow)


def test_now_ms_without_origin_is_zero():
    """계측 원점 미설정이어도 안전(0 반환) — 계측이 본 실행을 깨지 않는다."""
    timing._origin.set(None)
    assert timing.now_ms() == 0.0


def test_summarize_empty_events():
    out = timing.summarize([], "serial")
    assert out["stages"] == {} and out["critical_path"] == [] and out["coverage"] is None


def test_every_core_node_has_one_timing_event(monkeypatch):
    _inject_latency(monkeypatch, 0.2)
    st = run_workflow({"project_name": "t", "problem": "P"}, workflow_mode="serial")
    nodes = [e["node"] for e in st["timing_events"]]
    assert _CORE_NODES.issubset(set(nodes))                    # 모든 핵심 노드가 계측됨
    for e in st["timing_events"]:
        assert e["duration_ms"] >= 0                            # 음수 없음
        assert e["ended_at_ms"] >= e["started_at_ms"]
        assert nodes.count(e["node"]) == 1                     # 노드당 event 1개


def test_parallel_analysis_block_shows_overlap(monkeypatch):
    """병렬에서는 분석 node duration 의 '합' > analysis_block wall (실제 겹침 측정)."""
    _inject_latency(monkeypatch, 0.3)
    st = run_workflow({"project_name": "t", "problem": "P"}, workflow_mode="parallel")
    t = st["timing"]
    node_sum = sum(t["nodes"].values())
    block = t["stages"]["analysis_block"]
    assert node_sum > block * 1.3                              # 합이 wall 보다 확연히 큼 = 겹침


def test_serial_analysis_block_no_overlap(monkeypatch):
    """직렬에서는 분석 node duration 합 ≈ analysis_block wall (겹침 없음)."""
    _inject_latency(monkeypatch, 0.3)
    st = run_workflow({"project_name": "t", "problem": "P"}, workflow_mode="serial")
    t = st["timing"]
    node_sum = sum(t["nodes"].values())
    block = t["stages"]["analysis_block"]
    assert block >= node_sum * 0.9                             # 합과 wall 이 비슷(순차)


def test_stage_coverage_explains_active_window(monkeypatch):
    """단계 합이 노드 실행 구간(span)의 대부분(≥95%)을 설명한다 — 성공 기준."""
    _inject_latency(monkeypatch, 0.5)
    for mode in ("serial", "parallel"):
        t = run_workflow({"project_name": "t", "problem": "P"}, workflow_mode=mode)["timing"]
        assert t["coverage_active"] is not None and t["coverage_active"] >= 0.95
        assert t["stage_sum_ms"] <= t["span_ms"] + 1           # 단계 합은 span 을 넘지 않음


def test_serial_and_parallel_same_timing_schema(monkeypatch):
    _inject_latency(monkeypatch, 0.2)
    s = run_workflow({"project_name": "t", "problem": "P"}, workflow_mode="serial")["timing"]
    p = run_workflow({"project_name": "t", "problem": "P"}, workflow_mode="parallel")["timing"]
    assert set(s.keys()) == set(p.keys())                     # 동일 스키마
    assert s["critical_path"][0] == "preprocess" and s["critical_path"][-1] == "verify"


def test_timing_survives_in_run_and_saved_state(monkeypatch, tmp_path):
    """timing 이 /run 응답과 저장 State(이력)에 유지된다."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.services import llm, store
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "p.db")
    monkeypatch.setattr(llm, "is_dummy", lambda: True)        # 라우트 경로는 더미(무료)
    c = TestClient(app)
    d = c.post("/run", json={"project_name": "타이밍", "problem": "P"}).json()
    assert "timing" in d and "stages" in d["timing"]          # /run 응답에 포함
    saved = c.get(f"/projects/{d['project_id']}").json()["state"]
    assert "timing" in saved and "stages" in saved["timing"]  # 이력에 저장됨
