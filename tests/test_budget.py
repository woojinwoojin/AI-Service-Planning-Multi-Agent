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
    budget.reset()
    assert budget.should_skip_call() is False
    assert budget.check_and_reserve() is True


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


# ── 외부 리뷰 3차 트랙 C: 병렬 전파 · 원자적 예약 · 시도별 집계 ──────────────────

def test_enforced_propagates_from_child_thread():
    """C-1: 자식 스레드에서 상한에 걸려도 부모(실행 종료 후처리)의 status 에 enforced 가 잡힌다.

    이전에는 bool 을 ContextVar 에 담아 자식의 .set(True) 가 부모로 전파되지 않았고,
    실제로 호출을 생략했는데 state·UI 에는 enforced=false 로 기록될 수 있었다.
    """
    import contextvars
    import threading

    usage.start()
    budget.start({"max_llm_calls": 1, "max_cost_usd": None, "max_wall_ms": None})
    usage.record("gpt-4o-mini", 10, 10, 1.0, False)      # 1건 기록 → 상한 도달

    result = {}

    def child():
        result["skipped"] = budget.should_skip_call()     # 자식 스레드에서 상한 판정

    ctx = contextvars.copy_context()                      # 병렬 노드와 동일한 전파 방식
    t = threading.Thread(target=ctx.run, args=(child,))
    t.start()
    t.join()

    assert result["skipped"] is True
    assert budget.status()["enforced"] is True            # 부모에서도 보인다(공유 객체)
    assert budget.enforced() is True


def test_check_and_reserve_is_atomic_under_parallel_calls():
    """C-2: 여러 스레드가 동시에 승인 요청해도 상한을 넘겨 통과하지 않는다.

    '확인 → 호출'이 나뉘어 있으면 상한 10·현재 9 에서 4개가 동시에 통과할 수 있었다.
    usage 기록이 아직 없는(진행 중) 호출도 예약 카운터로 세므로 초과 승인이 없어야 한다.
    """
    import contextvars
    import threading

    usage.start()
    budget.start({"max_llm_calls": 10, "max_cost_usd": None, "max_wall_ms": None})
    for _ in range(9):
        usage.record("gpt-4o-mini", 1, 1, 0.1, False)     # 9건 소비 → 남은 여유 1건

    granted: list[bool] = []
    lock = threading.Lock()
    ready = threading.Barrier(4)

    def worker():
        ready.wait()                                      # 4개 스레드를 동시에 출발시킴
        ok = budget.check_and_reserve()
        with lock:
            granted.append(ok)

    threads = []
    for _ in range(4):
        ctx = contextvars.copy_context()
        t = threading.Thread(target=ctx.run, args=(worker,))
        threads.append(t)
        t.start()
    for t in threads:
        t.join()

    assert sum(granted) == 1                              # 남은 1건만 승인
    assert budget.status()["enforced"] is True            # 거절된 시도가 표면화됨
    assert budget.limits()["max_llm_calls"] == 10


def test_reserve_counts_each_attempt_separately():
    """C-2: 승인은 호출 시도 단위 — 상한 3이면 3번만 통과한다(감소하지 않는 예약 카운터)."""
    usage.start()
    budget.start({"max_llm_calls": 3, "max_cost_usd": None, "max_wall_ms": None})
    assert [budget.check_and_reserve() for _ in range(5)] == [True, True, True, False, False]
    assert budget.status()["spent"]["provider_attempts"] == 3


def test_provider_retry_consumes_budget_per_attempt(monkeypatch):
    """C-2: provider 재시도(_invoke_with_retry 내부 2회)도 각각 상한을 소비한다."""
    calls = {"n": 0}

    class FlakyChat:
        def invoke(self, _messages):
            calls["n"] += 1
            raise RuntimeError("provider 오류")

    usage.start()
    budget.start({"max_llm_calls": 5, "max_cost_usd": None, "max_wall_ms": None})
    try:
        llm._invoke_with_retry(FlakyChat(), "s", "u")
    except llm.LLMError as exc:
        assert exc.reason != "예산"                        # 상한 여유 → 예산이 아닌 실패 사유
    assert calls["n"] == 2                                 # 2번 시도
    assert budget.status()["spent"]["provider_attempts"] == 2   # 시도마다 1건씩 소비


def test_retry_stops_immediately_when_budget_exhausted(monkeypatch):
    """C-2: 상한이 이미 도달했으면 재시도하지 않고 '예산' 사유로 즉시 fallback 경로로 간다."""
    calls = {"n": 0}

    class Chat:
        def invoke(self, _messages):
            calls["n"] += 1
            raise RuntimeError("provider 오류")

    usage.start()
    budget.start({"max_llm_calls": 1, "max_cost_usd": None, "max_wall_ms": None})
    usage.record("gpt-4o-mini", 10, 10, 1.0, False)         # 상한 도달
    try:
        llm._invoke_with_retry(Chat(), "s", "u")
        raise AssertionError("LLMError 가 발생해야 한다")
    except llm.LLMError as exc:
        assert exc.reason == "예산"
    assert calls["n"] == 0                                  # provider 호출 자체가 없음
    assert budget.status()["enforced"] is True


def test_json_parse_retry_consumes_budget(monkeypatch):
    """C-2: JSON 파싱 실패로 인한 재호출도 예산을 소비한다(호출당 1회 확인이 아니라 시도별)."""
    monkeypatch.setattr(llm, "is_dummy", lambda: False)

    class BadJson:
        content = "JSON 아님"
        usage_metadata = {"input_tokens": 1, "output_tokens": 1}

    class Chat:
        def invoke(self, _messages):
            return BadJson()

    monkeypatch.setattr(llm, "_get_model", lambda model="": Chat())
    usage.start()
    budget.start({"max_llm_calls": 10, "max_cost_usd": None, "max_wall_ms": None})
    fb = {"fallback": True}
    st: dict = {}
    assert llm.complete_json("s", "u", fallback=fb, status=st) is fb   # 재시도 후에도 실패
    assert st.get("reason") == "형식"
    # 파싱 재호출까지 2번의 provider 호출 시도가 있었고, 각각 상한을 소비했다.
    assert budget.status()["spent"]["provider_attempts"] == 2


def test_reserved_attempts_reconcile_with_recorded_calls():
    """예약 없이 기록된 호출(호출 없이 fallback 처리된 논리 호출 등)도 상한 계산에 반영된다."""
    usage.start()
    budget.start({"max_llm_calls": 2, "max_cost_usd": None, "max_wall_ms": None})
    usage.record("gpt-4o-mini", 10, 10, 1.0, True)          # 예약 경로를 거치지 않은 기록 1건
    assert budget.check_and_reserve() is True               # 남은 1건 승인
    assert budget.check_and_reserve() is False              # 상한 도달
    assert budget.status()["spent"]["provider_attempts"] == 2


def test_run_workflow_surfaces_budget_state():
    """실행 종료 state 에 예산 상태(상한·소비·강제 여부)가 표면화된다(더미: 강제 없음)."""
    from app.graph.workflow import run_workflow

    state = run_workflow({"project_name": "예산", "problem": "P"})
    bgt = state.get("budget") or {}
    assert set(bgt.get("limits", {})) == {"max_llm_calls", "max_cost_usd", "max_wall_ms"}
    assert bgt.get("enforced") is False                   # 더미 실행은 실제 호출 없음 → 강제 안 됨
    assert "spent" in bgt and "exceeded" in bgt
