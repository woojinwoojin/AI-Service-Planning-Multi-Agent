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
import sys

from dotenv import load_dotenv

load_dotenv()


class LLMError(RuntimeError):
    """LLM 호출이 재시도 후에도 실패했음을 나타낸다."""


def _invoke_with_retry(chat, system: str, user: str, attempts: int = 2):
    """model.invoke를 재시도한다. 모두 실패하면 LLMError를 던진다.

    8일 차: 관통 중 일시적 LLM 오류(레이트리밋/네트워크)가 파이프라인 전체를
    중단시키지 않도록, 호출부(complete_*)가 이 예외를 잡아 fallback으로 넘어간다.
    """
    last_err: Exception | None = None
    for _ in range(attempts):
        try:
            return chat.invoke([("system", system), ("human", user)])
        except Exception as exc:  # provider별 예외 종류가 다양하므로 광범위하게 잡는다
            last_err = exc
    raise LLMError(str(last_err)) from last_err


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


# provider별 선택 가능한 모델 목록.
# label: 화면 표시용, cost: 상대적 비용 감(대략적 참고치, 프론트/문서용).
# 새 모델은 여기에 추가하면 /models 응답과 검증에 함께 반영된다.
AVAILABLE_MODELS: dict[str, list[dict]] = {
    "openai": [
        {"id": "gpt-4o-mini", "label": "GPT-4o mini (저렴·기본)", "cost": "low"},
        {"id": "gpt-4.1-mini", "label": "GPT-4.1 mini (중간)", "cost": "medium"},
        {"id": "gpt-4o", "label": "GPT-4o (고품질)", "cost": "high"},
        {"id": "gpt-4.1", "label": "GPT-4.1 (고품질)", "cost": "high"},
    ],
    "anthropic": [
        {"id": "claude-haiku-4-5", "label": "Claude Haiku 4.5 (저렴)", "cost": "low"},
        {"id": "claude-sonnet-5", "label": "Claude Sonnet 5 (기본)", "cost": "medium"},
        {"id": "claude-opus-4-8", "label": "Claude Opus 4.8 (고품질)", "cost": "high"},
    ],
}


def current_provider() -> str:
    return _env("LLM_PROVIDER", "dummy").lower()


def default_model() -> str:
    """env에 설정된 provider 기본 모델 id."""
    provider = current_provider()
    if provider == "anthropic":
        return _env("ANTHROPIC_MODEL", "claude-sonnet-5")
    if provider == "openai":
        return _env("OPENAI_MODEL", "gpt-4o-mini")
    return "dummy"


def list_models() -> list[dict]:
    """현재 provider에서 선택 가능한 모델 목록."""
    return AVAILABLE_MODELS.get(current_provider(), [])


def resolve_model(requested: str = "") -> str:
    """요청 모델이 현재 provider의 허용목록에 있으면 그대로, 아니면 env 기본값.

    사용자가 잘못된/타 provider 모델을 넘겨도 실행 도중 API 오류로 죽지 않게 한다.
    """
    requested = (requested or "").strip()
    if requested:
        allowed = {m["id"] for m in AVAILABLE_MODELS.get(current_provider(), [])}
        if requested in allowed:
            return requested
    return default_model()


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


def _get_model(model: str = ""):
    """설정된 provider에 맞는 LangChain chat model 반환.

    model 이 주어지면(그리고 허용목록에 있으면) 그 모델을, 아니면 env 기본값을 쓴다.
    """
    provider = current_provider()
    chosen = resolve_model(model)
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=chosen,
            api_key=_env("ANTHROPIC_API_KEY"),
            temperature=0.3,
        )
    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=chosen,
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


def _warn(msg: str) -> None:
    print(f"[llm] {msg}", file=sys.stderr)


def _flag(status: dict | None, reason: str) -> None:
    """호출부가 넘긴 status dict에 'fallback 발생'을 기록한다(로그 표면화용)."""
    if status is not None:
        status["fallback"] = True
        status["reason"] = reason


def mode_label(status: dict | None, model: str = "") -> str:
    """Agent 로그용 실행 모드 문자열. 더미/실제/fallback을 정직하게 구분한다."""
    if is_dummy():
        return "더미"
    if status and status.get("fallback"):
        return f"fallback·{status.get('reason', '오류')}"
    return f"실제 LLM·{resolve_model(model)}"


def complete_text(system: str, user: str, *, fallback: str = "", model: str = "",
                  status: dict | None = None) -> str:
    """텍스트 응답. 더미 모드면 fallback 반환. model로 사용 모델 지정 가능.

    호출/재시도가 모두 실패하면 예외를 전파하지 않고 fallback을 반환한다(관통 보장).
    fallback으로 넘어가면 status['fallback']=True 로 알린다.
    """
    if is_dummy():
        return fallback
    try:
        chat = _get_model(model)
        resp = _invoke_with_retry(chat, system, user)
        return resp.content if isinstance(resp.content, str) else str(resp.content)
    except Exception as exc:
        _warn(f"complete_text 실패 → fallback 사용 ({type(exc).__name__}: {exc})")
        _flag(status, "호출오류")
        return fallback


def complete_json(system: str, user: str, *, fallback: dict, model: str = "",
                  status: dict | None = None) -> dict:
    """JSON 응답. 더미 모드면 fallback 반환.

    - 호출 실패 시 fallback 반환(관통 보장).
    - 파싱 실패 시 1회 재시도 후 fallback.
    fallback으로 넘어가면 status['fallback']=True 로 알린다.
    """
    if is_dummy():
        return fallback
    try:
        chat = _get_model(model)
    except Exception as exc:
        _warn(f"모델 초기화 실패 → fallback 사용 ({type(exc).__name__}: {exc})")
        _flag(status, "호출오류")
        return fallback

    prompt_user = user
    for attempt in range(2):  # 11일 차 요구사항: LLM 재호출 1회
        try:
            resp = _invoke_with_retry(chat, system, prompt_user)
        except LLMError as exc:
            _warn(f"complete_json 호출 실패 → fallback 사용 ({exc})")
            _flag(status, "호출오류")
            return fallback
        content = resp.content if isinstance(resp.content, str) else str(resp.content)
        try:
            return _extract_json(content)
        except (json.JSONDecodeError, ValueError):
            if attempt == 0:
                prompt_user = user + "\n\n반드시 유효한 JSON 객체만 출력하세요."
                continue
    _flag(status, "파싱실패")
    return fallback
