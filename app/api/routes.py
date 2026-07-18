"""FastAPI 라우트 — 입력 API + 워크플로 실행."""
from __future__ import annotations

from fastapi import APIRouter

from app.graph.workflow import run_workflow
from app.schemas.state import ProjectInput, RunResult
from app.services import llm
from app.services.markdown_export import save_markdown

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


@router.post("/run", response_model=RunResult)
def run(payload: ProjectInput) -> RunResult:
    """아이디어를 입력받아 4-Agent 워크플로를 실행하고 전체 결과를 반환."""
    state = run_workflow(payload.to_state_input())
    return RunResult(
        structured_input=state.get("structured_input", {}),
        research_result=state.get("research_result", {}),
        pestel_result=state.get("pestel_result", {}),
        draft=state.get("draft", ""),
        review_result=state.get("review_result", {}),
        final_draft=state.get("final_draft", ""),
        revision_count=state.get("revision_count", 0),
        logs=state.get("logs", []),
    )


@router.post("/run/save")
def run_and_save(payload: ProjectInput) -> dict:
    """워크플로 실행 후 최종 기획서를 Markdown으로 저장."""
    state = run_workflow(payload.to_state_input())
    path = save_markdown(payload.project_name, state.get("final_draft", ""))
    return {"saved_to": path, "revision_count": state.get("revision_count", 0)}
