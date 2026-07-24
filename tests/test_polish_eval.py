"""PR-8 Polish 품질 검증 — 블라인드 매핑·집계·실행 배선 테스트 (LLM 호출 없음).

evaluate() 는 실제 LLM 을 호출하지만, 블라인드 A/B 매핑·리포트 수학·"Polish 실행 주제 제외"
배선은 mock 으로 결정론적으로 검증한다(무료).
"""
from __future__ import annotations

from app.services import polish_eval


def test_judge_pair_blind_mapping_no_swap(monkeypatch):
    """swap=False → 문서A=생략본, 문서B=편집본. 심판 'B' 는 polished 로 매핑."""
    monkeypatch.setattr(polish_eval.llm, "complete_json", lambda *a, **k: {"winner": "B", "reason": "흐름"})
    assert polish_eval.judge_pair("생략본", "편집본", "m", swap=False)["winner"] == "polished"
    monkeypatch.setattr(polish_eval.llm, "complete_json", lambda *a, **k: {"winner": "A"})
    assert polish_eval.judge_pair("생략본", "편집본", "m", swap=False)["winner"] == "skipped"


def test_judge_pair_blind_mapping_swap(monkeypatch):
    """swap=True → 문서A=편집본, 문서B=생략본(위치 교차). 심판 'A' 는 polished 로 매핑."""
    monkeypatch.setattr(polish_eval.llm, "complete_json", lambda *a, **k: {"winner": "A"})
    assert polish_eval.judge_pair("생략본", "편집본", "m", swap=True)["winner"] == "polished"
    monkeypatch.setattr(polish_eval.llm, "complete_json", lambda *a, **k: {"winner": "B"})
    assert polish_eval.judge_pair("생략본", "편집본", "m", swap=True)["winner"] == "skipped"


def test_judge_pair_invalid_winner_is_tie(monkeypatch):
    monkeypatch.setattr(polish_eval.llm, "complete_json", lambda *a, **k: {"winner": "몰라"})
    assert polish_eval.judge_pair("s", "p", "m", swap=False)["winner"] == "tie"


def test_report_math():
    results = [
        {"topic": "a", "compared": True, "winner": "tie"},
        {"topic": "b", "compared": True, "winner": "skipped"},
        {"topic": "c", "compared": True, "winner": "polished"},
        {"topic": "d", "compared": False},                       # Polish 실행됨 → 비교 제외
    ]
    rep = polish_eval.report(results)
    assert rep["n_total"] == 4 and rep["n_compared"] == 3
    assert rep["polished_wins"] == "1/3"
    assert rep["skipped_wins"] == "1/3"
    assert rep["ties"] == "1/3"


def test_evaluate_excludes_runs_where_polish_applied(monkeypatch):
    """실제 실행에서 Polish 가 돈 주제는 compared=False(생략 비교 대상 아님)."""
    states = iter([
        {"final_draft": "생략본1", "polish_applied": False},   # 비교 대상
        {"final_draft": "편집됨2", "polish_applied": True},    # 제외
    ])
    monkeypatch.setattr(polish_eval, "run_workflow", lambda t: next(states))
    monkeypatch.setattr(polish_eval, "_force_polish", lambda state, model: "편집본1")
    monkeypatch.setattr(polish_eval, "judge_pair",
                        lambda skipped, polished, model, swap: {"winner": "tie", "reason": ""})
    results = polish_eval.evaluate([{"project_name": "T1"}, {"project_name": "T2"}], model="m")
    assert results[0]["compared"] is True and results[0]["winner"] == "tie"
    assert results[1]["compared"] is False
    rep = polish_eval.report(results)
    assert rep["n_compared"] == 1


def test_judge_prompt_focuses_on_reading_quality():
    from app.prompts import templates
    assert "일관성" in templates.POLISH_JUDGE
    assert "중복" in templates.POLISH_JUDGE
    assert "tie" in templates.POLISH_JUDGE
