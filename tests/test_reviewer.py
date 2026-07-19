"""Reviewer 초안 평가 + 최종본 재평가(final_reviewer) 테스트 (LLM 호출 없음).

item 3: 표시 점수가 '실제 최종 문서' 점수와 일치하도록 final_reviewer가
재작성·편집 후 최종본을 다시 채점하는지 검증한다.
"""
from __future__ import annotations

from app.agents import reviewer


def _scores(v: int) -> dict:
    return {
        "section_scores": {k: v for k in reviewer.SECTION_KEYS},
        "revision_instructions": ["개선하라"],
        "strengths": [], "weaknesses": [], "unsupported_claims": [],
    }


def test_reviewer_writes_review_and_initial(monkeypatch):
    monkeypatch.setattr(reviewer.llm, "is_dummy", lambda: False)
    monkeypatch.setattr(reviewer.llm, "complete_json", lambda *a, **k: _scores(14))  # 14*5=70
    out = reviewer.reviewer({"draft": "초안 본문", "logs": []})
    assert out["review_result"]["total_score"] == 70
    assert out["initial_review_result"] == out["review_result"]       # 초안 평가를 기록으로도 저장
    assert out["logs"][-1].startswith("[reviewer] 초안 평가 완료")


def test_final_reviewer_rescoring_and_delta(monkeypatch):
    monkeypatch.setattr(reviewer.llm, "is_dummy", lambda: False)
    monkeypatch.setattr(reviewer.llm, "complete_json", lambda *a, **k: _scores(18))  # 18*5=90
    out = reviewer.final_reviewer({
        "final_draft": "편집까지 끝난 최종본",
        "initial_review_result": {"total_score": 70},
        "logs": [],
    })
    assert out["final_review_result"]["total_score"] == 90            # 최종본 재평가 점수
    assert "review_result" not in out                                 # 재작성 분기 로직은 건드리지 않음
    assert "Δ+20 vs 초안" in out["logs"][-1]                          # 초안 대비 변화 로그


def test_final_reviewer_scores_final_draft_not_initial_draft(monkeypatch):
    seen: dict = {}
    def fake(system, user, **k):
        seen["user"] = user
        return _scores(15)
    monkeypatch.setattr(reviewer.llm, "is_dummy", lambda: False)
    monkeypatch.setattr(reviewer.llm, "complete_json", fake)
    reviewer.final_reviewer({"draft": "초안내용", "final_draft": "최종본문서", "logs": []})
    assert "최종본문서" in seen["user"] and "초안내용" not in seen["user"]
