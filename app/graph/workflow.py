"""LangGraph 워크플로 정의.

흐름:
  START → preprocess → research → pestel → draft → reviewer
        → (needs_revision?) → revise → END
                            → finalize → END

자동 재작성은 최대 1회(revision_count < 1). Reviewer 총점이 충분히 높으면 재작성 없이 종료.
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.agents import draft_writer, pestel, preprocess, research, reviewer
from app.schemas.state import ProjectState

# 이 점수 이상이면 재작성 생략
PASS_SCORE = 90


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

    g.add_node("preprocess", preprocess.preprocess)
    g.add_node("research", research.research)
    g.add_node("pestel", pestel.pestel)
    g.add_node("draft", draft_writer.draft)
    g.add_node("reviewer", reviewer.reviewer)
    g.add_node("revise", draft_writer.revise)
    g.add_node("finalize", _finalize)

    g.add_edge(START, "preprocess")
    g.add_edge("preprocess", "research")
    g.add_edge("research", "pestel")
    g.add_edge("pestel", "draft")
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
    initial: ProjectState = {"user_input": user_input, "logs": []}
    return GRAPH.invoke(initial)
