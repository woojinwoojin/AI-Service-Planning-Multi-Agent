"""LangGraph 워크플로 State와 API 입출력 스키마.

ROADMAP.md / docs/PRD.md 의 State 구조를 그대로 따른다.
"""
from __future__ import annotations

from typing import TypedDict

from pydantic import BaseModel, Field


class ProjectState(TypedDict, total=False):
    """전체 workflow가 공유하는 단일 State."""

    user_input: dict
    structured_input: dict
    research_result: dict
    pestel_result: dict
    draft: str
    review_result: dict
    final_draft: str
    revision_count: int
    logs: list  # 실행 로그 / 진행 상태 표시용


# ---- API 입출력 ----

class ProjectInput(BaseModel):
    """사용자 아이디어 입력."""

    project_name: str = Field(..., description="프로젝트명")
    description: str = Field("", description="아이디어 설명")
    target_user: str = Field("", description="목표 사용자")
    problem: str = Field("", description="해결하려는 문제")
    keywords: list[str] = Field(default_factory=list, description="주요 키워드")

    def to_state_input(self) -> dict:
        return self.model_dump()


class RunResult(BaseModel):
    """워크플로 실행 결과 (Agent별 결과 확인용)."""

    structured_input: dict
    research_result: dict
    pestel_result: dict
    draft: str
    review_result: dict
    final_draft: str
    revision_count: int
    logs: list
