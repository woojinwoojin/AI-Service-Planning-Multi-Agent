"""품질 게이트 — 최종본이 '출력 가능한 수준'인지 판정해 State·응답에 표면화 (로드맵 Phase 4).

단순 점수가 아니라 **출력 가능 여부를 결정하는 게이트**다. 로드맵 스케치:

    release_ready = (
        score >= calibrated_threshold
        and critical_issues == 0
        and major_issues <= 1
        and structure_valid
        and evidence_coverage >= 0.8
    )

- 점수·이슈는 **최종본 재평가(final_review_result)** 기준(초안 아님) — 실제 보여줄 문서의 상태.
- structure_valid: 최종본이 고정 서식 14섹션을 지키는지.
- evidence_coverage: 사실 주장 중 근거로 뒷받침된 비율(verifier.fact_support_rate). 검증할 사실
  주장이 없으면 공허 충족(1.0).
- 미통과 시 **막은 이유(blocking_reasons)** 와 **미해결 이슈(unresolved_issues)** 를 함께 실어,
  사용자가 무엇을 고쳐야 하는지 알 수 있게 한다(완료 게이트: unresolved issue 를 UI 계약에 포함).

임계값은 상수로 두되, 사람 기준선 보정 전이므로 **잠정 기본값**임을 명시한다(calibrated_threshold 는 후속).
"""
from __future__ import annotations

from app.services import sections

# 잠정 임계값(사람↔LLM 보정 전 기본값). 보정 후 조정 대상.
SCORE_MIN = 80              # 최종 총점(100) 하한
MAJOR_MAX = 1               # 허용 major 이슈 수
EVIDENCE_COVERAGE_MIN = 0.8  # 사실 주장 근거 충족률 하한

_SEVERITY_ORDER = {"critical": 0, "major": 1, "minor": 2}


def _evidence_coverage(state: dict) -> float:
    """사실 주장 근거 충족률. 검증 결과가 없으면 0.0, 사실 주장이 없으면 1.0(공허 충족)."""
    vr = state.get("verification_result") or {}
    if not vr:
        return 0.0
    fact_total = vr.get("fact_total")
    if fact_total is None:                       # 옛 데이터: Tier2 지표 없음 → support_rate 대체
        return float(vr.get("support_rate", 0.0) or 0.0)
    if fact_total == 0:                          # 검증할 사실 주장이 없음
        return 1.0
    return float(vr.get("fact_support_rate", 0.0) or 0.0)


def _unresolved_issues(issues: list) -> list[dict]:
    """최종본의 critical/major 이슈를 사용자 안내용으로 정리(심각도순)."""
    out: list[dict] = []
    for it in issues or []:
        if not isinstance(it, dict) or it.get("severity") not in ("critical", "major"):
            continue
        sid = it.get("target_section_id")
        out.append({
            "severity": it.get("severity"),
            "section_id": sid,
            "section_title": sections.ID_TO_TITLE.get(sid, sid),
            "issue_type": it.get("issue_type", ""),
            "instruction": it.get("revision_instruction") or it.get("description", ""),
        })
    out.sort(key=lambda i: _SEVERITY_ORDER.get(i["severity"], 9))
    return out


def evaluate(state: dict) -> dict:
    """최종본 상태로 품질 게이트를 판정한다(로드맵 Phase 4). 실행 종료 후처리에서 호출.

    반환:
      {release_ready, checks{...bool}, metrics{...}, thresholds{...},
       unresolved_issues[...], blocking_reasons[...]}
    """
    review = state.get("final_review_result") or state.get("review_result") or {}
    score = review.get("total_score", 0) or 0
    issues = review.get("issues") or []
    critical = sum(1 for i in issues if isinstance(i, dict) and i.get("severity") == "critical")
    major = sum(1 for i in issues if isinstance(i, dict) and i.get("severity") == "major")

    final_draft = state.get("final_draft") or state.get("draft") or ""
    structure_valid = sections.parse_sections(final_draft)["valid"]
    coverage = round(_evidence_coverage(state), 3)

    checks = {
        "score": score >= SCORE_MIN,
        "critical_issues": critical == 0,
        "major_issues": major <= MAJOR_MAX,
        "structure": structure_valid,
        "evidence": coverage >= EVIDENCE_COVERAGE_MIN,
    }
    release_ready = all(checks.values())
    return {
        "release_ready": release_ready,
        "checks": checks,
        "blocking_reasons": [k for k, ok in checks.items() if not ok],
        "metrics": {
            "score": score,
            "critical_count": critical,
            "major_count": major,
            "structure_valid": structure_valid,
            "evidence_coverage": coverage,
        },
        "thresholds": {
            "score_min": SCORE_MIN,
            "major_max": MAJOR_MAX,
            "evidence_coverage_min": EVIDENCE_COVERAGE_MIN,
            "calibrated": False,     # 사람 기준선 보정 전 잠정값
        },
        "unresolved_issues": _unresolved_issues(issues),
    }
