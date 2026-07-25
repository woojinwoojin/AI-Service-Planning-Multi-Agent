"""실행 산출물 저장 — 최종 기획서(.md)와 전체 실행 결과(.json).

- 최종 .md: 발표/공유용 기획서
- 전체 .json: Agent별 중간 산출물까지 포함 (Agent별 결과 확인 · 10일 차 단일 vs 멀티 비교용)
"""
from __future__ import annotations

import json
import re
from pathlib import Path

OUTPUT_DIR = Path("outputs")

# 실행 결과 JSON에 담을 State 키 (Agent별 산출물 전체)
_RUN_KEYS = [
    # 실행 입력·모델: 재조회 후 /revise 하면 이 값들이 다음 실행의 근거가 된다. 빠지면 최초 입력
    # 문맥과 사용 모델이 유실돼, 수정이 서버 기본 모델로 실행된다(외부 리뷰 3차 B-3).
    "user_input", "model",
    "structured_input", "research_result", "competitor_result", "competitor_sources",
    "customer_result", "swot_result", "business_model_result", "risk_result", "pestel_result",
    "draft", "review_result", "initial_review_result", "final_draft", "revision_count",
    "best_version", "reverted_from_revision",
    # PR-7/8 실행 기록: 섹션 단위 수정 전략·조건부 Polish. 누락 시 저장·재조회에서
    # migrate 기본값(none/[]/True)으로 복원돼 실제 실행 이력이 왜곡됨(외부 리뷰 P0-1).
    "revision_strategy", "revised_section_ids", "revision_fallback_reason",
    "polish_applied", "polish_skip_reason",
    "final_review_result", "verification_result", "verification_summary", "quality_gate",
    "evidence_registry", "usage", "logs",
    "run_status", "failed_nodes", "fallback_nodes", "fallback_reasons", "workflow_mode",
    "reviewer_model", "timing", "timing_events", "budget", "state_version",
]


def _slugify(name: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z가-힣]+", "-", name).strip("-")
    return slug or "plan"


def save_markdown(project_name: str, content: str) -> str:
    OUTPUT_DIR.mkdir(exist_ok=True)
    path = OUTPUT_DIR / f"{_slugify(project_name)}.md"
    path.write_text(content, encoding="utf-8")
    return str(path)


def save_run_json(project_name: str, state: dict) -> str:
    """전체 실행 결과(각 Agent 출력 포함)를 JSON으로 저장."""
    OUTPUT_DIR.mkdir(exist_ok=True)
    path = OUTPUT_DIR / f"{_slugify(project_name)}.json"
    data = {key: state.get(key) for key in _RUN_KEYS}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)
