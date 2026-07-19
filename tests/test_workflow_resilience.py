"""전체 연결 안정성(_safe, fault-injection)과 mode_label 정직성 테스트 (LLM 호출 없음)."""
from __future__ import annotations

import json

from app.graph import workflow
from app.graph.workflow import run_workflow


def test_safe_wrapper_logs_and_continues():
    def boom(state):
        raise ValueError("의도된 오류")
    out = workflow._safe("research", boom)({"logs": []})
    assert out["logs"][-1].startswith("[research] 오류로 건너뜀")


def test_mode_label_honest(monkeypatch):
    from app.services import llm
    monkeypatch.setattr(llm, "is_dummy", lambda: True)
    assert llm.mode_label({}, "") == "더미"
    monkeypatch.setattr(llm, "is_dummy", lambda: False)
    assert llm.mode_label({"fallback": True, "reason": "호출오류"}, "gpt-4o-mini").startswith("fallback")
    assert "실제 LLM" in llm.mode_label({}, "gpt-4o-mini")


def test_pipeline_completes_despite_llm_failure(force_real_llm):
    """LLM 호출이 전부 실패해도 6개 산출물이 모두 생성되고 예외가 나지 않는다."""
    state = run_workflow({
        "project_name": "장애주입", "description": "D",
        "target_user": "U", "problem": "P", "model": "gpt-4o-mini",
    })
    for key in ["structured_input", "research_result", "pestel_result",
                "draft", "review_result", "initial_review_result",
                "final_draft", "final_review_result"]:
        assert state.get(key), f"누락 산출물: {key}"
    # fallback이 로그에 정직하게 표면화되는지
    joined = json.dumps(state["logs"], ensure_ascii=False)
    assert "fallback" in joined
    # item 9: 실행 품질이 상태로 표면화(전부 fallback → degraded)
    assert state.get("run_status") == "degraded"
    assert state.get("fallback_nodes")


def test_assess_quality_classifies(monkeypatch):
    """item 9: 로그로 run_status/failed/fallback을 판정한다."""
    from app.services import llm
    monkeypatch.setattr(llm, "is_dummy", lambda: False)
    failed = workflow._assess_quality({"logs": ["[pestel] 오류로 건너뜀 (ValueError: x)"]})
    assert failed["run_status"] == "failed" and failed["failed_nodes"] == ["pestel"]
    fb = workflow._assess_quality({"logs": ["[research] 시장조사 완료 (fallback·호출오류, 검색 결과 없음)"]})
    assert fb["run_status"] == "degraded" and "research" in fb["fallback_nodes"]
    ok = workflow._assess_quality({"logs": ["[research] 시장조사 완료 (실제 LLM·gpt-4o-mini, 웹검색 3건)"]})
    assert ok["run_status"] == "success" and not ok["failed_nodes"]


def test_assess_quality_dummy_is_degraded(monkeypatch):
    from app.services import llm
    monkeypatch.setattr(llm, "is_dummy", lambda: True)
    out = workflow._assess_quality({"logs": ["[research] 시장조사 완료 (더미, 검색 비활성)"]})
    assert out["run_status"] == "degraded"
