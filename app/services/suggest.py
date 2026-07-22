"""프로젝트 입력 자동완성 — 사용자가 채우지 않은 필드만 LLM 1회 호출로 초안 생성.

핵심 정책(입력 보존):
- 사용자가 이미 입력한 필드는 절대 변경하지 않는다(응답에서 None으로 돌려 프론트가 건드리지 않게).
- 사용자가 입력한 값은 '문맥'으로 함께 넘겨, 빈 필드를 그와 일관되게 채운다.
더미 모드/호출 실패 시에는 안전한 fallback(빈 필드에 한함)을 돌려준다.
"""
from __future__ import annotations

from app.prompts.templates import SUGGEST_COMPARE_SYSTEM, SUGGEST_SYSTEM
from app.services import llm

_STR_KEYS = ["description", "target_user", "problem"]
_ALL = [*_STR_KEYS, "keywords"]
_CONF = {"high", "medium", "low"}

# 프롬프트 표기용 한글 라벨
_LABEL = {"description": "서비스 설명", "target_user": "목표 사용자",
          "problem": "해결하려는 문제", "keywords": "키워드"}


def _clean_existing(existing: dict | None) -> dict:
    """사용자가 실제로 입력한(비어있지 않은) 필드만 남긴다."""
    out: dict = {}
    for k in _STR_KEYS:
        v = (existing or {}).get(k)
        if isinstance(v, str) and v.strip():
            out[k] = v.strip()
    kw = (existing or {}).get("keywords")
    if isinstance(kw, list):
        kw = [s.strip() for s in kw if isinstance(s, str) and s.strip()]
        if kw:
            out["keywords"] = kw
    elif isinstance(kw, str) and kw.strip():
        out["keywords"] = [s.strip() for s in kw.split(",") if s.strip()]
    return out


def _dummy(project_name: str, empty: list[str]) -> dict:
    """빈 필드에만 더미 초안을 채운다(사용자 입력 필드는 None)."""
    name = (project_name or "제목 없는 프로젝트").strip()
    base = {
        "description": f"[더미] {name}에 대한 서비스 설명 초안",
        "target_user": "[더미] 핵심 목표 사용자",
        "problem": "[더미] 이 서비스가 해결하려는 사용자 문제",
        "keywords": ["[더미] 키워드1", "[더미] 키워드2"],
    }
    return {k: (base[k] if k in empty else None) for k in _ALL}


def _entry_value(entry, is_list: bool):
    """LLM 항목 응답에서 value 를 뽑는다. {value,reason,...} 객체 또는 값 자체 둘 다 허용."""
    v = entry.get("value") if isinstance(entry, dict) else entry
    if is_list:
        return [s.strip() for s in v if isinstance(s, str) and s.strip()] if isinstance(v, list) else []
    return v.strip() if isinstance(v, str) and v.strip() else ""


def _meta_of(entry) -> dict:
    """항목 응답에서 추천 이유·확신도·참고 입력을 정리한다(없으면 안전 기본값)."""
    reason = entry.get("reason") if isinstance(entry, dict) else ""
    conf = entry.get("confidence") if isinstance(entry, dict) else ""
    based = entry.get("based_on") if isinstance(entry, dict) else []
    return {
        "reason": reason.strip() if isinstance(reason, str) else "",
        "confidence": conf if conf in _CONF else "medium",
        "based_on": [b for b in based if b in _ALL] if isinstance(based, list) else [],
    }


def _validate(result: dict, fallback: dict, empty: list[str]) -> dict:
    """LLM 응답을 정리한다. 빈 필드에만 값을 채우고, 사용자 입력 필드는 None으로 보존.

    각 추천 필드에 추천 이유·확신도(meta)를 함께 담는다(개선안 §5). 빈 필드가 아닌 키에
    값이 와도 무시한다(덮어쓰기 방지). 값이 비면 fallback 초안(확신도 low)으로 채운다.
    반환: {..필드 값.., "meta": {필드: {reason, confidence, based_on}}}.
    """
    if not isinstance(result, dict):
        result = {}
    values: dict = {k: None for k in _ALL}
    meta: dict = {}
    for k in empty:
        val = _entry_value(result.get(k), is_list=(k == "keywords"))
        if val:
            values[k] = val
            meta[k] = _meta_of(result.get(k))
        else:
            values[k] = fallback[k]
            meta[k] = {"reason": "기본 초안(자동 생성)", "confidence": "low", "based_on": []}
    return {**values, "meta": meta}


def _build_user(project_name: str, memo: str, existing: dict, empty: list[str]) -> str:
    lines = [f"[프로젝트명]\n{(project_name or '').strip()}"]
    if memo.strip():
        lines.append(f"[사용자 메모]\n{memo.strip()}")
    if existing:
        ctx = "\n".join(
            f"- {_LABEL[k]}: {', '.join(existing[k]) if isinstance(existing[k], list) else existing[k]}"
            for k in _ALL if k in existing)
        lines.append("[사용자가 이미 작성한 내용 — 절대 변경하지 말고 문맥으로만 활용]\n" + ctx)
    lines.append("[채울 빈 항목]\n" + ", ".join(_LABEL[k] for k in empty))
    return "\n\n".join(lines)


def _build_compare_user(project_name: str, memo: str, existing: dict) -> str:
    lines = [f"[프로젝트명]\n{(project_name or '').strip()}"]
    if memo.strip():
        lines.append(f"[사용자 메모]\n{memo.strip()}")
    if existing:
        cur = "\n".join(
            f"- {_LABEL[k]}: {', '.join(existing[k]) if isinstance(existing[k], list) else existing[k]}"
            for k in _ALL if k in existing)
        lines.append("[사용자 현재 값 — 존중하되 더 구체적·일관된 대안을 제시]\n" + cur)
    lines.append("[요청] 아래 4개 항목 모두에 비교용 제안을 작성: " + ", ".join(_LABEL[k] for k in _ALL))
    return "\n\n".join(lines)


def suggest_fields(project_name: str, memo: str = "", model: str = "",
                   existing: dict | None = None, compare: bool = False) -> dict:
    """프로젝트명(+메모+기존 입력)으로 입력 필드 초안을 추천한다.

    - compare=False(기본): '빈 필드만' 추천, 사용자 입력 필드는 None(보존).
      채울 빈 필드가 없으면 전부 None.
    - compare=True: 4개 항목 '모두'에 대해 비교용 제안을 생성(사용자 입력은 문맥으로만 사용,
      실제 덮어쓰기는 프론트에서 사용자가 선택). 반환은 4개 필드 모두 값(None 없음).
    반환 키: {description, target_user, problem, keywords}.
    """
    filled = _clean_existing(existing)

    if compare:
        fallback = _dummy(project_name, _ALL)
        user = _build_compare_user(project_name, memo, filled)
        raw = llm.complete_json(SUGGEST_COMPARE_SYSTEM, user, fallback=fallback, model=model)
        return _validate(raw, fallback, _ALL)

    empty = [k for k in _ALL if k not in filled]
    if not empty:                       # 모두 채워져 있으면 추천 안 함(입력 보존)
        return {k: None for k in _ALL}
    fallback = _dummy(project_name, empty)
    user = _build_user(project_name, memo, filled, empty)
    raw = llm.complete_json(SUGGEST_SYSTEM, user, fallback=fallback, model=model)
    return _validate(raw, fallback, empty)
