"""LLM 호출 래퍼.

- 키가 없거나 USE_DUMMY=1 이면 '더미 모드'로 동작한다(3일 차 골격 검증용).
- 실제 Agent 구현(4~7일 차)부터는 provider 키를 채우면 자동으로 실제 LLM을 사용한다.
- 각 Agent는 complete_json()/complete_text() 를 호출하고, 더미 모드일 때는
  호출부가 넘긴 fallback 값을 그대로 돌려받는다. 따라서 Agent 코드는
  더미/실제 모드에서 동일한 흐름으로 동작한다.
"""
from __future__ import annotations

import json
import os
import re

from dotenv import load_dotenv

load_dotenv()


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def is_dummy() -> bool:
    """더미 모드 여부. USE_DUMMY=1 이거나 사용 가능한 키가 없으면 True."""
    if _env("USE_DUMMY", "0") == "1":
        return True
    provider = _env("LLM_PROVIDER", "dummy").lower()
    if provider == "anthropic":
        return not _env("ANTHROPIC_API_KEY")
    if provider == "openai":
        return not _env("OPENAI_API_KEY")
    return True


def _get_model():
    """설정된 provider에 맞는 LangChain chat model 반환."""
    provider = _env("LLM_PROVIDER", "dummy").lower()
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=_env("ANTHROPIC_MODEL", "claude-sonnet-5"),
            api_key=_env("ANTHROPIC_API_KEY"),
            temperature=0.3,
        )
    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=_env("OPENAI_MODEL", "gpt-4o-mini"),
            api_key=_env("OPENAI_API_KEY"),
            temperature=0.3,
        )
    raise RuntimeError(f"지원하지 않는 LLM_PROVIDER: {provider}")


def _extract_json(text: str) -> dict:
    """LLM 응답에서 JSON 블록을 추출한다. (11일 차: 파싱 실패 대비)"""
    text = text.strip()
    # ```json ... ``` 코드펜스 제거
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    else:
        brace = re.search(r"\{.*\}", text, re.DOTALL)
        if brace:
            text = brace.group(0)
    return json.loads(text)


def complete_text(system: str, user: str, *, fallback: str = "") -> str:
    """텍스트 응답. 더미 모드면 fallback 반환."""
    if is_dummy():
        return fallback
    model = _get_model()
    resp = model.invoke([("system", system), ("human", user)])
    return resp.content if isinstance(resp.content, str) else str(resp.content)


def complete_json(system: str, user: str, *, fallback: dict) -> dict:
    """JSON 응답. 더미 모드면 fallback 반환. 파싱 실패 시 1회 재시도 후 fallback."""
    if is_dummy():
        return fallback
    model = _get_model()
    for attempt in range(2):  # 11일 차 요구사항: LLM 재호출 1회
        resp = model.invoke([("system", system), ("human", user)])
        content = resp.content if isinstance(resp.content, str) else str(resp.content)
        try:
            return _extract_json(content)
        except (json.JSONDecodeError, ValueError):
            if attempt == 0:
                user = user + "\n\n반드시 유효한 JSON 객체만 출력하세요."
                continue
    return fallback
