"""`/run/stream` 경로가 그래프 안 reducer 와 같은 결과를 내는지 고정한다.

**왜 이 파일이 필요했나** — `apply_node_update` 는 그래프 '밖'에서 노드 업데이트를 합치는데
(`/run/stream` · `/revise`), reducer 필드를 손으로 나열한다. `kosena` 가 그 목록에서 빠져
있어서 **UI 로 실행하면 KOSENA 준수가 11/28** 로 떨어졌다(스크립트 경유 `/run` 은 정상 26~27/28).
KOSENA Agent 4개는 의도적으로 '자기 키만' 반환하고 병합을 reducer 에 맡기기 때문에,
그래프 밖에서 `dict.update` 로 덮으면 마지막 노드(`kosena_roadmap`)의 10개 키만 남는다.

테스트 667개를 통과하면서 이 결함이 살아 있었던 이유는 **두 실행 경로를 대조하는 테스트가
없었기** 때문이다. 여기서 세 층으로 막는다:
  1) 전체 워크플로 대조 — invoke 와 stream 의 `kosena` 키 집합이 같다
  2) 단위 — `apply_node_update` 가 `kosena` 를 누적한다(덮어쓰지 않는다)
  3) 구조 — `ProjectState` 의 reducer 필드가 늘면 이 테스트가 깨져서 대응을 강제한다

LLM 호출은 더미로 대체(무료·결정론).
"""
from __future__ import annotations

import typing

from app.graph import workflow
from app.schemas.state import ProjectState

# `apply_node_update` 가 그래프 밖에서 누적 처리하는 필드. `ProjectState` 의 reducer 필드와
# **정확히 일치**해야 한다 — 새 reducer 필드를 추가하고 여기를 잊으면 그 값은 stream 경로에서만
# 조용히 사라진다(그래서 재현이 어렵다).
HANDLED_REDUCER_FIELDS = {"logs", "timing_events", "evidence_registry", "artifacts", "kosena"}


def _dummy(monkeypatch):
    from app.services import llm
    monkeypatch.setattr(llm, "is_dummy", lambda: True)


def _stream_final(user_input: dict, mode: str) -> dict:
    """`/run/stream` 이 최종적으로 내보내는 state(= UI 가 화면에 그리는 값)."""
    final = None
    for ev in workflow.run_workflow_stream(dict(user_input), workflow_mode=mode):
        if ev.get("type") == "done":
            final = ev["state"]
    assert final is not None, "stream 이 done 이벤트를 내지 않았다"
    return final


# ---------------------------------------------------------------- 1) 경로 대조

def test_stream_and_invoke_agree_on_kosena_keys(monkeypatch):
    """UI 경로(stream)와 API 경로(invoke)의 KOSENA 산출물이 같아야 한다.

    이 단정이 깨지면 '측정은 스크립트로 하고 시연은 UI 로 하는' 상황에서 화면 수치와
    발표 수치가 어긋난다 — 실제로 그런 상태였다.
    """
    _dummy(monkeypatch)
    ui = {"project_name": "reducer 대조", "problem": "P"}

    invoked = workflow.run_workflow(dict(ui), workflow_mode="parallel")
    streamed = _stream_final(ui, "parallel")

    invoked_keys = set((invoked.get("kosena") or {}).keys())
    streamed_keys = set((streamed.get("kosena") or {}).keys())

    assert invoked_keys, "invoke 경로에서 kosena 가 비어 있다(전제 실패)"
    lost = invoked_keys - streamed_keys
    assert not lost, (
        f"stream 경로에서 KOSENA 키 {len(lost)}개가 사라졌다: {sorted(lost)} — "
        "apply_node_update 의 reducer 필드 목록을 확인하라"
    )
    assert streamed_keys == invoked_keys


def test_stream_and_invoke_agree_on_compliance_count(monkeypatch):
    """준수 판정 수치까지 같아야 한다(키만 있고 내용이 비면 준수 수가 갈린다)."""
    _dummy(monkeypatch)
    ui = {"project_name": "준수 대조", "problem": "P"}

    invoked = workflow.run_workflow(dict(ui), workflow_mode="parallel")
    streamed = _stream_final(ui, "parallel")

    assert (streamed.get("kosena_compliance") or {}).get("ok") == \
           (invoked.get("kosena_compliance") or {}).get("ok")


def test_stream_serial_mode_also_agrees(monkeypatch):
    """직렬 모드에서도 같다 — 이 결함은 실행 구조가 아니라 '그래프 밖 병합'의 문제였다."""
    _dummy(monkeypatch)
    ui = {"project_name": "직렬 대조", "problem": "P"}

    invoked = workflow.run_workflow(dict(ui), workflow_mode="serial")
    streamed = _stream_final(ui, "serial")

    assert set((streamed.get("kosena") or {}).keys()) == set((invoked.get("kosena") or {}).keys())


# ---------------------------------------------------------------- 2) 단위

def test_apply_node_update_accumulates_kosena():
    """KOSENA 노드는 '자기 키만' 반환한다 → 그래프 밖 병합도 누적이어야 한다."""
    state: dict = {}
    workflow.apply_node_update(state, {"kosena": {"porter": {"rivalry": "높음"}, "ksf": [1, 2]}})
    workflow.apply_node_update(state, {"kosena": {"lean_canvas": {"problem": "P"}}})
    workflow.apply_node_update(state, {"kosena": {"wireframes": ["w1"]}})

    assert set(state["kosena"]) == {"porter", "ksf", "lean_canvas", "wireframes"}
    assert state["kosena"]["porter"] == {"rivalry": "높음"}      # 앞 값이 보존된다


def test_apply_node_update_later_key_wins_on_conflict():
    """같은 키가 겹치면 나중 값이 이긴다(그래프 안 merge_kosena 와 같은 규칙)."""
    state: dict = {"kosena": {"ksf": ["old"]}}
    workflow.apply_node_update(state, {"kosena": {"ksf": ["new"]}})
    assert state["kosena"]["ksf"] == ["new"]


def test_apply_node_update_tolerates_empty_kosena():
    """빈 dict·None 이 와도 기존 값을 지우지 않는다."""
    state: dict = {"kosena": {"ksf": ["keep"]}}
    workflow.apply_node_update(state, {"kosena": {}})
    assert state["kosena"] == {"ksf": ["keep"]}
    workflow.apply_node_update(state, {"kosena": None})
    assert state["kosena"] == {"ksf": ["keep"]}


def test_apply_node_update_still_accumulates_list_fields():
    """기존에 처리하던 필드가 회귀하지 않았는지 함께 고정한다."""
    state: dict = {}
    workflow.apply_node_update(state, {"logs": ["a"], "timing_events": [{"node": "n1"}]})
    workflow.apply_node_update(state, {"logs": ["b"], "timing_events": [{"node": "n2"}]})
    assert state["logs"] == ["a", "b"]
    assert [e["node"] for e in state["timing_events"]] == ["n1", "n2"]


# ---------------------------------------------------------------- 3) 구조 가드

def test_every_reducer_field_is_handled_outside_the_graph():
    """`ProjectState` 의 reducer 필드와 `apply_node_update` 의 처리 목록이 일치해야 한다.

    새 reducer 필드를 추가하고 `apply_node_update` 를 잊으면 그 값은 **UI 경로에서만**
    사라진다. 그래프 안 테스트로는 잡히지 않으므로 여기서 구조적으로 막는다.
    """
    hints = typing.get_type_hints(ProjectState, include_extras=True)
    reducer_fields = {
        name for name, hint in hints.items()
        if typing.get_origin(hint) is typing.Annotated
    }

    assert reducer_fields == HANDLED_REDUCER_FIELDS, (
        "ProjectState 의 reducer 필드가 바뀌었다.\n"
        f"  선언된 필드: {sorted(reducer_fields)}\n"
        f"  처리 중인 필드: {sorted(HANDLED_REDUCER_FIELDS)}\n"
        "새 필드를 추가했다면 apply_node_update() 에 누적 규칙을 넣고 이 목록도 갱신하라 — "
        "빠뜨리면 /run/stream(UI) 에서만 값이 조용히 사라진다."
    )
