"""LangGraph 워크플로 State와 API 입출력 스키마.

ROADMAP.md / docs/PRD.md 의 State 구조를 그대로 따른다.
"""
from __future__ import annotations

from typing import TypedDict

from pydantic import BaseModel, Field


class ProjectState(TypedDict, total=False):
    """전체 workflow가 공유하는 단일 State."""

    user_input: dict
    model: str  # 이번 실행에 사용할 LLM 모델 id(빈 값이면 env 기본값)
    structured_input: dict
    research_result: dict
    competitor_result: dict
    swot_result: dict
    business_model_result: dict
    risk_result: dict
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
    model: str = Field("", description="사용할 LLM 모델 id(빈 값이면 서버 기본값). /models 참고")

    def to_state_input(self) -> dict:
        return self.model_dump()


class ReviseInput(BaseModel):
    """Human-in-the-Loop 수동 수정요청 (최종 화면에서 사용)."""

    project_name: str = Field("", description="프로젝트명")
    draft: str = Field(..., description="현재 기획서(수정 대상)")
    revision_request: str = Field(..., description="사용자 수정 요청")
    revision_instructions: list[str] = Field(
        default_factory=list, description="(선택) Reviewer의 기존 개선지시"
    )
    model: str = Field("", description="사용할 LLM 모델 id(빈 값이면 서버 기본값)")


class ExportInput(BaseModel):
    """기획서 Markdown → Word(.docx) 내보내기 입력."""

    project_name: str = Field("", description="프로젝트명(파일명)")
    markdown: str = Field(..., description="변환할 기획서 Markdown")


class RunResult(BaseModel):
    """워크플로 실행 결과 (Agent별 결과 확인용)."""

    structured_input: dict
    research_result: dict
    competitor_result: dict
    swot_result: dict
    business_model_result: dict
    risk_result: dict
    pestel_result: dict
    draft: str
    review_result: dict
    final_draft: str
    revision_count: int
    logs: list
