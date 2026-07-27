"""AI 활용 로그 + KOSENA 7종 산출물 문서 조립 (체크포인트 3) — 실 LLM 없음.

가장 중요한 불변식: **기존 14섹션 기획서를 건드리지 않는다.** KOSENA 문서는 별도로 조립하고,
`final_draft`·`sections` 왕복·`section_revise`·`quality_gate` 는 그대로 남아야 한다.
"""
from __future__ import annotations

import io

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


# ---- 판정↔문서 일관성 (순환 의존을 조립→판정→재조립으로 끊는다) ----

def test_document_reports_the_same_compliance_as_the_final_state(state):
    """본문에 실린 준수율이 최종 State 의 준수율과 **같아야** 한다.

    조립 시점에 판정이 없으면 `build()` 가 즉석 판정을 돌렸고, 그때는 `kosena_plan` 이 아직
    State 에 없어 14섹션 초안으로 분량·가설 표기를 재고 있었다. 그래서 문서가 말하는 준수율과
    최종 State 의 준수율이 어긋났다(문서 26/28 · State 27/28 같은 형태).
    """
    assert state["kosena_compliance"]["summary"] in state["kosena_plan"]


def test_deck_reports_the_decided_compliance_not_undecided(state):
    """발표자료는 판정 **뒤**에 조립돼야 한다 — 이전에는 '(미판정)' 이 그대로 실렸다."""
    assert state["kosena_compliance"]["summary"] in state["kosena_deck"]
    assert "(미판정)" not in state["kosena_deck"]


def test_page_estimate_survives_reassembly(state):
    """판정 전/후 조립의 **줄 수가 같아야** 판정이 실제 최종 문서의 분량을 말한 것이 된다.

    `_compliance_section` 이 판정 전에도 같은 행 수로 렌더링하기 때문에 성립한다. 이게 깨지면
    '약 N쪽(추정)' 이 실제로 내려받는 문서와 다른 문서의 분량을 가리킨다.
    """
    before = dict(state)
    before.pop("kosena_compliance")                       # 판정 전 상태를 재현
    first_pass = len(kosena_doc.build(before).splitlines())
    assert first_pass == len(state["kosena_plan"].splitlines())


def test_revise_reassembles_kosena_outputs(state):
    """`/revise` 후에도 KOSENA 산출물이 다시 조립돼야 한다.

    고치지 않으면 14섹션 기획서만 수정되고 KOSENA 문서·준수 판정·AI 로그는 수정 전 내용으로
    남는다. 화면(STEP 4)이 그 값을 그대로 보여주므로 옛 산출물이 눈에 보인다.
    """
    from app.graph.workflow import rerun_finalizers

    for key in ("kosena_plan", "kosena_deck", "kosena_compliance", "ai_usage_log"):
        state.pop(key)                                    # 재조립하지 않으면 비어 있을 것이다
    rerun_finalizers(state)
    assert state["kosena_plan"] and state["kosena_deck"] and state["ai_usage_log"]
    assert state["kosena_compliance"]["summary"] in state["kosena_plan"]


def test_deck_is_within_the_required_slide_range(state):
    """발표 15~20쪽(p4).

    Markdown 의 `##` 개수가 아니라 **실제로 만들어진 PPTX 의 슬라이드 수**를 센다. `##` 만 세면
    맨 앞의 `#` 제목 슬라이드가 빠져 한 장씩 적게 나오고, 실제로 그 한 장 차이 때문에 21장짜리
    발표자료가 상한 검사를 통과했다. 요건은 파일의 장수에 대한 것이므로 파일을 본다.
    """
    from pptx import Presentation
    from app.services import pptx_export

    deck = Presentation(io.BytesIO(pptx_export.pptx_bytes(state["kosena_deck"], "KOSENA")))
    n = len(deck.slides)
    assert kosena.DECK_PAGES_MIN <= n <= kosena.DECK_PAGES_MAX, n


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


# ---- AI 활용 로그가 실제 입력·산출을 담는가 (KOSENA p4) ----

def test_kosena_nodes_record_their_real_inputs(state):
    """KOSENA 노드는 Artifact 를 내지 않는다 — 그래서 예전엔 inputs 가 비어 있었다.

    "프롬프트 템플릿 있음 · 채택 Yes" 만으로는 KOSENA 가 요구하는
    *프롬프트 + 입력 + 응답 + 채택 여부*(p4)를 남겼다고 말할 수 없다.
    """
    log = {e["agent"]: e for e in state["ai_usage_log"]}
    assert set(log["kosena_research"]["inputs"]) == {
        "research_analysis", "customer_analysis", "competitor_analysis"}
    assert "kosena.ksf" in log["kosena_model"]["inputs"]        # 앞 KOSENA 노드의 결과도 입력이다


def test_kosena_nodes_record_the_fields_they_produced(state):
    out = next(e for e in state["ai_usage_log"] if e["agent"] == "kosena_research")["output"]
    assert out["state_key"] == "kosena"
    assert {"personas", "cjm", "market_sizing", "competitor_comparison"} <= set(out["produced_fields"])


def test_every_log_entry_carries_all_five_kosena_items(state):
    """준수 검사가 보는 5항목(프롬프트·입력·산출·검증·채택)이 **모든** 항목에 있어야 한다."""
    for e in state["ai_usage_log"]:
        out = e["output"]
        assert e["prompt"]["template"], e["agent"]
        assert e["inputs"], e["agent"]
        assert out.get("artifact") or out.get("produced_fields"), e["agent"]
        assert e["verification"]["status"], e["agent"]
        assert "adopted" in e, e["agent"]


def test_produced_fields_do_not_drift_from_the_agents(state):
    """선언한 `produces` 합집합이 실제 `state["kosena"]` 키를 덮어야 한다.

    Agent 출력 키가 늘었는데 선언을 안 고치면 로그가 산출물을 조용히 누락한다(손으로 적은
    목록의 유일한 위험이라 여기서 고정한다).
    """
    from app.services import ai_log as mod

    declared = {f for s in mod._DOC_NODES if s["state_key"] == "kosena" for f in s["produces"]}
    assert set(state["kosena"]) <= declared, set(state["kosena"]) - declared


def test_failed_node_does_not_claim_produced_fields():
    """실패한 노드가 "10개 필드를 만들었다"고 적히면 로그가 거짓이 된다."""
    st = {"artifacts": [], "kosena": {}, "failed_nodes": ["kosena_roadmap"]}
    entry = next(e for e in ai_log.build(st) if e["agent"] == "kosena_roadmap")
    assert entry["output"]["produced_fields"] == []
    assert entry["adopted"] is False
