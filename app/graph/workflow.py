"""LangGraph 워크플로 정의.

흐름:
  START → preprocess → research → competitor → pestel → swot → business_model → risk → draft → reviewer
        → (needs_revision?) → revise → END
                            → finalize → END

자동 재작성은 최대 1회(revision_count < 1). Reviewer 총점이 충분히 높으면 재작성 없이 종료.
"""
from __future__ import annotations

from collections.abc import Callable

from langgraph.graph import END, START, StateGraph

from app.agents import (
    business_model,
    competitor,
    draft_writer,
    pestel,
    preprocess,
    research,
    reviewer,
    risk,
    swot,
)
from app.schemas.state import ProjectState

# 이 점수 이상이면 재작성 생략
PASS_SCORE = 90


def _safe(name: str, fn: Callable[[ProjectState], dict]) -> Callable[[ProjectState], dict]:
    """노드 실행 중 예외가 나도 파이프라인을 멈추지 않게 감싼다(관통 보장).

    한 노드가 실패하면 로그에 사유를 남기고 상태 갱신 없이 진행한다. 다음 노드들은
    각자의 fallback(_dummy)으로 빈 입력에도 구조를 유지하므로 처음~끝 완주한다.
    """
    def wrapped(state: ProjectState) -> dict:
        try:
            return fn(state)
        except Exception as exc:
            logs = state.get("logs", []) + [f"[{name}] 오류로 건너뜀 ({type(exc).__name__}: {exc})"]
            return {"logs": logs}

    return wrapped


def _needs_revision(state: ProjectState) -> str:
    review = state.get("review_result", {})
    score = review.get("total_score", 0)
    already = state.get("revision_count", 0)
    if already < 1 and score < PASS_SCORE:
        return "revise"
    return "finalize"


def _finalize(state: ProjectState) -> dict:
    """재작성 없이 초안을 최종본으로 확정."""
    logs = state.get("logs", []) + ["[finalize] 초안을 최종본으로 확정 (재작성 없음)"]
    return {"final_draft": state.get("draft", ""), "logs": logs}


def build_graph():
    g = StateGraph(ProjectState)

    g.add_node("preprocess", _safe("preprocess", preprocess.preprocess))
    g.add_node("research", _safe("research", research.research))
    g.add_node("competitor", _safe("competitor", competitor.competitor))
    g.add_node("pestel", _safe("pestel", pestel.pestel))
    g.add_node("swot", _safe("swot", swot.swot))
    g.add_node("business_model", _safe("business_model", business_model.business_model))
    g.add_node("risk", _safe("risk", risk.risk))
    g.add_node("draft", _safe("draft", draft_writer.draft))
    g.add_node("reviewer", _safe("reviewer", reviewer.reviewer))
    g.add_node("revise", _safe("revise", draft_writer.revise))
    g.add_node("finalize", _safe("finalize", _finalize))

    g.add_edge(START, "preprocess")
    g.add_edge("preprocess", "research")
    g.add_edge("research", "competitor")
    g.add_edge("competitor", "pestel")
    g.add_edge("pestel", "swot")
    g.add_edge("swot", "business_model")
    g.add_edge("business_model", "risk")
    g.add_edge("risk", "draft")
    g.add_edge("draft", "reviewer")
    g.add_conditional_edges(
        "reviewer", _needs_revision, {"revise": "revise", "finalize": "finalize"}
    )
    g.add_edge("revise", END)
    g.add_edge("finalize", END)

    return g.compile()


# 앱 로드 시 1회 컴파일
GRAPH = build_graph()


def run_workflow(user_input: dict) -> ProjectState:
    initial: ProjectState = {
        "user_input": user_input,
        "model": (user_input.get("model") or "").strip(),
        "logs": [],
    }
    return GRAPH.invoke(initial)
