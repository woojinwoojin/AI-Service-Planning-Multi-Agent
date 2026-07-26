"""Artifact Selector + ARTIFACT_READ_MODE (로드맵 2-2 PR 5) — 실 LLM·검색 호출 없음.

PR 1~4 는 전부 추가형이라 읽는 쪽을 건드리지 않았다. **이 PR 에서 처음 읽기 경로가 바뀐다.**
그래서 핵심 검증은 하나다:

    세 모드(legacy / prefer_artifact / artifact_only)에서 **산출물이 동일한가.**

같지 않다면 Artifact 가 평면 결과를 제대로 대신하지 못한다는 뜻이고, 그건 곧 `prefer_artifact`
로 넘기면 안 된다는 신호다. 기본값은 `legacy` 라 이 PR 자체는 동작을 바꾸지 않는다.
"""
from __future__ import annotations

import pytest

from app.agents import draft_writer, verifier
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

    for mod in (draft_writer, verifier):
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
        # Agent 간 읽기 — 뒤 Agent 가 앞 Agent 결과를 읽는 경로(전환 예정)
        "app/agents/competitor.py", "app/agents/customer.py", "app/agents/pestel.py",
        "app/agents/swot.py", "app/agents/business_model.py", "app/agents/risk.py",
        "app/agents/research.py",
        # 표시·집계 계층 — 문서 내용을 만들지 않으므로 뒤로 미룸
        "app/api/routes.py", "app/services/parallel_bench.py",
    }, found
