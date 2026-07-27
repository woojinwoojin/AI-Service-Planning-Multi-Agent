"""KOSENA M2·M3 Agent 2종 (페르소나·CJM·시장사이징 / VPC·MVP·Epic-Story) — 실 LLM 없음.

M1 Agent 테스트와 같은 원칙 — **지어내지 않는가**를 중심으로 본다. 더해서 이 두 Agent 에만
있는 요건을 따로 고정한다:

  - `wont` 는 비어 있어도 **키를 남겨야** 한다('이번 범위 제외'를 명시하는 칸, p17)
  - Acceptance Criteria 는 Given-When-Then **세 요소를 모두** 채워야 한다(p18)
  - 페르소나는 **가설·미검증**임을 밝혀야 한다(인터뷰를 못 하므로, p4·p20)
"""
from __future__ import annotations

import pytest

from app.agents import kosena_research as rsc
from app.agents import kosena_roadmap as rdm
from app.graph.workflow import run_workflow
from app.services import kosena, llm


@pytest.fixture
def dummy(monkeypatch):
    monkeypatch.setattr(llm, "is_dummy", lambda: True)


# ---- 지어내지 않는가 ----

def test_research_keeps_short_lists():
    out = rsc._validate({"personas": [{"name": "A", "demographics": "d"}],
                         "comparison_criteria": ["c1", "c2"]}, rsc._dummy())
    assert len(out["personas"]) == 1                    # 2종으로 부풀리지 않는다
    assert out["comparison_criteria"] == ["c1", "c2"]   # 10개로 채우지 않는다
    r = kosena.evaluate({"kosena": out})
    assert next(c for c in r["checks"] if c["id"] == "personas_2")["status"] == kosena.PARTIAL


def test_research_drops_positioning_map_without_axes():
    """축이 없으면 '2축 맵'이 아니다 — 좌표만 있고 축이 없으면 버린다.

    (다른 필드를 하나 채워 둔다. 전부 비면 '아무것도 못 받았다'로 보고 fallback 이 도는데,
    그건 별도 동작이라 여기서 검증하려는 것과 섞이면 안 된다.)
    """
    out = rsc._validate({"comparison_criteria": ["가격"],
                         "positioning_map": {"points": [{"name": "A", "x": 1, "y": 2}]}},
                        rsc._dummy())
    assert out["positioning_map"] == {}


def test_research_ignores_non_numeric_points():
    raw = {"positioning_map": {"x_axis": "가격", "y_axis": "기능",
                               "points": [{"name": "A", "x": "비쌈", "y": 2},
                                          {"name": "자사", "x": 3, "y": 4}]}}
    out = rsc._validate(raw, rsc._dummy())
    assert [p["name"] for p in out["positioning_map"]["points"]] == ["자사"]


def test_roadmap_caps_features_and_use_cases():
    out = rdm._validate({"core_features": [{"name": f"f{i}"} for i in range(12)],
                         "use_cases": [{"actor": f"a{i}"} for i in range(9)]}, rdm._dummy())
    assert len(out["core_features"]) == rdm.FEATURE_MAX
    assert len(out["use_cases"]) == rdm.USE_CASE_COUNT


def test_roadmap_keeps_wont_key_even_when_empty():
    """`wont` 는 '이번 범위 제외'를 명시하는 칸이라 비어도 키가 남아야 한다(p17)."""
    out = rdm._validate({"moscow": {"must": ["a"]}}, rdm._dummy())
    assert set(out["moscow"]) == set(rdm.MOSCOW_KEYS)
    assert out["moscow"]["wont"] == []
    assert kosena.evaluate({"kosena": out})["checks"][18]["status"] == kosena.OK


def test_roadmap_keeps_incomplete_acceptance_criteria_visible():
    """AC 가 GWT 를 다 못 채웠으면 **채운 척하지 않고** 부분 충족으로 보이게 둔다."""
    out = rdm._validate({"epics": [{"name": "E", "stories": [{"story": "s", "given": "g"}]}]},
                        rdm._dummy())
    assert out["epics"][0]["stories"][0]["then"] == ""
    r = kosena.evaluate({"kosena": out})
    assert next(c for c in r["checks"] if c["id"] == "epic_story_ac")["status"] == kosena.PARTIAL


def test_validate_falls_back_only_when_everything_is_empty():
    assert rsc._validate({}, rsc._dummy())["personas"] == rsc._dummy()["personas"]
    assert rdm._validate("문자열", rdm._dummy())["vpc"] == rdm._dummy()["vpc"]  # type: ignore[arg-type]


def test_persona_dummy_labels_itself_as_hypothesis():
    """인터뷰를 못 하므로 페르소나는 **가설**임을 밝혀야 한다(p4·p20)."""
    text = str(rsc._dummy()["personas"])
    assert "가설" in text or "미검증" in text


def test_prompts_require_hypothesis_labeling_and_five_part_structure():
    from app.prompts.templates import KOSENA_RESEARCH_SYSTEM, KOSENA_ROADMAP_SYSTEM

    for tpl in (KOSENA_RESEARCH_SYSTEM, KOSENA_ROADMAP_SYSTEM):
        for part in ("[역할]", "[입력]", "[요구사항]", "[출력 형식]", "[검증 조건]"):
            assert part in tpl, part
    # 리서치 프롬프트는 '인터뷰 미수행 → 가설 표기'를 명시적으로 요구해야 한다.
    assert "가설" in KOSENA_RESEARCH_SYSTEM and "추정" in KOSENA_RESEARCH_SYSTEM


# ---- 워크플로 통합 ----

@pytest.mark.parametrize("workflow_mode", ["serial", "parallel"])
def test_four_kosena_nodes_run_once_each(dummy, workflow_mode):
    state = run_workflow({"project_name": "4노드", "problem": "P"}, workflow_mode=workflow_mode)
    assert not state["failed_nodes"]
    logs = "\n".join(state["logs"])
    for node in ("kosena_industry", "kosena_model", "kosena_research", "kosena_roadmap"):
        assert logs.count(f"[{node}]") == 1, node


def test_all_three_modules_are_satisfied(dummy):
    """이 PR 의 목표 — M1·M2·M3 24항목이 전부 충족으로 바뀐다."""
    state = run_workflow({"project_name": "전모듈", "problem": "P"})
    checks = state["kosena_compliance"]["checks"]
    for module in ("M1", "M2", "M3"):
        rows = [c for c in checks if c["module"] == module]
        assert all(c["status"] == kosena.OK for c in rows), \
            (module, [c["title"] for c in rows if c["status"] != kosena.OK])


def test_later_nodes_do_not_erase_earlier_kosena_output(dummy):
    """4개 노드가 같은 `kosena` 키에 써도 앞 결과가 전부 남아야 한다(reducer)."""
    k = run_workflow({"project_name": "리듀서4", "problem": "P"})["kosena"]
    assert k.get("ksf") and k.get("lean_canvas")          # industry · model
    assert k.get("personas") and k.get("wireframes")      # research · roadmap


def test_timing_accounts_for_all_kosena_nodes(dummy):
    """새 노드가 stage 버킷에 없으면 coverage 가 조용히 떨어진다(B 에서 겪은 회귀)."""
    from app.services import timing

    t = run_workflow({"project_name": "계측", "problem": "P"})["timing"]
    assert "kosena_block" in t["stages"]
    assert len(timing.KOSENA_NODES) == 4
