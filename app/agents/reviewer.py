"""Reviewer Agent — 5항목 100점 평가 + 개선지시.

- 실제 모드: LLM이 초안을 5개 항목(각 0~20점)으로 평가하고 개선지시를 낸다.
- 더미 모드: 골격 평가를 반환한다.

Research/PESTEL과 동일하게 _validate()로 스키마를 강제한다. 특히 total_score는
LLM 값에 의존하지 않고 section_scores 합으로 재계산해, 워크플로의 재작성 판단
(_needs_revision, PASS_SCORE)이 항상 정합적인 총점 위에서 동작하도록 한다.
"""
from __future__ import annotations

from app.prompts.templates import REVIEWER_SYSTEM
from app.schemas.state import ProjectState
from app.services import llm, sections

SECTION_KEYS = [
    "problem_clarity", "market_validity", "solution_specificity",
    "differentiation", "feasibility",
]
_LIST_KEYS = ["strengths", "weaknesses", "unsupported_claims", "revision_instructions"]
_SECTION_MAX = 20
# 구조화 이슈 심각도(로드맵 2-3). critical/major 는 섹션 단위 자동 수정 대상, minor 는 Polish 로.
_SEVERITY = {"critical", "major", "minor"}


def _validate_issues(raw) -> list[dict]:
    """Reviewer 의 구조화 이슈를 스키마로 정규화한다(로드맵 2-3 PR-7).

    각 이슈는 `{issue_type, severity, target_section_id, description, revision_instruction}`.
    - target_section_id 는 14섹션 내부 ID(sections.KNOWN_IDS)만 통과시킨다 → LLM 이 지어낸
      섹션명·자유 문자열을 걸러 섹션 단위 수정이 항상 유효한 대상 위에서 동작하게 한다.
    - severity 는 critical/major/minor 만 허용(그 외 → major).
    - revision_instruction 이 비면 description 으로 대체, 둘 다 비면 이슈를 버린다
      (실행 가능한 지시가 없는 이슈는 수정 라우팅에 무의미).
    """
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for it in raw:
        if not isinstance(it, dict):
            continue
        sid = it.get("target_section_id")
        if sid not in sections.KNOWN_IDS:
            continue
        desc = it.get("description") if isinstance(it.get("description"), str) else ""
        instr = it.get("revision_instruction") if isinstance(it.get("revision_instruction"), str) else ""
        desc, instr = desc.strip(), instr.strip()
        instruction = instr or desc
        if not instruction:
            continue
        severity = it.get("severity") if it.get("severity") in _SEVERITY else "major"
        raw_type = it.get("issue_type")
        itype = raw_type.strip() if isinstance(raw_type, str) and raw_type.strip() else "general"
        out.append({
            "issue_type": itype,
            "severity": severity,
            "target_section_id": sid,
            "description": desc,
            "revision_instruction": instruction,
        })
    return out


def _clamp_score(value) -> int:
    """세부 점수를 0~20 정수로 정규화. 숫자가 아니면 0."""
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        return 0
    return max(0, min(_SECTION_MAX, n))


def _validate(result: dict, fallback: dict) -> dict:
    """평가 결과를 스키마에 맞게 정규화한다.

    - dict가 아니면 fallback 전체 사용.
    - section_scores 5개는 0~20 정수로 clamp, total_score는 그 합으로 재계산.
    - 리스트 4종은 비어있지 않은 문자열만 남기고, 없으면 빈 리스트.
    """
    if not isinstance(result, dict):
        return dict(fallback)
    raw_scores = result.get("section_scores")
    raw_scores = raw_scores if isinstance(raw_scores, dict) else {}
    section_scores = {k: _clamp_score(raw_scores.get(k)) for k in SECTION_KEYS}
    out: dict = {
        "total_score": sum(section_scores.values()),
        "section_scores": section_scores,
    }
    for key in _LIST_KEYS:
        value = result.get(key)
        if isinstance(value, list):
            out[key] = [s.strip() for s in value if isinstance(s, str) and s.strip()]
        else:
            out[key] = []
    # 구조화 이슈(로드맵 2-3): 섹션 단위 수정 라우팅의 입력. 없거나 무효면 빈 리스트(→ full revise).
    out["issues"] = _validate_issues(result.get("issues"))
    return out


def _dummy(draft: str) -> dict:
    section_scores = {k: 15 for k in SECTION_KEYS}
    return {
        "total_score": sum(section_scores.values()),
        "strengths": ["[더미] 구조가 서식을 잘 따름"],
        "weaknesses": ["[더미] 시장분석에 구체적 수치 부족"],
        "unsupported_claims": ["[더미] 출처 없는 시장 성장 주장"],
        "revision_instructions": [
            "[더미] 시장분석에 출처 기반 근거를 보강할 것",
            "[더미] 차별성 섹션을 경쟁 대비로 구체화할 것",
        ],
        "issues": [
            {"issue_type": "insufficient_evidence", "severity": "major",
             "target_section_id": "market_analysis",
             "description": "[더미] 시장 규모 근거 부족",
             "revision_instruction": "[더미] 검색 근거로 시장 현황을 구체화할 것"},
            {"issue_type": "weak_differentiation", "severity": "major",
             "target_section_id": "differentiation",
             "description": "[더미] 경쟁 대비 차별점 모호",
             "revision_instruction": "[더미] 차별성 섹션을 경쟁 대비로 구체화할 것"},
        ],
        "section_scores": section_scores,
    }


def _review(draft: str, model: str) -> tuple[dict, dict]:
    """기획서 하나를 5항목으로 평가해 (검증된 결과, status)를 반환. 초안/최종본 평가 공통."""
    fallback = _dummy(draft)
    status: dict = {}
    raw = llm.complete_json(
        REVIEWER_SYSTEM, f"아래 기획서를 평가 기준에 따라 심사하세요.\n\n{draft}",
        fallback=fallback, model=model, status=status,
    )
    return _validate(raw, fallback), status


def _reviewer_model(state: ProjectState) -> str:
    """심판(Reviewer) 모델. 지정되면 작성자 모델과 분리해 '자기 채점 편향'을 완화한다(로드맵 Phase 4).

    미지정이면 작성 모델(state['model'])로 폴백하므로 기존 동작과 동일하다(회귀 없음).
    소스: state['reviewer_model'](API reviewer_model 필드 또는 env REVIEWER_MODEL, _prepare_run 에서 주입).
    """
    return (state.get("reviewer_model") or "").strip() or state.get("model", "")


def _split_note(state: ProjectState) -> str:
    """작성 모델과 다른 심판 모델을 썼을 때 로그에 '심판 분리' 표기(관측성)."""
    rm = (state.get("reviewer_model") or "").strip()
    return " · 심판 모델 분리" if rm and rm != (state.get("model") or "").strip() else ""


def reviewer(state: ProjectState) -> dict:
    """초안 평가. review_result(재작성 판단용)와 initial_review_result(기록용)에 저장."""
    model = _reviewer_model(state)
    result, status = _review(state.get("draft", ""), model)
    mode = llm.mode_label(status, model)
    logs = [
        f"[reviewer] 초안 평가 완료 (총점={result['total_score']}, {mode}{_split_note(state)})"
    ]
    return {"review_result": result, "initial_review_result": result, "logs": logs}


def final_reviewer(state: ProjectState) -> dict:
    """최종본(재작성·일관성 편집 후) 재평가.

    UI·이력에 표시되는 점수가 '실제로 보여주는 최종 문서'와 일치하도록, 초안 점수와
    별개로 최종본을 다시 채점해 final_review_result에 저장한다. 초안 대비 변화(Δ)도 로그에 남긴다.
    """
    model = _reviewer_model(state)
    result, status = _review(state.get("final_draft", "") or state.get("draft", ""), model)
    initial = state.get("initial_review_result") or state.get("review_result") or {}
    before = initial.get("total_score")
    delta = f", Δ{result['total_score'] - before:+d} vs 초안" if isinstance(before, int) else ""
    mode = llm.mode_label(status, model)
    logs = [
        f"[final_reviewer] 최종본 재평가 완료 (총점={result['total_score']}{delta}, {mode}{_split_note(state)})"
    ]
    return {"final_review_result": result, "logs": logs}
