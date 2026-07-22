"""실행 관측성 — LLM 호출별 토큰·지연을 모아 실행 단위로 집계.

llm.complete_*가 매 호출마다 record()로 (모델·입출력 토큰·지연)을 남기고,
run_workflow가 실행 시작에 start(), 종료에 summary()로 총 토큰·추정 비용·지연을 얻는다.
contextvar를 써서 실행(호출 스택)별로 격리한다.
"""
from __future__ import annotations

import contextvars
import time

_calls: contextvars.ContextVar = contextvars.ContextVar("llm_calls", default=None)
# 실행 시작 시각(perf_counter). wall time(사용자 실제 대기시간) 산출용.
_t0: contextvars.ContextVar = contextvars.ContextVar("run_t0", default=None)

# 1M 토큰당 (입력, 출력) 단가(USD) — 추정치. 없는 모델은 0으로 계산.
PRICES: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-opus-4-8": (15.00, 75.00),
}


def start() -> None:
    """이번 실행의 호출 기록을 초기화하고 시작 시각을 기록."""
    _calls.set([])
    _t0.set(time.perf_counter())


def record(model: str, input_tokens: int, output_tokens: int, latency_ms: float, fallback: bool) -> None:
    calls = _calls.get()
    if calls is None:
        return
    calls.append({
        "model": model,
        "input_tokens": int(input_tokens or 0),
        "output_tokens": int(output_tokens or 0),
        "latency_ms": round(latency_ms, 1),
        "fallback": bool(fallback),
    })


def _cost(model: str, inp: int, out: int) -> float:
    pin, pout = PRICES.get(model, (0.0, 0.0))
    return (inp * pin + out * pout) / 1_000_000


def summary() -> dict:
    """이번 실행의 집계: 호출 수·토큰·추정 비용(USD)·지연·fallback 수.

    지연 지표는 둘로 나눈다(병렬화 비교의 핵심):
    - llm_latency_sum_ms: LLM 호출 시간의 '합'. 병렬 실행이면 실제 대기시간보다 커진다.
    - wall_time_ms: start()~summary() 사이 실제 경과(사용자 대기시간). 병렬화 성능의 주 지표.
    latency_ms 는 하위호환용 별칭(= llm_latency_sum_ms)으로 유지한다(기존 UI·테스트).
    """
    calls = _calls.get() or []
    inp = sum(c["input_tokens"] for c in calls)
    out = sum(c["output_tokens"] for c in calls)
    cost = round(sum(_cost(c["model"], c["input_tokens"], c["output_tokens"]) for c in calls), 4)
    llm_latency_sum = round(sum(c["latency_ms"] for c in calls), 1)
    t0 = _t0.get()
    wall_ms = round((time.perf_counter() - t0) * 1000, 1) if t0 is not None else 0.0
    return {
        "calls": len(calls),
        "input_tokens": inp,
        "output_tokens": out,
        "total_tokens": inp + out,
        "est_cost_usd": cost,
        "llm_latency_sum_ms": llm_latency_sum,
        "wall_time_ms": wall_ms,
        "latency_ms": llm_latency_sum,   # 하위호환 별칭
        "fallback_calls": sum(1 for c in calls if c["fallback"]),
    }
