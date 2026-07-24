"""품질 게이트(로드맵 Phase 4) 테스트 — 출력 가능 여부 판정 (LLM 호출 없음)."""
from __future__ import annotations

from app.services import quality_gate, sections


def _valid_draft() -> str:
    return "# P 기획서\n" + "\n".join(f"## {t}\n내용입니다." for t in sections.SECTION_TITLES)


def _passing_state(**over) -> dict:
    """모든 게이트 기준을 통과하는 기본 state. over 로 일부만 바꿔 실패 케이스를 만든다."""
    state = {
        "final_draft": _valid_draft(),
        "final_review_result": {"total_score": 85, "issues": []},
        "verification_result": {"fact_total": 5, "fact_supported": 5,
                                "fact_support_rate": 1.0, "support_rate": 1.0},
    }
    state.update(over)
    return state


def test_release_ready_when_all_pass():
    g = quality_gate.evaluate(_passing_state())
    assert g["release_ready"] is True
    assert g["blocking_reasons"] == []
    assert all(g["checks"].values())


def test_blocks_on_low_score():
    g = quality_gate.evaluate(_passing_state(
        final_review_result={"total_score": 70, "issues": []}))
    assert g["release_ready"] is False
    assert "score" in g["blocking_reasons"]


def test_blocks_on_critical_issue():
    g = quality_gate.evaluate(_passing_state(
        final_review_result={"total_score": 90, "issues": [
            {"severity": "critical", "target_section_id": "market_analysis",
             "revision_instruction": "치명적 근거 오류 수정"}]}))
    assert g["release_ready"] is False
    assert "critical_issues" in g["blocking_reasons"]
    assert g["metrics"]["critical_count"] == 1


def test_blocks_on_too_many_major():
    issues = [{"severity": "major", "target_section_id": "service", "revision_instruction": f"m{i}"}
              for i in range(2)]                       # major 2 > MAJOR_MAX(1)
    g = quality_gate.evaluate(_passing_state(
        final_review_result={"total_score": 90, "issues": issues}))
    assert g["release_ready"] is False
    assert "major_issues" in g["blocking_reasons"]


def test_blocks_on_broken_structure():
    g = quality_gate.evaluate(_passing_state(final_draft="# P 기획서\n## 프로젝트 개요\n일부만"))
    assert g["release_ready"] is False
    assert "structure" in g["blocking_reasons"]
    assert g["metrics"]["structure_valid"] is False


def test_blocks_on_low_evidence():
    g = quality_gate.evaluate(_passing_state(
        verification_result={"fact_total": 5, "fact_support_rate": 0.4}))
    assert g["release_ready"] is False
    assert "evidence" in g["blocking_reasons"]
    assert g["metrics"]["evidence_coverage"] == 0.4


def test_evidence_coverage_vacuous_when_no_fact_claims():
    """검증할 사실 주장이 없으면(추론·제안만) 근거 충족은 공허하게 통과(1.0)."""
    g = quality_gate.evaluate(_passing_state(
        verification_result={"fact_total": 0, "fact_support_rate": 0.0}))
    assert g["metrics"]["evidence_coverage"] == 1.0
    assert g["checks"]["evidence"] is True


def test_evidence_coverage_zero_when_no_verification():
    g = quality_gate.evaluate(_passing_state(verification_result={}))
    assert g["metrics"]["evidence_coverage"] == 0.0
    assert "evidence" in g["blocking_reasons"]


def test_unresolved_issues_only_critical_major_sorted():
    issues = [
        {"severity": "minor", "target_section_id": "service", "revision_instruction": "사소"},
        {"severity": "major", "target_section_id": "revenue_model", "revision_instruction": "주요"},
        {"severity": "critical", "target_section_id": "market_analysis", "revision_instruction": "치명"},
    ]
    g = quality_gate.evaluate(_passing_state(
        final_review_result={"total_score": 90, "issues": issues}))
    un = g["unresolved_issues"]
    assert [i["severity"] for i in un] == ["critical", "major"]     # minor 제외 + 심각도순
    assert un[0]["section_title"] == "시장 및 산업 분석"            # 섹션 ID → 표시 제목


def test_falls_back_to_review_result_when_no_final():
    """final_review_result 가 없으면 review_result(초안 평가)로 판정(회귀 안전)."""
    st = _passing_state()
    del st["final_review_result"]
    st["review_result"] = {"total_score": 85, "issues": []}
    assert quality_gate.evaluate(st)["release_ready"] is True
