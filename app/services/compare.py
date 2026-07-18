"""단일 vs Multi-Agent 비교실험 (10일 차).

같은 심판(COMPARE_JUDGE)으로 두 방식의 기획서를 동일 기준으로 채점하고,
주제별·평균 점수표를 만든다. "정밀 통계보다 개선되었음"을 보여주는 것이 목적.
"""
from __future__ import annotations

import re

from app.agents import single_agent
from app.graph.workflow import run_workflow
from app.prompts.templates import COMPARE_JUDGE
from app.services import llm

_URL_RE = re.compile(r"https?://[^\s)\]]+")


def count_citations(plan_text: str) -> int:
    """기획서 본문에 명시된 '검증 가능한 실제 출처(고유 URL)' 수. LLM 채점이 아닌 하드 카운트."""
    return len(set(_URL_RE.findall(plan_text or "")))

# 로드맵 7절 비교 기준 (키 → 한글 라벨)
CRITERIA = {
    "problem_clarity": "문제 정의 명확성",
    "market_specificity": "시장분석 구체성",
    "pestel_completeness": "PESTEL 완성도",
    "consistency": "기획서 일관성",
    "evidence": "근거와 출처",
}
_MAX = 20
JUDGE_SAMPLES = 3  # 심판 노이즈 완화: 플랜당 여러 번 채점해 평균


def _clamp(value) -> int:
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        return 0
    return max(0, min(_MAX, n))


def judge(plan_text: str, model: str = "", samples: int = JUDGE_SAMPLES) -> dict:
    """기획서 하나를 5개 기준으로 채점. 심판을 samples회 반복해 기준별 평균을 낸다.

    total 은 (평균낸) 세부 점수의 합. 여러 번 채점해 LLM 심판의 변동을 줄인다.
    """
    fallback = {"comment": "(더미) 평가 생략", "scores": {k: 10 for k in CRITERIA}}
    per_run = []
    comment = ""
    for _ in range(max(1, samples)):
        raw = llm.complete_json(
            COMPARE_JUDGE, f"아래 기획서를 평가하세요.\n\n{plan_text}",
            fallback=fallback, model=model,
        )
        raw = raw if isinstance(raw, dict) else fallback
        raw_scores = raw.get("scores") if isinstance(raw.get("scores"), dict) else {}
        per_run.append({k: _clamp(raw_scores.get(k)) for k in CRITERIA})
        if isinstance(raw.get("comment"), str) and raw.get("comment"):
            comment = raw["comment"]
    n = len(per_run)
    scores = {k: round(sum(r[k] for r in per_run) / n, 1) for k in CRITERIA}
    return {"scores": scores, "total": round(sum(scores.values()), 1),
            "comment": comment, "samples": n}


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
        "single": {"plan": single_plan, "judge": judge(single_plan, model=model),
                   "citations": count_citations(single_plan)},
        "multi": {"plan": multi_plan, "judge": judge(multi_plan, model=model),
                  "citations": count_citations(multi_plan)},
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
    # 객관 지표: 검증 가능한 실제 출처 수 (LLM 채점 아님)
    table["citations"] = {
        "single": round(sum(r["single"]["citations"] for r in results) / n, 1),
        "multi": round(sum(r["multi"]["citations"] for r in results) / n, 1),
    }
    return table
