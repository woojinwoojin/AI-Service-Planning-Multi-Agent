"""Artifact Selector + ARTIFACT_READ_MODE (로드맵 2-2 PR 5) — 실 LLM·검색 호출 없음.

PR 1~4 는 전부 추가형이라 읽는 쪽을 건드리지 않았다. **이 PR 에서 처음 읽기 경로가 바뀐다.**
그래서 핵심 검증은 하나다:

    세 모드(legacy / prefer_artifact / artifact_only)에서 **산출물이 동일한가.**

같지 않다면 Artifact 가 평면 결과를 제대로 대신하지 못한다는 뜻이고, 그건 곧 `prefer_artifact`
로 넘기면 안 된다는 신호다. 기본값은 `legacy` 라 이 PR 자체는 동작을 바꾸지 않는다.
"""
from __future__ import annotations

import pytest

from app.agents import (
    business_model,
    competitor,
    customer,
    draft_writer,
    pestel,
    research,
    risk,
    swot,
    verifier,
)
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


# 모드를 바꿔도 **달라져서는 안 되는** State 키(PR 5d 비교 항목 확대).
#
# PR 5 때는 `final_draft`·`verification_result` 만 봤는데, 그것만으로는 부족하다 —
# 더미 초안은 `_dummy_draft(si, research, pestel)` 로 만들어져 **competitor·customer·swot·
# business_model·risk 의 결과가 최종 문서에 반영되지 않는다.** 그 5개 Agent 가 모드에 따라
# 다른 입력을 읽어 다른 결과를 내도 최종 문서 해시는 그대로다.
# 그래서 **7개 분석 결과 자체**를 직접 비교한다 — 이러면 문서에 안 실리는 Agent 도 커버된다.
_MODE_INVARIANT_KEYS = [
    *[s["legacy_key"] for s in artifact.LEGACY_ARTIFACT_SPECS],   # 7개 분석 결과(핵심 확대분)
    "draft", "final_draft",                                       # 문서
    "review_result", "initial_review_result", "final_review_result",
    "verification_result", "quality_gate",                        # 평가·검증·게이트
    "sources", "source_objects", "evidence_registry", "evidence_gaps",  # 근거 계열
    "revision_strategy", "revised_section_ids", "revision_fallback_reason",
    "run_status", "failed_nodes", "fallback_nodes",               # 실행 결말
]


@pytest.mark.parametrize("workflow_mode", ["serial", "parallel"])
def test_all_read_modes_produce_identical_output(monkeypatch, workflow_mode):
    """세 모드의 산출물이 **완전히 같아야** 한다.

    다르면 Artifact 가 평면 결과를 제대로 대신하지 못한다는 뜻이므로
    prefer_artifact 로 넘기면 안 된다는 신호다.

    비교 대상은 최종 문서만이 아니라 `_MODE_INVARIANT_KEYS` 전체 + Artifact content 다
    (PR 5d 에서 확대 — 이유는 위 상수 주석 참고).
    """
    outs = {m: _run(monkeypatch, m, workflow_mode) for m in MODES}
    base = outs[artifact.READ_LEGACY]
    for m in MODES[1:]:
        for key in _MODE_INVARIANT_KEYS:
            assert outs[m].get(key) == base.get(key), (m, key)
        # Artifact 쪽 내용도 같아야 한다 — 평면 결과만 같고 봉투가 다르면 다음 소비자가 갈린다.
        assert _contents(outs[m]) == _contents(base), m
        assert outs[m]["artifact_parity"]["ok"], m


def _contents(state: dict) -> dict:
    return {a["artifact_type"]: a["content"] for a in state["artifacts"]}


# 원본을 **import 시점에** 붙잡아 둔다. 헬퍼 안에서 `llm.complete_json` 을 그때그때 읽으면
# 한 테스트에서 두 번째 실행이 첫 번째 래퍼를 감싸 첫 실행의 기록에 프롬프트가 섞인다.
_REAL_COMPLETE_JSON = llm.complete_json
_REAL_COMPLETE_TEXT = llm.complete_text


def _run_capturing_prompts(monkeypatch, mode: str, workflow_mode: str) -> list[str]:
    """실행 전체의 LLM user 프롬프트를 순서 무관하게 모은다(정렬)."""
    seen: list[str] = []

    def cap(real):
        def _wrapped(system, user, *a, **k):
            seen.append(user)
            return real(system, user, *a, **k)
        return _wrapped

    monkeypatch.setattr(llm, "complete_json", cap(_REAL_COMPLETE_JSON))
    monkeypatch.setattr(llm, "complete_text", cap(_REAL_COMPLETE_TEXT))
    _run(monkeypatch, mode, workflow_mode)
    return sorted(seen)          # 병렬은 도착 순서가 흔들리므로 정렬해 비교


@pytest.mark.parametrize("workflow_mode", ["serial", "parallel"])
def test_every_agent_receives_identical_input_in_every_mode(monkeypatch, workflow_mode):
    """**모든 Agent 가 세 모드에서 똑같은 입력을 받는가** — 산출물 비교의 공백을 메운다.

    산출물만 비교하면 더미 모드에서 대부분 공허하다. `_dummy()` 가 앞 Agent 결과를 실제로
    쓰는 Agent 는 `competitor` 뿐이고(나머지 5개는 입력을 무시한 고정값을 낸다), 최종 문서는
    research·pestel 만 반영한다. 즉 어떤 Agent 가 `artifact_only` 에서 빈 값을 읽어도
    산출물 해시는 그대로일 수 있다.

    프롬프트를 직접 대조하면 이 구멍이 닫힌다 — 프롬프트에는 읽어 온 값이 그대로 직렬화돼
    들어가므로, 모드가 달라 다른 값을 읽으면 **반드시** 다르다. 5c-2/5c-3 이 Agent 단위로
    하던 가로채기를, 여기서는 손으로 만든 State 가 아니라 **관통 실행**에 대해 한다.
    """
    prompts = {m: _run_capturing_prompts(monkeypatch, m, workflow_mode) for m in MODES}
    base = prompts[artifact.READ_LEGACY]
    assert base, "프롬프트가 하나도 안 잡혔다면 이 테스트는 아무것도 검증하지 못한다"
    for m in MODES[1:]:
        assert prompts[m] == base, m


@pytest.mark.parametrize("workflow_mode", ["serial", "parallel"])
@pytest.mark.parametrize("mode", MODES)
def test_run_completes_in_every_mode(monkeypatch, mode, workflow_mode):
    """병렬도 함께 본다 — 분석 4분기는 fan-out 뒤에서 앞 Agent 결과를 읽으므로(PR 5c-2),
    Artifact 가 fan-out 경계를 넘어 보이지 않으면 `artifact_only` 에서 노드가 실패한다.
    """
    state = _run(monkeypatch, mode, workflow_mode)
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


# ---- Agent 간 읽기: research 단일 의존 4개 (PR 5c-2) ----
#
# **최종 문서 해시만으로는 이 4개 경로가 검증되지 않는다.** 더미 초안은
# `_dummy_draft(si, research, pestel)` 로 만들어져 competitor·customer·business_model 의
# 결과는 산출물에 반영되지 않는다. 그래서 각 Agent 의 **프롬프트를 직접 가로채** Artifact
# 내용이 실제 입력으로 들어갔는지 본다.

_RESEARCH_DEPENDENTS = [
    pytest.param(competitor.competitor, id="competitor"),
    pytest.param(customer.customer, id="customer"),
    pytest.param(pestel.pestel, id="pestel"),
    pytest.param(business_model.business_model, id="business_model"),
]


@pytest.fixture
def captured_prompt(monkeypatch):
    """Agent 의 LLM 입력을 가로채 담아 둔다. is_dummy 는 competitor 의 웹검색을 막기 위함."""
    monkeypatch.setattr(llm, "is_dummy", lambda: True)
    seen: dict = {}

    def _capture(system, user, **kwargs):
        seen["user"] = user
        return {}                      # 각 Agent 의 _validate 가 중립값으로 처리한다

    monkeypatch.setattr(llm, "complete_json", _capture)
    return seen


def _research_state(*, flat: dict | None, art: dict | None) -> ProjectState:
    state: ProjectState = {"structured_input": {"project_name": "P"}}
    if flat is not None:
        state["research_result"] = flat
    if art is not None:
        state["artifacts"] = [artifact.make_artifact("research_analysis", art)]
    return state


@pytest.mark.parametrize("agent_fn", _RESEARCH_DEPENDENTS)
@pytest.mark.parametrize(("mode", "expected", "absent"), [
    (artifact.READ_LEGACY, "평면쪽", "아티팩트쪽"),
    (artifact.READ_PREFER_ARTIFACT, "아티팩트쪽", "평면쪽"),
    (artifact.READ_ARTIFACT_ONLY, "아티팩트쪽", "평면쪽"),
])
def test_research_dependents_read_through_selector(monkeypatch, captured_prompt, agent_fn,
                                                   mode, expected, absent):
    """평면·Artifact 에 다른 값을 넣고, **모드에 맞는 쪽**이 프롬프트에 들어가는지 본다."""
    monkeypatch.setenv(artifact.READ_MODE_ENV, mode)
    agent_fn(_research_state(flat={"market_overview": "평면쪽"},
                             art={"market_overview": "아티팩트쪽"}))
    assert expected in captured_prompt["user"]
    assert absent not in captured_prompt["user"]


@pytest.mark.parametrize("agent_fn", _RESEARCH_DEPENDENTS)
def test_research_dependents_run_without_flat_key(monkeypatch, captured_prompt, agent_fn):
    """평면 키가 아예 없어도 Artifact 만으로 동작하는가(전환의 실질 성공 기준)."""
    monkeypatch.setenv(artifact.READ_MODE_ENV, artifact.READ_ARTIFACT_ONLY)
    agent_fn(_research_state(flat=None, art={"market_overview": "아티팩트쪽"}))
    assert "아티팩트쪽" in captured_prompt["user"]


@pytest.mark.parametrize("agent_fn", _RESEARCH_DEPENDENTS)
def test_research_dependents_fail_explicitly_when_artifact_missing(monkeypatch, captured_prompt,
                                                                  agent_fn):
    """artifact_only 에서 의존 Artifact 가 없으면 빈 dict 로 조용히 진행하지 않는다.

    조용히 넘어가면 '근거 없이 만든 분석'이 정상 산출물처럼 저장된다.
    """
    monkeypatch.setenv(artifact.READ_MODE_ENV, artifact.READ_ARTIFACT_ONLY)
    with pytest.raises(artifact.ArtifactUnavailable):
        agent_fn(_research_state(flat={"market_overview": "평면쪽"}, art=None))
    assert "user" not in captured_prompt          # LLM 호출 전에 실패


@pytest.mark.parametrize("agent_fn", _RESEARCH_DEPENDENTS)
def test_research_dependents_do_not_consume_failed_artifact(monkeypatch, captured_prompt, agent_fn):
    """status=failed 인 Artifact 를 정상값처럼 소비하지 않는다(사유가 남거나 실패한다)."""
    monkeypatch.setenv(artifact.READ_MODE_ENV, artifact.READ_ARTIFACT_ONLY)
    art = artifact.make_artifact("research_analysis", {"market_overview": "아티팩트쪽"})
    art["status"] = artifact.STATUS_FAILED
    state: ProjectState = {"structured_input": {"project_name": "P"},
                           "research_result": {"market_overview": "평면쪽"}, "artifacts": [art]}
    with pytest.raises(artifact.ArtifactUnavailable) as e:
        agent_fn(state)
    assert "failed" in str(e.value)


# ---- Agent 간 읽기: 복수 의존 2개 (PR 5c-3) ----
#
# `swot`(research+competitor)·`risk`(research+pestel)는 의존이 **2개**다. 여기서 처음으로
# 명세의 `depends_on` 이 실제 런타임 입력 관계와 일치하는지를 직접 검증할 수 있다 —
# 지금까지 `depends_on` 은 '코드를 읽고 사람이 적은' 값이었다.

_MULTI_DEP = [
    pytest.param(swot.swot, "competitor_analysis", "competitor_result", id="swot"),
    pytest.param(risk.risk, "pestel_analysis", "pestel_result", id="risk"),
]


def _two_dep_state(*, second_type: str, second_key: str,
                   flat: bool = True, art: bool = True, drop_second_art: bool = False) -> ProjectState:
    """research + 두 번째 의존을 평면·Artifact 양쪽에 서로 다른 값으로 넣는다."""
    state: ProjectState = {"structured_input": {"project_name": "P"}}
    if flat:
        state["research_result"] = {"market_overview": "평면-research"}
        state[second_key] = {"note": "평면-second"}
    if art:
        arts = [artifact.make_artifact("research_analysis", {"market_overview": "아티팩트-research"})]
        if not drop_second_art:
            arts.append(artifact.make_artifact(second_type, {"note": "아티팩트-second"}))
        state["artifacts"] = arts
    return state


@pytest.mark.parametrize(("agent_fn", "second_type", "second_key"), _MULTI_DEP)
@pytest.mark.parametrize("mode", [artifact.READ_PREFER_ARTIFACT, artifact.READ_ARTIFACT_ONLY])
def test_multi_dep_reads_both_dependencies_through_selector(monkeypatch, captured_prompt,
                                                            agent_fn, second_type, second_key, mode):
    """의존 **둘 다** Artifact 쪽에서 와야 한다 — 하나만 전환되면 여기서 잡힌다."""
    monkeypatch.setenv(artifact.READ_MODE_ENV, mode)
    agent_fn(_two_dep_state(second_type=second_type, second_key=second_key))
    user = captured_prompt["user"]
    assert "아티팩트-research" in user and "아티팩트-second" in user
    assert "평면-research" not in user and "평면-second" not in user


@pytest.mark.parametrize(("agent_fn", "second_type", "second_key"), _MULTI_DEP)
def test_multi_dep_legacy_mode_still_reads_flat_keys(monkeypatch, captured_prompt,
                                                     agent_fn, second_type, second_key):
    monkeypatch.setenv(artifact.READ_MODE_ENV, artifact.READ_LEGACY)
    agent_fn(_two_dep_state(second_type=second_type, second_key=second_key))
    user = captured_prompt["user"]
    assert "평면-research" in user and "평면-second" in user
    assert "아티팩트" not in user


@pytest.mark.parametrize(("agent_fn", "second_type", "second_key"), _MULTI_DEP)
def test_multi_dep_runs_without_flat_keys(monkeypatch, captured_prompt,
                                          agent_fn, second_type, second_key):
    """평면 키가 아예 없어도 Artifact 둘만으로 동작하는가."""
    monkeypatch.setenv(artifact.READ_MODE_ENV, artifact.READ_ARTIFACT_ONLY)
    agent_fn(_two_dep_state(second_type=second_type, second_key=second_key, flat=False))
    assert "아티팩트-research" in captured_prompt["user"]
    assert "아티팩트-second" in captured_prompt["user"]


@pytest.mark.parametrize(("agent_fn", "second_type", "second_key"), _MULTI_DEP)
def test_multi_dep_fails_when_one_dependency_is_missing(monkeypatch, captured_prompt,
                                                        agent_fn, second_type, second_key):
    """**의존 하나만** 빠져도 명시적으로 실패해야 한다.

    research 는 있고 두 번째 의존만 없는 상황 — 조용히 빈 dict 로 진행하면 '경쟁사 분석을
    안 보고 만든 SWOT'이 정상 산출물처럼 저장된다.
    """
    monkeypatch.setenv(artifact.READ_MODE_ENV, artifact.READ_ARTIFACT_ONLY)
    with pytest.raises(artifact.ArtifactUnavailable) as e:
        agent_fn(_two_dep_state(second_type=second_type, second_key=second_key,
                                drop_second_art=True))
    assert second_type in str(e.value) and "missing" in str(e.value)
    assert "user" not in captured_prompt          # LLM 호출 전에 실패


# ---- depends_on 선언 ↔ 실제 런타임 읽기 ----

_DEPENDENT_AGENTS = {
    "competitor": competitor.competitor,
    "customer": customer.customer,
    "pestel": pestel.pestel,
    "swot": swot.swot,
    "business_model": business_model.business_model,
    "risk": risk.risk,
}


def test_declared_depends_on_matches_actual_runtime_reads(monkeypatch):
    """명세의 `depends_on` 이 **실제로 읽는 Artifact** 와 정확히 일치하는지 확인한다.

    지금까지 `depends_on` 은 코드를 읽고 사람이 적은 선언이었다(artifact.py 설계 메모 참조).
    Agent 간 읽기가 전부 `artifact.read` 를 지나게 된 지금은, 호출을 기록해 선언과 대조할 수
    있다 — 선언이 실제와 어긋나면 PR 6(선택적 Agent 재실행)이 **잘못된 Agent를 재실행**한다.
    """
    monkeypatch.setattr(llm, "is_dummy", lambda: True)
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: {})
    real_read = artifact.read
    id_by_type = {s["artifact_type"]: s["artifact_id"] for s in artifact.LEGACY_ARTIFACT_SPECS}
    spec_by_owner = {s["owner_agent"]: s for s in artifact.LEGACY_ARTIFACT_SPECS}
    # 모든 의존이 채워진 상태(무엇을 읽는지만 보므로 content 는 최소)
    state: ProjectState = {
        "structured_input": {"project_name": "P"},
        "artifacts": [artifact.make_artifact(s["artifact_type"], {"x": 1})
                      for s in artifact.LEGACY_ARTIFACT_SPECS],
    }
    monkeypatch.setenv(artifact.READ_MODE_ENV, artifact.READ_ARTIFACT_ONLY)

    for owner, fn in _DEPENDENT_AGENTS.items():
        seen: list[str] = []
        monkeypatch.setattr(artifact, "read",
                            lambda st, t, _s=seen: (_s.append(t), real_read(st, t))[1])
        fn(state)
        assert {id_by_type[t] for t in seen} == set(spec_by_owner[owner]["depends_on"]), owner


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


# ---- 런타임 읽기 계측 (PR 5d) ----
#
# read_status 의 스냅샷은 '끝난 시점에 쓸 수 있었나'만 답한다. 전환 판단에 필요한 건
# '실제로 몇 번 읽었고 몇 번 떨어졌나'다 — 아무도 안 읽는 Artifact 도, 10번 읽히는
# Artifact 도 스냅샷에서는 똑같이 usable 1 로 보인다.

def _mixed_state() -> dict:
    """research 는 정상, competitor 는 비었고, customer 는 아예 없는 상태."""
    arts = [artifact.make_artifact("research_analysis", {"ok": 1}),
            artifact.make_artifact("competitor_analysis", {})]          # empty
    return {"research_result": {"flat": 1}, "competitor_result": {"flat": 2},
            "customer_result": {"flat": 3}, "artifacts": arts}


_MIXED_TYPES = ["research_analysis", "competitor_analysis", "customer_analysis"]


def _reads_over(state: dict, mode: str, types=None) -> dict:
    """주어진 모드로 여러 번 읽고 집계를 돌려준다(artifact_only 의 실패도 계속 진행)."""
    artifact.reads_start()
    for t in types or _MIXED_TYPES:
        try:
            artifact.get_artifact_content(state, t, artifact.SPEC_BY_TYPE[t]["legacy_key"],
                                          mode=mode)
        except artifact.ArtifactUnavailable:
            pass
    return artifact.reads_summary()


def test_unmeasured_is_not_reported_as_zero_fallbacks():
    """`reads_start()` 없이 부른 집계는 **0 이 아니라 '측정 안 함'** 이어야 한다.

    0 으로 보이면 계측을 안 걸어 둔 실행을 '폴백 한 번도 없었다'로 읽는다 — 근거 없는 안심.
    """
    artifact._reads.set(None)
    s = artifact.reads_summary()
    assert s["measured"] is False and s["total"] == 0 and s["by_type"] == []


def test_runtime_counter_separates_artifact_reads_from_fallbacks():
    s = _reads_over(_mixed_state(), artifact.READ_PREFER_ARTIFACT)
    assert s["measured"] is True and s["total"] == 3
    assert s["from_artifact"] == 1 and s["from_legacy"] == 2
    assert s["fallbacks"] == 2 and s["fallback_reasons"] == {"empty": 1, "missing": 1}


def test_artifact_only_failures_are_counted_not_lost():
    """던지고 끝내면 '몇 번 못 읽었는지'가 기록에 남지 않는다."""
    s = _reads_over(_mixed_state(), artifact.READ_ARTIFACT_ONLY)
    assert s["total"] == 3 and s["from_artifact"] == 1
    assert s["fallbacks"] == 2 and s["fallback_reasons"] == {"empty": 1, "missing": 1}


def test_legacy_mode_measures_would_be_fallbacks_without_switching():
    """legacy 로 도는 동안에도 '전환하면 몇 번 떨어질지'를 잰다 — 값은 그대로 평면 키.

    이게 없으면 준비도를 알려고 운영 트래픽을 실제로 prefer_artifact 로 넘겨 봐야 한다.
    """
    st = _mixed_state()
    s = _reads_over(st, artifact.READ_LEGACY)
    assert s["from_artifact"] == 0 and s["fallbacks"] == 0        # 실제 폴백은 아니다
    assert s["shadow_fallbacks"] == 2
    assert s["shadow_reasons"] == {"empty": 1, "missing": 1}
    # 그러면서 반환값은 legacy 그대로여야 한다(계측이 동작을 바꾸면 안 된다).
    assert artifact.get_artifact_content(st, "research_analysis", "research_result",
                                         mode=artifact.READ_LEGACY) == {"flat": 1}


def test_shadow_measurement_predicts_actual_fallbacks_after_switching():
    """legacy 의 shadow 가 곧 prefer_artifact 의 실제 폴백이어야 예측 지표로 쓸 수 있다."""
    st = _mixed_state()
    shadow = _reads_over(st, artifact.READ_LEGACY)
    actual = _reads_over(st, artifact.READ_PREFER_ARTIFACT)
    assert shadow["shadow_fallbacks"] == actual["fallbacks"]
    assert shadow["shadow_reasons"] == actual["fallback_reasons"]


def test_by_type_shows_which_artifacts_are_actually_read():
    """스냅샷은 못 하는 구분 — 아무도 안 읽은 유형과 여러 번 읽힌 유형."""
    s = _reads_over(_mixed_state(), artifact.READ_PREFER_ARTIFACT,
                    types=["research_analysis", "research_analysis", "customer_analysis"])
    by_type = {r["artifact_type"]: r for r in s["by_type"]}
    assert set(by_type) == {"research_analysis", "customer_analysis"}   # 안 읽은 5개는 없음
    assert by_type["research_analysis"]["reads"] == 2
    assert by_type["research_analysis"]["from_artifact"] == 2
    assert by_type["customer_analysis"]["fallbacks"] == 1


@pytest.mark.parametrize("workflow_mode", ["serial", "parallel"])
@pytest.mark.parametrize("mode", MODES)
def test_run_records_runtime_reads(monkeypatch, mode, workflow_mode):
    """관통 실행이 읽기를 실제로 센다. **병렬도 함께 본다** — 분석 4분기는 fan-out 뒤
    별도 스레드에서 도는데, contextvar 가 그 경계를 넘지 못하면 그쪽 읽기가 통째로 누락돼
    '폴백 0'이 실제보다 좋아 보인다.
    """
    r = _run(monkeypatch, mode, workflow_mode)["artifact_read"]["runtime"]
    assert r["measured"] is True
    assert r["total"] >= len(artifact.LEGACY_ARTIFACT_SPECS)   # 최소한 7유형은 읽힌다
    assert {t["artifact_type"] for t in r["by_type"]} == {s["artifact_type"]
                                                          for s in artifact.LEGACY_ARTIFACT_SPECS}
    if mode == artifact.READ_LEGACY:
        assert r["from_artifact"] == 0 and r["shadow_fallbacks"] == 0
    else:
        assert r["from_artifact"] == r["total"] and r["fallbacks"] == 0


def test_parallel_fanout_reads_are_not_lost(monkeypatch):
    """직렬·병렬의 읽기 건수가 같아야 한다 — 다르면 한쪽이 계측에서 새고 있다."""
    serial = _run(monkeypatch, artifact.READ_PREFER_ARTIFACT, "serial")["artifact_read"]["runtime"]
    par = _run(monkeypatch, artifact.READ_PREFER_ARTIFACT, "parallel")["artifact_read"]["runtime"]
    assert par["total"] == serial["total"]
    assert {(t["artifact_type"], t["reads"]) for t in par["by_type"]} == \
           {(t["artifact_type"], t["reads"]) for t in serial["by_type"]}


def test_revise_measures_its_own_reads_not_the_previous_runs(tmp_path, monkeypatch):
    """`/revise` 도 selector 를 타므로 함께 센다. 단 **직전 `/run` 의 카운트를 이어받으면 안 된다.**

    계측을 요청마다 초기화하지 않으면 수정 실행의 폴백률이 원 실행 값에 섞여, 수정 경로만의
    준비도를 볼 수 없다.
    """
    from fastapi.testclient import TestClient

    from app.main import app
    from app.services import store

    _dummy(monkeypatch)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "projects.db")
    monkeypatch.setenv(artifact.READ_MODE_ENV, artifact.READ_PREFER_ARTIFACT)
    client = TestClient(app)

    run = client.post("/run", json={"project_name": "수정계측", "problem": "P"}).json()
    run_reads = client.get(f"/projects/{run['project_id']}").json()[
        "state"]["artifact_read"]["runtime"]

    client.post("/revise", json={"project_name": "수정계측", "draft": run["final_draft"],
                                 "revision_request": "톤 정리", "project_id": run["project_id"]})
    rev_reads = client.get(f"/projects/{run['project_id']}").json()[
        "state"]["artifact_read"]["runtime"]

    assert run_reads["measured"] and rev_reads["measured"]
    assert 0 < rev_reads["total"] < run_reads["total"]     # 수정 구간만 셈(누적 아님)
    assert rev_reads["fallbacks"] == 0


def test_converted_consumers_have_no_direct_flat_key_reads():
    """**전환한 소비자에 한해** 평면 키 직접 참조가 남지 않았는지 확인한다.

    ⚠️ 보조 수단이다. 문자열 대조라 별칭·주석에 취약하고, 무엇보다 **여기 나열한 모듈만**
    본다 — 아래 `test_unconverted_readers_are_known` 이 '아직 안 옮긴 곳'을 따로 고정한다.
    (실제로 이 테스트는 draft_writer·verifier 만 보던 탓에 Agent 간 읽기 7곳을 통과시켰다.)
    """
    from pathlib import Path

    for mod in (draft_writer, verifier, research,
                competitor, customer, pestel, business_model, swot, risk):
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
        # ✅ Agent 간 읽기 7곳 전부 전환 완료(5c-1 research · 5c-2 단일 의존 4개 ·
        #    5c-3 복수 의존 swot·risk). 이제 문서 내용을 만드는 경로에는 평면 키 직접 읽기가 없다.
        # 남은 것은 **표시·집계 계층** — State 를 그대로 직렬화해 보여줄 뿐 문서 내용을
        # 만들지 않으므로 뒤로 미뤘다(평면 키는 외부 호환용으로 계속 제공한다).
        "app/api/routes.py", "app/services/parallel_bench.py",
    }, found
