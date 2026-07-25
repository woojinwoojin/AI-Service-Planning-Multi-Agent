"""실행별 예산·시간 상한 정책 (로드맵 v2 트랙 D + 외부 리뷰 3차 트랙 C).

동적 실행(2-5) 도입 전 반드시 선행돼야 하는 안전장치다. 한 번의 실행이 과도한 LLM 호출·비용·
시간으로 번지지 않도록 상한을 둔다. 상한을 넘어도 **파이프라인을 강제 중단하지 않고**(관통 보장),
이후의 실제 LLM 호출을 생략하고 각 노드의 fallback 으로 완주시킨다 — 즉 '그 지점부터 더 쓰지
않기'. 넘긴 사실은 `state["budget"]` 에 표면화해 화면·이력에서 정직하게 보인다.

- 상한은 실행별로 `usage` 의 실시간 소비(호출수·추정 비용·wall time)와 비교한다.
- 비용은 호출 전 토큰을 알 수 없으므로 '이미 쓴 비용이 상한 이상이면 이후 호출 생략'으로 근사한다.
- env 로 조정한다(`BUDGET_MAX_LLM_CALLS`·`BUDGET_MAX_COST_USD`·`BUDGET_MAX_WALL_MS`). 0/빈값 =
  해당 항목 무제한. 기본값은 정상 실행(호출 ~20·$0.01·~2분)에는 걸리지 않도록 넉넉히 둔다.

병렬 실행 대응(리뷰 3차 C):
- 실행 상태를 **공유 가변 객체**(`BudgetState`)로 두고 ContextVar 에는 그 참조만 담는다. bool 을
  ContextVar 에 직접 담으면 자식 스레드의 `.set(True)` 가 부모 컨텍스트로 전파되지 않아, 실제로는
  예산 때문에 호출을 생략했는데 state·UI 에는 `enforced=false` 로 기록될 수 있었다(usage 의 공유
  리스트와 같은 원리로 해결).
- 상한 판정과 호출 승인을 **원자적으로**(`check_and_reserve`, lock) 처리한다. '확인 → 호출'이
  나뉘어 있으면 병렬 Agent 가 동시에 통과해 상한을 넘길 수 있다(상한 10·현재 9 에서 4개 동시 통과).
- 예약 카운터는 provider 로 나간 **호출 시도**를 센다(감소하지 않음). JSON 파싱 재호출·provider
  재시도도 각각 별도 시도로 승인받아야 하므로, 논리 호출 1건이 시도 2건이면 상한도 2건을 쓴다.
  `usage` 의 집계(=논리 호출·토큰·비용, UI 표시용)와는 목적이 달라 따로 세고, 차이는
  `status()["spent"]["provider_attempts"]` 로 함께 노출한다.
"""
from __future__ import annotations

import contextvars
import os
import threading
from dataclasses import dataclass, field

from app.services import usage


@dataclass
class BudgetState:
    """실행 하나의 예산 상태. 참조를 ContextVar 로 공유해 병렬 노드에서도 같은 객체를 갱신한다."""

    limits: dict = field(default_factory=dict)
    enforced: bool = False          # 상한 때문에 실제로 호출을 생략했는가
    reserved_calls: int = 0         # 승인한 provider 호출 시도 수(감소하지 않음)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


_state: contextvars.ContextVar = contextvars.ContextVar("budget_state", default=None)

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
    _state.set(BudgetState(limits=dict(limits) if limits is not None else _load_limits()))


def reset() -> None:
    """상한 미설정 상태로 되돌린다(테스트·독립 호출 경로에서 회귀 없이 통과시키기 위함)."""
    _state.set(None)


def limits() -> dict:
    st = _state.get()
    return dict(st.limits) if st and st.limits else {}


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


def _effective_spend(st: BudgetState) -> dict:
    """호출수는 '기록된 호출'과 '승인한 시도' 중 큰 값으로 본다.

    승인 카운터는 아직 usage 에 기록되지 않은 진행 중 호출까지 포함하므로 병렬 초과를 막고,
    승인 없이 기록되는 경로(호출 없이 fallback 처리된 논리 호출 등)는 usage 쪽이 더 크므로
    둘 중 큰 값을 쓰면 어느 쪽도 과소 집계되지 않는다.
    """
    spent = usage.live_spend()
    return {**spent, "calls": max(spent["calls"], st.reserved_calls)}


def should_skip_call() -> bool:
    """다음 실제 LLM 호출을 생략해야 하는지(이미 상한 도달). 생략 시 관통은 fallback 으로 유지.

    상한이 설정되지 않았으면(start 미호출) 항상 False — 회귀 없이 기존 동작 유지.
    예약은 하지 않는 '사전 확인'이다(모델 초기화 전에 값싸게 걸러내는 용도). 실제 호출 승인은
    `check_and_reserve()` 가 원자적으로 한다.
    """
    st = _state.get()
    if not st or not st.limits:
        return False
    if _exceeded(st.limits, _effective_spend(st)):
        st.enforced = True
        return True
    return False


def check_and_reserve() -> bool:
    """provider 호출 1건을 예산에서 원자적으로 승인한다. True=호출 진행, False=상한 도달로 생략.

    lock 안에서 '판정 → 예약'을 함께 처리하므로, 병렬 Agent 가 동시에 확인해도 상한을 넘겨
    승인되지 않는다. 상한 미설정이면 항상 True(회귀 없음).
    """
    st = _state.get()
    if not st or not st.limits:
        return True
    with st.lock:
        spent = _effective_spend(st)
        if _exceeded(st.limits, spent):
            st.enforced = True
            return False
        st.reserved_calls = spent["calls"] + 1
        return True


def enforced() -> bool:
    """예산 상한 때문에 호출을 생략한 적이 있는가(병렬 노드의 기록도 반영)."""
    st = _state.get()
    return bool(st and st.enforced)


def status() -> dict:
    """실행 종료 후처리에서 state 에 실을 예산 상태(상한·소비·초과 항목·강제 여부)."""
    st = _state.get()
    lim = st.limits if st else {}
    spent = usage.live_spend()
    # 승인한 provider 호출 시도 수를 함께 노출한다 — 논리 호출(usage.calls)보다 크면 재시도·
    # 파싱 재호출이 있었다는 뜻이고, 상한은 이 시도 수를 기준으로 적용됐다.
    spent = {**spent, "provider_attempts": st.reserved_calls if st else 0}
    return {
        "limits": dict(lim),
        "spent": spent,
        "exceeded": _exceeded(lim, _effective_spend(st)) if st and lim else [],
        "enforced": bool(st and st.enforced),
    }
