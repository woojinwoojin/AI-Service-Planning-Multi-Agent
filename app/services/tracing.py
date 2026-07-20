"""Langfuse 관측성 연동 — 워크플로 실행을 트레이스로, 노드/LLM 호출별 지연을 그 아래로 기록.

- LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY 가 없으면 자동으로 비활성(no-op)이다.
  usage.py의 자체 집계와 독립적이라, 키만 넣으면 켜지고 지우면 꺼진다(더미 모드와 동일 철학).
- 콜백을 GRAPH.invoke 한 곳에만 실으면(run_config), LangChain의 config 전파로 각 노드와
  그 안의 chat.invoke까지 자동으로 내려가 하나의 트레이스 아래 중첩된다. 개별 호출을
  손댈 필요가 없다.
"""
from __future__ import annotations

import os
import sys
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


def enabled() -> bool:
    """Langfuse 키가 모두 설정돼 있으면 True."""
    return bool(
        os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()
        and os.getenv("LANGFUSE_SECRET_KEY", "").strip()
    )


def _warn(msg: str) -> None:
    print(f"[tracing] {msg}", file=sys.stderr)


@lru_cache(maxsize=1)
def _handler():
    """LangChain 콜백 핸들러(성공 시 캐시). 키가 없거나 초기화 실패면 None."""
    if not enabled():
        return None
    try:
        from langfuse.langchain import CallbackHandler

        return CallbackHandler()
    except Exception as exc:  # SDK 부재/버전 불일치 등 — 관측성 때문에 파이프라인이 죽지 않게
        _warn(f"Langfuse 초기화 실패 → 비활성 ({type(exc).__name__}: {exc})")
        return None


def run_config(name: str, **attrs) -> dict:
    """GRAPH.invoke(config=...)에 넘길 실행 설정.

    name: 트레이스 이름(예: 서비스 아이디어). attrs: langfuse_session_id / langfuse_user_id /
    langfuse_tags 등 트레이스 속성이나 임의 메타데이터. 비활성이면 빈 dict(무영향).
    """
    handler = _handler()
    if handler is None:
        return {}
    metadata = {k: v for k, v in attrs.items() if v is not None}
    return {"callbacks": [handler], "run_name": name, "metadata": metadata}


@lru_cache(maxsize=1)
def _client():
    if not enabled():
        return None
    try:
        from langfuse import get_client

        return get_client()
    except Exception as exc:
        _warn(f"Langfuse 클라이언트 획득 실패 ({type(exc).__name__}: {exc})")
        return None


def flush() -> None:
    """버퍼된 트레이스를 Langfuse로 즉시 전송(짧게 사는 실행/CLI에서 유실 방지)."""
    client = _client()
    if client is None:
        return
    try:
        client.flush()
    except Exception as exc:
        _warn(f"flush 실패 ({type(exc).__name__}: {exc})")
