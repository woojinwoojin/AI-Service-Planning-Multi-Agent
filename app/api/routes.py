"""FastAPI 라우트 — 입력 API + 워크플로 실행."""
from __future__ import annotations

import contextvars
import json
import logging
import queue
import threading
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import StreamingResponse

from app.agents import draft_writer
from app.api.errors import error_payload
from app.api.errors import responses as error_responses
from app.graph.workflow import (
    _safe,
    apply_node_update,
    rerun_finalizers,
    run_workflow,
    run_workflow_stream,
)
from app.schemas import artifact
from app.schemas.state import (
    AiLogExportInput,
    ExportInput,
    ProjectInput,
    ReviseInput,
    RunResult,
    SuggestInput,
)
from app.services import (
    ai_log,
    budget,
    docx_export,
    llm,
    pptx_export,
    public_guard,
    reliability,
    store,
    suggest,
    timing,
    usage,
)
from app.services.markdown_export import save_markdown, save_run_json

router = APIRouter()
_log = logging.getLogger("app.routes")


@router.get("/health", tags=["시스템"], summary="서버 상태")
def health() -> dict:
    # artifact_read_mode: 설정값은 프로세스 시작 시 확정되므로(.env 는 import 때 1회 로드)
    # '지금 이 서버가 어느 읽기 경로로 도는지'를 여기서 확인할 수 있어야 한다.
    # invalid=True 면 값이 있는데 못 알아들어 legacy 로 떨어진 것(오타 신호).
    read = artifact.read_mode_info()
    return {
        "status": "ok",
        "dummy_mode": llm.is_dummy(),
        "provider": llm.current_provider(),
        "default_model": llm.default_model(),
        "artifact_read_mode": read["mode"],
        "artifact_read_mode_invalid": read["invalid"],
        # 공개 배포 상한(트랙 E). 켜져 있는데 남은 여유가 안 보이면 운영자가 '왜 429 가 나는지'
        # 를 알 수 없다. 꺼져 있으면 enabled:false 만 나간다.
        "public_limits": public_guard.status(),
    }


@router.get("/models", tags=["시스템"], summary="사용 가능 모델 목록")
def models() -> dict:
    """현재 provider에서 선택 가능한 모델 목록. /run 의 model 필드에 id를 넣어 사용."""
    return {
        "provider": llm.current_provider(),
        "default_model": llm.default_model(),
        "models": llm.list_models(),
    }


@router.get("/projects", tags=["이력"], summary="프로젝트 이력 목록")
def projects(limit: int = Query(50, ge=1, le=100, description="가져올 최대 건수(1~100)")) -> dict:
    """저장된 프로젝트 이력 목록(최신순).

    limit 에 경계를 둔다 — 음수/0 은 SQLite 에서 '제한 없음'으로 해석되고, 과도한 값은 큰 응답을
    만들 수 있다. 범위를 벗어나면 422(통일 오류 형식)로 거절한다.
    """
    return {"projects": store.list_projects(limit=limit)}


@router.get("/projects/{project_id}", tags=["이력"], summary="프로젝트 상세",
            responses=error_responses(404))
def project_detail(project_id: int) -> dict:
    """저장된 프로젝트 상세(전체 실행 결과)."""
    found = store.get_project(project_id)
    if not found:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다.")
    # 옛 기록 정규화(누락 필드 기본값·게이트 소급·verification_summary)는 store.get_project 가
    # migrate.upgrade_state 로 이미 처리한다(Phase 5). 여기서 별도 보정 불필요.
    return found


@router.post("/suggest", tags=["입력 보조"], summary="입력 자동완성",
             responses=error_responses(400))
def suggest_input(payload: SuggestInput) -> dict:
    """프로젝트명(+메모+기존 입력)으로 '빈 항목만' 초안을 추천(사용자 입력은 보존·문맥 활용)."""
    if not payload.project_name.strip():
        raise HTTPException(status_code=400, detail="프로젝트명을 입력하세요.")
    return suggest.suggest_fields(payload.project_name, payload.memo, payload.model,
                                  payload.existing, payload.compare)


def _record_public_cost(state: dict) -> None:
    """공개 배포 일일 비용 누계에 이 실행의 **실측 비용**을 더한다(트랙 E).

    `_result_payload` 안에서 처리하지 않는다 — 그건 순수 변환기이고, 거기에 부수효과를 넣으면
    '응답을 만들었더니 카운터가 올라가는' 숨은 결합이 생긴다. 상한이 꺼져 있으면 no-op.
    """
    public_guard.record_cost((state.get("usage") or {}).get("est_cost_usd") or 0.0)


def _result_payload(state: dict, project_id: int) -> RunResult:
    """실행 state를 API 응답(RunResult)으로 변환(/run·/run/stream 공통)."""
    return RunResult(
        project_id=project_id,
        structured_input=state.get("structured_input", {}),
        research_result=state.get("research_result", {}),
        competitor_result=state.get("competitor_result", {}),
        customer_result=state.get("customer_result", {}),
        swot_result=state.get("swot_result", {}),
        business_model_result=state.get("business_model_result", {}),
        risk_result=state.get("risk_result", {}),
        pestel_result=state.get("pestel_result", {}),
        draft=state.get("draft", ""),
        review_result=state.get("review_result", {}),
        initial_review_result=state.get("initial_review_result", {}),
        final_draft=state.get("final_draft", ""),
        revision_count=state.get("revision_count", 0),
        # PR-7/8 실행 기록(섹션 수정 전략·조건부 Polish) — 넘기지 않으면 응답이 항상 기본값(외부 리뷰 P0-1)
        revision_strategy=state.get("revision_strategy", "none"),
        revised_section_ids=state.get("revised_section_ids", []),
        revision_fallback_reason=state.get("revision_fallback_reason"),
        polish_applied=state.get("polish_applied", False),
        polish_skip_reason=state.get("polish_skip_reason"),
        best_version=state.get("best_version", "revised"),
        reverted_from_revision=state.get("reverted_from_revision", False),
        final_review_result=state.get("final_review_result", {}),
        verification_result=state.get("verification_result", {}),
        verification_summary=state.get("verification_summary") or reliability.summary(),
        quality_gate=state.get("quality_gate", {}),
        evidence_registry=state.get("evidence_registry", []),
        evidence_gaps=state.get("evidence_gaps", []),
        dynamic_research=state.get("dynamic_research", {}),
        logs=state.get("logs", []),
        usage=state.get("usage", {}),
        run_status=state.get("run_status", "success"),
        failed_nodes=state.get("failed_nodes", []),
        fallback_nodes=state.get("fallback_nodes", []),
        fallback_reasons=state.get("fallback_reasons", {}),
        workflow_mode=state.get("workflow_mode", "serial"),
        timing=state.get("timing", {}),
        budget=state.get("budget", {}),
        state_version=state.get("state_version", 0),
        # KOSENA 산출물(체크포인트 3). 저장·JSON 내보내기에는 이미 있었으나 응답에 없어서
        # 화면에서 확인·내려받기가 불가능했다. 옛 기록(이 키가 없는 실행)은 기본값으로 비어 있고,
        # 화면이 '없으면 카드를 숨기는' 방식이라 재조회가 깨지지 않는다.
        kosena=state.get("kosena", {}),
        kosena_compliance=state.get("kosena_compliance", {}),
        kosena_plan=state.get("kosena_plan", ""),
        kosena_deck=state.get("kosena_deck", ""),
        ai_usage_log=state.get("ai_usage_log", []),
    )


@router.post("/run", response_model=RunResult, tags=["실행"], summary="워크플로 실행(동기)")
def run(payload: ProjectInput) -> RunResult:
    """아이디어를 입력받아 Multi-Agent 워크플로를 실행하고, 결과를 이력에 저장·반환."""
    state = run_workflow(payload.to_state_input())
    state["verification_summary"] = reliability.summary()
    _record_public_cost(state)
    project_id = store.save_run(state)
    return _result_payload(state, project_id)


def _sse(obj: dict) -> str:
    """SSE 한 프레임(`data: {json}\\n\\n`)으로 직렬화한다."""
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


# 이벤트가 없는 구간에 흘리는 SSE comment 주기(초). 노드 하나가 길게 걸리면(초안 작성·Polish 는
# 10초 이상) 그 사이 아무 바이트도 나가지 않아, 앞단 reverse proxy 가 유휴 연결로 보고 끊을 수 있다.
_SSE_HEARTBEAT_SEC = 15.0


def _with_heartbeat(events, heartbeat_sec: float = _SSE_HEARTBEAT_SEC):
    """블로킹 생성기를 워커 스레드에서 돌리며, 이벤트 공백 구간에 `None`(하트비트 신호)을 낸다.

    `run_workflow_stream` 은 노드가 끝날 때까지 블로킹하므로, 소비자 쪽에서 주기적으로 무언가를
    내보내려면 생산과 소비를 분리해야 한다. 워커에는 현재 컨텍스트 복사본을 넘겨(usage·budget·
    timing contextvar 가 워커 안에서 일관되게 시작·종료되도록) 실행한다. 워커 예외는 소비자
    스레드에서 다시 raise 해, 기존 오류 이벤트 경로를 그대로 타게 한다.
    """
    q: queue.Queue = queue.Queue()

    def worker():
        try:
            for ev in events:
                q.put(("event", ev))
        except BaseException as exc:   # noqa: BLE001 (소비자 스레드로 그대로 전달)
            q.put(("error", exc))
        finally:
            q.put(("end", None))

    ctx = contextvars.copy_context()
    threading.Thread(target=ctx.run, args=(worker,), daemon=True).start()
    while True:
        try:
            kind, payload = q.get(timeout=heartbeat_sec)
        except queue.Empty:
            yield None                # 이벤트 없음 → 하트비트를 내보낼 시점
            continue
        if kind == "event":
            yield payload
        elif kind == "error":
            raise payload
        else:
            return


@router.post("/run/stream", tags=["실행"], summary="워크플로 실행(SSE 스트리밍)",
             responses={200: {"description": "SSE 이벤트 스트림 (start → node* → done|error)",
                              "content": {"text/event-stream": {"schema": {"type": "string"}}}}})
def run_stream(payload: ProjectInput) -> StreamingResponse:
    """워크플로를 실행하며 진행 상황을 SSE로 실시간 전송한다(진행 표시·ETA용).

    이벤트 계약(각 프레임 `data: {json}\\n\\n`, JSON 의 `type` 으로 구분):
      {"type":"start","workflow_mode":"serial|parallel"}      — 스트림 시작(실행 구조)
      {"type":"node","node":<노드명>,"order":n}               — 노드 하나 완료(순번)
      {"type":"done","result":<RunResult>}                    — 완료(결과 포함, 이력 저장됨)
      {"type":"error","message":<안내>,"error":{code,message,status}}
                                                              — 실행 중 예외(HTTP 오류 봉투와 동일 구조)
      `: keep-alive` (data 없는 comment)                      — 유휴 구간 하트비트(소비자는 무시)
    - 순서 보장: start → node* → (done | error). 소비자는 모르는 type 을 무시해야 한다(전방 호환).
    - error 는 내부 예외 상세를 노출하지 않는다(HTTP 500 과 동일 원칙, 상세는 서버 로그).
    """
    def event_stream():
        try:
            for ev in _with_heartbeat(run_workflow_stream(payload.to_state_input())):
                if ev is None:
                    yield ": keep-alive\n\n"   # SSE comment — 규격상 소비자가 무시(연결 유지용)
                    continue
                if ev.get("type") == "done":
                    state = ev["state"]
                    state["verification_summary"] = reliability.summary()
                    _record_public_cost(state)
                    project_id = store.save_run(state)
                    yield _sse({"type": "done",
                                "result": _result_payload(state, project_id).model_dump()})
                else:
                    yield _sse(ev)
        except Exception:  # 스트림 도중 실패해도 클라이언트에 통일 형식으로 사유 전달
            _log.exception("SSE stream failed on /run/stream")
            err = error_payload(500, "실행 중 오류가 발생했습니다.")
            # message 는 UI 하위호환(ev.message 소비), error 는 HTTP 오류 봉투와 동일 구조.
            yield _sse({"type": "error", "message": err["error"]["message"], **err})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/revise", response_model=RunResult, tags=["실행"],
             summary="사용자 수정 반영 재작성(HITL)", responses=error_responses(404))
def revise(payload: ReviseInput) -> RunResult:
    """Human-in-the-Loop: 사용자의 수정 요청을 현재 기획서에 1회 반영해 재작성.

    - project_id가 주어지면 저장된 상태를 기반으로 삼아 근거(research_result·sources)를 유지한다.
      **없는 id면 404** — 조용히 신규 프로젝트로 저장하면 사용자는 갱신됐다고 오해하고 이력이
      쪼개진다(외부 리뷰 3차 D-4).
    - 재작성 후 최종본을 다시 채점(final_reviewer)해 표시 점수를 정합하게 맞춘다.
    - 결과를 이력에 반영(기존 프로젝트는 갱신, project_id 없으면 신규 저장)하고 반환한다.
    - 응답은 `/run` 과 같은 `RunResult` 다(외부 리뷰 3차 D-1). 수동 dict 를 조립하면 State 에 새
      필드가 생길 때마다 수정 응답에서 누락돼, 수정 후 다운로드에 옛 값이 남는다.
    """
    base: dict = {}
    if payload.project_id:
        found = store.get_project(payload.project_id)
        if not found:
            raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다.")
        base = dict(found.get("state") or {})

    state = {
        **base,
        "draft": payload.draft,
        "review_result": {**base.get("review_result", {}),
                          "revision_instructions": payload.revision_instructions},
        "user_input": {**base.get("user_input", {}),
                       "revision_request": payload.revision_request},
        "model": payload.model or base.get("model", ""),
        "reviewer_model": base.get("reviewer_model", ""),  # 원 실행의 심판 모델 분리 설정 유지(Phase 4)
        "revision_count": base.get("revision_count", 0),  # 누적(매 수정마다 0으로 초기화하지 않음)
        "logs": [],
        "timing_events": [],   # 이번 재작성 구간만 계측(옛 실행 이벤트 이어받지 않음)
    }
    # project_id 없이 수정하는 경로(저장된 base 없음)에서도 응답의 프로젝트명이 비지 않게 채운다.
    if not state.get("structured_input") and payload.project_name.strip():
        state["structured_input"] = {"project_name": payload.project_name.strip()}

    usage.start()                                  # 수정 재작성의 토큰·비용도 관측
    artifact.reads_start()                         # 섹션 수정도 selector 를 타므로 함께 계측(2-2 PR 5d)
    budget.start()                                 # 수정 재작성도 예산·시간 상한 적용(트랙 D)
    timing.start()                                 # 단계별 계측 시각 원점
    # 재작성 노드도 _safe 로 감싸 timing event 를 남긴다. 직접 호출하면 가장 큰 비용인 문서
    # 재작성 시간이 timing_events 에서 빠져 수정 실행의 coverage·단계별 시간이 부정확해진다
    # (외부 리뷰 P2-7). 이후 rerun_finalizers 도 polish/final_reviewer/verify 를 _safe 로 계측한다.
    apply_node_update(state, _safe("revise", draft_writer.revise)(state))  # 자기 로그만 반환 → 누적 병합
    # 수정된 최종본을 /run 뒷부분과 동일하게 재처리(polish→재평가→근거검증→품질판정).
    # 옛 문서의 verification_result·run_status 가 수정본과 함께 남지 않도록(외부 리뷰 P0-1).
    rerun_finalizers(state)
    state["usage"] = usage.summary()
    state["budget"] = budget.status()              # 수정 실행의 예산 상태도 표면화(트랙 D)
    state["timing"] = timing.summarize(state.get("timing_events", []),
                                       state.get("workflow_mode", "serial"),
                                       state["usage"].get("wall_time_ms"))
    state["verification_summary"] = reliability.summary()
    _record_public_cost(state)

    # 이력 반영: 기존 프로젝트가 있으면 갱신, 없으면 신규 저장(수정 결과가 이력에 남도록)
    if payload.project_id and store.update_run(payload.project_id, state):
        project_id = payload.project_id
    else:
        project_id = store.save_run(state)

    # /run 과 같은 변환기를 재사용 → 새 State 필드가 수정 응답에서 누락되지 않는다(D-1).
    return _result_payload(state, project_id)


@router.post("/export/docx", tags=["내보내기"], summary="DOCX 내보내기")
def export_docx(payload: ExportInput) -> Response:
    """기획서 Markdown을 Word(.docx)로 변환해 다운로드 응답으로 반환."""
    data = docx_export.docx_bytes(reliability.append_disclaimer(payload.markdown))
    fname = f"{docx_export._slugify(payload.project_name)}.docx"
    # RFC 5987: 한글 등 비-ASCII 파일명을 헤더(latin-1)에 안전하게 싣는다
    disposition = f"attachment; filename=\"plan.docx\"; filename*=UTF-8''{quote(fname)}"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": disposition},
    )


@router.post("/export/pptx", tags=["내보내기"], summary="PPTX 내보내기")
def export_pptx(payload: ExportInput) -> Response:
    """기획서 Markdown을 PowerPoint(.pptx)로 변환해 다운로드 응답으로 반환."""
    md = reliability.append_disclaimer(payload.markdown)
    data = pptx_export.pptx_bytes(md, title=payload.project_name)
    fname = f"{pptx_export._slugify(payload.project_name)}.pptx"
    # RFC 5987: 한글 등 비-ASCII 파일명을 헤더(latin-1)에 안전하게 싣는다
    disposition = f"attachment; filename=\"plan.pptx\"; filename*=UTF-8''{quote(fname)}"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers={"Content-Disposition": disposition},
    )


@router.post("/export/ai-log", tags=["내보내기"], summary="AI 활용 로그 Markdown 내보내기")
def export_ai_log(payload: AiLogExportInput) -> Response:
    """AI 활용 로그를 별도 첨부용 Markdown 으로 변환해 다운로드 응답으로 반환(KOSENA p4).

    DOCX·PPTX 내보내기와 같은 계약이다 — 화면이 가진 내용을 보내고 **서버가 형식을 만든다**.
    조립을 `ai_log.to_markdown` 한 곳에만 두어야 문서 안의 로그와 내려받은 파일이 갈라지지 않는다.
    """
    md = ai_log.to_markdown(payload.ai_usage_log)
    fname = f"{docx_export._slugify(payload.project_name or 'ai-log')}-AI활용로그.md"
    disposition = f"attachment; filename=\"ai-usage-log.md\"; filename*=UTF-8''{quote(fname)}"
    return Response(content=md.encode("utf-8"), media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": disposition})


@router.post("/run/save", tags=["실행"], summary="실행 + 산출물(.md/.docx/.pptx/.json) 저장")
def run_and_save(payload: ProjectInput) -> dict:
    """워크플로 실행 후 최종 기획서(.md/.docx/.pptx)와 전체 실행 결과(.json)를 저장."""
    state = run_workflow(payload.to_state_input())
    state["verification_summary"] = reliability.summary()
    final = reliability.append_disclaimer(state.get("final_draft", ""))  # 내보내기 문서에 한계 문구
    saved_md = save_markdown(payload.project_name, final)
    saved_json = save_run_json(payload.project_name, state)
    saved_docx = docx_export.save_docx(payload.project_name, final)
    saved_pptx = pptx_export.save_pptx(payload.project_name, final)

    # KOSENA 7종 산출물(체크포인트 3, p5)과 AI 활용 로그(p4: '별도 파일 첨부').
    # 기존 산출물은 그대로 두고 **추가로** 저장한다 — 제출 형식이 본문/발표/AI 로그로 나뉜다.
    name = payload.project_name
    kosena_files = {}
    if state.get("kosena_plan"):
        plan = reliability.append_disclaimer(state["kosena_plan"])
        kosena_files["saved_kosena_md"] = save_markdown(f"{name}-KOSENA", plan)
        kosena_files["saved_kosena_docx"] = docx_export.save_docx(f"{name}-KOSENA", plan)
    if state.get("kosena_deck"):
        kosena_files["saved_kosena_pptx"] = pptx_export.save_pptx(
            f"{name}-KOSENA-발표", state["kosena_deck"])
    if state.get("ai_usage_log"):
        kosena_files["saved_ai_log_md"] = save_markdown(
            f"{name}-AI활용로그", ai_log.to_markdown(state["ai_usage_log"]))

    return {
        "saved_md": saved_md,
        "saved_json": saved_json,
        "saved_docx": saved_docx,
        "saved_pptx": saved_pptx,
        **kosena_files,
        "kosena_compliance": (state.get("kosena_compliance") or {}).get("summary", ""),
        "revision_count": state.get("revision_count", 0),
    }
