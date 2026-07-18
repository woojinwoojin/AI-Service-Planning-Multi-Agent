"""단일 LLM 기준선 (10일 차 비교실험용).

Multi-Agent(Research→PESTEL→Draft→Reviewer)와 대비되는 '단일 프롬프트 1회 호출'
방식. 아이디어만 보고 기획서 전체를 한 번에 생성한다. 형식(12섹션 + PESTEL 표)은
Multi-Agent와 동일하게 요구하므로, 점수 차이는 형식이 아니라 '내용 품질'에서 나온다.
"""
from __future__ import annotations

import json

from app.agents.draft_writer import _dummy_draft, _strip_wrapping_fence
from app.prompts.templates import SINGLE_AGENT_SYSTEM
from app.services import llm


def generate(structured_input: dict, model: str = "") -> str:
    """아이디어 → 단일 LLM 호출로 기획서 전체 텍스트."""
    # 더미 모드에서는 Draft의 더미 렌더를 재사용(빈 조사/PESTEL 기반)
    fallback = _dummy_draft(structured_input, {}, {})
    user = (
        "다음 사업 아이디어로 기획서를 작성하세요.\n"
        f"{json.dumps(structured_input, ensure_ascii=False, indent=2)}"
    )
    text = llm.complete_text(SINGLE_AGENT_SYSTEM, user, fallback=fallback, model=model)
    return _strip_wrapping_fence(text)
