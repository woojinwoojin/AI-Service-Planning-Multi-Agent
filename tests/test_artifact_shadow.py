"""Shadow Artifact 생성 배선(로드맵 2-2 PR 2) 테스트 — LLM 호출 없음(더미), 결정론적.

PR 1 은 변환기 자체를 검증했다. 여기서는 **실제 실행 경로에 붙었는지**를 본다:
신규 실행 / 수동 수정(/revise) / 옛 기록 재조회 세 경로 모두에서 Artifact 가 생기고,
**기존 평면 키와 내용이 100% 일치**하며, 기존 동작이 그대로인지.
"""
from __future__ import annotations

import pytest

from app.graph import workflow
from app.graph.workflow import run_workflow
from app.schemas import artifact
from app.services import migrate, sections, store
from app.services.markdown_export import _RUN_KEYS


def _dummy(monkeypatch):
    from app.services import llm
    monkeypatch.setattr(llm, "is_dummy", lambda: True)


def _assert_parity(state: dict) -> None:
    """Artifact 7개가 기존 평면 키와 정확히 일치하는지 — 이 PR 의 핵심 성공 기준."""
    arts = state["artifacts"]
    assert len(arts) == 7
    assert [a["artifact_id"] for a in arts] == artifact.ARTIFACT_IDS
    for a in arts:
        legacy_key = a["metadata"]["legacy_key"]
        assert a["content"] == (state.get(legacy_key) or {}), legacy_key


# ---- 신규 실행 ----

def test_run_generates_shadow_artifacts(monkeypatch):
    _dummy(monkeypatch)
    state = run_workflow({"project_name": "그림자", "problem": "P"})
    _assert_parity(state)
    # 평면 키는 그대로 남아 있다(대체가 아니라 병행 기록).
    for key in artifact.LEGACY_KEYS:
        assert key in state
    assert state["state_version"] == migrate.STATE_VERSION == 3


def test_artifact_evidence_ids_exist_in_registry(monkeypatch):
    """Artifact 가 참조하는 evidence_id 는 레지스트리에 실재해야 한다(계획서 중단 조건)."""
    _dummy(monkeypatch)
    state = run_workflow({"project_name": "근거", "problem": "P"})
    known = {e["evidence_id"] for e in state.get("evidence_registry") or []
             if isinstance(e, dict) and e.get("evidence_id")}
    for a in state["artifacts"]:
        for eid in a["evidence_ids"]:
            assert eid in known, eid


def test_serial_and_parallel_produce_same_artifact_set(monkeypatch):
    """실행 구조가 달라도 Artifact 집합(구조)은 같아야 한다 — 계획서 중단 조건."""
    _dummy(monkeypatch)
    s = run_workflow({"project_name": "동일", "problem": "P"}, workflow_mode="serial")
    p = run_workflow({"project_name": "동일", "problem": "P"}, workflow_mode="parallel")

    def shape(state):
        return [(a["artifact_id"], a["artifact_type"], a["owner_agent"],
                 tuple(a["depends_on"]), tuple(a["target_sections"]), a["status"])
                for a in state["artifacts"]]

    assert shape(s) == shape(p)
    _assert_parity(s)
    _assert_parity(p)


def test_shadow_generation_adds_no_llm_calls(monkeypatch):
    """순수 변환이므로 LLM 호출 수가 늘면 안 된다 — 계획서 중단 조건."""
    _dummy(monkeypatch)
    state = run_workflow({"project_name": "비용", "problem": "P"})
    calls_before = (state.get("usage") or {}).get("calls")
    # finalize 를 한 번 더 돌려도(=Artifact 재생성) 호출 수는 그대로다.
    workflow._finalize_artifacts(state)
    assert (state.get("usage") or {}).get("calls") == calls_before
    _assert_parity(state)


def test_finalize_artifacts_is_idempotent(monkeypatch):
    _dummy(monkeypatch)
    state = run_workflow({"project_name": "멱등", "problem": "P"})
    first = state["artifacts"]
    workflow._finalize_artifacts(state)
    assert state["artifacts"] == first


# ---- 수동 수정(/revise) ----

def test_rerun_finalizers_regenerates_artifacts(monkeypatch):
    """수정 후에도 Artifact 가 최신 결과를 반영해야 한다(옛 값 잔존 금지)."""
    _dummy(monkeypatch)
    state = run_workflow({"project_name": "수정", "problem": "P"})
    state["research_result"] = {"market_overview": "수정 후 값"}
    workflow.rerun_finalizers(state)
    arts = {a["artifact_type"]: a for a in state["artifacts"]}
    assert arts["research_analysis"]["content"] == {"market_overview": "수정 후 값"}
    _assert_parity(state)


# ---- 저장·재조회 ----

@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "projects.db")
    return store


def test_artifacts_survive_save_and_reload(monkeypatch, tmp_db):
    _dummy(monkeypatch)
    state = run_workflow({"project_name": "저장", "problem": "P"})
    pid = tmp_db.save_run(state)
    loaded = tmp_db.get_project(pid)["state"]
    assert loaded["artifacts"] == state["artifacts"]
    _assert_parity(loaded)


def test_artifacts_are_persisted_not_only_regenerated():
    """_RUN_KEYS 에 실려야 PR 4 의 Agent 작성 Artifact 가 저장에서 사라지지 않는다."""
    assert "artifacts" in _RUN_KEYS


def test_saved_artifact_ids_and_deps_do_not_change(monkeypatch, tmp_db):
    """저장 왕복 후 artifact_id·depends_on 이 바뀌면 안 된다 — 계획서 중단 조건."""
    _dummy(monkeypatch)
    state = run_workflow({"project_name": "안정", "problem": "P"})
    before = [(a["artifact_id"], tuple(a["depends_on"])) for a in state["artifacts"]]
    loaded = tmp_db.get_project(tmp_db.save_run(state))["state"]
    assert [(a["artifact_id"], tuple(a["depends_on"])) for a in loaded["artifacts"]] == before


# ---- 옛 기록(v2) 재조회 ----

def _v2_state() -> dict:
    """artifacts 가 없던 시절(v2)의 저장 기록."""
    return {
        "state_version": 2,
        "research_result": {"market_overview": "옛 기록"},
        "competitor_result": {"positioning": "옛 포지션"},
        "final_draft": "# 옛 기획서\n" + "\n".join(f"## {t}\n내용." for t in sections.SECTION_TITLES),
        "review_result": {"total_score": 80},
    }


def test_v2_record_gets_artifacts_on_read():
    up = migrate.upgrade_state(_v2_state())
    assert up["state_version"] == 3
    assert len(up["artifacts"]) == 7
    arts = {a["artifact_type"]: a for a in up["artifacts"]}
    assert arts["research_analysis"]["content"] == {"market_overview": "옛 기록"}
    # 결과가 없던 Agent 는 '없음'으로 정직하게 남는다(빈 봉투를 complete 로 위장하지 않는다).
    assert arts["swot_analysis"]["status"] == artifact.STATUS_MISSING


def test_v2_upgrade_is_idempotent():
    """반복 실행해도 결과가 변하지 않아야 한다 — 계획서 완료 기준."""
    st = migrate.upgrade_state(_v2_state())
    first = [dict(a) for a in st["artifacts"]]
    migrate.upgrade_state(st)
    assert st["artifacts"] == first


def test_upgrade_does_not_overwrite_existing_artifacts():
    """PR 4 에서 Agent 가 직접 쓴 Artifact 를 legacy 파생본으로 되돌리면 안 된다."""
    st = _v2_state()
    st["artifacts"] = [{"artifact_id": "artifact-research", "artifact_type": "research_analysis",
                        "content": {"market_overview": "Agent 가 직접 쓴 값"}}]
    up = migrate.upgrade_state(st)
    assert len(up["artifacts"]) == 1
    assert up["artifacts"][0]["content"] == {"market_overview": "Agent 가 직접 쓴 값"}


def test_old_record_without_any_result_still_upgrades():
    up = migrate.upgrade_state({"draft": "빈 기록"})
    assert len(up["artifacts"]) == 7
    assert {a["status"] for a in up["artifacts"]} == {artifact.STATUS_MISSING}
