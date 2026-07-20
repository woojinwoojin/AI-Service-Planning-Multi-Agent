"""FastAPI 라우트 — 입력 API + 워크플로 실행."""
from __future__ import annotations

import json
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import StreamingResponse

from app.agents import draft_writer, reviewer
from app.graph.workflow import run_workflow, run_workflow_stream
from app.schemas.state import ExportInput, ProjectInput, ReviseInput, RunResult, SuggestInput
from app.services import docx_export, llm, store, suggest, usage
from app.services.markdown_export import save_markdown, save_run_json

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "dummy_mode": llm.is_dummy(),
        "provider": llm.current_provider(),
        "default_model": llm.default_model(),
    }


@router.get("/models")
def models() -> dict:
    """현재 provider에서 선택 가능한 모델 목록. /run 의 model 필드에 id를 넣어 사용."""
    return {
        "provider": llm.current_provider(),
        "default_model": llm.default_model(),
        "models": llm.list_models(),
    }


@router.get("/projects")
def projects(limit: int = 50) -> dict:
    """저장된 프로젝트 이력 목록(최신순)."""
    return {"projects": store.list_projects(limit=limit)}


@router.get("/projects/{project_id}")
def project_detail(project_id: int) -> dict:
    """저장된 프로젝트 상세(전체 실행 결과)."""
    found = store.get_project(project_id)
    if not found:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다.")
    return found


@router.post("/suggest")
def suggest_input(payload: SuggestInput) -> dict:
    """프로젝트명(+선택 메모)으로 나머지 입력 필드(설명·목표사용자·문제·키워드) 초안을 추천."""
    if not payload.project_name.strip():
        raise HTTPException(status_code=400, detail="프로젝트명을 입력하세요.")
    return suggest.suggest_fields(payload.project_name, payload.memo, payload.model)


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
        final_review_result=state.get("final_review_result", {}),
        verification_result=state.get("verification_result", {}),
        logs=state.get("logs", []),
        usage=state.get("usage", {}),
        run_status=state.get("run_status", "success"),
        failed_nodes=state.get("failed_nodes", []),
        fallback_nodes=state.get("fallback_nodes", []),
        fallback_reasons=state.get("fallback_reasons", {}),
    )


@router.post("/run", response_model=RunResult)
def run(payload: ProjectInput) -> RunResult:
    """아이디어를 입력받아 Multi-Agent 워크플로를 실행하고, 결과를 이력에 저장·반환."""
    state = run_workflow(payload.to_state_input())
    project_id = store.save_run(state)
    return _result_payload(state, project_id)


@router.post("/run/stream")
def run_stream(payload: ProjectInput) -> StreamingResponse:
    """워크플로를 실행하며 노드 완료를 SSE로 실시간 전송한다(진행 표시·ETA용).

    이벤트(각 줄 `data: {json}\\n\\n`):
      {"type":"node","node":<노드명>,"order":n}  — 노드 하나 완료
      {"type":"done","result":<RunResult>}        — 완료(결과 포함, 이력 저장됨)
      {"type":"error","message":...}               — 실행 중 예외
    """
    def event_stream():
        try:
            for ev in run_workflow_stream(payload.to_state_input()):
                if ev.get("type") == "done":
                    state = ev["state"]
                    project_id = store.save_run(state)
                    payload_out = {"type": "done", "result": _result_payload(state, project_id).model_dump()}
                    yield f"data: {json.dumps(payload_out, ensure_ascii=False)}\n\n"
                else:
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        except Exception as exc:  # 스트림 도중 실패해도 클라이언트에 사유 전달
            yield f"data: {json.dumps({'type': 'error', 'message': f'{type(exc).__name__}: {exc}'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/revise")
def revise(payload: ReviseInput) -> dict:
    """Human-in-the-Loop: 사용자의 수정 요청을 현재 기획서에 1회 반영해 재작성.

    - project_id가 주어지면 저장된 상태를 기반으로 삼아 근거(research_result·sources)를 유지한다.
    - 재작성 후 최종본을 다시 채점(final_reviewer)해 표시 점수를 정합하게 맞춘다.
    - 결과를 이력에 반영(기존 프로젝트는 갱신, 없으면 신규 저장)하고, 관측치·수정횟수를 반환한다.
    """
    base: dict = {}
    if payload.project_id:
        found = store.get_project(payload.project_id)
        if found:
            base = dict(found.get("state") or {})

    state = {
        **base,
        "draft": payload.draft,
        "review_result": {**base.get("review_result", {}),
                          "revision_instructions": payload.revision_instructions},
        "user_input": {**base.get("user_input", {}),
                       "revision_request": payload.revision_request},
        "model": payload.model or base.get("model", ""),
        "revision_count": 0,
        "logs": [],
    }

    usage.start()                                  # 수정 재작성의 토큰·비용도 관측
    state.update(draft_writer.revise(state))
    state.update(reviewer.final_reviewer(state))   # 수정된 최종본 재평가(표시 점수 정합)
    state["usage"] = usage.summary()

    # 이력 반영: 기존 프로젝트가 있으면 갱신, 없으면 신규 저장(수정 결과가 이력에 남도록)
    if payload.project_id and store.update_run(payload.project_id, state):
        project_id = payload.project_id
    else:
        project_id = store.save_run(state)

    return {
        "project_id": project_id,
        "final_draft": state.get("final_draft", ""),
        "revision_count": state.get("revision_count", 0),
        "final_review_result": state.get("final_review_result", {}),
        "usage": state.get("usage", {}),
        "logs": state.get("logs", []),
    }


@router.post("/export/docx")
def export_docx(payload: ExportInput) -> Response:
    """기획서 Markdown을 Word(.docx)로 변환해 다운로드 응답으로 반환."""
    data = docx_export.docx_bytes(payload.markdown)
    fname = f"{docx_export._slugify(payload.project_name)}.docx"
    # RFC 5987: 한글 등 비-ASCII 파일명을 헤더(latin-1)에 안전하게 싣는다
    disposition = f"attachment; filename=\"plan.docx\"; filename*=UTF-8''{quote(fname)}"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": disposition},
    )


@router.post("/run/save")
def run_and_save(payload: ProjectInput) -> dict:
    """워크플로 실행 후 최종 기획서(.md/.docx)와 전체 실행 결과(.json)를 저장."""
    state = run_workflow(payload.to_state_input())
    final = state.get("final_draft", "")
    saved_md = save_markdown(payload.project_name, final)
    saved_json = save_run_json(payload.project_name, state)
    saved_docx = docx_export.save_docx(payload.project_name, final)
    return {
        "saved_md": saved_md,
        "saved_json": saved_json,
        "saved_docx": saved_docx,
        "revision_count": state.get("revision_count", 0),
    }
