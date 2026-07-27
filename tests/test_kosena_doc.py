"""AI 활용 로그 + KOSENA 7종 산출물 문서 조립 (체크포인트 3) — 실 LLM 없음.

가장 중요한 불변식: **기존 14섹션 기획서를 건드리지 않는다.** KOSENA 문서는 별도로 조립하고,
`final_draft`·`sections` 왕복·`section_revise`·`quality_gate` 는 그대로 남아야 한다.
"""
from __future__ import annotations

import pytest

from app.agents import kosena_research as rsc
from app.graph.workflow import run_workflow
from app.services import ai_log, kosena, kosena_doc, llm, sections


@pytest.fixture
def dummy(monkeypatch):
    monkeypatch.setattr(llm, "is_dummy", lambda: True)


@pytest.fixture
def state(dummy):
    return run_workflow({"project_name": "문서조립", "problem": "P"})


# ---- AI 활용 로그 ----

def test_ai_log_covers_every_artifact_agent(state):
    agents = {e["agent"] for e in state["ai_usage_log"]}
    for node in ("research", "competitor", "customer", "pestel", "swot",
                 "business_model", "risk", "kosena_industry", "kosena_roadmap", "verify"):
        assert node in agents, node


def test_ai_log_records_inputs_from_depends_on(state):
    """'무엇을 입력으로 썼는가'는 Artifact 의 depends_on 에서 온다(새 계측 없음)."""
    swot = next(e for e in state["ai_usage_log"] if e["agent"] == "swot")
    assert set(swot["inputs"]) == {"research_analysis", "competitor_analysis"}


def test_ai_log_marks_five_part_prompts(state):
    """KOSENA 표준 5단 구조(p19) 준수 여부가 로그에 표기돼야 한다."""
    ind = next(e for e in state["ai_usage_log"] if e["agent"] == "kosena_industry")
    assert ind["prompt"]["five_part_structure"] is True
    # 기존 Agent 프롬프트는 5단 구조가 아니다 — 그 사실도 정직하게 나와야 한다.
    swot = next(e for e in state["ai_usage_log"] if e["agent"] == "swot")
    assert swot["prompt"]["five_part_structure"] is False


def test_ai_log_reports_rejection_when_revision_is_reverted():
    """'채택 여부'의 핵심 — AI 재작성이 초안보다 나빠 되돌린 경우를 미채택으로 기록한다."""
    st = {"artifacts": [], "revision_strategy": "full", "reverted_from_revision": True}
    entry = next(e for e in ai_log.build(st) if e["agent"] == "revise")
    assert entry["adopted"] is False and "초안을 채택" in entry["note"]


def test_ai_log_reports_polish_skip_as_not_adopted():
    st = {"artifacts": [], "polish_applied": False, "polish_skip_reason": "표현 이슈 없음"}
    entry = next(e for e in ai_log.build(st) if e["agent"] == "polish")
    assert entry["adopted"] is False and "생략" in entry["note"]


def test_ai_log_markdown_states_what_is_not_logged(state):
    """user 프롬프트 원문을 안 남긴다는 사실을 문서에 밝혀야 한다(정직 표기)."""
    md = ai_log.to_markdown(state["ai_usage_log"])
    assert "user 프롬프트 원문은 남기지 않는다" in md
    assert "채택" in md and "select_best" in md


def test_ai_log_is_safe_on_empty_state():
    assert ai_log.build({}) and ai_log.build(None) == []      # type: ignore[arg-type]


# ---- 문서 조립 ----

def test_plan_contains_all_seven_deliverables(state):
    plan = state["kosena_plan"]
    for title in kosena_doc.DELIVERABLES:
        assert title in plan, title


def test_plan_leads_with_the_hypothesis_disclaimer(state):
    """정량 주장이 가설임을 **문서 머리에서** 밝힌다(p4·p20)."""
    plan = state["kosena_plan"]
    assert "가설" in plan[:1500] and "1차 자료로 검증되지 않" in plan or "검증되지 않" in plan[:1500]


def test_plan_embeds_compliance_and_ai_log(state):
    plan = state["kosena_plan"]
    assert "KOSENA 준수 현황" in plan and "AI 활용 로그" in plan


def test_deck_is_within_the_required_slide_range(state):
    """발표 15~20쪽(p4). `##` 하나가 슬라이드 하나가 된다(pptx_export 규칙)."""
    slides = state["kosena_deck"].count("\n## ")
    assert kosena.DECK_PAGES_MIN <= slides <= kosena.DECK_PAGES_MAX, slides


def test_deck_closes_with_limits_not_just_highlights(state):
    assert "한계와 다음 단계" in state["kosena_deck"]


# ---- 기존 문서를 건드리지 않는가 (핵심 불변식) ----

def test_existing_draft_is_untouched(state):
    """14섹션 기획서는 그대로여야 한다 — 재구성하면 왕복 불변식·section_revise 가 깨진다."""
    parsed = sections.parse_sections(state["final_draft"])
    assert parsed["valid"], parsed["reason"]
    assert state["quality_gate"]["checks"]["structure"] is True


def test_kosena_plan_is_separate_from_final_draft(state):
    assert state["kosena_plan"] != state["final_draft"]
    assert len(state["kosena_plan"]) > len(state["final_draft"])


def test_exporters_accept_the_assembled_markdown(state):
    """새 익스포터 없이 기존 함수가 그대로 처리해야 한다(설계 전제)."""
    from app.services import docx_export, pptx_export

    assert len(docx_export.docx_bytes(state["kosena_plan"])) > 0
    assert len(pptx_export.pptx_bytes(state["kosena_deck"], "KOSENA")) > 0


def test_build_is_safe_on_empty_state():
    assert kosena_doc.build({}) and kosena_doc.build(None) == ""      # type: ignore[arg-type]
    assert kosena_doc.build_deck(None) == ""                          # type: ignore[arg-type]


# ---- 실측에서 잡힌 회귀 ----

def test_competitor_groups_accept_objects_not_just_strings():
    """실 LLM 이 경쟁사를 객체로 반환해 분류가 통째로 0/0/0 이 됐던 회귀."""
    raw = {"competitor_groups": {"direct": [{"name": "A사"}, {"name": "B사"}, "C사"],
                                 "indirect": [{"company": "D사"}], "potential": ["E사"]}}
    out = rsc._validate(raw, rsc._dummy())
    assert out["competitor_groups"]["direct"] == ["A사", "B사", "C사"]
    assert out["competitor_groups"]["indirect"] == ["D사"]


def test_page_estimate_counts_lines_not_characters():
    """표가 많은 문서에서 글자 수 기준은 크게 빗나간다(실측 8.1쪽 vs 11.0쪽)."""
    table = "\n".join("| a | b |" for _ in range(45 * 35))     # 짧은 글자, 많은 줄
    r = kosena.evaluate({"kosena_plan": table})
    doc = next(c for c in r["checks"] if c["id"] == "doc_length")
    assert doc["status"] == kosena.OK and "추정" in doc["detail"]
