"""데모/개발용 장애 주입 — 특정 노드를 일부러 실패시켜 '정직한 미완성 안내'(Phase 1)를 시연.

⚠ 임시 도구. 운영 배포 시 제거하거나 비활성 상태로 둔다. 아무 설정도 없으면 완전 무영향(no-op).

두 경로로 설정(둘 다 있으면 요청 설정 우선):
  1) 요청 단위: /run·/run/stream payload의 demo_fail_nodes / demo_fail_reason (UI 토글)
  2) 환경변수:  DEMO_FAIL_NODES=customer,risk   DEMO_FAIL_REASON=혼잡
원인: 혼잡 | 연결 | 형식 | 처리 (기본 혼잡)

동작: _safe가 노드 진입 시 apply_for_node(state, name)로 '이 노드를 실패시킬 원인'을
현재(노드) 컨텍스트에 기록하고, llm._timed_invoke가 fail_reason_for()로 그 원인을 읽어
해당 LLMError를 던진다 → 정상 fallback 경로로 흘러 fallback_reasons에 잡힌다.

설정 판단을 노드 진입 시 state에서 직접 하므로, 스트리밍(anyio 스레드풀)에서도
값을 같은 스레드 안에서 읽고 쓴다(크로스 스레드 contextvar 전파에 의존하지 않음).
"""
from __future__ import annotations

import contextvars
import os

# 현재 노드에서 주입할 실패 원인(없으면 None). 노드 진입 시 그 노드 스레드에서 설정된다.
_node_fail: contextvars.ContextVar = contextvars.ContextVar("demo_node_fail", default=None)

VALID_REASONS = {"혼잡", "연결", "형식", "처리"}


def _norm_reason(reason: str) -> str:
    reason = (reason or "").strip()
    return reason if reason in VALID_REASONS else "혼잡"


def _env_targets():
    raw = os.getenv("DEMO_FAIL_NODES", "").strip()
    nodes = {n.strip() for n in raw.split(",") if n.strip()}
    if not nodes:
        return None
    return nodes, _norm_reason(os.getenv("DEMO_FAIL_REASON", ""))


def _reason_for(state, node: str) -> str | None:
    """이 노드를 실패시켜야 하면 원인, 아니면 None. 요청 설정(state) 우선, 없으면 env."""
    if not node:
        return None
    ui = (state or {}).get("user_input") or {}
    req_nodes = {str(n).strip() for n in (ui.get("demo_fail_nodes") or []) if str(n).strip()}
    if req_nodes:
        return _norm_reason(ui.get("demo_fail_reason", "")) if node in req_nodes else None
    env = _env_targets()
    if env and node in env[0]:
        return env[1]
    return None


def apply_for_node(state, node: str) -> None:
    """노드 진입 시 호출: 이 노드를 데모용으로 실패시킬 원인을 현재 컨텍스트에 설정."""
    _node_fail.set(_reason_for(state, node))


def fail_reason_for() -> str | None:
    """현재 노드에서 주입할 실패 원인(없으면 None). llm._timed_invoke가 호출."""
    return _node_fail.get()
