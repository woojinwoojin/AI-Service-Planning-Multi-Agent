"""State 버전 관리 & 옛 프로젝트 재조회 호환 (로드맵 Phase 5).

State 는 SQLite 에 JSON blob 으로 저장되므로 DDL migration 이 없다. 대신 **읽는 시점에** 옛 기록을
현재 스키마로 정규화(누락 필드에 안전 기본값 주입)해, 새 필드를 소비하는 UI·API 가 옛 프로젝트를
재조회해도 깨지지 않게 한다(완료 게이트: 옛 프로젝트 재조회 회귀 테스트).

- `STATE_VERSION`: 현재 State 스키마 버전. 실행 종료 시 새 State 에 태깅하고, 재조회 시 이 버전으로 올린다.
- `upgrade_state`: 멱등. 이미 최신이어도 안전하게 통과(기존 값은 보존, 없는 것만 채움).

세션 누적으로 늘어난 필드(revision_strategy·polish_applied·best_version·quality_gate·
reviewer_model·evidence_registry 등)를 여기서 한곳에 정리한다.
"""
from __future__ import annotations

from copy import deepcopy

from app.schemas import artifact
from app.services import quality_gate, reliability

# v1=초기, v2=문서재생성/신뢰도/게이트 필드 추가(2026-07-24), v3=Artifact Contract 병행 기록(2-2 PR 2)
STATE_VERSION = 3

# 재조회 시 없으면 채울 안전 기본값(가변 값은 매번 deepcopy 해 공유 참조 방지).
_DEFAULTS: dict = {
    "revision_strategy": "none",
    "revised_section_ids": [],
    "revision_fallback_reason": None,
    "polish_applied": True,
    "polish_skip_reason": None,
    "best_version": "revised",
    "reverted_from_revision": False,
    "reviewer_model": "",
    "evidence_registry": [],
    "evidence_gaps": [],       # 로드맵 2-5(옛 기록엔 없음 → 빈 목록)
    "dynamic_research": {},
    "workflow_mode": "serial",
    "run_status": "success",
    "failed_nodes": [],
    "fallback_nodes": [],
    "fallback_reasons": {},
    "timing": {},
    "budget": {},
}


def upgrade_state(state: dict) -> dict:
    """옛 State 를 현재 스키마로 정규화한다(제자리 갱신·멱등). dict 가 아니면 그대로 반환."""
    if not isinstance(state, dict):
        return state
    for key, default in _DEFAULTS.items():
        if key not in state or state[key] is None and default is not None:
            state[key] = deepcopy(default)
    # 신뢰성 한계 문구: 시스템 성격이라 옛 기록에도 동일하게 채운다.
    if not state.get("verification_summary"):
        state["verification_summary"] = reliability.summary()
    # 품질 게이트: 옛 기록엔 없으므로 저장된 점수·검증·최종본으로 재계산해 채운다(Phase 4 지표 소급).
    if not state.get("quality_gate"):
        state["quality_gate"] = quality_gate.evaluate(state)
    # v2→v3: Artifact Contract(2-2). 옛 기록엔 artifacts 가 없으므로 저장된 평면 결과 키에서
    # 파생 생성한다. LLM·검색 호출이 없는 순수 변환이라 재조회가 느려지거나 비용이 들지 않는다.
    # 이미 있으면 덮어쓰지 않는다 — Agent 가 직접 쓴 Artifact(PR 4)를 legacy 파생본으로
    # 되돌려 버리면 안 되기 때문. 새 실행의 재생성은 workflow._finalize_artifacts 가 담당한다.
    if not state.get("artifacts"):
        state["artifacts"] = artifact.build_artifacts_from_legacy(state)
    # 정합성 자기점검(2-2 PR 3)도 옛 기록에 소급한다 — 저장 당시 없던 판정을 재조회 시 채워야
    # '옛 기록이라 판정이 없음'과 '판정했는데 통과'를 구분할 수 있다.
    if not state.get("artifact_parity"):
        state["artifact_parity"] = artifact.check_parity(state)
    state["state_version"] = STATE_VERSION
    return state
