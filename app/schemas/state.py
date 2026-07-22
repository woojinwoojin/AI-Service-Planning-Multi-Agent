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
    competitor_sources: list  # Competitor Agent가 쓴 실제 검색 출처(참고자료·검증 근거로 보존)
    customer_result: dict
    swot_result: dict
    business_model_result: dict
    risk_result: dict
    pestel_result: dict
    draft: str
    review_result: dict          # 재작성 판단에 쓰는 초안 평가 (= initial_review_result)
    initial_review_result: dict  # 초안 평가(기록용)
    final_draft: str
    revision_count: int
    final_review_result: dict    # 재작성·편집 후 최종본 재평가 (표시 점수)
    verification_result: dict
    verification_summary: dict   # 검증 범위·한계 문구(UI·내보내기·JSON 공통)
    logs: list  # 실행 로그 / 진행 상태 표시용
    usage: dict  # 토큰·추정 비용·지연 관측치(실행 종료 시 집계해 기록)
    run_status: str      # success / degraded / failed (실행 품질)
    failed_nodes: list   # 예외로 건너뛴 노드
    fallback_nodes: list # fallback/더미로 처리된 노드
    fallback_reasons: dict  # {노드: 원인(혼잡/연결/형식/처리)} — 사용자 안내용


# ---- API 입출력 ----

class ProjectInput(BaseModel):
    """사용자 아이디어 입력."""

    project_name: str = Field(..., description="프로젝트명")
    description: str = Field("", description="아이디어 설명")
    target_user: str = Field("", description="목표 사용자")
    problem: str = Field("", description="해결하려는 문제")
    keywords: list[str] = Field(default_factory=list, description="주요 키워드")
    model: str = Field("", description="사용할 LLM 모델 id(빈 값이면 서버 기본값). /models 참고")
    # 데모/개발용 장애 주입(임시). 비우면 무영향. 운영에선 사용하지 않는다.
    demo_fail_nodes: list[str] = Field(default_factory=list, description="[데모] 일부러 실패시킬 노드")
    demo_fail_reason: str = Field("", description="[데모] 실패 원인: 혼잡|연결|형식|처리")

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
    project_id: int = Field(
        0, description="(선택) 기존 프로젝트 id. 주면 저장된 상태를 근거로 삼아 출처를 유지하고 이력을 갱신한다."
    )


class SuggestInput(BaseModel):
    """프로젝트명 기반 입력 자동완성 요청."""

    project_name: str = Field(..., description="프로젝트명(필수)")
    memo: str = Field("", description="(선택) 아이디어에 대한 짧은 메모")
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
    customer_result: dict
    swot_result: dict
    business_model_result: dict
    risk_result: dict
    pestel_result: dict
    draft: str
    review_result: dict
    initial_review_result: dict = Field(default_factory=dict)  # 초안 평가
    final_draft: str
    revision_count: int
    final_review_result: dict = Field(default_factory=dict)    # 최종본 재평가(표시 점수)
    verification_result: dict
    verification_summary: dict = Field(default_factory=dict)   # 검증 범위·한계 문구
    logs: list
    project_id: int = 0  # 저장된 프로젝트 id (이력 조회용)
    usage: dict = Field(default_factory=dict)  # 토큰·비용·지연 관측치
    run_status: str = "success"                    # 실행 품질: success/degraded/failed
    failed_nodes: list = Field(default_factory=list)
    fallback_nodes: list = Field(default_factory=list)
    fallback_reasons: dict = Field(default_factory=dict)  # {노드: 원인} 사용자 안내용
