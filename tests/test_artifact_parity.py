"""Shadow Artifact 정합성 검증(로드맵 2-2 PR 3) 테스트 — LLM 호출 없음, 결정론적.

검사기가 '통과'만 잘 내는 것으로는 부족하다. **일부러 깨뜨렸을 때 실제로 잡는지**를
reason 별로 확인한다. 안 그러면 항상 ok=True 를 뱉는 검사기를 통과로 오해하게 된다.
"""
from __future__ import annotations

from copy import deepcopy

from app.graph import workflow
from app.graph.workflow import run_workflow
from app.schemas import artifact
from app.services import migrate, sections, store
from app.services.markdown_export import _RUN_KEYS


def _dummy(monkeypatch):
    from app.services import llm
    monkeypatch.setattr(llm, "is_dummy", lambda: True)


def _good_state() -> dict:
    st = {
        "research_result": {"market_overview": "성장"},
        "competitor_result": {"positioning": "니치"},
        "customer_result": {"target_persona": "20대"},
        "pestel_result": {"political": {"content": "규제"}},
        "swot_result": {"strengths": ["빠름"]},
        "business_model_result": {"revenue_streams": ["구독"]},
        "risk_result": {"risks": [{"category": "시장"}]},
        "evidence_registry": [
            {"evidence_id": "ev1", "url": "https://a.com", "source_agents": ["research"]},
            {"evidence_id": "ev2", "url": "https://b.com", "source_agents": ["competitor"]},
        ],
    }
    st["artifacts"] = artifact.build_artifacts_from_legacy(st)
    return st


def _reasons(report: dict) -> set[str]:
    return {m["reason"] for m in report["mismatched"]}


# ---- 통과 경로 ----

def test_parity_passes_on_consistent_state():
    r = artifact.check_parity(_good_state())
    assert r == {"expected": 7, "generated": 7, "matched": 7, "mismatched": [], "ok": True}


def test_parity_passes_on_real_dummy_run(monkeypatch):
    """계획서 완료 기준 — 더미 전체 흐름에서 matched=7."""
    _dummy(monkeypatch)
    state = run_workflow({"project_name": "정합성", "problem": "P"})
    r = state["artifact_parity"]
    assert r["ok"] and r["matched"] == 7 and r["mismatched"] == []


def test_parity_report_is_deterministic():
    st = _good_state()
    assert artifact.check_parity(st) == artifact.check_parity(st)


def test_check_parity_does_not_mutate_state():
    st = _good_state()
    before = deepcopy(st)
    artifact.check_parity(st)
    assert st == before


# ---- 깨뜨렸을 때 잡는가 (reason 별) ----

def test_detects_content_mismatch():
    """가장 중요한 검사 — 병행 기록이 원본과 어긋나면 잡아야 한다."""
    st = _good_state()
    st["research_result"] = {"market_overview": "원본만 바뀜"}
    r = artifact.check_parity(st)
    assert not r["ok"] and _reasons(r) == {"content_mismatch"}
    assert r["matched"] == 6
    assert r["mismatched"][0]["artifact_id"] == "artifact-research"


def test_detects_missing_artifact():
    st = _good_state()
    st["artifacts"] = [a for a in st["artifacts"] if a["artifact_id"] != "artifact-swot"]
    r = artifact.check_parity(st)
    assert not r["ok"] and "missing_artifact" in _reasons(r)
    assert r["generated"] == 6 and r["matched"] == 6


def test_detects_unknown_artifact():
    st = _good_state()
    st["artifacts"].append({"artifact_id": "artifact-ghost", "artifact_type": "ghost",
                            "content": {}, "depends_on": [], "evidence_ids": []})
    r = artifact.check_parity(st)
    assert not r["ok"] and "unknown_artifact" in _reasons(r)


def test_detects_duplicate_id():
    st = _good_state()
    st["artifacts"].append(dict(st["artifacts"][0]))
    r = artifact.check_parity(st)
    assert not r["ok"] and "duplicate_id" in _reasons(r)


def test_detects_missing_dependency():
    st = _good_state()
    for a in st["artifacts"]:
        if a["artifact_id"] == "artifact-swot":
            a["depends_on"] = ["artifact-does-not-exist"]
    r = artifact.check_parity(st)
    assert not r["ok"] and "missing_dependency" in _reasons(r)


def test_detects_unknown_evidence_id():
    """계획서 중단 조건 — 레지스트리에 없는 근거를 Artifact 가 참조하면 안 된다."""
    st = _good_state()
    for a in st["artifacts"]:
        if a["artifact_id"] == "artifact-research":
            a["evidence_ids"] = ["ev1", "ev999"]
    r = artifact.check_parity(st)
    assert not r["ok"] and "unknown_evidence_id" in _reasons(r)


def test_detects_several_problems_at_once():
    st = _good_state()
    st["risk_result"] = {"risks": ["원본만 바뀜"]}
    st["artifacts"] = [a for a in st["artifacts"] if a["artifact_id"] != "artifact-customer"]
    r = artifact.check_parity(st)
    assert _reasons(r) == {"content_mismatch", "missing_artifact"}


def test_empty_and_invalid_state_are_reported_not_crashed():
    assert artifact.check_parity({})["ok"] is False          # artifacts 없음 → 7건 missing
    assert artifact.check_parity({})["generated"] == 0
    assert artifact.check_parity(None)["ok"] is False        # 크래시 대신 판정으로


# ---- 실행 경로 표면화 ----

def test_run_surfaces_parity_and_does_not_fail_on_mismatch(monkeypatch):
    """정합성이 깨져도 실행은 완주해야 한다 — 계획서 원칙."""
    _dummy(monkeypatch)
    # 생성기가 고장나 Artifact 를 3개만 만드는 상황을 흉내낸다.
    monkeypatch.setattr(artifact, "build_artifacts_from_legacy",
                        lambda s: artifact.LEGACY_ARTIFACT_SPECS and
                        [{"artifact_id": spec["artifact_id"], "artifact_type": spec["artifact_type"],
                          "content": s.get(spec["legacy_key"]) or {}, "depends_on": [],
                          "evidence_ids": [], "metadata": {"legacy_key": spec["legacy_key"]}}
                         for spec in artifact.LEGACY_ARTIFACT_SPECS[:3]])
    state = run_workflow({"project_name": "비실패", "problem": "P"})
    # 정합성은 깨졌다고 보고하되…
    assert not state["artifact_parity"]["ok"]
    assert state["artifact_parity"]["generated"] == 3
    # …실행 자체는 완주하고 결과물도 그대로 나온다.
    assert state["run_status"] in ("success", "degraded")
    assert state["final_draft"]


def test_mismatch_is_logged(monkeypatch):
    """조용히 넘어가면 '통과'와 '검증이 깨짐'을 구분할 수 없다."""
    _dummy(monkeypatch)
    state = run_workflow({"project_name": "로그", "problem": "P"})
    assert not any(ln.startswith("[artifact] 정합성 불일치") for ln in state["logs"])
    # 원본을 바꿔 불일치를 만든 뒤 재생성 → 로그가 남아야 한다.
    state["swot_result"] = {"strengths": ["바뀜"]}
    monkeypatch.setattr(artifact, "build_artifacts_from_legacy", lambda s: s["artifacts"])
    workflow._finalize_artifacts(state)
    assert any(ln.startswith("[artifact] 정합성 불일치") for ln in state["logs"])
    assert "content_mismatch" in state["logs"][-1]


def test_parity_is_persisted_and_reloaded(monkeypatch, tmp_path):
    _dummy(monkeypatch)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "projects.db")
    state = run_workflow({"project_name": "저장", "problem": "P"})
    loaded = store.get_project(store.save_run(state))["state"]
    assert loaded["artifact_parity"] == state["artifact_parity"]
    assert "artifact_parity" in _RUN_KEYS


def test_revise_recomputes_parity(monkeypatch):
    """정상 /revise 후에도 판정이 다시 계산되고 통과해야 한다."""
    _dummy(monkeypatch)
    state = run_workflow({"project_name": "수정", "problem": "P"})
    state["artifact_parity"] = {"ok": False, "stale": True}   # 옛 판정이 남아 있다면
    workflow.rerun_finalizers(state)
    assert state["artifact_parity"]["ok"] and "stale" not in state["artifact_parity"]


def test_dual_write_divergence_is_reported_not_hidden(monkeypatch):
    """Dual Write 된 Agent 의 Artifact 와 평면 결과가 어긋나면 **덮어 감추지 않고 보고**한다.

    Agent 가 쓴 값이 생산 경로의 사실이므로 파생본으로 되돌리지 않는다. 대신 불일치를
    표면화해 사람이 원인을 보게 한다 — 조용히 맞춰버리면 정합성 검사가 무의미해진다.
    """
    _dummy(monkeypatch)
    state = run_workflow({"project_name": "발산", "problem": "P"})
    assert state["artifact_parity"]["ok"]
    # 어떤 운영 경로도 하지 않는 조작(평면 키만 직접 변경)으로 인위적 발산을 만든다.
    state["research_result"] = {"market_overview": "평면 키만 바뀜"}
    workflow.rerun_finalizers(state)
    r = state["artifact_parity"]
    assert not r["ok"]
    assert _reasons(r) == {"content_mismatch"}
    assert r["mismatched"][0]["artifact_id"] == "artifact-research"
    # Agent 가 쓴 값이 그대로 남아 있다(파생본으로 되돌리지 않음).
    arts = {a["artifact_type"]: a for a in state["artifacts"]}
    assert arts["research_analysis"]["content"] != {"market_overview": "평면 키만 바뀜"}
    assert arts["research_analysis"]["metadata"]["source"] == artifact.SOURCE_AGENT


# ---- 옛 기록 소급 ----

def test_v2_record_gets_parity_on_read():
    old = {"state_version": 2, "research_result": {"market_overview": "옛 기록"},
           "final_draft": "# P\n" + "\n".join(f"## {t}\n내용." for t in sections.SECTION_TITLES)}
    up = migrate.upgrade_state(old)
    assert up["artifact_parity"]["ok"]           # 파생 생성 직후라 일치
    assert up["artifact_parity"]["matched"] == 7


def test_v2_parity_upgrade_is_idempotent():
    st = migrate.upgrade_state({"research_result": {"market_overview": "x"}})
    first = deepcopy(st["artifact_parity"])
    migrate.upgrade_state(st)
    assert st["artifact_parity"] == first
