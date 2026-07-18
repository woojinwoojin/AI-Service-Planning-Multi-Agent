"""테스트 공통 설정.

모든 테스트는 실제 LLM을 호출하지 않는다(무료·빠름·결정론적).
LLM 경로가 필요한 테스트는 monkeypatch로 is_dummy/_get_model/_invoke_with_retry를 대체한다.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def force_real_llm(monkeypatch):
    """is_dummy()=False + LLM 호출을 강제 실패시켜, fallback/관통 경로를 테스트."""
    from app.services import llm

    monkeypatch.setattr(llm, "is_dummy", lambda: False)
    monkeypatch.setattr(llm, "_get_model", lambda model="": object())

    def _boom(*args, **kwargs):
        raise llm.LLMError("강제 실패(테스트)")

    monkeypatch.setattr(llm, "_invoke_with_retry", _boom)
    return llm
