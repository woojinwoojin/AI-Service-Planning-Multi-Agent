"""평가 파이프라인 테스트 (Phase 1). 실제 LLM 미호출 — 더미/몽키패치로 결정론적.

검증 축:
- 루브릭 ↔ 판정 프롬프트 키 일치(불일치 시 채점 누락)
- 채점 결과 스키마·클램프·100환산·심판 변동성(total_stdev) 계산
- 구조 검사 통합 + 비용/서명(rubric/prompt 버전) 기록
- 사람 보정 채점 경로
"""
from __future__ import annotations

import pytest

from app.prompts.templates import EVAL_JUDGE
from app.services import eval_set, evaluation


@pytest.fixture(autouse=True)
def _force_dummy(monkeypatch):
    """실제 LLM 미호출 보장(conftest 원칙): 이 모듈은 항상 더미 모드로 돈다.

    로컬 .env에 실제 키가 있어도 USE_DUMMY=1이 is_dummy()보다 우선한다.
    """
    monkeypatch.setenv("USE_DUMMY", "1")


def test_rubric_keys_present_in_prompt():
    """8개 기준 key가 모두 EVAL_JUDGE 프롬프트에 있어야 채점이 누락되지 않는다."""
    assert len(eval_set.RUBRIC) == 8
    for key in eval_set.RUBRIC:
        assert key in EVAL_JUDGE


def test_get_topics_limit_and_ids():
    assert len(eval_set.get_topics()) == len(eval_set.TOPICS)
    assert len(eval_set.get_topics(3)) == 3
    ids = [t["id"] for t in eval_set.TOPICS]
    assert len(ids) == len(set(ids))          # id 고유
    assert all(t.get("id") for t in eval_set.TOPICS)


def test_score_plan_dummy_schema():
    """더미 모드: fallback(각 10점) → 스키마·100환산·표준편차 필드가 채워진다."""
    r = evaluation.score_plan("# 더미 기획서", samples=3)
    assert set(r["scores"]) == set(eval_set.RUBRIC)
    assert set(r["scores_stdev"]) == set(eval_set.RUBRIC)
    assert r["total"] == 8 * 10                # 8기준 × 10
    assert r["total_100"] == 50.0             # 80/160*100
    assert r["total_stdev"] == 0.0            # 더미는 매번 동일 → 변동 0
    assert r["samples"] == 3


def test_score_plan_clamps_and_measures_variability(monkeypatch):
    """심판이 회차마다 다른(그리고 범위를 벗어나는) 점수를 줘도 클램프 + 변동성 측정."""
    from app.services import llm

    seq = iter([
        {"scores": {k: 25 for k in eval_set.RUBRIC}, "comment": "높음"},   # 25→20 클램프
        {"scores": {k: 5 for k in eval_set.RUBRIC}, "comment": "낮음"},
        {"scores": {k: -3 for k in eval_set.RUBRIC}, "comment": "음수"},   # -3→0 클램프
    ])
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: next(seq))
    r = evaluation.score_plan("문서", samples=3)
    # 기준별 평균 = (20+5+0)/3 ≈ 8.3
    assert all(abs(v - 8.3) < 0.1 for v in r["scores"].values())
    assert r["total_stdev"] > 0               # 회차 간 변동 존재 → 심판 변동성 포착
    assert r["comment"] == "음수"             # 마지막 총평 유지


def test_evaluate_topic_dummy_integrates_structure_and_cost():
    topic = eval_set.get_topics(1)[0]
    r = evaluation.evaluate_topic(topic, samples=1)
    assert r["id"] == topic["id"]
    assert "sections_present" in r["structural"]   # 구조 검사 통합
    assert set(r["rubric"]["scores"]) == set(eval_set.RUBRIC)
    assert "wall_time_ms" in r["usage"] and "est_cost_usd" in r["usage"]
    assert r["run_status"] in ("success", "degraded", "failed")


def test_experiment_signature_pins_versions():
    sig = evaluation.experiment_signature(eval_set.get_topics(2), samples=3, model="")
    assert sig["rubric_version"] == eval_set.RUBRIC_VERSION
    assert sig["prompt_version"] == eval_set.PROMPT_VERSION
    assert sig["topic_count"] == 2 and sig["judge_samples"] == 3
    # 주제 구성이 다르면 지문이 달라져 이어하기 캐시가 섞이지 않는다
    other = evaluation.experiment_signature(eval_set.get_topics(3), samples=3, model="")
    assert sig["topics_hash"] != other["topics_hash"]


def test_aggregate_shape():
    results = [evaluation.evaluate_topic(t, samples=1) for t in eval_set.get_topics(2)]
    agg = evaluation.aggregate(results)
    assert agg["topics"] == 2
    assert set(agg["per_criterion"]) == set(eval_set.RUBRIC)
    assert "sections_complete_rate" in agg["structural"]
    assert "est_cost_usd_mean" in agg["cost"]
    assert 0 <= agg["total_100_mean"] <= 100


def test_calibrate_against_human_dummy():
    human = [{"id": "h1", "plan": "# 사람이 쓴 기획서\n내용"}]
    out = evaluation.calibrate_against_human(human, samples=1)
    assert out[0]["id"] == "h1" and out[0]["source"] == "human"
    assert set(out[0]["rubric"]["scores"]) == set(eval_set.RUBRIC)
