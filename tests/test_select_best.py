"""최고 버전 채택(로드맵 Phase 4) 테스트 — 재작성본이 나쁘면 초안 유지 (LLM 호출 없음)."""
from __future__ import annotations

from app.graph import workflow


def _state(strategy="section", initial=80, final=None, draft="초안본", revised="재작성본") -> dict:
    st = {
        "draft": draft,
        "final_draft": revised,
        "revision_strategy": strategy,
        "initial_review_result": {"total_score": initial, "issues": []},
    }
    if final is not None:
        st["final_review_result"] = {"total_score": final, "issues": []}
    return st


def test_keeps_revision_when_not_worse():
    out = workflow._select_best(_state(initial=80, final=85))
    assert out["best_version"] == "revised"
    assert out["reverted_from_revision"] is False
    assert "final_draft" not in out                 # 재작성본 유지 → 변경 없음


def test_reverts_to_draft_when_revision_worse():
    out = workflow._select_best(_state(initial=88, final=72, draft="초안본"))
    assert out["reverted_from_revision"] is True
    assert out["best_version"] == "draft"
    assert out["final_draft"] == "초안본"            # 초안으로 되돌림
    assert out["final_review_result"]["total_score"] == 88   # 표시 점수도 초안 점수로 정정


def test_no_comparison_when_no_revision():
    out = workflow._select_best(_state(strategy="none", initial=80, final=80))
    assert out["best_version"] == "draft"
    assert out["reverted_from_revision"] is False
    assert "final_draft" not in out


def test_equal_scores_keep_revision():
    """동점이면 재작성본 유지(되돌리지 않음)."""
    out = workflow._select_best(_state(initial=80, final=80))
    assert out["reverted_from_revision"] is False


def test_missing_scores_are_safe():
    """점수가 없으면(평가 실패) 되돌리지 않는다(안전)."""
    out = workflow._select_best(_state(initial=80, final=None))
    assert out["reverted_from_revision"] is False
    assert out["best_version"] == "revised"
