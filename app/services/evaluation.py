"""서비스 품질 평가 파이프라인 (로드맵 v2 · Phase 1).

기존 자산을 '평가 세트+루브릭+변동성/구조 통합'으로 승격한다.
- 루브릭 채점(8기준, EVAL_JUDGE) — 심판을 N회 반복해 기준별 평균 + 표준편차(변동성)를 낸다.
- 결정론적 구조 검사(parallel_bench.structural_quality) 통합.
- 비용·latency·실험 서명(rubric/prompt 버전 포함) 기록.
- 사람이 쓴 기획서도 같은 루브릭으로 채점(사람↔모델 점수 보정용).

실 LLM 미사용(더미)일 때도 관통하도록 설계 — CI/테스트에서 그대로 돈다.
"""
from __future__ import annotations

import hashlib
import statistics
import subprocess

from app.prompts.templates import EVAL_JUDGE
from app.services import eval_set, llm
from app.services.parallel_bench import structural_quality

RUBRIC = eval_set.RUBRIC
_MAX = eval_set.CRITERION_MAX
JUDGE_SAMPLES = 3  # 심판 변동성 완화 + 측정: 플랜당 여러 번 채점


def _clamp(value) -> int:
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        return 0
    return max(0, min(_MAX, n))


def _stdev(values: list[float]) -> float:
    return round(statistics.pstdev(values), 2) if len(values) > 1 else 0.0


def score_plan(plan_text: str, model: str = "", samples: int = JUDGE_SAMPLES) -> dict:
    """기획서 하나를 8개 루브릭 기준으로 samples회 채점.

    반환: 기준별 평균(scores)·표준편차(scores_stdev), raw 총점(0~160)·100환산(total_100),
    총점 표준편차(total_stdev = 심판 변동성 지표), 마지막 총평(comment), 실제 채점 횟수.
    """
    fallback = {"comment": "(더미) 평가 생략", "scores": {k: 10 for k in RUBRIC}}
    per_run: list[dict[str, int]] = []
    comment = ""
    for _ in range(max(1, samples)):
        raw = llm.complete_json(
            EVAL_JUDGE, f"아래 기획서를 평가하세요.\n\n{plan_text}",
            fallback=fallback, model=model,
        )
        raw = raw if isinstance(raw, dict) else fallback
        raw_scores = raw.get("scores") if isinstance(raw.get("scores"), dict) else {}
        per_run.append({k: _clamp(raw_scores.get(k)) for k in RUBRIC})
        if isinstance(raw.get("comment"), str) and raw.get("comment"):
            comment = raw["comment"]

    n = len(per_run)
    scores = {k: round(sum(r[k] for r in per_run) / n, 1) for k in RUBRIC}
    scores_stdev = {k: _stdev([r[k] for r in per_run]) for k in RUBRIC}
    totals = [sum(r.values()) for r in per_run]
    total = round(sum(scores.values()), 1)
    return {
        "scores": scores,
        "scores_stdev": scores_stdev,
        "total": total,                          # raw 0~160
        "total_100": round(total / (_MAX * len(RUBRIC)) * 100, 1),
        "total_stdev": _stdev([float(t) for t in totals]),  # 심판 변동성(총점 기준)
        "comment": comment,
        "samples": n,
    }


def _usage_subset(state: dict) -> dict:
    u = state.get("usage") or {}
    return {
        "wall_time_ms": u.get("wall_time_ms"),
        "llm_latency_sum_ms": u.get("llm_latency_sum_ms"),
        "calls": u.get("calls"),
        "total_tokens": u.get("total_tokens"),
        "est_cost_usd": u.get("est_cost_usd"),
        "fallback_calls": u.get("fallback_calls"),
    }


def evaluate_topic(topic: dict, model: str = "", samples: int = JUDGE_SAMPLES) -> dict:
    """한 주제: 멀티 워크플로 실행 → 구조 검사 + 루브릭 채점 + 비용/latency 수집."""
    from app.graph.workflow import run_workflow

    state = run_workflow({**topic, "model": model})
    plan = state.get("final_draft", "")
    return {
        "id": topic.get("id") or topic.get("project_name", ""),
        "topic": topic.get("project_name", ""),
        "run_status": state.get("run_status"),
        "structural": structural_quality(state),
        "rubric": score_plan(plan, model=model, samples=samples),
        "usage": _usage_subset(state),
        "plan": plan,
    }


def experiment_signature(topics: list[dict], samples: int, model: str) -> dict:
    """실험 조건 지문 — 옛 리포트와의 직접 비교 가능 여부 판단·이어하기 검증에 쓴다.

    루브릭/프롬프트 버전을 포함해, 기준이 바뀐 결과가 섞이지 않게 한다(트랙 C).
    """
    ids = "|".join(t.get("id") or t.get("project_name", "") for t in topics)
    topics_hash = hashlib.sha256(ids.encode("utf-8")).hexdigest()[:12]
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        commit = "unknown"
    return {
        "git_commit": commit,
        "model": model or "dummy",
        "topics_hash": topics_hash,
        "topic_count": len(topics),
        "judge_samples": samples,
        "rubric_version": eval_set.RUBRIC_VERSION,
        "prompt_version": eval_set.PROMPT_VERSION,
        # LLM temperature 고정값(=0.3). 완전 결정론은 아니므로 변동성을 total_stdev로 측정한다.
        "note": "temperature=0.3; 심판 변동성은 total_stdev 참고",
    }


def _mean(values: list[float]) -> float | None:
    nums = [v for v in values if isinstance(v, (int, float))]
    return round(sum(nums) / len(nums), 2) if nums else None


def aggregate(results: list[dict]) -> dict:
    """주제별 결과 → 기준별 평균·평균 변동성 + 구조 요약 + 비용/latency 요약."""
    n = max(len(results), 1)
    per_criterion = {
        k: {
            "mean": round(sum(r["rubric"]["scores"][k] for r in results) / n, 1),
            "stdev_mean": round(sum(r["rubric"]["scores_stdev"][k] for r in results) / n, 2),
        }
        for k in RUBRIC
    }
    return {
        "topics": n,
        "per_criterion": per_criterion,
        "total_mean": round(sum(r["rubric"]["total"] for r in results) / n, 1),
        "total_100_mean": round(sum(r["rubric"]["total_100"] for r in results) / n, 1),
        # 심판 변동성: 주제별 총점 표준편차의 평균(작을수록 평가가 안정적)
        "judge_variability_mean": round(sum(r["rubric"]["total_stdev"] for r in results) / n, 2),
        "structural": {
            "sections_complete_rate": round(
                sum(1 for r in results if r["structural"]["sections_complete"]) / n, 3),
            "sections_ordered_rate": round(
                sum(1 for r in results if r["structural"]["sections_ordered"]) / n, 3),
            "pestel_table_rate": round(
                sum(1 for r in results if r["structural"]["pestel_table"]) / n, 3),
            "empty_sections_mean": round(
                sum(r["structural"]["empty_sections"] for r in results) / n, 2),
            "unique_source_urls_mean": round(
                sum(r["structural"]["unique_source_urls"] for r in results) / n, 2),
        },
        "cost": {
            "wall_time_ms_mean": _mean([r["usage"]["wall_time_ms"] for r in results]),
            "total_tokens_mean": _mean([r["usage"]["total_tokens"] for r in results]),
            "est_cost_usd_mean": round(
                sum(r["usage"]["est_cost_usd"] or 0 for r in results) / n, 6),
            "fallback_calls_total": sum(r["usage"]["fallback_calls"] or 0 for r in results),
        },
        "run_status": {
            s: sum(1 for r in results if r["run_status"] == s)
            for s in ("success", "degraded", "failed")
            if any(r["run_status"] == s for r in results)
        },
    }


def calibrate_against_human(human_plans: list[dict], model: str = "",
                            samples: int = JUDGE_SAMPLES) -> list[dict]:
    """사람이 쓴 기획서를 같은 루브릭으로 채점(사람↔모델 점수 보정용).

    human_plans: [{"id", "plan"}...]. → 각 항목에 rubric 점수를 붙여 반환.
    사람 기준선 점수와 모델 결과 점수를 비교해 '90점 절대 신뢰 금지' 보정에 쓴다.
    """
    out = []
    for item in human_plans:
        out.append({
            "id": item.get("id", ""),
            "source": "human",
            "rubric": score_plan(item.get("plan", ""), model=model, samples=samples),
        })
    return out
