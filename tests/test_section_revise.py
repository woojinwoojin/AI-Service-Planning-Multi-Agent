"""섹션 단위 수정 + 라우팅 + full-revise fallback 테스트 (로드맵 2-4 PR-7, LLM 호출 없음).

계획 문서(docs/병렬화_측정결과_및_PR7_계획.md) 3절 테스트 항목을 커버:
  1) 지정 섹션만 변경  2) 미지정 섹션 byte 동일  3) 같은 섹션 다중 이슈 → 1회 수정
  4) 참고자료 보존  5) 섹션 생성 실패 → full fallback  6) 대상 과다 → full
  7) 수정 후 14섹션 순서·개수 유지  10) 라우팅 분기
"""
from __future__ import annotations

import pytest

from app.agents import draft_writer
from app.graph import workflow
from app.services import sections
from tests.test_sections import make_draft


def _issue(sid: str, severity: str = "major", instr: str | None = None) -> dict:
    return {"issue_type": "x", "severity": severity,
            "target_section_id": sid, "revision_instruction": instr or f"{sid} 보강"}


def _state(targets, severity="major", draft=None, score=60) -> dict:
    issues = [_issue(t, severity) for t in targets]
    return {
        "draft": draft if draft is not None else make_draft(),
        "structured_input": {"project_name": "테스트"},
        "review_result": {"total_score": score, "issues": issues,
                          "revision_instructions": ["보강하라"]},
        "revision_count": 0,
        "logs": [],
    }


# ── plan_section_revision: 대상 판정·fallback 사유 ─────────────────────────

def test_plan_targets_major_issues():
    targets, reason = draft_writer.plan_section_revision(_state(["market_analysis", "differentiation"]))
    assert reason is None
    assert targets == ["market_analysis", "differentiation"]


def test_plan_no_targets_when_only_minor():
    _, reason = draft_writer.plan_section_revision(_state(["market_analysis"], severity="minor"))
    assert reason == "no_targets"


def test_plan_too_many_targets():
    st = _state(["overview", "background", "problem", "target_user", "market_analysis"])  # 5 > MAX(4)
    _, reason = draft_writer.plan_section_revision(st)
    assert reason == "too_many"


def test_plan_parse_fail_on_broken_draft():
    _, reason = draft_writer.plan_section_revision(_state(["market_analysis"], draft="제목 없는 평문"))
    assert reason.startswith("parse")


def test_plan_user_request_forces_full():
    st = _state(["market_analysis"])
    st["user_input"] = {"revision_request": "이 부분 바꿔줘"}
    _, reason = draft_writer.plan_section_revision(st)
    assert reason == "user_request"


def test_plan_dedup_same_section():
    st = _state(["market_analysis"])
    st["review_result"]["issues"].append(_issue("market_analysis", "critical", "또 다른 지시"))
    targets, reason = draft_writer.plan_section_revision(st)
    assert reason is None
    assert targets == ["market_analysis"]              # 같은 섹션 다중 이슈 → 1개 대상


# ── section_revise: 섹션만 교체 + 나머지 byte 동일 ─────────────────────────

def _dummy_llm(monkeypatch):
    monkeypatch.setattr(draft_writer.llm, "is_dummy", lambda: True)
    monkeypatch.setattr(draft_writer.llm, "complete_json", lambda *a, **k: k.get("fallback"))
    monkeypatch.setattr(draft_writer.llm, "complete_text", lambda *a, **k: k.get("fallback"))


def test_section_revise_changes_only_targets(monkeypatch):
    _dummy_llm(monkeypatch)
    orig = make_draft()
    out = draft_writer.section_revise(_state(["market_analysis", "differentiation"], draft=orig))
    assert out["revision_strategy"] == "section"
    assert set(out["revised_section_ids"]) == {"market_analysis", "differentiation"}
    assert out["revision_count"] == 1
    assert out["revision_fallback_reason"] is None

    op = sections.parse_sections(orig)
    np = sections.parse_sections(out["final_draft"])
    assert np["valid"] is True                          # 14섹션 순서·개수 유지(계획 테스트 7)
    for sid in sections.KNOWN_IDS:
        if sid in ("market_analysis", "differentiation"):
            assert "[더미 섹션 보완]" in sections.section_body(np, sid)   # 지정 섹션만 변경
        else:
            assert sections.section_body(np, sid) == sections.section_body(op, sid)  # byte 동일


def test_section_revise_preserves_references(monkeypatch):
    _dummy_llm(monkeypatch)
    out = draft_writer.section_revise(_state(["market_analysis"]))
    assert "http://a.example" in out["final_draft"]     # 참고자료 보존(계획 테스트 4)
    assert "http://b.example" in out["final_draft"]


def test_same_section_multiple_issues_revised_once(monkeypatch):
    _dummy_llm(monkeypatch)
    st = _state(["market_analysis"])
    st["review_result"]["issues"].append(_issue("market_analysis", "major", "추가 지시"))
    out = draft_writer.section_revise(st)
    assert out["revised_section_ids"] == ["market_analysis"]   # 계획 테스트 3


# ── full-revise fallback (계획 테스트 5·6) ─────────────────────────────────

def test_section_gen_failure_falls_back_to_full(monkeypatch):
    monkeypatch.setattr(draft_writer.llm, "is_dummy", lambda: True)
    monkeypatch.setattr(draft_writer.llm, "complete_json", lambda *a, **k: {"content": ""})  # 빈 본문 → 실패
    monkeypatch.setattr(draft_writer.llm, "complete_text", lambda *a, **k: k.get("fallback"))
    out = draft_writer.section_revise(_state(["market_analysis"]))
    assert out["revision_strategy"] == "full"
    assert out["revision_fallback_reason"] == "section_gen"
    assert out["revised_section_ids"] == []


def test_too_many_falls_back_to_full(monkeypatch):
    _dummy_llm(monkeypatch)
    st = _state(["overview", "background", "problem", "target_user", "market_analysis"])
    out = draft_writer.section_revise(st)
    assert out["revision_strategy"] == "full"
    assert out["revision_fallback_reason"] == "too_many"


def test_full_revise_records_reason_when_called_directly(monkeypatch):
    """라우터의 full 분기(직접 revise 호출)도 왜 전체 재작성인지 사유를 기록한다."""
    _dummy_llm(monkeypatch)
    st = _state([], score=50)                           # 이슈 없음 → no_targets
    out = draft_writer.revise(st)
    assert out["revision_strategy"] == "full"
    assert out["revision_fallback_reason"] == "no_targets"


# ── 라우팅 _route_revision (계획 테스트 10) ────────────────────────────────

def test_route_finalize_on_high_score():
    st = {"review_result": {"total_score": 95, "issues": []}, "revision_count": 0}
    assert workflow._route_revision(st) == "finalize"


def test_route_finalize_after_one_revision():
    st = _state(["market_analysis"], score=50)
    st["revision_count"] = 1
    assert workflow._route_revision(st) == "finalize"   # 자동 재작성 1회 제한


def test_route_section_revise_when_viable():
    assert workflow._route_revision(_state(["market_analysis"], score=50)) == "section_revise"


def test_route_full_revise_when_no_targets():
    st = {"draft": make_draft(), "review_result": {"total_score": 50, "issues": []},
          "revision_count": 0}
    assert workflow._route_revision(st) == "revise"


# ── E2E: 전체 그래프에서 섹션 단위 수정(직렬·병렬 동일 동작, 계획 테스트 8·9·10) ──────

@pytest.mark.parametrize("mode", ["serial", "parallel"])
def test_full_workflow_uses_section_revise_in_dummy(monkeypatch, mode):
    monkeypatch.setenv("USE_DUMMY", "1")   # 결정론적 더미 실행(LLM 미호출)
    state = workflow.run_workflow(
        {"project_name": "통합테스트", "description": "D", "target_user": "U", "problem": "P"},
        workflow_mode=mode,
    )
    # 더미 reviewer 이슈(market_analysis·differentiation)로 섹션 단위 수정 경로를 탄다
    assert state["revision_strategy"] == "section"
    assert set(state["revised_section_ids"]) == {"market_analysis", "differentiation"}
    assert state["revision_count"] == 1
    # 수정 후 최종본은 14섹션 유지, final_reviewer·verify 가 '새 문서'를 평가(계획 테스트 8)
    assert sections.parse_sections(state["final_draft"])["valid"] is True
    assert state["final_review_result"]
    assert state["verification_result"]
    assert state["workflow_mode"] == mode   # 직렬·병렬 모두 동일 동작(계획 테스트 10)
