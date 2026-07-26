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


def test_artifact_only_does_not_fall_back():
    """폴백이 없어야 '정말 Artifact 만으로 도는지' 확인할 수 있다."""
    got = artifact.get_artifact_content({"research_result": {"src": "평면"}},
                                        "research_analysis", "research_result",
                                        mode=artifact.READ_ARTIFACT_ONLY)
    assert got == {}


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


def test_no_consumer_reads_flat_result_keys_directly():
    """전환한 소비자에 평면 키 직접 참조가 남아 있으면 모드 전환이 반쪽이 된다."""
    from pathlib import Path

    for mod in (draft_writer, verifier):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        for spec in artifact.LEGACY_ARTIFACT_SPECS:
            assert f'state.get("{spec["legacy_key"]}"' not in src, (mod.__name__, spec["legacy_key"])
