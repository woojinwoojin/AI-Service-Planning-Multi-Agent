"""각 Agent의 스키마 검증(_validate) 순수 로직 테스트 (LLM 호출 없음)."""
from __future__ import annotations

import json

from app.agents import research, pestel, reviewer
from app.services import compare


# ── Research ────────────────────────────────────────────────
def test_research_validate_fills_and_no_dummy_leak():
    fb = research._dummy({"project_name": "t"})
    partial = {"market_overview": ["틀린타입"], "industry_trends": ["A", "B"], "competitors": ["X"]}
    out = research._validate(partial, fb)
    assert set(out) == set(fb)                       # 7키 유지
    assert out["industry_trends"] == ["A", "B"]      # 실제값 유지
    assert out["market_overview"] == ""              # 타입오류 → 중립 빈값
    assert out["customer_needs"] == []               # 누락 → 중립 빈값
    assert "[더미]" not in json.dumps(out, ensure_ascii=False)


def test_research_validate_non_dict_falls_back():
    fb = research._dummy({"project_name": "t"})
    assert research._validate("깨짐", fb) == fb


# ── PESTEL ──────────────────────────────────────────────────
def test_pestel_validate_enforces_6x4():
    fb = pestel._dummy({})
    partial = {"Political": {"content": "정책", "threat": 123}, "Legal": "타입오류"}
    out = pestel._validate(partial, fb)
    assert list(out) == pestel._FACTORS
    for f in pestel._FACTORS:
        assert set(out[f]) == set(pestel._SUBKEYS)
    assert out["Political"]["content"] == "정책"
    assert out["Political"]["threat"] == ""          # 타입오류 → 빈값
    assert out["Social"]["content"] == ""            # 누락 요인 → 빈값
    assert "[더미]" not in json.dumps(out, ensure_ascii=False)


def test_pestel_validate_non_dict_falls_back():
    fb = pestel._dummy({})
    assert pestel._validate("깨짐", fb) == fb


# ── Reviewer ────────────────────────────────────────────────
def test_reviewer_clamps_and_recomputes_total():
    fb = reviewer._dummy("draft")
    raw = {
        "total_score": 999,  # 세부합과 불일치 → 무시되어야 함
        "section_scores": {"problem_clarity": 25, "market_validity": -3,
                           "solution_specificity": "14", "differentiation": None, "feasibility": 17},
        "strengths": ["좋음", "", 42], "weaknesses": "리스트아님",
        "revision_instructions": ["시장 섹션 보강"],
    }
    out = reviewer._validate(raw, fb)
    assert out["section_scores"] == {"problem_clarity": 20, "market_validity": 0,
                                     "solution_specificity": 14, "differentiation": 0, "feasibility": 17}
    assert out["total_score"] == 51                  # 세부합으로 재계산
    assert out["total_score"] == sum(out["section_scores"].values())
    assert out["strengths"] == ["좋음"]              # 빈/비문자열 제거
    assert out["weaknesses"] == []                   # 리스트 아님 → []
    assert out["revision_instructions"] == ["시장 섹션 보강"]


def test_reviewer_non_dict_falls_back():
    fb = reviewer._dummy("draft")
    assert reviewer._validate("깨짐", fb) == fb


# ── Compare judge ───────────────────────────────────────────
def test_compare_judge_clamp_and_total(monkeypatch):
    monkeypatch.setattr(compare.llm, "complete_json",
                        lambda *a, **k: {"comment": "c", "scores": {
                            "problem_clarity": 25, "market_specificity": -2,
                            "pestel_completeness": "18", "consistency": None, "evidence": 12}})
    j = compare.judge("plan")
    assert j["scores"] == {"problem_clarity": 20, "market_specificity": 0,
                           "pestel_completeness": 18, "consistency": 0, "evidence": 12}
    assert j["total"] == 50
    assert j["comment"] == "c"


def test_compare_aggregate_averages():
    fake = [
        {"single": {"judge": {"scores": {k: 10 for k in compare.CRITERIA}, "total": 50}},
         "multi": {"judge": {"scores": {k: 16 for k in compare.CRITERIA}, "total": 80}}},
        {"single": {"judge": {"scores": {k: 12 for k in compare.CRITERIA}, "total": 60}},
         "multi": {"judge": {"scores": {k: 18 for k in compare.CRITERIA}, "total": 90}}},
    ]
    t = compare.aggregate(fake)
    assert t["total"]["single"] == 55.0 and t["total"]["multi"] == 85.0
    assert t["problem_clarity"]["single"] == 11.0 and t["problem_clarity"]["multi"] == 17.0
