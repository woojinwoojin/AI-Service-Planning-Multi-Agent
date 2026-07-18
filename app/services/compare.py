"""단일 vs Multi-Agent 비교실험 (10일 차).

같은 심판(COMPARE_JUDGE)으로 두 방식의 기획서를 동일 기준으로 채점하고,
주제별·평균 점수표를 만든다. "정밀 통계보다 개선되었음"을 보여주는 것이 목적.
"""
from __future__ import annotations

from app.agents import single_agent
from app.graph.workflow import run_workflow
from app.prompts.templates import COMPARE_JUDGE
from app.services import llm

# 로드맵 7절 비교 기준 (키 → 한글 라벨)
CRITERIA = {
    "problem_clarity": "문제 정의 명확성",
    "market_specificity": "시장분석 구체성",
    "pestel_completeness": "PESTEL 완성도",
    "consistency": "기획서 일관성",
    "evidence": "근거와 출처",
}
_MAX = 20


def _clamp(value) -> int:
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        return 0
    return max(0, min(_MAX, n))


def judge(plan_text: str, model: str = "") -> dict:
    """기획서 하나를 5개 기준으로 채점. total은 세부합으로 재계산."""
    fallback = {"comment": "(더미) 평가 생략", "scores": {k: 10 for k in CRITERIA}}
    raw = llm.complete_json(
        COMPARE_JUDGE, f"아래 기획서를 평가하세요.\n\n{plan_text}",
        fallback=fallback, model=model,
    )
    raw = raw if isinstance(raw, dict) else fallback
    raw_scores = raw.get("scores") if isinstance(raw.get("scores"), dict) else {}
    scores = {k: _clamp(raw_scores.get(k)) for k in CRITERIA}
    comment = raw.get("comment") if isinstance(raw.get("comment"), str) else ""
    return {"scores": scores, "total": sum(scores.values()), "comment": comment}


def run_topic(topic: dict, model: str = "") -> dict:
    """한 주제에 대해 단일/멀티 실행 + 채점."""
    # Multi-Agent
    multi_state = run_workflow({**topic, "model": model})
    multi_plan = multi_state.get("final_draft", "")
    # 단일 LLM (동일 입력의 구조화 결과를 사용해 공정하게)
    si = multi_state.get("structured_input", topic)
    single_plan = single_agent.generate(si, model=model)
    return {
        "topic": topic.get("project_name", ""),
        "single": {"plan": single_plan, "judge": judge(single_plan, model=model)},
        "multi": {"plan": multi_plan, "judge": judge(multi_plan, model=model)},
    }


def aggregate(results: list[dict]) -> dict:
    """주제별 결과 → 기준별 평균 점수표."""
    n = max(len(results), 1)
    table = {}
    for key in CRITERIA:
        table[key] = {
            "single": round(sum(r["single"]["judge"]["scores"][key] for r in results) / n, 1),
            "multi": round(sum(r["multi"]["judge"]["scores"][key] for r in results) / n, 1),
        }
    table["total"] = {
        "single": round(sum(r["single"]["judge"]["total"] for r in results) / n, 1),
        "multi": round(sum(r["multi"]["judge"]["total"] for r in results) / n, 1),
    }
    return table
