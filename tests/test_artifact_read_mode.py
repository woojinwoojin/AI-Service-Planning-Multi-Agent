"""Artifact Selector + ARTIFACT_READ_MODE (로드맵 2-2 PR 5) — 실 LLM·검색 호출 없음.

PR 1~4 는 전부 추가형이라 읽는 쪽을 건드리지 않았다. **이 PR 에서 처음 읽기 경로가 바뀐다.**
그래서 핵심 검증은 하나다:

    세 모드(legacy / prefer_artifact / artifact_only)에서 **산출물이 동일한가.**

같지 않다면 Artifact 가 평면 결과를 제대로 대신하지 못한다는 뜻이고, 그건 곧 `prefer_artifact`
로 넘기면 안 된다는 신호다. 기본값은 `legacy` 라 이 PR 자체는 동작을 바꾸지 않는다.
"""
from __future__ import annotations

import pytest

from app.agents import draft_writer, research, verifier
from app.graph.workflow import run_workflow
from app.schemas import artifact
from app.schemas.state import ProjectState
from app.services import llm

MODES = [artifact.READ_LEGACY, artifact.READ_PREFER_ARTIFACT, artifact.READ_ARTIFACT_ONLY]


def _dummy(monkeypatch):
    monkeypatch.setattr(llm, "is_dummy", lambda: True)


# ---- 모드 해석 ----

def test_default_mode_is_legacy(monkeypatch):
    monkeypatch.delenv(artifact.READ_MODE_ENV, raising=False)
    assert artifact.read_mode() == artifact.READ_LEGACY


@pytest.mark.parametrize("mode", MODES)
def test_env_selects_mode(monkeypatch, mode):
    monkeypatch.setenv(artifact.READ_MODE_ENV, mode)
    assert artifact.read_mode() == mode


def test_unknown_mode_falls_back_to_legacy(monkeypatch):
    """오타·잘못된 값이 조용히 Artifact 경로를 켜면 안 된다 — 가장 안전한 쪽으로."""
    monkeypatch.setenv(artifact.READ_MODE_ENV, "이상한값")
    assert artifact.read_mode() == artifact.READ_LEGACY
    monkeypatch.setenv(artifact.READ_MODE_ENV, "  PREFER_ARTIFACT  ")
    assert artifact.read_mode() == artifact.READ_PREFER_ARTIFACT   # 공백·대문자는 허용


# ---- selector 동작 ----

def _state_with_divergence() -> dict:
    """평면 키와 Artifact 가 다른 상태 — 어느 쪽을 읽는지 구분하려고 일부러 다르게 둔다."""
    return {
        "research_result": {"src": "평면"},
        "artifacts": [artifact.make_artifact("research_analysis", {"src": "아티팩트"})],
    }


def test_legacy_mode_reads_flat_key():
    got = artifact.get_artifact_content(_state_with_divergence(), "research_analysis",
                                        "research_result", mode=artifact.READ_LEGACY)
    assert got == {"src": "평면"}


def test_prefer_artifact_reads_artifact():
    got = artifact.get_artifact_content(_state_with_divergence(), "research_analysis",
                                        "research_result", mode=artifact.READ_PREFER_ARTIFACT)
    assert got == {"src": "아티팩트"}


def test_prefer_artifact_falls_back_when_missing():
    """옛 프로젝트처럼 Artifact 가 없어도 깨지지 않아야 한다."""
    got = artifact.get_artifact_content({"research_result": {"src": "평면"}},
                                        "research_analysis", "research_result",
                                        mode=artifact.READ_PREFER_ARTIFACT)
    assert got == {"src": "평면"}


def test_artifact_only_fails_explicitly_instead_of_falling_back():
    """검증 모드이므로 빈 dict 로 계속 돌리지 않고 **명시적으로 실패**해야 한다.

    조용히 {} 를 넘기면 '평면 키 없이도 도는가'라는 질문 자체에 답할 수 없게 된다.
    """
    with pytest.raises(artifact.ArtifactUnavailable) as e:
        artifact.get_artifact_content({"research_result": {"src": "평면"}},
                                      "research_analysis", "research_result",
                                      mode=artifact.READ_ARTIFACT_ONLY)
    assert "missing" in str(e.value)


@pytest.mark.parametrize(("bad", "reason"), [
    ({"content": {}}, "empty"),
    ({"content": {"x": 1}, "status": artifact.STATUS_FAILED}, "failed"),
])
def test_unusable_artifact_is_distinguished_from_missing(bad, reason):
    """'없음'과 '있는데 못 씀'은 다르다 — 후자는 실제 오류일 수 있어 사유를 남겨야 한다."""
    a = {**artifact.make_artifact("research_analysis", {"x": 1}), **bad}
    st = {"research_result": {"src": "평면"}, "artifacts": [a]}
    # prefer_artifact: 폴백하되 사유가 남는다
    assert artifact.get_artifact_content(st, "research_analysis", "research_result",
                                         mode=artifact.READ_PREFER_ARTIFACT) == {"src": "평면"}
    # artifact_only: 폴백 없이 실패, 사유 포함
    with pytest.raises(artifact.ArtifactUnavailable) as e:
        artifact.get_artifact_content(st, "research_analysis", "research_result",
                                      mode=artifact.READ_ARTIFACT_ONLY)
    assert reason in str(e.value)


def test_selector_honors_env_when_mode_arg_omitted(monkeypatch):
    monkeypatch.setenv(artifact.READ_MODE_ENV, artifact.READ_PREFER_ARTIFACT)
    got = artifact.get_artifact_content(_state_with_divergence(),
                                        "research_analysis", "research_result")
    assert got == {"src": "아티팩트"}


def test_selector_safe_on_invalid_state():
    assert artifact.get_artifact_content(None, "research_analysis", "research_result") == {}


# ---- 핵심: 세 모드에서 산출물이 같은가 ----

def _run(monkeypatch, mode: str, workflow_mode: str = "serial") -> dict:
    _dummy(monkeypatch)
    monkeypatch.setenv(artifact.READ_MODE_ENV, mode)
    return run_workflow({"project_name": "모드", "description": "d",
                         "problem": "p", "keywords": ["k"]}, workflow_mode=workflow_mode)


@pytest.mark.parametrize("workflow_mode", ["serial", "parallel"])
def test_all_read_modes_produce_identical_output(monkeypatch, workflow_mode):
    """세 모드의 최종 기획서·검증 결과가 **완전히 같아야** 한다.

    다르면 Artifact 가 평면 결과를 제대로 대신하지 못한다는 뜻이므로
    prefer_artifact 로 넘기면 안 된다는 신호다.
    """
    outs = {m: _run(monkeypatch, m, workflow_mode) for m in MODES}
    base = outs[artifact.READ_LEGACY]
    for m in MODES[1:]:
        assert outs[m]["draft"] == base["draft"], m
        assert outs[m]["final_draft"] == base["final_draft"], m
        assert outs[m]["verification_result"] == base["verification_result"], m
        assert outs[m]["artifact_parity"]["ok"], m


@pytest.mark.parametrize("mode", MODES)
def test_run_completes_in_every_mode(monkeypatch, mode):
    state = _run(monkeypatch, mode)
    assert state["final_draft"] and state["run_status"] in ("success", "degraded")
    assert not state["failed_nodes"]


# ---- 소비자가 실제로 selector 를 타는가 ----

def test_draft_reads_through_selector(monkeypatch):
    """prefer_artifact 에서 Artifact 내용이 초안 프롬프트에 실제로 반영되는지."""
    monkeypatch.setenv(artifact.READ_MODE_ENV, artifact.READ_PREFER_ARTIFACT)
    seen: dict = {}

    def _capture(system, user, fallback, model, status):
        seen["user"] = user
        return "# 초안", []

    monkeypatch.setattr(draft_writer, "_generate", _capture)
    state: ProjectState = {
        "structured_input": {"project_name": "P"},
        "swot_result": {"strengths": ["평면쪽"]},
        "artifacts": [artifact.make_artifact("swot_analysis", {"strengths": ["아티팩트쪽"]})],
    }
    draft_writer.draft(state)
    assert "아티팩트쪽" in seen["user"] and "평면쪽" not in seen["user"]


def test_verify_reads_through_selector(monkeypatch):
    monkeypatch.setattr(llm, "is_dummy", lambda: False)
    monkeypatch.setenv(artifact.READ_MODE_ENV, artifact.READ_PREFER_ARTIFACT)
    seen: dict = {}
    monkeypatch.setattr(llm, "complete_json",
                        lambda system, user, **k: seen.setdefault("user", user) or {"claims": []})
    state: ProjectState = {
        "final_draft": "# 문서",
        "research_result": {"market_overview": "평면쪽"},
        "artifacts": [artifact.make_artifact("research_analysis",
                                             {"market_overview": "아티팩트쪽"})],
    }
    verifier.verify(state)
    assert "아티팩트쪽" in seen["user"] and "평면쪽" not in seen["user"]


def test_section_evidence_uses_artifact_types():
    """_SECTION_EVIDENCE 가 평면 키를 중복해 들고 있지 않은지(두 곳에 적으면 어긋난다)."""
    for _label, artifact_type in draft_writer._SECTION_EVIDENCE.values():
        assert artifact_type in artifact.SPEC_BY_TYPE, artifact_type


def test_section_revision_evidence_reads_through_selector(monkeypatch):
    """섹션 단위 수정(2-4)이 실어 주는 분석 근거도 selector 를 타야 한다."""
    monkeypatch.setenv(artifact.READ_MODE_ENV, artifact.READ_PREFER_ARTIFACT)
    state: ProjectState = {
        "swot_result": {"strengths": ["평면쪽"]},
        "artifacts": [artifact.make_artifact("swot_analysis", {"strengths": ["아티팩트쪽"]})],
    }
    block = draft_writer._relevant_analysis(state, "swot")
    assert "아티팩트쪽" in block and "평면쪽" not in block


# ---- Agent 간 읽기: research_gap → research (PR 5c-1) ----
#
# 여기서 처음으로 **Agent 가 앞 Agent 의 결과를 읽는 경로**가 selector 를 탄다. 앞선 전환
# (draft·verify)은 문서를 쓰거나 검증하는 쪽이었고, 이 경로는 다음 Agent 의 입력 데이터를
# 만든다 — 그래서 '읽은 값이 실제로 결과에 반영되는가'까지 확인한다.

@pytest.fixture
def gap_mode(monkeypatch):
    """research_gap 이 실제로 도는 조건(실 모드·검색 활성). 외부 호출은 테스트가 대체한다."""
    from app.services import budget, search

    monkeypatch.delenv("DYNAMIC_MAX_GAP_SEARCHES", raising=False)
    budget.reset()
    monkeypatch.setattr(llm, "is_dummy", lambda: False)
    monkeypatch.setattr(search, "search_enabled", lambda: True)
    monkeypatch.setattr(llm, "mode_label", lambda *a, **k: "테스트")
    return monkeypatch


def _research_content(marker: str, url: str = "https://known") -> dict:
    return {"industry_trends": [marker], "customer_needs": [], "opportunities": [], "risks": [],
            "sources": [f"{marker} 출처"], "source_objects": [{"url": url, "title": marker}]}


def _gap_state(*, flat: dict | None = None, art: dict | None = None) -> dict:
    state: dict = {"evidence_gaps": [{"topic": "시장 규모", "query": "시장 규모"}]}
    if flat is not None:
        state["research_result"] = flat
    if art is not None:
        state["artifacts"] = [artifact.make_artifact("research_analysis", art)]
    return state


def _one_new_hit(mp, url: str = "https://new"):
    from app.services import search

    mp.setattr(search, "web_search", lambda q, **k: [{"url": url, "title": "새 보고서",
                                                     "content": "검색 요약문"}])


@pytest.mark.parametrize(("mode", "expected"), [
    (artifact.READ_LEGACY, "평면"),
    (artifact.READ_PREFER_ARTIFACT, "아티팩트"),
    (artifact.READ_ARTIFACT_ONLY, "아티팩트"),
])
def test_research_gap_merges_onto_the_mode_selected_base(gap_mode, mode, expected):
    """평면·Artifact 에 다른 값을 넣고, 보강 결과가 **모드에 맞는 쪽 위에** 쌓이는지 본다."""
    gap_mode.setenv(artifact.READ_MODE_ENV, mode)
    _one_new_hit(gap_mode)
    gap_mode.setattr(llm, "complete_json", lambda *a, **k: {"industry_trends": ["새 트렌드"]})

    out = research.research_gap(_gap_state(flat=_research_content("평면"),
                                           art=_research_content("아티팩트")))
    assert out["research_result"]["industry_trends"] == [expected, "새 트렌드"]
    # 보강본은 평면 키와 Artifact 양쪽에 같은 내용으로 나가야 한다(Dual Write 유지).
    assert out["artifacts"][0]["content"] == out["research_result"]


def test_research_gap_dedupes_against_artifact_urls(gap_mode):
    """중복 URL 판정도 selector 를 타야 한다 — 안 그러면 이미 가진 근거를 '새 근거'로 센다."""
    gap_mode.setenv(artifact.READ_MODE_ENV, artifact.READ_PREFER_ARTIFACT)
    _one_new_hit(gap_mode, "https://only-in-artifact")
    gap_mode.setattr(llm, "complete_json", lambda *a, **k: pytest.fail("LLM 호출 불필요"))

    out = research.research_gap(_gap_state(
        flat=_research_content("평면", url="https://only-in-flat"),
        art=_research_content("아티팩트", url="https://only-in-artifact")))
    assert out["dynamic_research"]["skip_reason"] == "새 근거 없음"
    assert out["dynamic_research"]["new_sources"] == 0


def test_research_gap_runs_without_flat_key_in_artifact_only(gap_mode):
    """평면 키가 아예 없어도 Artifact 만으로 보강이 완주하는가(전환의 실질 성공 기준)."""
    gap_mode.setenv(artifact.READ_MODE_ENV, artifact.READ_ARTIFACT_ONLY)
    _one_new_hit(gap_mode)
    gap_mode.setattr(llm, "complete_json", lambda *a, **k: {"opportunities": ["새 기회"]})

    out = research.research_gap(_gap_state(art=_research_content("아티팩트")))
    rr = out["research_result"]
    assert rr["industry_trends"] == ["아티팩트"] and rr["opportunities"] == ["새 기회"]
    # 기존 근거와 새 근거가 모두 남는다(유실 없음)
    assert [o["url"] for o in rr["source_objects"]] == ["https://known", "https://new"]
    assert any("https://new" in s for s in rr["sources"])
    assert [e["url"] for e in out["evidence_registry"]] == ["https://new"]


def test_research_gap_fails_before_spending_calls_when_artifact_missing(gap_mode):
    """artifact_only 에서 Artifact 가 없으면 **검색·LLM 비용을 쓰기 전에** 실패해야 한다."""
    from app.services import search

    gap_mode.setenv(artifact.READ_MODE_ENV, artifact.READ_ARTIFACT_ONLY)
    gap_mode.setattr(search, "web_search", lambda *a, **k: pytest.fail("검색해서는 안 된다"))
    gap_mode.setattr(llm, "complete_json", lambda *a, **k: pytest.fail("LLM 호출해서는 안 된다"))

    with pytest.raises(artifact.ArtifactUnavailable):
        research.research_gap(_gap_state(flat=_research_content("평면")))


def test_research_gap_skip_path_does_not_require_artifact(gap_mode):
    """생략 경로(공백 보고 없음)는 Artifact 를 읽지 않는다 — 필요 없는 실패를 만들지 않는다."""
    gap_mode.setenv(artifact.READ_MODE_ENV, artifact.READ_ARTIFACT_ONLY)
    out = research.research_gap({"evidence_gaps": []})
    assert out["dynamic_research"]["skip_reason"] == "근거 공백 보고 없음"


# ---- 관측성 (PR 5b) ----

def test_invalid_mode_is_reported_not_just_swallowed(monkeypatch, caplog):
    """오타를 조용히 무시하면 운영자는 Artifact 모드로 믿는데 실제로는 평면 키를 읽는다."""
    monkeypatch.setenv(artifact.READ_MODE_ENV, "prefer_artifcat")
    info = artifact.read_mode_info()
    assert info == {"mode": artifact.READ_LEGACY, "raw": "prefer_artifcat", "invalid": True}
    with caplog.at_level("WARNING", logger="app.artifact"):
        assert artifact.read_mode() == artifact.READ_LEGACY
    assert "prefer_artifcat" in caplog.text


def test_unset_mode_is_not_flagged_invalid(monkeypatch):
    """미설정은 정상(기본값 사용) — 오타와 구분해야 경고가 의미를 갖는다."""
    monkeypatch.delenv(artifact.READ_MODE_ENV, raising=False)
    assert artifact.read_mode_info() == {"mode": artifact.READ_LEGACY, "raw": "", "invalid": False}


def test_read_status_reports_usable_artifacts(monkeypatch):
    monkeypatch.delenv(artifact.READ_MODE_ENV, raising=False)
    st = {"artifacts": [artifact.make_artifact(s["artifact_type"], {"x": 1})
                        for s in artifact.LEGACY_ARTIFACT_SPECS]}
    r = artifact.read_status(st)
    assert r["mode"] == artifact.READ_LEGACY and r["invalid"] is False
    assert r["expected"] == 7 and r["usable"] == 7 and r["unusable"] == []


def test_read_status_lists_unusable_with_reasons(monkeypatch):
    """전환 전에 '얼마나 폴백이 날지' 미리 보는 지표."""
    monkeypatch.delenv(artifact.READ_MODE_ENV, raising=False)
    arts = [artifact.make_artifact(s["artifact_type"], {"x": 1})
            for s in artifact.LEGACY_ARTIFACT_SPECS]
    arts[0]["content"] = {}                                  # empty
    arts[1]["status"] = artifact.STATUS_FAILED               # failed
    r = artifact.read_status({"artifacts": arts[:-1]})       # 마지막 하나는 missing
    assert r["usable"] == 4
    assert {u["reason"] for u in r["unusable"]} == {"empty", "failed", "missing"}


def test_run_records_read_status(monkeypatch):
    _dummy(monkeypatch)
    monkeypatch.delenv(artifact.READ_MODE_ENV, raising=False)
    state = run_workflow({"project_name": "관측", "problem": "P"})
    r = state["artifact_read"]
    assert r["mode"] == artifact.READ_LEGACY and r["usable"] == 7 and r["unusable"] == []


def test_run_logs_invalid_mode(monkeypatch):
    """실행 기록만 봐도 잘못된 설정으로 돌았음을 알 수 있어야 한다."""
    _dummy(monkeypatch)
    monkeypatch.setenv(artifact.READ_MODE_ENV, "artifact_onlyy")
    state = run_workflow({"project_name": "오타", "problem": "P"})
    assert state["artifact_read"]["invalid"] is True
    assert state["artifact_read"]["raw"] == "artifact_onlyy"
    assert any("ARTIFACT_READ_MODE" in ln for ln in state["logs"])


def test_health_exposes_read_mode(monkeypatch):
    """설정은 시작 시 확정되므로 '지금 이 서버가 어느 경로로 도는지'를 볼 수 있어야 한다."""
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setenv(artifact.READ_MODE_ENV, artifact.READ_PREFER_ARTIFACT)
    body = TestClient(app).get("/health").json()
    assert body["artifact_read_mode"] == artifact.READ_PREFER_ARTIFACT
    assert body["artifact_read_mode_invalid"] is False

    monkeypatch.setenv(artifact.READ_MODE_ENV, "틀린값")
    body = TestClient(app).get("/health").json()
    assert body["artifact_read_mode"] == artifact.READ_LEGACY
    assert body["artifact_read_mode_invalid"] is True


def test_converted_consumers_have_no_direct_flat_key_reads():
    """**전환한 소비자에 한해** 평면 키 직접 참조가 남지 않았는지 확인한다.

    ⚠️ 보조 수단이다. 문자열 대조라 별칭·주석에 취약하고, 무엇보다 **여기 나열한 모듈만**
    본다 — 아래 `test_unconverted_readers_are_known` 이 '아직 안 옮긴 곳'을 따로 고정한다.
    (실제로 이 테스트는 draft_writer·verifier 만 보던 탓에 Agent 간 읽기 7곳을 통과시켰다.)
    """
    from pathlib import Path

    for mod in (draft_writer, verifier, research):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        for spec in artifact.LEGACY_ARTIFACT_SPECS:
            assert f'state.get("{spec["legacy_key"]}"' not in src, (mod.__name__, spec["legacy_key"])


def test_unconverted_readers_are_known():
    """아직 selector 를 타지 않는 곳을 **명시적으로 고정**한다.

    이 목록이 있어야 `artifact_only` 통과의 범위를 정직하게 말할 수 있다 — 이 경로들은
    selector 를 아예 타지 않으므로 모드를 바꿔도 영향을 받지 않는다.
    목록이 줄면(=전환이 진행되면) 이 테스트를 함께 갱신한다. 늘어나면 실패한다.
    """
    import re
    from pathlib import Path

    root = Path(draft_writer.__file__).parents[2]
    keys = "|".join(s["legacy_key"] for s in artifact.LEGACY_ARTIFACT_SPECS)
    pattern = re.compile(rf'state\.get\("({keys})"|\["({keys})"\]')
    found = {p.relative_to(root).as_posix() for p in root.joinpath("app").rglob("*.py")
             if pattern.search(p.read_text(encoding="utf-8"))}
    assert found == {
        # Agent 간 읽기 — 뒤 Agent 가 앞 Agent 결과를 읽는 경로.
        # research.py(research_gap → research)는 PR 5c-1 에서 전환됨.
        # 남은 6개는 PR 5c-2(research 단일 의존 4개)·5c-3(swot·risk 복수 의존)에서 전환한다.
        "app/agents/competitor.py", "app/agents/customer.py", "app/agents/pestel.py",
        "app/agents/swot.py", "app/agents/business_model.py", "app/agents/risk.py",
        # 표시·집계 계층 — 문서 내용을 만들지 않으므로 뒤로 미룸
        "app/api/routes.py", "app/services/parallel_bench.py",
    }, found
