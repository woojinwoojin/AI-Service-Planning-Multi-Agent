"""FastAPI 라우트 — 입력 API + 워크플로 실행."""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Response

from app.agents import draft_writer
from app.graph.workflow import run_workflow
from app.schemas.state import ExportInput, ProjectInput, ReviseInput, RunResult
from app.services import docx_export, llm, store
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


@router.post("/run", response_model=RunResult)
def run(payload: ProjectInput) -> RunResult:
    """아이디어를 입력받아 Multi-Agent 워크플로를 실행하고, 결과를 이력에 저장·반환."""
    state = run_workflow(payload.to_state_input())
    project_id = store.save_run(state)
    return RunResult(
        project_id=project_id,
        structured_input=state.get("structured_input", {}),
        research_result=state.get("research_result", {}),
        competitor_result=state.get("competitor_result", {}),
        swot_result=state.get("swot_result", {}),
        business_model_result=state.get("business_model_result", {}),
        risk_result=state.get("risk_result", {}),
        pestel_result=state.get("pestel_result", {}),
        draft=state.get("draft", ""),
        review_result=state.get("review_result", {}),
        final_draft=state.get("final_draft", ""),
        revision_count=state.get("revision_count", 0),
        verification_result=state.get("verification_result", {}),
        logs=state.get("logs", []),
    )


@router.post("/revise")
def revise(payload: ReviseInput) -> dict:
    """Human-in-the-Loop: 사용자의 수정 요청을 현재 기획서에 1회 반영해 재작성."""
    state = {
        "draft": payload.draft,
        "review_result": {"revision_instructions": payload.revision_instructions},
        "user_input": {"revision_request": payload.revision_request},
        "model": payload.model,
        "revision_count": 0,
        "logs": [],
    }
    result = draft_writer.revise(state)
    return {
        "final_draft": result.get("final_draft", ""),
        "logs": result.get("logs", []),
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
