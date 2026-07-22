"""병렬 그래프 (PR-3): Research 이후 4분기 병렬 → Draft 합류.

Agent 입력·프롬프트·State 구조는 직렬과 동일하고 '실행 순서만' 다르다.
LLM 호출은 더미/주입 mock 으로 대체(무료·결정론).
"""
from __future__ import annotations

import time

from app.graph import workflow
from app.graph.workflow import run_workflow


def _dummy(monkeypatch):
    from app.services import llm
    monkeypatch.setattr(llm, "is_dummy", lambda: True)


def test_resolve_mode_arg_and_env(monkeypatch):
    monkeypatch.delenv("WORKFLOW_MODE", raising=False)
    assert workflow._resolve_mode(None) == "serial"           # 기본 직렬
    assert workflow._resolve_mode("parallel") == "parallel"
    assert workflow._resolve_mode("PARALLEL") == "parallel"    # 대소문자 무관
    assert workflow._resolve_mode("이상값") == "serial"        # 알 수 없으면 직렬
    monkeypatch.setenv("WORKFLOW_MODE", "parallel")
    assert workflow._resolve_mode(None) == "parallel"          # env 반영
    assert workflow._resolve_mode("serial") == "serial"        # 인자가 env 보다 우선


def _draft_writes(state) -> int:
    return sum(1 for ln in state["logs"] if ln.startswith("[draft_writer] 초안"))


def test_parallel_records_mode_and_draft_runs_once(monkeypatch):
    """병렬 실행: workflow_mode 기록 + Draft 는 4분기 합류 후 '정확히 1회'만 실행(중복/조기 실행 없음)."""
    _dummy(monkeypatch)
    state = run_workflow({"project_name": "병렬", "problem": "P"}, workflow_mode="parallel")
    assert state["workflow_mode"] == "parallel"
    assert _draft_writes(state) == 1                           # fan-in join → 초안 작성 1회
    # 네 분기 노드가 모두 실행됨
    joined = "\n".join(state["logs"])
    for marker in ["[competitor]", "[swot]", "[customer]", "[pestel]", "[risk]", "[business_model]"]:
        assert marker in joined


def test_serial_and_parallel_produce_equivalent_results(monkeypatch):
    """동일 입력·프롬프트에서 직렬/병렬의 Agent 산출물과 최종 Draft 가 동일해야 한다(비열등성 전제)."""
    _dummy(monkeypatch)
    payload = {"project_name": "동등성", "problem": "P", "target_user": "U", "description": "D"}
    s = run_workflow(payload, workflow_mode="serial")
    p = run_workflow(payload, workflow_mode="parallel")
    keys = ["structured_input", "research_result", "competitor_result", "customer_result",
            "swot_result", "business_model_result", "risk_result", "pestel_result",
            "draft", "final_draft", "verification_result"]
    for k in keys:
        assert s.get(k) == p.get(k), f"직렬/병렬 결과 불일치: {k}"


def _fake_real_llm(monkeypatch, per_call_tokens: int = 15):
    """실제 LLM 모드로 두되 호출을 고정 응답으로 대체(무료). 호출당 토큰을 usage 에 기록."""
    from app.services import llm
    monkeypatch.setattr(llm, "is_dummy", lambda: False)
    monkeypatch.setattr(llm, "_get_model", lambda model="": object())
    inp, out = 10, per_call_tokens - 10

    class _Resp:
        content = "{}"
        usage_metadata = {"input_tokens": inp, "output_tokens": out}

    monkeypatch.setattr(llm, "_invoke_with_retry", lambda chat, s, u, attempts=2: _Resp())
    return llm


def test_parallel_usage_collects_all_calls(monkeypatch):
    """리뷰 최우선: 병렬 분기의 모든 LLM 호출이 usage 에 누락 없이 집계되는지 검증.

    ContextVar 로 실행별 collector 를 공유하는데, 병렬 노드가 별도 작업 스레드에서
    record() 를 호출해도 같은 실행의 calls/토큰에 모두 잡혀야 한다(직렬과 동일해야 함).
    """
    _fake_real_llm(monkeypatch, per_call_tokens=15)
    payload = {"project_name": "usage", "problem": "P", "target_user": "U", "description": "D"}
    s = run_workflow(payload, workflow_mode="serial")["usage"]
    p = run_workflow(payload, workflow_mode="parallel")["usage"]
    assert s["calls"] > 0 and p["calls"] > 0                  # 실제로 호출이 기록됨
    assert s["calls"] == p["calls"]                            # 병렬에서 호출 누락 없음
    assert s["total_tokens"] == p["total_tokens"]             # 토큰 합계 동일(누락 없음)
    assert p["total_tokens"] == p["calls"] * 15               # 모든 호출의 토큰이 합산됨
    assert s["fallback_calls"] == 0 and p["fallback_calls"] == 0


def test_parallel_actually_overlaps_nodes(monkeypatch):
    """직접 증거: 병렬 실행 중 최소 2개 노드가 동시에 실행된다(peak concurrency ≥ 2).

    시간 임계값 비교보다 CI 에서 안정적으로 '겹침'을 증명한다.
    """
    import threading
    import time

    from app.services import llm
    monkeypatch.setattr(llm, "is_dummy", lambda: False)
    monkeypatch.setattr(llm, "_get_model", lambda model="": object())
    lock = threading.Lock()
    counters = {"active": 0, "peak": 0}

    class _Resp:
        content = "{}"
        usage_metadata = {}

    def _slow(chat, system, user, attempts=2):
        with lock:
            counters["active"] += 1
            counters["peak"] = max(counters["peak"], counters["active"])
        time.sleep(0.15)
        with lock:
            counters["active"] -= 1
        return _Resp()

    monkeypatch.setattr(llm, "_invoke_with_retry", _slow)
    run_workflow({"project_name": "overlap", "problem": "P"}, workflow_mode="parallel")
    assert counters["peak"] >= 2                               # 노드 실행 구간이 실제로 겹침


def test_parallel_faster_than_serial(monkeypatch):
    """분석 노드가 실제로 겹쳐 실행돼 병렬 wall time 이 직렬보다 짧다(LLM 호출에 고정 지연 주입)."""
    from app.services import llm
    monkeypatch.setattr(llm, "is_dummy", lambda: False)       # 실제 경로(주입된 지연 사용)
    monkeypatch.setattr(llm, "_get_model", lambda model="": object())

    class _Resp:
        content = "{}"
        usage_metadata = {}

    def _slow(chat, system, user, attempts=2):
        time.sleep(0.2)                                       # LLM 호출 1건 = 0.2초로 흉내
        return _Resp()

    monkeypatch.setattr(llm, "_invoke_with_retry", _slow)

    def wall(mode):
        return run_workflow({"project_name": "속도", "problem": "P"},
                            workflow_mode=mode)["usage"]["wall_time_ms"]

    serial_ms = wall("serial")
    parallel_ms = wall("parallel")
    # 분석 6노드가 직렬(6단계)→병렬(최대 2단계 깊이)로 겹치므로 유의미하게 빨라야 한다
    assert parallel_ms < serial_ms * 0.9, f"parallel={parallel_ms} serial={serial_ms}"
