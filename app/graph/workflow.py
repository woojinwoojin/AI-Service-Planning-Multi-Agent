"""LangGraph 워크플로 정의.

흐름:
  START → preprocess → research → competitor → customer → pestel → swot → business_model → risk → draft → reviewer
        → (needs_revision?) → revise → polish → final_reviewer → verify → END
                            → finalize → polish → final_reviewer → verify → END

자동 재작성은 최대 1회(revision_count < 1). Reviewer 총점이 충분히 높으면 재작성 없이 종료.
초안 평가(reviewer)와 별개로, 재작성·편집이 끝난 최종본을 final_reviewer가 다시 채점해
표시 점수가 실제 최종 문서와 일치하도록 한다.
"""
from __future__ import annotations

import re
from collections.abc import Callable

from langgraph.graph import END, START, StateGraph

from app.services import demo, llm, tracing, usage
from app.agents import (
    business_model,
    competitor,
    customer,
    draft_writer,
    pestel,
    preprocess,
    research,
    reviewer,
    risk,
    swot,
    verifier,
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
        demo.apply_for_node(state, name)  # 데모 장애 주입: 이 노드를 실패시킬지 판단해 설정
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
    g.add_node("customer", _safe("customer", customer.customer))
    g.add_node("pestel", _safe("pestel", pestel.pestel))
    g.add_node("swot", _safe("swot", swot.swot))
    g.add_node("business_model", _safe("business_model", business_model.business_model))
    g.add_node("risk", _safe("risk", risk.risk))
    g.add_node("draft", _safe("draft", draft_writer.draft))
    g.add_node("reviewer", _safe("reviewer", reviewer.reviewer))
    g.add_node("revise", _safe("revise", draft_writer.revise))
    g.add_node("finalize", _safe("finalize", _finalize))
    g.add_node("polish", _safe("polish", draft_writer.polish))
    g.add_node("final_reviewer", _safe("final_reviewer", reviewer.final_reviewer))
    g.add_node("verify", _safe("verify", verifier.verify))

    g.add_edge(START, "preprocess")
    g.add_edge("preprocess", "research")
    g.add_edge("research", "competitor")
    g.add_edge("competitor", "customer")
    g.add_edge("customer", "pestel")
    g.add_edge("pestel", "swot")
    g.add_edge("swot", "business_model")
    g.add_edge("business_model", "risk")
    g.add_edge("risk", "draft")
    g.add_edge("draft", "reviewer")
    g.add_conditional_edges(
        "reviewer", _needs_revision, {"revise": "revise", "finalize": "finalize"}
    )
    g.add_edge("revise", "polish")
    g.add_edge("finalize", "polish")
    g.add_edge("polish", "final_reviewer")
    g.add_edge("final_reviewer", "verify")
    g.add_edge("verify", END)

    return g.compile()


# 앱 로드 시 1회 컴파일
GRAPH = build_graph()


_FAILED_RE = re.compile(r"\[(.+?)\] 오류로 건너뜀")
_NODE_RE = re.compile(r"\[(.+?)\]")
_REASON_RE = re.compile(r"fallback·([^,)\s]+)")


def _assess_quality(state: ProjectState) -> dict:
    """로그·더미여부로 실행 품질을 판정해 표면화한다(fallback이 실패를 숨기지 않도록, item 9).

    - failed_nodes: `_safe`가 예외로 건너뛴 노드
    - fallback_nodes: 로그에 fallback이 표기된(=fallback/오류 흡수) 노드
    - fallback_reasons: {노드: 원인} — 사용자에게 정직한 안내를 위해(혼잡/연결/형식/처리)
      fallback 노드는 로그의 `fallback·<원인>`에서, 예외로 건너뛴 노드는 '처리'로 매핑.
    - run_status: 실패 노드 있으면 failed, fallback/더미면 degraded, 아니면 success
    """
    logs = state.get("logs", []) or []
    failed = [m.group(1) for line in logs if (m := _FAILED_RE.search(line))]
    fallback = sorted({m.group(1) for line in logs
                       if "fallback" in line and (m := _NODE_RE.search(line))})
    reasons: dict[str, str] = {}
    for line in logs:
        node_m = _NODE_RE.search(line)
        if not node_m:
            continue
        if (rm := _REASON_RE.search(line)):
            reasons[node_m.group(1)] = rm.group(1)
    for node in failed:
        reasons.setdefault(node, "처리")
    if failed:
        status = "failed"
    elif fallback or llm.is_dummy():
        status = "degraded"
    else:
        status = "success"
    return {"run_status": status, "failed_nodes": failed,
            "fallback_nodes": fallback, "fallback_reasons": reasons}


def _prepare_run(user_input: dict):
    """실행 초기 상태와 Langfuse config를 만든다(invoke/stream 공통)."""
    initial: ProjectState = {
        "user_input": user_input,
        "model": (user_input.get("model") or "").strip(),
        "logs": [],
    }
    usage.start()                       # 이번 실행의 토큰·지연 관측 시작
    idea = (user_input.get("project_name") or user_input.get("description") or "planning-run")
    trace_name = str(idea)[:80]
    # 콜백을 GRAPH 실행 한 곳에만 실으면 각 노드/LLM 호출이 하나의 Langfuse 트레이스로 중첩된다.
    # (키 없으면 run_config가 빈 dict → 관측성 무영향)
    config = tracing.run_config(trace_name, langfuse_tags=[initial["model"] or llm.default_model()])
    return initial, (config or None)


def _finalize_run(state: ProjectState) -> ProjectState:
    """실행 종료 공통 후처리: 트레이스 flush + 관측치·실행 품질 표면화."""
    tracing.flush()                     # CLI/짧은 실행에서도 트레이스 유실 방지
    state["usage"] = usage.summary()    # 총 토큰·추정 비용·지연 집계
    state.update(_assess_quality(state))  # 실행 품질(run_status/failed/fallback) 표면화
    return state


def run_workflow(user_input: dict) -> ProjectState:
    initial, config = _prepare_run(user_input)
    state = GRAPH.invoke(initial, config=config)
    return _finalize_run(state)


def run_workflow_stream(user_input: dict):
    """워크플로를 스트리밍 실행하며 노드 완료 이벤트를 yield하고, 마지막에 최종 state를 yield한다.

    - GRAPH.stream(stream_mode="updates")로 각 노드 완료 직후 부분 업데이트를 받는다.
    - 각 노드는 완결된 값을 반환하므로(dict.update로 누적) 최종 state는 invoke 결과와 동일하다.
    - yield 형식: {"type": "node", "node": name, "order": n}
                  {"type": "done", "state": <최종 ProjectState>}
    """
    initial, config = _prepare_run(user_input)
    state: ProjectState = dict(initial)
    order = 0
    for chunk in GRAPH.stream(initial, config=config, stream_mode="updates"):
        for node, update in chunk.items():
            if isinstance(update, dict):
                state.update(update)
            order += 1
            yield {"type": "node", "node": node, "order": order}
    yield {"type": "done", "state": _finalize_run(state)}
