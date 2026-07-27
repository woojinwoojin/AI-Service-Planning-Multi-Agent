"""공개 배포용 요청 상한 (상시 트랙 E · Phase 7 사용자 테스트).

기존 `budget.py` 는 **실행 1건 안의** 상한(LLM 호출수·비용·wall time)이다. 공개 주소에서
문제가 되는 축은 다르다 — **실행이 몇 번 일어나는가**. 링크가 퍼지면 실행당 상한을 전부
지켜도 크레딧이 마른다(`/run` 1건 ≈ LLM 13~15콜 ≈ $0.012). 그래서 여기서는 세 가지를
따로 센다:

  1) **IP 당 빈도**    — 한 사람이 연타하는 것을 막는다
  2) **전역 일일 실행 수** — 하루에 몇 건까지 허용할지
  3) **전역 일일 비용**   — 실행이 예상보다 비싸질 때의 최종 방어선

**기본값은 전부 0 = 비활성**이다. 로컬·CI·테스트 동작은 바뀌지 않고, 공개 배포에서만 켠다
(`ARTIFACT_READ_MODE`·`ENABLE_DEMO_TOOLS` 와 같은 옵트인 방식).

⚠️ **한계를 분명히 해 둔다.**
- 카운터는 **프로세스 메모리**에 있다. 인스턴스가 여러 개면 각자 세므로 전역 상한이 인스턴스
  수만큼 곱해진다. Cloud Run 은 `--max-instances=1` 로 묶어 쓴다(부하 관측상 한 인스턴스가
  동시 5를 지연 증가 없이 처리한다). 재시작하면 카운터도 초기화된다.
- IP 는 `X-Forwarded-For` 에서 얻으므로 **위조 가능**하다. 그래서 IP 제한은 '실수·연타 방지'
  수준으로 보고, **실제 보증은 전역 일일 상한**이 한다(IP 와 무관하게 걸린다).
- 비용은 **집계된 실측치**를 누적한다(추정 선차감이 아니다). 따라서 동시 요청이 몰리면 상한을
  마지막 몇 건만큼 넘을 수 있다 — 정확한 정산이 아니라 **폭주 차단**이 목적이다.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone

# 상한이 적용되는 경로 — 실제로 LLM 을 태우는 것만. 조회·내보내기는 대상이 아니다.
GUARDED_PATHS = frozenset({"/run", "/run/stream", "/revise"})

_lock = threading.Lock()
_ip_hits: dict[str, list[float]] = {}     # ip -> 최근 요청 시각(monotonic)
_day: str = ""                            # 현재 집계 중인 UTC 날짜
_day_runs: int = 0
_day_cost: float = 0.0


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


def limits() -> dict:
    """현재 상한 설정. 0 이면 해당 축 비활성.

    매 요청 읽는다 — 재기동 없이 조일 수 있어야 한다(공개 중 사고 대응).
    """
    return {
        "max_runs_per_ip": _env_int("PUBLIC_MAX_RUNS_PER_IP", 0),
        "ip_window_sec": _env_int("PUBLIC_IP_WINDOW_SEC", 3600),
        "max_runs_per_day": _env_int("PUBLIC_MAX_RUNS_PER_DAY", 0),
        "max_cost_per_day_usd": _env_float("PUBLIC_MAX_COST_PER_DAY_USD", 0.0),
    }


def enabled() -> bool:
    lim = limits()
    return any((lim["max_runs_per_ip"], lim["max_runs_per_day"], lim["max_cost_per_day_usd"]))


def _roll_day_locked() -> None:
    """UTC 날짜가 바뀌면 일일 카운터를 리셋한다(호출자가 lock 보유)."""
    global _day, _day_runs, _day_cost
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if today != _day:
        _day, _day_runs, _day_cost = today, 0, 0.0


def client_ip(headers, fallback: str = "") -> str:
    """요청자 IP. Cloud Run 등 프록시 뒤에서는 `X-Forwarded-For` 의 첫 항목이 클라이언트다.

    ⚠️ 클라이언트가 직접 이 헤더를 보내면 앞에 끼워 넣을 수 있다(위조 가능). 위에 적은 대로
    IP 제한은 보증이 아니라 완충이고, 보증은 전역 일일 상한이 한다.
    """
    xff = (headers.get("x-forwarded-for") or "").split(",")[0].strip()
    return xff or fallback or "unknown"


def check(ip: str) -> tuple[bool, str]:
    """이 요청을 받아도 되는지. `(허용여부, 거절 사유 메시지)`.

    사유는 **사용자에게 그대로 보여줄 문장**이다 — 무엇에 걸렸고 언제 풀리는지 알려준다.
    """
    global _day_runs
    lim = limits()
    if not any((lim["max_runs_per_ip"], lim["max_runs_per_day"], lim["max_cost_per_day_usd"])):
        return True, ""
    now = time.monotonic()
    with _lock:
        _roll_day_locked()
        if lim["max_runs_per_day"] and _day_runs >= lim["max_runs_per_day"]:
            return False, (f"오늘 이 서비스의 실행 한도({lim['max_runs_per_day']}건)를 모두 "
                           "사용했습니다. 내일(UTC 기준) 다시 시도해 주세요.")
        if lim["max_cost_per_day_usd"] and _day_cost >= lim["max_cost_per_day_usd"]:
            return False, ("오늘 이 서비스의 사용 예산을 모두 사용했습니다. "
                           "내일(UTC 기준) 다시 시도해 주세요.")
        if lim["max_runs_per_ip"]:
            window = lim["ip_window_sec"]
            hits = [t for t in _ip_hits.get(ip, []) if now - t < window]
            _ip_hits[ip] = hits
            if len(hits) >= lim["max_runs_per_ip"]:
                mins = max(1, int((window - (now - hits[0])) / 60))
                return False, (f"요청이 너무 잦습니다. {window // 60}분 안에 "
                               f"{lim['max_runs_per_ip']}건까지 실행할 수 있습니다. "
                               f"약 {mins}분 뒤 다시 시도해 주세요.")
            hits.append(now)
        _day_runs += 1
    return True, ""


def record_cost(cost_usd: float) -> None:
    """실행이 끝난 뒤 **실측 비용**을 일일 누계에 더한다(다음 요청의 판정에 쓰인다)."""
    global _day_cost
    if not cost_usd:
        return
    with _lock:
        _roll_day_locked()
        _day_cost = round(_day_cost + float(cost_usd), 6)


def status() -> dict:
    """현재 소비 상태(관측용 — `/health` 에 노출).

    상한이 꺼져 있으면 `enabled: False` 로만 알린다. 켜져 있는데 얼마나 남았는지 안 보이면
    운영자가 '왜 429 가 나는지'를 알 수 없다.
    """
    lim = limits()
    with _lock:
        _roll_day_locked()
        runs, cost = _day_runs, _day_cost
    return {
        "enabled": enabled(),
        "limits": lim,
        "today_utc": _day,
        "runs_today": runs,
        "cost_today_usd": round(cost, 4),
        # 단일 인스턴스 전제를 값으로도 남긴다 — 여러 인스턴스면 이 숫자는 인스턴스별 집계다.
        "scope": "single_process",
    }


def reset() -> None:
    """테스트용 초기화(운영 경로에서는 쓰지 않는다)."""
    global _day, _day_runs, _day_cost
    with _lock:
        _ip_hits.clear()
        _day, _day_runs, _day_cost = "", 0, 0.0
