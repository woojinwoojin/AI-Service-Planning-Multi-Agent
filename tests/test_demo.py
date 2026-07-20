"""데모용 장애 주입 로직 테스트."""
from __future__ import annotations

import pytest

from app.services import demo


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("DEMO_FAIL_NODES", raising=False)
    monkeypatch.delenv("DEMO_FAIL_REASON", raising=False)
    demo._node_fail.set(None)
    yield


def _state(nodes, reason):
    return {"user_input": {"demo_fail_nodes": nodes, "demo_fail_reason": reason}}


def test_no_config_is_noop():
    demo.apply_for_node({"user_input": {}}, "customer")
    assert demo.fail_reason_for() is None


def test_request_config_targets_selected_node():
    st = _state(["customer", "risk"], "형식")
    demo.apply_for_node(st, "customer")
    assert demo.fail_reason_for() == "형식"
    demo.apply_for_node(st, "risk")
    assert demo.fail_reason_for() == "형식"
    demo.apply_for_node(st, "research")               # 대상 아님
    assert demo.fail_reason_for() is None


def test_empty_nodes_disables():
    demo.apply_for_node(_state([], "혼잡"), "customer")
    assert demo.fail_reason_for() is None


def test_invalid_reason_defaults_to_busy():
    demo.apply_for_node(_state(["customer"], "이상한값"), "customer")
    assert demo.fail_reason_for() == "혼잡"


def test_env_var_config(monkeypatch):
    monkeypatch.setenv("DEMO_FAIL_NODES", "pestel, swot")
    monkeypatch.setenv("DEMO_FAIL_REASON", "연결")
    demo.apply_for_node({"user_input": {}}, "pestel")
    assert demo.fail_reason_for() == "연결"
    demo.apply_for_node({"user_input": {}}, "draft")
    assert demo.fail_reason_for() is None


def test_request_config_overrides_env(monkeypatch):
    monkeypatch.setenv("DEMO_FAIL_NODES", "pestel")
    st = _state(["customer"], "형식")
    demo.apply_for_node(st, "customer")
    assert demo.fail_reason_for() == "형식"            # 요청 설정 우선
    demo.apply_for_node(st, "pestel")
    assert demo.fail_reason_for() is None              # env는 무시됨
