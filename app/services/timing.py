"""단계별 실행시간 계측 (PR-6) — 병목 '위치'를 측정한다(최적화 아님).

각 노드를 감싼 wrapper 가 노드 진입/종료 시각을 기록하고(run 시작을 0으로 둔 상대 ms),
노드는 '자기 timing event 만' 반환한다(logs 처럼 reducer 로 병합 → 병렬 write 충돌 없음).
실행 종료 후 summarize() 가 stage wall time·node duration·coverage 로 집계한다.

정직한 구분(섞으면 안 됨):
- node duration     : 노드 자체의 시작~종료(자기 LLM 호출 포함).
- stage wall time   : 그 구간의 실제 대기시간. 병렬 analysis_block 은 node duration 의 '합'이 아니다.
- llm_latency_sum   : (usage) 병렬 호출까지 모두 합산한 논리적 총 호출시간(겹침 포함).
"""
from __future__ import annotations

import contextvars
import time

# run 시작 시각(perf_counter). 상대시각(now_ms)의 원점. usage 와 별개로 둔다.
_origin: contextvars.ContextVar = contextvars.ContextVar("timing_origin", default=None)

# 분석 병렬 구간(Research 이후 독립 4분기의 6개 노드)
ANALYSIS_NODES = ("competitor", "customer", "pestel", "swot", "business_model", "risk")


def start() -> None:
    """이번 실행의 시각 원점을 기록(실행 시작 시 1회)."""
    _origin.set(time.perf_counter())


def now_ms() -> float:
    """실행 시작(0) 기준 경과 ms. 원점 미설정이면 0."""
    o = _origin.get()
    if o is None:
        return 0.0
    return round((time.perf_counter() - o) * 1000, 1)


def event(node: str, started_ms: float, ended_ms: float) -> dict:
    return {"node": node, "started_at_ms": started_ms, "ended_at_ms": ended_ms,
            "duration_ms": round(ended_ms - started_ms, 1)}


# stage 이름 → 담당 노드(순차 구간). analysis_block 은 별도(6노드의 span).
_STAGE_NODE = [
    ("preprocess", "preprocess"),
    ("research", "research"),
    ("draft", "draft"),
    ("initial_review", "reviewer"),
    ("polish", "polish"),
    ("final_review", "final_reviewer"),
    ("verify", "verify"),
]


def summarize(events: list[dict], mode: str, wall_time_ms: float | None = None) -> dict:
    """timing event 목록을 stage/node/critical_path/coverage 로 집계.

    stages 는 서로 겹치지 않는 순차 구간이므로 그 합이 전체 wall time 을 설명해야 한다
    (coverage = stage 합 / 전체). 병렬 효과는 analysis_block 이 node duration 합보다 작은 데서 드러난다.
    """
    evs = [e for e in events if isinstance(e, dict) and e.get("node")]
    empty = {"workflow_mode": mode, "wall_time_ms": wall_time_ms, "stages": {},
             "nodes": {}, "critical_path": [], "coverage": None}
    if not evs:
        return empty
    by_node = {e["node"]: e for e in evs}          # 노드명은 실행당 1회

    stages: dict[str, float] = {}
    for stage, node in _STAGE_NODE[:2]:            # preprocess, research
        if node in by_node:
            stages[stage] = by_node[node]["duration_ms"]

    analysis = [by_node[n] for n in ANALYSIS_NODES if n in by_node]
    if analysis:
        stages["analysis_block"] = round(
            max(a["ended_at_ms"] for a in analysis) - min(a["started_at_ms"] for a in analysis), 1)

    if "draft" in by_node:
        stages["draft"] = by_node["draft"]["duration_ms"]
    if "reviewer" in by_node:
        stages["initial_review"] = by_node["reviewer"]["duration_ms"]
    # revise 또는 finalize(둘 중 실행된 것)
    for n in ("revise", "finalize"):
        if n in by_node:
            stages["revise_or_finalize"] = by_node[n]["duration_ms"]
            break
    for stage, node in _STAGE_NODE[4:]:            # polish, final_review, verify
        if node in by_node:
            stages[stage] = by_node[node]["duration_ms"]

    nodes = {n: by_node[n]["duration_ms"] for n in ANALYSIS_NODES if n in by_node}

    span = round(max(e["ended_at_ms"] for e in evs) - min(e["started_at_ms"] for e in evs), 1)
    total = wall_time_ms or span
    stage_sum = round(sum(stages.values()), 1)
    # coverage: 전체 사용자 대기시간(wall) 대비. coverage_active: 노드 실행 구간(span) 대비.
    # wall 은 그래프 밖 프레임워크 오버헤드(첫 invoke warmup·flush 등)를 포함하므로 span 기준이
    # '측정이 파이프라인을 얼마나 설명하는지'의 주 지표다(성공 기준 ≥ 0.95).
    coverage = round(stage_sum / total, 3) if total else None
    coverage_active = round(stage_sum / span, 3) if span else None

    # critical path: analysis 는 '가장 늦게 끝난 분기 노드'(draft 진입을 게이트함)
    seq = [n for n in ("preprocess", "research") if n in by_node]
    if analysis:
        seq.append(max(analysis, key=lambda a: a["ended_at_ms"])["node"])
    for n in ("draft", "reviewer", "revise", "finalize", "polish", "final_reviewer", "verify"):
        if n in by_node:
            seq.append(n)

    return {"workflow_mode": mode, "wall_time_ms": total, "span_ms": span, "stages": stages,
            "nodes": nodes, "critical_path": seq, "stage_sum_ms": stage_sum,
            "coverage": coverage, "coverage_active": coverage_active}
