"""FastAPI 진입점.

실행: uvicorn app.main:app --reload
문서: http://localhost:8000/docs
"""
from __future__ import annotations

from fastapi import FastAPI

from app.api.routes import router

app = FastAPI(
    title="AI 서비스 기획 보조 Multi-Agent",
    description="Research → PESTEL → Draft Writer → Reviewer 4-Agent 기획서 자동화 (MVP)",
    version="0.1.0",
)

app.include_router(router)


@app.get("/")
def root() -> dict:
    return {
        "service": "AI 서비스 기획 보조 Multi-Agent",
        "docs": "/docs",
        "run": "POST /run",
    }
