"""Artifact Contract v1(로드맵 2-2, PR 1) 단위 테스트 — LLM 호출 없음, 결정론적.

이 PR의 성공 기준은 '기존 키 삭제'가 아니라 **Artifact가 기존 결과와 100% 동일하게,
결정적으로 생성되는 것**이다. 그래서 아래 테스트는 content 동일성·결정성·무부작용에
집중한다(생성 배선은 PR 2, 정합성 리포트는 PR 3).
"""
from __future__ import annotations

from copy import deepcopy

from app.schemas import artifact
from app.services import sections


def _state() -> dict:
    """7개 Agent 결과가 모두 있는 정상 실행 State(축약)."""
    return {
        "research_result": {"market_overview": "성장 중", "industry_trends": ["A", "B"]},
        "competitor_result": {"competitors": [{"name": "X"}], "positioning": "니치"},
        "customer_result": {"target_persona": "20대", "pain_points": ["불편"]},
        "pestel_result": {"political": {"content": "규제"}},
        "swot_result": {"strengths": ["빠름"]},
        "business_model_result": {"revenue_streams": ["구독"]},
        "risk_result": {"risks": [{"category": "시장"}]},
        "evidence_registry": [
            {"evidence_id": "ev1", "url": "https://a.com", "source_agents": ["research"]},
            {"evidence_id": "ev2", "url": "https://b.com", "source_agents": ["competitor"]},
            {"evidence_id": "ev3", "url": "https://c.com", "source_agents": ["research_gap"]},
            {"evidence_id": "ev4", "url": "https://d.com",
             "source_agents": ["research", "competitor"]},
        ],
    }


# ---- 생성 기본 ----

def test_builds_seven_artifacts_in_topological_order():
    arts = artifact.build_artifacts_from_legacy(_state())
    assert len(arts) == 7
    assert [a["artifact_id"] for a in arts] == artifact.ARTIFACT_IDS
    # 의존 대상은 목록에서 항상 자기보다 앞에 있어야 한다(위상 순서).
    seen: set[str] = set()
    for a in arts:
        for dep in a["depends_on"]:
            assert dep in seen, f"{a['artifact_id']} 의 의존 {dep} 가 뒤에 있음"
        seen.add(a["artifact_id"])


def test_content_matches_legacy_exactly():
    """핵심 성공 기준 — 7개 모두 기존 평면 키와 내용이 같아야 한다."""
    st = _state()
    arts = artifact.build_artifacts_from_legacy(st)
    assert len(arts) == 7
    for a in arts:
        legacy_key = a["metadata"]["legacy_key"]
        assert a["content"] == st[legacy_key], legacy_key


def test_artifact_ids_and_types_are_unique():
    arts = artifact.build_artifacts_from_legacy(_state())
    assert len({a["artifact_id"] for a in arts}) == 7
    assert len({a["artifact_type"] for a in arts}) == 7


def test_deterministic_across_calls():
    st = _state()
    assert artifact.build_artifacts_from_legacy(st) == artifact.build_artifacts_from_legacy(st)


def test_does_not_mutate_input_state():
    st = _state()
    before = deepcopy(st)
    artifact.build_artifacts_from_legacy(st)
    assert st == before


def test_content_is_deepcopied_not_aliased():
    """Artifact content 를 고쳐도 원본 State 가 따라 바뀌면 안 된다(숨은 결합 방지)."""
    st = _state()
    arts = artifact.build_artifacts_from_legacy(st)
    research = arts[0]
    research["content"]["market_overview"] = "변조"
    assert st["research_result"]["market_overview"] == "성장 중"


# ---- 근거 귀속 ----

def test_evidence_attributed_to_searching_agents_only():
    arts = {a["artifact_type"]: a for a in artifact.build_artifacts_from_legacy(_state())}
    # research 는 자기 근거 + research_gap(2-5 추가 검색)까지 귀속받는다.
    assert arts["research_analysis"]["evidence_ids"] == ["ev1", "ev3", "ev4"]
    assert arts["competitor_analysis"]["evidence_ids"] == ["ev2", "ev4"]
    # 검색하지 않는 Agent 는 직접 근거가 없다(상속 관계는 depends_on 으로 표현).
    for t in ("customer_analysis", "pestel_analysis", "swot_analysis",
              "business_model_analysis", "risk_analysis"):
        assert arts[t]["evidence_ids"] == [], t


def test_evidence_ids_skip_unnormalized_entries():
    """normalize() 전 원시 항목(evidence_id 없음)은 임의로 번호를 매기지 않고 건너뛴다."""
    st = _state()
    st["evidence_registry"] = [
        {"url": "https://raw.com", "source_agents": ["research"]},   # id 없음
        {"evidence_id": "ev1", "url": "https://a.com", "source_agents": ["research"]},
    ]
    arts = artifact.build_artifacts_from_legacy(st)
    assert arts[0]["evidence_ids"] == ["ev1"]


def test_evidence_ids_for_empty_registry():
    assert artifact.evidence_ids_for([], ["research"]) == []
    assert artifact.evidence_ids_for(None, ["research"]) == []
    # 검색하지 않는 Agent 는 레지스트리가 있어도 빈 목록.
    assert artifact.evidence_ids_for(
        [{"evidence_id": "ev1", "source_agents": ["research"]}], []) == []


# ---- status ----

def test_status_complete_for_normal_run():
    arts = artifact.build_artifacts_from_legacy(_state())
    assert {a["status"] for a in arts} == {artifact.STATUS_COMPLETE}


def test_status_missing_when_result_absent():
    st = _state()
    del st["swot_result"]
    st["risk_result"] = {}
    arts = {a["artifact_type"]: a for a in artifact.build_artifacts_from_legacy(st)}
    assert arts["swot_analysis"]["status"] == artifact.STATUS_MISSING
    assert arts["swot_analysis"]["content"] == {}
    assert arts["risk_analysis"]["status"] == artifact.STATUS_MISSING


def test_status_reflects_failed_and_fallback_nodes():
    """owner_agent 가 LangGraph 노드 이름과 같으므로 실행 결말을 그대로 대조할 수 있다."""
    st = _state()
    st["failed_nodes"] = ["customer"]
    st["fallback_nodes"] = ["research"]
    arts = {a["artifact_type"]: a for a in artifact.build_artifacts_from_legacy(st)}
    assert arts["customer_analysis"]["status"] == artifact.STATUS_FAILED
    # fallback 은 산출물이 있어도 붙는다 — '어떻게 만들어졌는지'를 먼저 알린다.
    assert arts["research_analysis"]["status"] == artifact.STATUS_FALLBACK
    assert arts["research_analysis"]["content"]


def test_empty_state_still_yields_seven_missing_artifacts():
    """옛 기록·미실행 State 여도 개수는 고정(PR 3 정합성 검사를 단순하게 유지)."""
    arts = artifact.build_artifacts_from_legacy({})
    assert len(arts) == 7
    assert {a["status"] for a in arts} == {artifact.STATUS_MISSING}
    assert all(a["content"] == {} for a in arts)


def test_non_dict_state_is_safe():
    assert artifact.build_artifacts_from_legacy(None) == []
    assert artifact.build_artifacts_from_legacy("nope") == []


# ---- 매핑이 실제 코드와 어긋나지 않는지 ----

def test_target_sections_are_real_section_ids():
    """섹션 ID 는 sections.SECTION_SPECS(14섹션 단일 진실원천)에 실재해야 한다."""
    for spec in artifact.LEGACY_ARTIFACT_SPECS:
        for sid in spec["target_sections"]:
            assert sid in sections.KNOWN_IDS, sid


def test_depends_on_targets_exist():
    known = set(artifact.ARTIFACT_IDS)
    for spec in artifact.LEGACY_ARTIFACT_SPECS:
        for dep in spec["depends_on"]:
            assert dep in known, dep


def test_legacy_keys_match_project_state_fields():
    """평면 키 7개가 ProjectState 에 실재해야 한다(키 이름 오타 방지)."""
    from app.schemas.state import ProjectState

    fields = set(ProjectState.__annotations__)
    for key in artifact.LEGACY_KEYS:
        assert key in fields, key


def test_owner_agents_are_graph_node_names():
    """owner_agent 가 실제 LangGraph 노드 이름과 일치해야 failed/fallback 대조가 성립한다."""
    from pathlib import Path

    from app.graph import workflow

    # cwd 에 의존하지 않도록 모듈 위치에서 경로를 얻는다.
    src = Path(workflow.__file__).read_text(encoding="utf-8")
    for spec in artifact.LEGACY_ARTIFACT_SPECS:
        assert f'g.add_node("{spec["owner_agent"]}"' in src, spec["owner_agent"]


# ---- selector ----

def test_find_artifact_locates_by_type():
    st = _state()
    st["artifacts"] = artifact.build_artifacts_from_legacy(st)
    found = artifact.find_artifact(st, "research_analysis")
    assert found is not None and found["artifact_id"] == "artifact-research"
    assert artifact.find_artifact(st, "없는유형") is None


def test_content_reads_artifact_in_prefer_mode():
    """모드별 동작 상세는 test_artifact_read_mode.py — 여기서는 selector 배선만 확인."""
    st = _state()
    st["artifacts"] = artifact.build_artifacts_from_legacy(st)
    st["artifacts"][0]["content"] = {"market_overview": "Artifact 쪽 값"}
    got = artifact.get_artifact_content(st, "research_analysis", "research_result",
                                        mode=artifact.READ_PREFER_ARTIFACT)
    assert got == {"market_overview": "Artifact 쪽 값"}


def test_get_artifact_content_falls_back_to_legacy_key():
    """Artifact 가 아직 없는 옛 프로젝트에서도 그대로 동작해야 한다(회귀 0)."""
    st = _state()  # artifacts 없음
    assert artifact.find_artifact(st, "research_analysis") is None
    got = artifact.get_artifact_content(st, "research_analysis", "research_result",
                                        mode=artifact.READ_PREFER_ARTIFACT)
    assert got == st["research_result"]


def test_get_artifact_content_falls_back_when_artifact_empty():
    st = _state()
    st["artifacts"] = artifact.build_artifacts_from_legacy(st)
    for a in st["artifacts"]:
        if a["artifact_type"] == "research_analysis":
            a["content"] = {}
    assert artifact.get_artifact_content(
        st, "research_analysis", "research_result",
        mode=artifact.READ_PREFER_ARTIFACT) == st["research_result"]


def test_selector_safe_on_missing_state():
    assert artifact.find_artifact(None, "research_analysis") is None
    assert artifact.get_artifact_content({}, "research_analysis", "research_result") == {}
