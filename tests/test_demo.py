"""데모용 장애 주입 로직 테스트."""
from __future__ import annotations

import pytest

from app.services import demo


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("DEMO_FAIL_NODES", raising=False)
    monkeypatch.delenv("DEMO_FAIL_REASON", raising=False)
    # 요청 단위 주입은 게이트가 켜져야 동작한다(A-1). 기본 케이스는 켠 상태로 검증하고,
    # off 동작은 아래 전용 테스트에서 확인한다.
    monkeypatch.setenv("ENABLE_DEMO_TOOLS", "1")
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


# ---- 운영 안전 게이트 (A-1) ----

@pytest.mark.parametrize("value", [None, "0", "", "false", "off"])
def test_request_config_ignored_when_tools_disabled(monkeypatch, value):
    """게이트 off(기본)면 요청 payload의 장애 주입을 무시한다."""
    if value is None:
        monkeypatch.delenv("ENABLE_DEMO_TOOLS", raising=False)
    else:
        monkeypatch.setenv("ENABLE_DEMO_TOOLS", value)
    assert demo.tools_enabled() is False
    demo.apply_for_node(_state(["customer"], "형식"), "customer")
    assert demo.fail_reason_for() is None


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_tools_enabled_truthy_values(monkeypatch, value):
    monkeypatch.setenv("ENABLE_DEMO_TOOLS", value)
    assert demo.tools_enabled() is True


def test_env_config_works_even_when_tools_disabled(monkeypatch):
    """운영자가 직접 준 DEMO_FAIL_NODES 는 게이트와 무관하게 동작한다(명시 설정)."""
    monkeypatch.delenv("ENABLE_DEMO_TOOLS", raising=False)
    monkeypatch.setenv("DEMO_FAIL_NODES", "pestel")
    monkeypatch.setenv("DEMO_FAIL_REASON", "연결")
    demo.apply_for_node({"user_input": {}}, "pestel")
    assert demo.fail_reason_for() == "연결"


def test_request_config_ignored_but_env_still_applies_when_disabled(monkeypatch):
    """게이트 off + 요청 주입 시도 → 요청은 무시되고 env 대상만 실패한다."""
    monkeypatch.delenv("ENABLE_DEMO_TOOLS", raising=False)
    monkeypatch.setenv("DEMO_FAIL_NODES", "pestel")
    st = _state(["customer"], "형식")
    demo.apply_for_node(st, "customer")
    assert demo.fail_reason_for() is None               # 요청 대상은 차단
    demo.apply_for_node(st, "pestel")
    assert demo.fail_reason_for() == "혼잡"             # env 대상은 그대로(기본 원인)
