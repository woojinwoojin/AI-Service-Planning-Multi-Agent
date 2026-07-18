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
                "draft", "review_result", "final_draft"]:
        assert state.get(key), f"누락 산출물: {key}"
    # fallback이 로그에 정직하게 표면화되는지
    joined = json.dumps(state["logs"], ensure_ascii=False)
    assert "fallback" in joined
