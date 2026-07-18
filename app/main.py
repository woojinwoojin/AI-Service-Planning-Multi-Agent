"""FastAPI 진입점.

실행: uvicorn app.main:app --reload
문서: http://localhost:8000/docs
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.api.routes import router

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(
    title="AI 서비스 기획 보조 Multi-Agent",
    description="Research → PESTEL → Draft Writer → Reviewer 4-Agent 기획서 자동화 (MVP)",
    version="0.1.0",
)

app.include_router(router)


@app.get("/")
def root() -> FileResponse:
    """최소 UI(입력/결과/최종 3화면)를 제공한다."""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/info")
def info() -> dict:
    return {
        "service": "AI 서비스 기획 보조 Multi-Agent",
        "ui": "/",
        "docs": "/docs",
        "run": "POST /run",
    }
