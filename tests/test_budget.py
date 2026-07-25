"""실행별 예산·시간 상한 정책(트랙 D) 테스트.

상한 로드(env)·초과 판정·호출 생략(관통 보장)·표면화를 검증한다. 실제 LLM은 호출하지 않는다.
"""
from __future__ import annotations

from app.services import budget, llm, usage


def test_default_limits_loaded_when_env_absent(monkeypatch):
    for k in ("BUDGET_MAX_LLM_CALLS", "BUDGET_MAX_COST_USD", "BUDGET_MAX_WALL_MS"):
        monkeypatch.delenv(k, raising=False)
    budget.start()
    lim = budget.limits()
    assert lim["max_llm_calls"] == 50 and lim["max_cost_usd"] == 1.0 and lim["max_wall_ms"] == 600_000


def test_env_overrides_and_zero_means_unlimited(monkeypatch):
    monkeypatch.setenv("BUDGET_MAX_LLM_CALLS", "3")
    monkeypatch.setenv("BUDGET_MAX_COST_USD", "0")      # 0 = 무제한
    monkeypatch.setenv("BUDGET_MAX_WALL_MS", "5000")
    budget.start()
    lim = budget.limits()
    assert lim["max_llm_calls"] == 3
    assert lim["max_cost_usd"] is None                  # 무제한
    assert lim["max_wall_ms"] == 5000


def test_should_skip_call_trips_on_call_count():
    usage.start()
    budget.start({"max_llm_calls": 2, "max_cost_usd": None, "max_wall_ms": None})
    assert budget.should_skip_call() is False           # 0건 → 아직 여유
    usage.record("gpt-4o-mini", 10, 10, 1.0, False)
    usage.record("gpt-4o-mini", 10, 10, 1.0, False)     # 2건 도달
    assert budget.should_skip_call() is True            # 상한 도달 → 이후 생략
    st = budget.status()
    assert st["enforced"] is True and "호출수" in st["exceeded"]


def test_should_skip_call_trips_on_cost():
    usage.start()
    budget.start({"max_llm_calls": None, "max_cost_usd": 0.0001, "max_wall_ms": None})
    usage.record("gpt-4o", 1000, 1000, 1.0, False)      # 비용 = (1000*2.5 + 1000*10)/1e6 = 0.0125 > 0.0001
    assert budget.should_skip_call() is True
    assert "비용" in budget.status()["exceeded"]


def test_no_limits_means_never_skip():
    """start() 미호출(상한 없음)이면 항상 호출 허용 — 회귀 없이 기존 동작 유지."""
    budget._limits.set(None)
    assert budget.should_skip_call() is False


def test_llm_skips_calls_over_budget(monkeypatch):
    """예산 상한 도달 후 complete_json 은 실제 호출 없이 fallback 을 돌려주고 사유를 남긴다."""
    monkeypatch.setattr(llm, "is_dummy", lambda: False)
    monkeypatch.setattr(llm, "_get_model", lambda model="": object())

    class FakeResp:
        content = '{"ok": 1}'

    def fake_timed(chat, system, user, model):
        usage.record("gpt-4o-mini", 10, 10, 1.0, False)   # 실제 호출을 흉내 내 소비 누적
        return FakeResp()

    monkeypatch.setattr(llm, "_timed_invoke", fake_timed)

    usage.start()
    budget.start({"max_llm_calls": 2, "max_cost_usd": None, "max_wall_ms": None})
    fb = {"fallback": True}
    r1 = llm.complete_json("s", "u", fallback=fb)
    r2 = llm.complete_json("s", "u", fallback=fb)
    st: dict = {}
    r3 = llm.complete_json("s", "u", fallback=fb, status=st)

    assert r1 == {"ok": 1} and r2 == {"ok": 1}            # 상한 내 2건은 실제 수행
    assert r3 is fb                                        # 3번째는 생략 → fallback
    assert st.get("fallback") is True and st.get("reason") == "예산"
    assert budget.status()["enforced"] is True


def test_run_workflow_surfaces_budget_state():
    """실행 종료 state 에 예산 상태(상한·소비·강제 여부)가 표면화된다(더미: 강제 없음)."""
    from app.graph.workflow import run_workflow

    state = run_workflow({"project_name": "예산", "problem": "P"})
    bgt = state.get("budget") or {}
    assert set(bgt.get("limits", {})) == {"max_llm_calls", "max_cost_usd", "max_wall_ms"}
    assert bgt.get("enforced") is False                   # 더미 실행은 실제 호출 없음 → 강제 안 됨
    assert "spent" in bgt and "exceeded" in bgt
