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

import os
import re
from collections.abc import Callable

from langgraph.graph import END, START, StateGraph

from app.services import (
    ai_log,
    budget,
    demo,
    evidence,
    kosena,
    kosena_doc,
    llm,
    migrate,
    quality_gate,
    sections,
    timing,
    tracing,
    usage,
)
from app.agents import (
    business_model,
    competitor,
    customer,
    draft_writer,
    kosena_industry,
    kosena_model,
    kosena_research,
    kosena_roadmap,
    pestel,
    preprocess,
    research,
    reviewer,
    risk,
    swot,
    verifier,
)
from app.schemas import artifact
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
        started = timing.now_ms()
        try:
            result = fn(state)
        except Exception as exc:
            result = {"logs": [f"[{name}] 오류로 건너뜀 ({type(exc).__name__}: {exc})"]}
        # 단계별 계측 부착(자기 event 만 반환 → reducer 병합). 계측 실패는 본 실행에 영향 없음.
        try:
            ev = timing.event(name, started, timing.now_ms())
            result = {**result, "timing_events": [*result.get("timing_events", []), ev]}
        except Exception:
            pass
        return result

    return wrapped


def _route_revision(state: ProjectState) -> str:
    """Reviewer 이후 경로 결정(로드맵 2-4):

      수정 필요? ── 아니오 → finalize
                └─ 예 → 섹션 단위 수정 가능? ── 예 → section_revise
                                             └─ 아니오 → revise(전체 재작성)

    '수정 필요'는 기존 기준(총점<PASS_SCORE & 재작성 1회 미만) 유지. 섹션 단위 가능 여부는
    draft_writer.plan_section_revision 이 판정(구조화 이슈·파싱·대상 수). 실제 섹션 수정 중
    런타임 실패 시엔 section_revise 노드 안에서 다시 full-revise 로 안전하게 fallback 한다.
    """
    review = state.get("review_result", {})
    score = review.get("total_score", 0)
    already = state.get("revision_count", 0)
    if already >= 1 or score >= PASS_SCORE:
        return "finalize"
    _, reason = draft_writer.plan_section_revision(state)
    return "revise" if reason else "section_revise"


def _finalize(state: ProjectState) -> dict:
    """재작성 없이 초안을 최종본으로 확정."""
    logs = ["[finalize] 초안을 최종본으로 확정 (재작성 없음)"]
    return {"final_draft": state.get("draft", ""), "logs": logs,
            "revision_strategy": "none", "revised_section_ids": [],
            "revision_fallback_reason": None}


def _version_quality(review: dict, draft: str) -> dict:
    """한 버전(초안·재작성본)의 비교용 품질 지표를 뽑는다(_select_best 용).

    verify 는 select_best 뒤에서 실행되므로 이 시점엔 반대근거·근거충족률이 없다. 대신 이미
    확보된 지표(총점·critical/major 이슈 수·서식 정상 여부)로 회귀를 판정한다.
    """
    issues = review.get("issues") or []
    return {
        "score": review.get("total_score"),
        "critical": sum(1 for i in issues if isinstance(i, dict) and i.get("severity") == "critical"),
        "major": sum(1 for i in issues if isinstance(i, dict) and i.get("severity") == "major"),
        "structure_valid": sections.parse_sections(draft or "")["valid"],
    }


def _select_best(state: ProjectState) -> dict:
    """재작성본과 초안 중 '더 나은' 쪽을 최종본으로 채택한다(로드맵 Phase 4 '최고 버전 유지').

    자동 재작성이 문서를 오히려 나쁘게 만들 수 있으므로 같은 루브릭으로 채점한 초안
    (initial_review_result)과 재작성·편집 후 최종본(final_review_result)을 비교한다. 총점만 보면
    점수는 같거나 1점 올랐지만 새 critical/major 이슈가 생기거나 서식이 깨진 재작성본을 채택할 수
    있으므로(외부 리뷰 P1-5), 아래 지표 중 하나라도 나빠지면 회귀로 보고 초안으로 되돌린다.
      - 총점 하락
      - critical 이슈 수 증가
      - major 이슈 수 증가
      - 서식(14섹션) 정상 → 깨짐
    verify 는 이 노드 뒤에서 '채택된' 문서를 검증한다.

    - 재작성이 없었으면(finalize) 비교 대상이 없어 그대로 둔다.
    - 되돌릴 때 표시 점수(final_review_result)도 초안 점수로 정정해 화면·게이트가 실제 문서와 맞게 한다.
    - 수동 /revise(사용자 명시 수정)는 이 노드를 거치지 않는다(사용자 의도 존중).
    """
    if state.get("revision_strategy", "none") == "none":
        return {"best_version": "draft", "reverted_from_revision": False}
    iq = _version_quality(state.get("initial_review_result") or {}, state.get("draft", ""))
    fq = _version_quality(state.get("final_review_result") or {}, state.get("final_draft", ""))
    if not isinstance(iq["score"], int) or not isinstance(fq["score"], int):
        return {"best_version": "revised", "reverted_from_revision": False}
    regressions = []
    if fq["score"] < iq["score"]:
        regressions.append(f"총점 {fq['score']}<{iq['score']}")
    if fq["critical"] > iq["critical"]:
        regressions.append(f"치명이슈 {iq['critical']}→{fq['critical']}")
    if fq["major"] > iq["major"]:
        regressions.append(f"주요이슈 {iq['major']}→{fq['major']}")
    if iq["structure_valid"] and not fq["structure_valid"]:
        regressions.append("서식 정상→깨짐")
    if not regressions:
        return {"best_version": "revised", "reverted_from_revision": False}
    # 재작성본이 초안보다 나쁨(위 지표 중 하나라도 회귀) → 초안 채택(수정 되돌림)
    return {
        "final_draft": state.get("draft", ""),
        "final_review_result": dict(state.get("initial_review_result") or {}),
        "best_version": "draft",
        "reverted_from_revision": True,
        "logs": [f"[select_best] 재작성본 회귀({', '.join(regressions)}) → 초안 채택(수정 되돌림)"],
    }


def _register_nodes(g: StateGraph) -> None:
    """모든 노드를 등록한다(직렬·병렬 그래프 공통). 노드 함수·프롬프트는 동일."""
    g.add_node("preprocess", _safe("preprocess", preprocess.preprocess))
    g.add_node("research", _safe("research", research.research))
    # 제한된 동적 실행(로드맵 2-5): Research 가 보고한 근거 공백에만 추가 조사. 보고가 없으면
    # 아무 호출도 하지 않는 no-op 이라 항상 경로에 두고, 실행 여부는 노드 안에서 판단한다
    # (조건부 edge 로 빼면 직렬·병렬 두 그래프의 fan-out 배선이 갈라지고 진행 표시도 복잡해진다).
    g.add_node("research_gap", _safe("research_gap", research.research_gap))
    g.add_node("competitor", _safe("competitor", competitor.competitor))
    g.add_node("customer", _safe("customer", customer.customer))
    g.add_node("pestel", _safe("pestel", pestel.pestel))
    g.add_node("swot", _safe("swot", swot.swot))
    g.add_node("business_model", _safe("business_model", business_model.business_model))
    g.add_node("risk", _safe("risk", risk.risk))
    # KOSENA M1(체크포인트 3): 분석 결과 → Porter·Value Chain·KSF·시사점 → HMW·Lean Canvas.
    # PDF p7 의 체인(PESTEL→Porter→SWOT→KSF)을 그대로 따르므로 분석 구간 **뒤**, draft **앞**이다.
    # industry → model 순서는 필수 — HMW 가 KSF 를 입력으로 쓴다(p9).
    g.add_node("kosena_industry", _safe("kosena_industry", kosena_industry.kosena_industry))
    g.add_node("kosena_model", _safe("kosena_model", kosena_model.kosena_model))
    g.add_node("kosena_research", _safe("kosena_research", kosena_research.kosena_research))
    g.add_node("kosena_roadmap", _safe("kosena_roadmap", kosena_roadmap.kosena_roadmap))
    g.add_node("draft", _safe("draft", draft_writer.draft))
    g.add_node("reviewer", _safe("reviewer", reviewer.reviewer))
    g.add_node("revise", _safe("revise", draft_writer.revise))
    g.add_node("section_revise", _safe("section_revise", draft_writer.section_revise))
    g.add_node("finalize", _safe("finalize", _finalize))
    g.add_node("polish", _safe("polish", draft_writer.polish))
    g.add_node("final_reviewer", _safe("final_reviewer", reviewer.final_reviewer))
    g.add_node("select_best", _safe("select_best", _select_best))
    g.add_node("verify", _safe("verify", verifier.verify))


def _add_finish_edges(g: StateGraph) -> None:
    """draft 이후 마무리 구간(직렬·병렬 공통, 동일 순서)."""
    g.add_edge("draft", "reviewer")
    g.add_conditional_edges(
        "reviewer", _route_revision,
        {"section_revise": "section_revise", "revise": "revise", "finalize": "finalize"},
    )
    g.add_edge("section_revise", "polish")
    g.add_edge("revise", "polish")
    g.add_edge("finalize", "polish")
    g.add_edge("polish", "final_reviewer")
    g.add_edge("final_reviewer", "select_best")   # 재작성본 vs 초안 중 최고 점수 채택(Phase 4)
    g.add_edge("select_best", "verify")           # 채택된 문서를 검증
    g.add_edge("verify", END)


def build_serial_graph():
    """기존 직렬 구조: 분석 노드를 한 줄로 순차 실행(비교 기준)."""
    g = StateGraph(ProjectState)
    _register_nodes(g)
    g.add_edge(START, "preprocess")
    g.add_edge("preprocess", "research")
    g.add_edge("research", "research_gap")   # 2-5: 근거 공백 보고 시에만 추가 조사
    g.add_edge("research_gap", "competitor")
    g.add_edge("competitor", "customer")
    g.add_edge("customer", "pestel")
    g.add_edge("pestel", "swot")
    g.add_edge("swot", "business_model")
    g.add_edge("business_model", "risk")
    g.add_edge("risk", "kosena_industry")       # KOSENA M1(체크포인트 3)
    g.add_edge("kosena_industry", "kosena_model")
    # M2·M3 는 앞 결과를 이어받아야 일관성이 생긴다 — VPC 고객 프로필은 페르소나·CJM 에서,
    # Solution·UVP 는 Lean Canvas 에서 온다(평가표가 '블록 간 일관성'·'VPC Fit'을 본다).
    g.add_edge("kosena_model", "kosena_research")
    g.add_edge("kosena_research", "kosena_roadmap")
    g.add_edge("kosena_roadmap", "draft")
    _add_finish_edges(g)
    return g.compile()


def build_parallel_graph():
    """최소 변경 병렬 구조: Research 이후 서로 독립인 4분기를 동시 실행 → Draft 에서 합류.

    분기(실제 데이터 의존성만 반영, Agent 입력·프롬프트는 직렬과 동일):
      A: Competitor → SWOT      (SWOT 은 competitor_result 사용)
      B: Customer
      C: PESTEL → Risk          (Risk 는 pestel_result 사용)
      D: Business Model
    Draft 는 네 분기의 '끝 노드'(swot·customer·risk·business_model)를 리스트 edge 로 받아
    '모두 완료된 뒤 정확히 1회' 실행한다. 개별 edge 로 연결하면 깊이가 다른 분기 때문에
    Draft 가 조기 실행·중복 실행되므로(실측 확인), 반드시 리스트(fan-in join) 형태를 쓴다.
    """
    g = StateGraph(ProjectState)
    _register_nodes(g)
    g.add_edge(START, "preprocess")
    g.add_edge("preprocess", "research")
    g.add_edge("research", "research_gap")   # 2-5: 4분기 fan-out 전에 근거를 확정
    g.add_edge("research_gap", "competitor")
    g.add_edge("competitor", "swot")
    g.add_edge("research_gap", "customer")
    g.add_edge("research_gap", "pestel")
    g.add_edge("pestel", "risk")
    g.add_edge("research_gap", "business_model")
    # fan-in join → KOSENA M1 은 네 분기 결과를 **모두** 입력으로 쓰므로 합류 지점에 붙인다.
    g.add_edge(["swot", "customer", "risk", "business_model"], "kosena_industry")
    g.add_edge("kosena_industry", "kosena_model")
    # M2·M3 는 앞 결과를 이어받아야 일관성이 생긴다 — VPC 고객 프로필은 페르소나·CJM 에서,
    # Solution·UVP 는 Lean Canvas 에서 온다(평가표가 '블록 간 일관성'·'VPC Fit'을 본다).
    g.add_edge("kosena_model", "kosena_research")
    g.add_edge("kosena_research", "kosena_roadmap")
    g.add_edge("kosena_roadmap", "draft")
    _add_finish_edges(g)
    return g.compile()


# 앱 로드 시 1회씩 컴파일(두 구조 모두 유지 — 비교 실험용)
SERIAL_GRAPH = build_serial_graph()
PARALLEL_GRAPH = build_parallel_graph()
GRAPH = SERIAL_GRAPH   # 하위호환 별칭(기본은 직렬)


def _select_graph(mode: str):
    return PARALLEL_GRAPH if mode == "parallel" else SERIAL_GRAPH


def _resolve_mode(mode: str | None) -> str:
    """실행 모드 결정: 인자 우선, 없으면 env(WORKFLOW_MODE), 기본 serial."""
    resolved = (mode or os.getenv("WORKFLOW_MODE", "") or "serial").strip().lower()
    return "parallel" if resolved == "parallel" else "serial"


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


def apply_node_update(state: ProjectState, update: dict) -> ProjectState:
    """그래프 '밖'에서 노드 결과를 state 에 병합한다(logs 는 reducer 처럼 이어붙임).

    노드는 이제 '자기 새 값만' 반환한다(병렬 reducer 대응). LangGraph 안에서는 reducer 가
    자동 누적하지만, 그래프 밖(예: /revise, rerun_finalizers, stream 재구성)에서는
    dict.update 가 덮어써 이전 값이 사라진다. 여기서 reducer-list 필드(logs·timing_events)를 누적 병합한다.
    """
    merged = {k: list(state.get(k) or []) + list(update[k] or [])
              for k in ("logs", "timing_events", "evidence_registry") if k in update}
    # artifacts 는 단순 이어붙이기가 아니라 artifact_id 기준 병합이다(그래프 안 reducer 와 동일 규칙).
    # 여기서 concat 하면 같은 Artifact 가 중복돼 정합성 검사가 duplicate_id 로 잡는다.
    if "artifacts" in update:
        merged["artifacts"] = artifact.merge_artifacts(state.get("artifacts") or [],
                                                       update["artifacts"] or [])
    state.update(update)
    state.update(merged)   # reducer-list 필드는 덮어쓰기 대신 누적으로 교정
    return state


def rerun_finalizers(state: ProjectState) -> ProjectState:
    """수동 재작성(/revise) 후 최종본을 파이프라인 후반부와 동일하게 다시 처리한다.

    polish → final_reviewer → verify → 실행 품질 재판정 순으로, /run 의 뒷부분과 같은
    후처리를 적용한다. 이렇게 하지 않으면 수정 전 문서에 대한 옛 verification_result·
    run_status 가 수정 후 문서와 함께 저장돼 화면 점수·검증이 실제 문서와 어긋난다
    (외부 리뷰 P0-1). 각 단계는 _safe 로 감싸 한 단계가 실패해도 /revise 가 완주한다.
    그래프 밖이므로 apply_node_update 로 logs 를 누적한다(노드가 자기 로그만 반환).
    """
    for node, fn in (("polish", draft_writer.polish),
                     ("final_reviewer", reviewer.final_reviewer),
                     ("verify", verifier.verify)):
        apply_node_update(state, _safe(node, fn)(state))
    _finalize_evidence(state)   # 재작성 후에도 주장-근거 연결(used_by_claims) 재계산
    state.update(_assess_quality(state))
    state["quality_gate"] = quality_gate.evaluate(state)  # 수정본에 대해 품질 게이트 재판정
    _finalize_artifacts(state)    # 수정 후에도 Artifact 를 최신 결과로 재생성(로드맵 2-2)
    migrate.upgrade_state(state)  # 스키마 버전 태깅 + 누락 필드 보정(Phase 5)
    return state


def _prepare_run(user_input: dict, workflow_mode: str = "serial"):
    """실행 초기 상태와 Langfuse config를 만든다(invoke/stream 공통)."""
    initial: ProjectState = {
        "user_input": user_input,
        "model": (user_input.get("model") or "").strip(),
        # 심판 모델 분리(Phase 4): 입력 reviewer_model > env REVIEWER_MODEL. 비면 model 사용(reviewer 에서 폴백).
        "reviewer_model": (user_input.get("reviewer_model") or os.getenv("REVIEWER_MODEL", "") or "").strip(),
        "workflow_mode": workflow_mode,   # 실행 구조(serial/parallel) 기록 — 비교 실험용
        "logs": [],
    }
    usage.start()                       # 이번 실행의 토큰·지연 관측 시작
    artifact.reads_start()              # Artifact 읽기·폴백 계측 시작(2-2 PR 5d)
    budget.start()                      # 실행별 예산·시간 상한 활성화(env 기준, 트랙 D)
    timing.start()                      # 단계별 계측의 시각 원점
    idea = (user_input.get("project_name") or user_input.get("description") or "planning-run")
    trace_name = str(idea)[:80]
    # 콜백을 GRAPH 실행 한 곳에만 실으면 각 노드/LLM 호출이 하나의 Langfuse 트레이스로 중첩된다.
    # (키 없으면 run_config가 빈 dict → 관측성 무영향)
    config = tracing.run_config(trace_name, langfuse_tags=[initial["model"] or llm.default_model()])
    return initial, (config or None)


def _finalize_evidence(state: ProjectState) -> None:
    """근거 레지스트리를 확정한다(로드맵 2-1/2-1b).

    1) normalize: 누적 원시 근거를 URL 중복 제거·evidence_id 부여로 단일 레지스트리 확정.
    2) link_claims: verifier 가 주장별로 인용한 evidence_id 를 역인덱스로 뒤집어 각 근거의
       used_by_claims 를 채운다(주장-근거 연결). verify 이후에 호출돼야 한다.
    """
    reg = evidence.normalize(state.get("evidence_registry", []))
    claims = (state.get("verification_result") or {}).get("claims") or []
    state["evidence_registry"] = evidence.link_claims(reg, claims)


def _finalize_artifacts(state: ProjectState) -> None:
    """Agent 결과를 표준 Artifact 봉투로 병행 기록한다(로드맵 2-2 PR 2~5c).

    **기존 7개 평면 결과 키는 그대로 두고** 같은 내용을 병행 기록한다(Strangler 전환 — 평면
    키 삭제는 이 단계의 목표가 아니다). 읽는 쪽은 `ARTIFACT_READ_MODE` 에 달려 있고 기본값이
    `legacy` 라, 전환된 소비자(draft·verify·research_gap)도 기본 설정에서는 평면 키를 읽는다.

    호출 위치가 중요하다. 반드시 다음 **뒤에** 와야 한다:
      - `_finalize_evidence`  → 그래야 evidence_id 가 확정돼 Artifact 가 실제 id 를 참조한다
      - `_assess_quality`     → 그래야 failed_nodes·fallback_nodes 로 status 를 정할 수 있다
    그리고 `migrate.upgrade_state` **앞에** 와야 한다(upgrade 는 비어 있을 때만 채우므로,
    여기서 먼저 최신 결과로 재생성해 두면 옛 값이 남지 않는다).

    LLM·검색 호출 없는 순수 변환이다.

    `artifact.reconcile` 은 (a) 아직 Dual Write 안 된 Agent 를 평면 결과에서 파생해 메우고,
    (b) Agent 가 직접 쓴 Artifact 로 덮고, (c) `evidence_ids`·`status` 를 **이 시점 값으로 재확정**
    한다. (c)가 필요한 이유는 Agent 실행 시점에는 evidence_id(normalize 전)도 실행 결말
    (failed/fallback)도 알 수 없기 때문이다.

    확정 직후 `artifact.check_parity` 로 자기점검한다(PR 3). **어긋나도 실행을 실패시키지
    않는다** — 기본 모드(`legacy`)에서는 아무도 Artifact 를 읽지 않으므로, 이미 완주한 실행을
    병행 기록의 불일치로 죽이면 손해가 더 크다. 대신 `artifact_parity` 와 로그로 표면화해
    테스트·사람이 먼저 발견하게 한다(`prefer_artifact` 전환 판단의 근거이기도 하다).
    """
    state["artifacts"] = artifact.reconcile(state)
    # 읽기 모드·Artifact 가용성 스냅샷(2-2 PR 5b). prefer_artifact 로 전환하기 전에
    # '얼마나 폴백이 날지'를 미리 보고, 잘못된 모드 설정도 실행 기록으로 남긴다.
    read = artifact.read_status(state)
    state["artifact_read"] = read
    if read["invalid"]:
        state.setdefault("logs", []).append(
            f"[artifact] ARTIFACT_READ_MODE={read['raw']!r} 를 알 수 없어 {read['mode']} 로 동작")
    parity = artifact.check_parity(state)
    state["artifact_parity"] = parity
    if not parity["ok"]:
        # 조용히 넘어가면 '검증했는데 통과'와 '검증 자체가 깨짐'을 구분할 수 없다.
        reasons = ", ".join(sorted({m["reason"] for m in parity["mismatched"]}))
        state.setdefault("logs", []).append(
            f"[artifact] 정합성 불일치 {len(parity['mismatched'])}건 "
            f"(matched {parity['matched']}/{parity['expected']}): {reasons}")


def _finalize_run(state: ProjectState) -> ProjectState:
    """실행 종료 공통 후처리: 트레이스 flush + 관측치·실행 품질 표면화."""
    tracing.flush()                     # CLI/짧은 실행에서도 트레이스 유실 방지
    _finalize_evidence(state)           # 근거 레지스트리 확정 + 주장-근거 연결(로드맵 2-1/2-1b)
    state["usage"] = usage.summary()    # 총 토큰·추정 비용·지연 집계
    state["budget"] = budget.status()   # 예산·시간 상한 대비 소비·초과·강제 여부(트랙 D)
    state["timing"] = timing.summarize(  # 단계별 wall time·critical path·coverage
        state.get("timing_events", []), state.get("workflow_mode", "serial"),
        state["usage"].get("wall_time_ms"))
    state.update(_assess_quality(state))  # 실행 품질(run_status/failed/fallback) 표면화
    state["quality_gate"] = quality_gate.evaluate(state)  # 출력 가능 여부 게이트(로드맵 Phase 4)
    _finalize_artifacts(state)    # Agent 결과를 표준 Artifact 봉투로 병행 기록(로드맵 2-2)
    # KOSENA 방법론 준수 판정(체크포인트 3). **반드시 _finalize_artifacts 뒤**에 온다 —
    # 검사가 Artifact 를 selector 로 읽으므로, 확정 전이면 빈 값을 보고 전부 미충족으로 판정한다.
    # 미충족이어도 실행을 실패시키지 않는다(quality_gate·check_parity 와 같은 태도).
    # AI 활용 로그(체크포인트 3, p4) — Artifact 메타데이터·reviewer 판정·select_best 를
    # 재사용하므로 새 계측이 없다. kosena_compliance **앞**에 와야 검사가 이 로그를 본다.
    state["ai_usage_log"] = ai_log.build(state)
    # KOSENA 7종 산출물 문서·발표자료(p5). 기존 14섹션 기획서는 **그대로 두고** 별도로 조립한다
    # — 재구성하면 sections 왕복 불변식·section_revise·quality_gate 가 함께 깨진다.
    # 내보내기는 markdown 을 받는 기존 익스포터를 그대로 쓴다(새 익스포터 불필요).
    state["kosena_plan"] = kosena_doc.build(state)
    state["kosena_deck"] = kosena_doc.build_deck(state)
    # 준수 판정은 **문서 조립 뒤**에 온다 — 분량·가설 표기를 조립된 본문에서 재기 때문이다.
    state["kosena_compliance"] = kosena.evaluate(state)
    migrate.upgrade_state(state)  # 스키마 버전 태깅 + 누락 필드 보정(Phase 5)
    return state


def run_workflow(user_input: dict, workflow_mode: str | None = None) -> ProjectState:
    """워크플로 실행. workflow_mode=serial|parallel(없으면 env WORKFLOW_MODE, 기본 serial)."""
    mode = _resolve_mode(workflow_mode)
    initial, config = _prepare_run(user_input, mode)
    state = _select_graph(mode).invoke(initial, config=config)
    return _finalize_run(state)


def run_workflow_stream(user_input: dict, workflow_mode: str | None = None):
    """워크플로를 스트리밍 실행하며 노드 완료 이벤트를 yield하고, 마지막에 최종 state를 yield한다.

    - GRAPH.stream(stream_mode="updates")로 각 노드 완료 직후 부분 업데이트를 받는다.
    - 각 노드는 완결된 값을 반환하므로(dict.update로 누적) 최종 state는 invoke 결과와 동일하다.
    - yield 형식(SSE 이벤트 계약): {"type": "start", "workflow_mode": mode}
                  {"type": "node", "node": name, "order": n}
                  {"type": "done", "state": <최종 ProjectState>}
      (오류 이벤트 "error"는 스트림을 소비하는 라우트가 예외를 잡아 방출한다.)
    """
    mode = _resolve_mode(workflow_mode)
    initial, config = _prepare_run(user_input, mode)
    state: ProjectState = dict(initial)
    yield {"type": "start", "workflow_mode": mode}   # 스트림 시작 마커(+실행 구조)
    order = 0
    for chunk in _select_graph(mode).stream(initial, config=config, stream_mode="updates"):
        for node, update in chunk.items():
            if isinstance(update, dict):
                # updates 모드는 노드의 '원본 반환'(자기 로그만)을 준다 → logs 를 누적 병합해야
                # 최종 state 의 로그가 전 노드를 포함한다(reducer 는 그래프 내부에만 적용됨).
                apply_node_update(state, update)
            order += 1
            yield {"type": "node", "node": node, "order": order}
    yield {"type": "done", "state": _finalize_run(state)}
