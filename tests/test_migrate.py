"""State 버전·옛 프로젝트 재조회 호환(로드맵 Phase 5) 테스트 — LLM 호출 없음."""
from __future__ import annotations

from app.services import migrate, sections, store


def _valid_draft() -> str:
    return "# P 기획서\n" + "\n".join(f"## {t}\n내용." for t in sections.SECTION_TITLES)


def test_upgrade_fills_defaults_and_tags_version():
    old = {"draft": "옛 초안", "review_result": {"total_score": 80}}   # 새 필드 없음
    up = migrate.upgrade_state(old)
    assert up["state_version"] == migrate.STATE_VERSION
    for key in ("revision_strategy", "polish_applied", "best_version",
                "reverted_from_revision", "reviewer_model", "evidence_registry"):
        assert key in up
    assert up["evidence_registry"] == [] and up["revision_strategy"] == "none"


def test_upgrade_preserves_existing_values():
    st = {"revision_strategy": "section", "best_version": "draft", "evidence_registry": [{"url": "x"}]}
    up = migrate.upgrade_state(st)
    assert up["revision_strategy"] == "section"     # 기존 값 보존
    assert up["best_version"] == "draft"
    assert up["evidence_registry"] == [{"url": "x"}]


def test_upgrade_is_idempotent():
    st = migrate.upgrade_state({"draft": "d"})
    once = dict(st)
    migrate.upgrade_state(st)
    assert st == once                                # 두 번 올려도 동일


def test_upgrade_recomputes_quality_gate_for_old_record():
    """옛 기록엔 quality_gate 가 없으므로 저장된 점수·검증·최종본으로 소급 계산한다."""
    old = {
        "final_draft": _valid_draft(),
        "final_review_result": {"total_score": 85, "issues": []},
        "verification_result": {"fact_total": 4, "fact_support_rate": 1.0},
    }
    up = migrate.upgrade_state(old)
    assert up["quality_gate"]["release_ready"] is True   # 소급 판정
    assert "checks" in up["quality_gate"]


def test_upgrade_fills_verification_summary():
    up = migrate.upgrade_state({"draft": "d"})
    assert up["verification_summary"]["scope"] == "search_snippet_only"


def test_upgrade_non_dict_is_safe():
    assert migrate.upgrade_state(None) is None
    assert migrate.upgrade_state("x") == "x"


def test_get_project_normalizes_old_record(tmp_path, monkeypatch):
    """저장 후 재조회 시 옛 레코드가 현재 스키마로 정규화된다(회귀 안전)."""
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "p.db")
    # 새 필드가 하나도 없는 '옛' 상태를 저장
    pid = store.save_run({
        "structured_input": {"project_name": "옛 프로젝트"},
        "final_draft": _valid_draft(),
        "final_review_result": {"total_score": 82, "issues": []},
        "verification_result": {"fact_total": 3, "fact_support_rate": 1.0},
        "logs": [],
    })
    state = store.get_project(pid)["state"]
    assert state["state_version"] == migrate.STATE_VERSION
    assert state["best_version"] == "revised"          # 기본값 채움
    assert state["quality_gate"]["release_ready"] is True   # 게이트 소급
    assert state["verification_summary"]["scope"] == "search_snippet_only"


def test_new_run_is_tagged_with_state_version(monkeypatch):
    monkeypatch.setenv("USE_DUMMY", "1")
    from app.graph.workflow import run_workflow
    state = run_workflow({"project_name": "버전태깅", "problem": "P"})
    assert state["state_version"] == migrate.STATE_VERSION
