"""프로젝트명 기반 입력 자동완성 — 나머지 입력 필드를 LLM 1회 호출로 초안 생성.

사용자가 프로젝트명만 넣고 'AI로 채우기'를 누르면, 설명·목표사용자·문제·키워드를 추천해
필드에 채워 넣는다(사용자가 이후 수정). 더미 모드/호출 실패 시에는 안전한 fallback을 돌려준다.
"""
from __future__ import annotations

from app.prompts.templates import SUGGEST_SYSTEM
from app.services import llm

_STR_KEYS = ["description", "target_user", "problem"]


def _dummy(project_name: str) -> dict:
    name = (project_name or "제목 없는 프로젝트").strip()
    return {
        "description": f"[더미] {name}에 대한 서비스 설명 초안",
        "target_user": "[더미] 핵심 목표 사용자",
        "problem": "[더미] 이 서비스가 해결하려는 사용자 문제",
        "keywords": ["[더미] 키워드1", "[더미] 키워드2"],
    }


def _validate(result: dict, fallback: dict) -> dict:
    """LLM 응답을 안전한 형태로 정리한다. 형식이 어긋나면 fallback."""
    if not isinstance(result, dict):
        return dict(fallback)
    out: dict = {}
    for k in _STR_KEYS:
        v = result.get(k)
        out[k] = v.strip() if isinstance(v, str) else ""
    kw = result.get("keywords")
    out["keywords"] = [s.strip() for s in kw if isinstance(s, str) and s.strip()] if isinstance(kw, list) else []
    # 내용이 전혀 없으면 fallback
    if not any(out[k] for k in _STR_KEYS) and not out["keywords"]:
        return dict(fallback)
    return out


def suggest_fields(project_name: str, memo: str = "", model: str = "") -> dict:
    """프로젝트명(+선택 메모)으로 나머지 입력 필드 초안을 추천한다."""
    fallback = _dummy(project_name)
    user = f"[프로젝트명]\n{(project_name or '').strip()}"
    if memo.strip():
        user += f"\n\n[사용자 메모]\n{memo.strip()}"
    raw = llm.complete_json(SUGGEST_SYSTEM, user, fallback=fallback, model=model)
    return _validate(raw, fallback)
