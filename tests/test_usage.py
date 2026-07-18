"""실행 관측성(usage) 집계 테스트."""
from __future__ import annotations

from app.services import usage


def test_summary_aggregates_tokens_cost_latency():
    usage.start()
    usage.record("gpt-4o-mini", 1000, 500, 120.0, False)   # 비용 = 1000*0.15/1e6 + 500*0.60/1e6
    usage.record("gpt-4o-mini", 2000, 1000, 80.0, False)
    s = usage.summary()
    assert s["calls"] == 2
    assert s["input_tokens"] == 3000 and s["output_tokens"] == 1500
    assert s["total_tokens"] == 4500
    assert s["latency_ms"] == 200.0
    assert s["fallback_calls"] == 0
    # 비용: (3000*0.15 + 1500*0.60)/1e6 = (450 + 900)/1e6 = 0.00135
    assert s["est_cost_usd"] == round((3000 * 0.15 + 1500 * 0.60) / 1_000_000, 4)


def test_fallback_and_unknown_model():
    usage.start()
    usage.record("모르는모델", 100, 100, 0.0, True)         # 단가표에 없음 → 비용 0
    s = usage.summary()
    assert s["calls"] == 1 and s["fallback_calls"] == 1
    assert s["est_cost_usd"] == 0.0


def test_summary_without_start_is_empty():
    # start() 이전이면(컨텍스트 없음) 기록이 무시되고 0 집계
    usage._calls.set(None)
    usage.record("gpt-4o", 100, 100, 10.0, False)
    assert usage.summary()["calls"] == 0
