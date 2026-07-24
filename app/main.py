"""FastAPI 진입점.

실행: uvicorn app.main:app --reload
문서: http://localhost:8000/docs
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.api.errors import COMMON_ERROR_RESPONSES, register_error_handlers
from app.api.routes import router
from app.services.migrate import STATE_VERSION

STATIC_DIR = Path(__file__).parent / "static"

# OpenAPI 태그: /docs 에서 엔드포인트를 기능별로 묶어 보여준다(Phase 5).
OPENAPI_TAGS = [
    {"name": "실행", "description": "기획서 생성·수정 워크플로 실행(동기·SSE 스트리밍·저장)"},
    {"name": "이력", "description": "저장된 프로젝트 조회(재조회 시 현재 스키마로 정규화)"},
    {"name": "입력 보조", "description": "프로젝트명 기반 입력 자동완성"},
    {"name": "내보내기", "description": "기획서 Markdown → DOCX·PPTX 변환"},
    {"name": "시스템", "description": "서버 상태·사용 가능 모델"},
]

app = FastAPI(
    title="AI 서비스 기획 보조 Multi-Agent",
    description=(
        "14-섹션 Multi-Agent 기획서 자동화 · Research·Competitor·Customer·PESTEL·SWOT·"
        "Business Model·Risk·Draft·Reviewer·(Section)Revise·Polish·Final Reviewer·Select-Best·Verify "
        "(웹검색 grounding + 통합 근거 레지스트리 + 신뢰도 Tier 2 + 품질 게이트). "
        f"응답 State 스키마 버전 v{STATE_VERSION}. 오류 응답은 `{{error:{{code,message,status}}}}` 형식 통일."
    ),
    version="0.2.0",
    openapi_tags=OPENAPI_TAGS,
)

register_error_handlers(app)   # 통일 오류 응답 형식(Phase 5)
# 모든 라우트에 공통 오류 응답(422·500) 스키마를 OpenAPI 에 노출한다.
app.include_router(router, responses=COMMON_ERROR_RESPONSES)


@app.get("/")
def root() -> FileResponse:
    """최소 UI(입력/결과/최종/이력 4화면)를 제공한다."""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/admin")
def admin() -> FileResponse:
    """관리자 · 데모 도구(임시): 특정 Agent를 일부러 실패시켜 정직한 미완성 안내를 시연."""
    return FileResponse(STATIC_DIR / "admin.html")


@app.get("/info")
def info() -> dict:
    return {
        "service": "AI 서비스 기획 보조 Multi-Agent",
        "ui": "/",
        "docs": "/docs",
        "run": "POST /run",
    }
