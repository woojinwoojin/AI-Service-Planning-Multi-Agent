"""KOSENA M1 Agent 2종 (Porter·Value Chain·KSF / HMW·Lean Canvas) — 실 LLM 호출 없음.

두 가지를 집중해서 고정한다:

  1) **모자란 개수를 지어내 채우지 않는가.** KSF 가 4개면 4개로 두고 검사가 '부분 충족'을
     말하게 해야 한다. 빈 문자열로 5개를 맞추면 검사는 통과하는데 문서엔 빈칸이 남는,
     가장 나쁜 결과가 된다.
  2) **두 노드가 같은 `kosena` 키에 써도 앞 결과가 살아남는가.** reducer 가 없으면 뒤 노드가
     앞 노드 결과를 통째로 덮는다(`artifacts` 가 `operator.add` 를 못 쓴 것과 같은 종류).
"""
from __future__ import annotations

import pytest

from app.agents import kosena_industry as ind
from app.agents import kosena_model as mdl
from app.graph.workflow import run_workflow
from app.schemas import artifact
from app.schemas.state import merge_kosena
from app.services import kosena, llm


@pytest.fixture
def dummy(monkeypatch):
    monkeypatch.setattr(llm, "is_dummy", lambda: True)


# ---- 스키마 강제: 지어내지 않는다 ----

def test_industry_keeps_short_lists_instead_of_padding():
    """KSF 4개는 4개로 남아야 한다 — 5개로 채우면 검사만 속고 문서엔 빈칸이 남는다."""
    out = ind._validate({"ksf": ["a", "b", "c", "d"], "implications": ["x"]}, ind._dummy())
    assert out["ksf"] == ["a", "b", "c", "d"]
    assert out["implications"] == ["x"]
    # 그리고 그 사실이 준수 검사에 그대로 반영돼야 한다.
    assert kosena.evaluate({"kosena": out})["checks"][3]["status"] == kosena.PARTIAL


def test_industry_drops_unknown_forces_and_empty_entries():
    raw = {"porter": {"rivalry": {"level": "높음", "rationale": "r"},
                      "made_up_force": {"level": "높음"},
                      "substitutes": {}}}
    out = ind._validate(raw, ind._dummy())
    assert set(out["porter"]) == {"rivalry"}          # 정의에 없는 force·빈 항목은 제외


def test_industry_caps_lists_at_required_counts():
    out = ind._validate({"ksf": [f"k{i}" for i in range(9)],
                         "implications": [f"i{i}" for i in range(9)],
                         "critical_uncertainties": [{"factor": f"f{i}"} for i in range(9)]},
                        ind._dummy())
    assert len(out["ksf"]) == ind.KSF_COUNT
    assert len(out["implications"]) == ind.IMPLICATION_COUNT
    assert len(out["critical_uncertainties"]) == ind.CU_COUNT


def test_model_keeps_partial_lean_canvas():
    out = mdl._validate({"lean_canvas": {"problem": "p", "uvp": "u"}}, mdl._dummy())
    assert set(out["lean_canvas"]) == {"problem", "uvp"}   # 7블록을 빈 값으로 만들지 않는다


def test_validate_falls_back_only_when_everything_is_empty():
    assert ind._validate({}, ind._dummy())["ksf"] == ind._dummy()["ksf"]
    assert ind._validate("문자열", ind._dummy())["ksf"] == ind._dummy()["ksf"]  # type: ignore[arg-type]


def test_dummy_outputs_are_structurally_complete():
    """더미는 **구조가 완전**해야 키 없이도 배선·검사를 관통 확인할 수 있다.

    M1 8항목 중 `pestel_6` 만 KOSENA Agent 가 아니라 기존 PESTEL Agent 몫이라 함께 넣는다.
    """
    state = {"kosena": {**ind._dummy(), **mdl._dummy()},
             "pestel_result": {f"f{i}": {"content": "c"} for i in range(6)}}
    m1 = [c for c in kosena.evaluate(state)["checks"] if c["module"] == "M1"]
    assert all(c["status"] == kosena.OK for c in m1), [c for c in m1 if c["status"] != kosena.OK]


# ---- reducer: 앞 노드 결과가 살아남는가 ----

def test_merge_kosena_keeps_earlier_keys():
    merged = merge_kosena({"ksf": ["a"], "porter": {"x": 1}}, {"hmw": ["h"]})
    assert set(merged) == {"ksf", "porter", "hmw"}
    assert merge_kosena(None, None) == {}          # type: ignore[arg-type]


def test_second_node_does_not_erase_the_first(dummy, monkeypatch):
    """`kosena_model` 이 돌아도 `kosena_industry` 의 KSF·Porter 가 남아 있어야 한다."""
    state = run_workflow({"project_name": "리듀서", "problem": "P"})
    k = state["kosena"]
    assert k.get("ksf") and k.get("porter")          # industry 결과
    assert k.get("lean_canvas") and k.get("hmw")     # model 결과


# ---- 입력을 selector 로 읽는가 / 의존 순서 ----

def test_industry_reads_inputs_through_selector(monkeypatch, dummy):
    monkeypatch.setenv(artifact.READ_MODE_ENV, artifact.READ_PREFER_ARTIFACT)
    seen: dict = {}
    monkeypatch.setattr(llm, "complete_json",
                        lambda system, user, **k: seen.setdefault("user", user) or {})
    state = {
        "structured_input": {"project_name": "P"},
        "pestel_result": {"정치": {"content": "평면쪽"}},
        "artifacts": [artifact.make_artifact("pestel_analysis",
                                             {"정치": {"content": "아티팩트쪽"}})],
    }
    ind.kosena_industry(state)
    assert "아티팩트쪽" in seen["user"] and "평면쪽" not in seen["user"]


def test_model_receives_ksf_from_the_previous_node(monkeypatch, dummy):
    """HMW 는 **KSF + 시장 Gap** 을 결합해 만들어야 한다(p9) — 순서가 의미를 갖는 지점."""
    seen: dict = {}
    monkeypatch.setattr(llm, "complete_json",
                        lambda system, user, **k: seen.setdefault("user", user) or {})
    mdl.kosena_model({"structured_input": {"project_name": "P"},
                      "kosena": {"ksf": ["결정적 성공요인 A"], "implications": ["시사점 B"]}})
    assert "결정적 성공요인 A" in seen["user"] and "시사점 B" in seen["user"]


def test_prompts_follow_kosena_five_part_structure():
    """KOSENA 는 프롬프트를 [역할][입력][요구사항][출력 형식][검증 조건] 5단으로 쓰라고 한다(p19).

    형식을 지키는 것 자체가 'AI 활용' 평가 항목이라 코드로 고정한다.
    """
    from app.prompts.templates import KOSENA_INDUSTRY_SYSTEM, KOSENA_MODEL_SYSTEM

    for tpl in (KOSENA_INDUSTRY_SYSTEM, KOSENA_MODEL_SYSTEM):
        for part in ("[역할]", "[입력]", "[요구사항]", "[출력 형식]", "[검증 조건]"):
            assert part in tpl, part


# ---- 워크플로 통합 ----

@pytest.mark.parametrize("workflow_mode", ["serial", "parallel"])
def test_both_graphs_run_the_kosena_nodes(dummy, workflow_mode):
    """병렬에서도 fan-in 뒤에 붙어 **정확히 1회씩** 실행돼야 한다."""
    state = run_workflow({"project_name": "그래프", "problem": "P"},
                         workflow_mode=workflow_mode)
    assert not state["failed_nodes"]
    logs = "\n".join(state["logs"])
    assert logs.count("[kosena_industry]") == 1
    assert logs.count("[kosena_model]") == 1


def test_m1_becomes_fully_satisfied(dummy):
    """이 PR 의 목표 — KOSENA M1 8항목이 전부 충족으로 바뀐다."""
    state = run_workflow({"project_name": "M1", "problem": "P"})
    m1 = [c for c in state["kosena_compliance"]["checks"] if c["module"] == "M1"]
    assert len(m1) == 8
    assert all(c["status"] == kosena.OK for c in m1), [c for c in m1 if c["status"] != kosena.OK]


def test_kosena_outputs_persist_through_save_and_reload(dummy, tmp_path, monkeypatch):
    """저장·재조회에서 KOSENA 산출물이 살아남아야 제출본에서 볼 수 있다."""
    from app.services import store

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "p.db")
    state = run_workflow({"project_name": "보존", "problem": "P"})
    pid = store.save_run(state)
    back = store.get_project(pid)["state"]
    assert back["kosena"]["ksf"] and back["kosena"]["lean_canvas"]
    assert back["kosena_compliance"]["ok"] >= 8
