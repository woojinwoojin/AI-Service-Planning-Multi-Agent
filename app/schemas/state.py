"""LangGraph 워크플로 State와 API 입출력 스키마.

ROADMAP.md / docs/PRD.md 의 State 구조를 그대로 따른다.
"""
from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from pydantic import BaseModel, Field


class ProjectState(TypedDict, total=False):
    """전체 workflow가 공유하는 단일 State."""

    user_input: dict
    model: str  # 이번 실행에 사용할 LLM 모델 id(빈 값이면 env 기본값)
    reviewer_model: str  # 심판(Reviewer) 전용 모델 id — 작성 모델과 분리(자기 채점 편향 완화, Phase 4). 빈 값이면 model 사용
    structured_input: dict
    research_result: dict
    competitor_result: dict
    competitor_sources: list  # Competitor Agent가 쓴 실제 검색 출처(참고자료·검증 근거로 보존)
    # evidence_registry 도 reducer 필드: 병렬 분기의 여러 Agent(research/competitor)가 각자
    # 자기 근거만 방출해도 유실 없이 병합된다. 실행 종료 시 evidence.normalize()로 URL 중복 제거·
    # evidence_id 부여를 거쳐 단일 레지스트리로 확정한다(로드맵 2-1). 각 항목은
    # {evidence_id, source_agents[], queries[], url, title, snippet, source_type, used_by_claims[]}.
    evidence_registry: Annotated[list, operator.add]
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
    # 재작성 전략(로드맵 2-4 PR-7): none(재작성 없음) / section(문제 섹션만 수정) / full(전체 재작성).
    revision_strategy: str
    revised_section_ids: list        # section 전략일 때 실제 수정된 섹션 ID(sections.KNOWN_IDS)
    revision_fallback_reason: str    # full 로 fallback한 사유(user_request/parse_*/no_targets/too_many/section_gen/assemble)
    polish_applied: bool             # 조건부 Polish(PR-8): 일관성 편집을 실제로 수행했는지
    polish_skip_reason: str          # Polish 생략 사유(내용 이슈만·구조 정상 등). 실행 시 None
    best_version: str                # 채택한 최종본: draft(초안) / revised(재작성본) — Phase 4
    reverted_from_revision: bool     # 재작성본이 초안보다 나빠 초안으로 되돌렸는지
    final_review_result: dict    # 재작성·편집 후 최종본 재평가 (표시 점수)
    verification_result: dict
    verification_summary: dict   # 검증 범위·한계 문구(UI·내보내기·JSON 공통)
    quality_gate: dict           # 출력 가능 여부 게이트(release_ready·checks·미해결 이슈, Phase 4)
    # logs 는 reducer 필드: 병렬 노드가 동시에 로그를 추가해도 유실·충돌 없이 이어붙는다.
    # 각 노드는 '자기 새 로그만' 반환하고(operator.add 로 누적), 기존 전체 로그를 다시 반환하지 않는다.
    logs: Annotated[list, operator.add]  # 실행 로그 / 진행 상태 표시용
    # 단계별 계측 이벤트도 reducer 필드: 병렬 노드가 각자 자기 event 만 반환해도 유실 없이 병합된다.
    timing_events: Annotated[list, operator.add]  # [{node, started_at_ms, ended_at_ms, duration_ms}]
    timing: dict  # timing_events 집계(단계별 wall time·critical path·coverage)
    usage: dict  # 토큰·추정 비용·지연 관측치(실행 종료 시 집계해 기록)
    workflow_mode: str   # 실행 구조: serial / parallel (병렬화 비교 실험 태깅용)
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
    reviewer_model: str = Field(
        "", description="심판(Reviewer) 전용 모델 id(빈 값이면 model 과 동일). 작성/심사 모델 분리로 자기 채점 편향 완화")
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
    existing: dict = Field(
        default_factory=dict,
        description="사용자가 이미 입력한 항목(description/target_user/problem/keywords). 빈 항목만 채우고 이 값들은 보존·문맥으로만 사용",
    )
    compare: bool = Field(
        False,
        description="True면 비교 모드: 4개 항목 모두에 AI 제안을 생성(사용자 입력은 덮어쓰지 않고 화면 비교용)",
    )


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
    revision_strategy: str = "none"                            # none/section/full (로드맵 2-4)
    revised_section_ids: list = Field(default_factory=list)    # section 전략 시 수정된 섹션 ID
    revision_fallback_reason: str | None = None                # full 로 fallback한 사유
    polish_applied: bool = True                                # 조건부 Polish(PR-8) 수행 여부
    polish_skip_reason: str | None = None                      # Polish 생략 사유(실행 시 None)
    best_version: str = "revised"                              # 채택한 최종본: draft/revised (Phase 4)
    reverted_from_revision: bool = False                       # 재작성본→초안 되돌림 여부
    final_review_result: dict = Field(default_factory=dict)    # 최종본 재평가(표시 점수)
    verification_result: dict
    verification_summary: dict = Field(default_factory=dict)   # 검증 범위·한계 문구
    quality_gate: dict = Field(default_factory=dict)           # 출력 가능 여부 게이트(Phase 4)
    evidence_registry: list = Field(default_factory=list)      # 통합 근거 레지스트리(로드맵 2-1)
    logs: list
    project_id: int = 0  # 저장된 프로젝트 id (이력 조회용)
    usage: dict = Field(default_factory=dict)  # 토큰·비용·지연 관측치
    run_status: str = "success"                    # 실행 품질: success/degraded/failed
    failed_nodes: list = Field(default_factory=list)
    fallback_nodes: list = Field(default_factory=list)
    fallback_reasons: dict = Field(default_factory=dict)  # {노드: 원인} 사용자 안내용
    workflow_mode: str = "serial"                  # 실행 구조: serial/parallel
    timing: dict = Field(default_factory=dict)     # 단계별 실행시간·critical path·coverage
