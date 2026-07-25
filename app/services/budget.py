"""실행별 예산·시간 상한 정책 (로드맵 v2 트랙 D).

동적 실행(2-5) 도입 전 반드시 선행돼야 하는 안전장치다. 한 번의 실행이 과도한 LLM 호출·비용·
시간으로 번지지 않도록 상한을 둔다. 상한을 넘어도 **파이프라인을 강제 중단하지 않고**(관통 보장),
이후의 실제 LLM 호출을 생략하고 각 노드의 fallback 으로 완주시킨다 — 즉 '그 지점부터 더 쓰지
않기'. 넘긴 사실은 `state["budget"]` 에 표면화해 화면·이력에서 정직하게 보인다.

- 상한은 실행별로 `usage` 의 실시간 소비(호출수·추정 비용·wall time)와 비교한다.
- 비용은 호출 전 토큰을 알 수 없으므로 '이미 쓴 비용이 상한 이상이면 이후 호출 생략'으로 근사한다.
- env 로 조정한다(`BUDGET_MAX_LLM_CALLS`·`BUDGET_MAX_COST_USD`·`BUDGET_MAX_WALL_MS`). 0/빈값 =
  해당 항목 무제한. 기본값은 정상 실행(호출 ~20·$0.01·~2분)에는 걸리지 않도록 넉넉히 둔다.
- `usage` 와 같이 contextvar 로 실행(호출 스택)별 격리한다.
"""
from __future__ import annotations

import contextvars
import os

from app.services import usage

_limits: contextvars.ContextVar = contextvars.ContextVar("budget_limits", default=None)
_enforced: contextvars.ContextVar = contextvars.ContextVar("budget_enforced", default=None)

# 기본 상한(정상 실행에는 영향 없는 넉넉한 값). env 로 조정, 0 이하는 무제한.
_DEFAULTS = {"max_llm_calls": 50, "max_cost_usd": 1.0, "max_wall_ms": 600_000}


def _num(key: str, default, cast):
    """env 값을 파싱한다. 빈값=기본, 0 이하=무제한(None), 파싱 실패=기본."""
    raw = os.getenv(key, "").strip()
    if not raw:
        return default
    try:
        val = cast(raw)
    except ValueError:
        return default
    return val if val > 0 else None


def _load_limits() -> dict:
    return {
        "max_llm_calls": _num("BUDGET_MAX_LLM_CALLS", _DEFAULTS["max_llm_calls"], int),
        "max_cost_usd": _num("BUDGET_MAX_COST_USD", _DEFAULTS["max_cost_usd"], float),
        "max_wall_ms": _num("BUDGET_MAX_WALL_MS", _DEFAULTS["max_wall_ms"], int),
    }


def start(limits: dict | None = None) -> None:
    """이번 실행의 상한을 설정한다(usage.start 와 함께 호출). 인자 없으면 env 에서 로드."""
    _limits.set(dict(limits) if limits is not None else _load_limits())
    _enforced.set(False)


def limits() -> dict:
    lim = _limits.get()
    return dict(lim) if lim else {}


def _exceeded(lim: dict, spent: dict) -> list[str]:
    """현재 소비가 넘어선 상한 항목명을 돌려준다(무제한 항목은 건너뜀)."""
    out: list[str] = []
    if lim.get("max_llm_calls") and spent["calls"] >= lim["max_llm_calls"]:
        out.append("호출수")
    if lim.get("max_cost_usd") and spent["est_cost_usd"] >= lim["max_cost_usd"]:
        out.append("비용")
    if lim.get("max_wall_ms") and spent["wall_time_ms"] >= lim["max_wall_ms"]:
        out.append("시간")
    return out


def should_skip_call() -> bool:
    """다음 실제 LLM 호출을 생략해야 하는지(이미 상한 도달). 생략 시 관통은 fallback 으로 유지.

    상한이 설정되지 않았으면(start 미호출) 항상 False — 회귀 없이 기존 동작 유지.
    """
    lim = _limits.get()
    if not lim:
        return False
    if _exceeded(lim, usage.live_spend()):
        _enforced.set(True)
        return True
    return False


def status() -> dict:
    """실행 종료 후처리에서 state 에 실을 예산 상태(상한·소비·초과 항목·강제 여부)."""
    lim = _limits.get() or {}
    spent = usage.live_spend()
    return {
        "limits": lim,
        "spent": spent,
        "exceeded": _exceeded(lim, spent) if lim else [],
        "enforced": bool(_enforced.get()),
    }
