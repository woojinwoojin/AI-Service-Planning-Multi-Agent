"""정보 신뢰성 한계 문구 — UI·내보내기(MD/DOCX)·실행 JSON 3곳이 쓰는 단일 소스.

현재 시스템은 근거 추적성(참고 출처 표시)은 보장하나 URL 원문의 사실성은 재검증하지 않는다.
그 '검증 범위와 한계'를 세 표시 지점에서 **동일한 문구**로 정직하게 명시하기 위해, 문구·메타를
이 한 곳에서만 정의한다(중복·표류 방지). UI(JS)는 API 응답의 verification_summary.note 를
그대로 재사용하므로 문장을 다시 적지 않는다.
"""
from __future__ import annotations

from copy import deepcopy

DISCLAIMER_TEXT = (
    "본 기획서는 근거 추적성(참고 출처 표시)은 보장하지만, 출처 URL의 원문 사실성은 "
    "재검증하지 않았습니다. 검증 판정은 수집된 검색 요약 근거와의 일치 여부이며, "
    "수치·통계 등 정량 정보는 원문에서 직접 확인하시기 바랍니다."
)

# 실행 JSON·API 응답에 담는 구조화 메타. note 에 같은 문구를 포함해 UI 가 재사용한다.
VERIFICATION_SUMMARY: dict = {
    "scope": "search_snippet_only",     # 검증 범위: 검색 요약 근거 기준
    "original_document_checked": False,  # URL 원문 확인 안 함
    "fact_check_completed": False,       # 외부 사실성 검증 완료 아님
    "note": DISCLAIMER_TEXT,
}

def summary() -> dict:
    """VERIFICATION_SUMMARY 의 '복사본'을 돌려준다.

    공유 상수 dict 를 그대로 state·응답에 넣으면 어느 호출부가 수정할 때 원본이 오염될 수
    있으므로, 매번 deepcopy 한 사본을 준다(잠재적 부작용 차단).
    """
    return deepcopy(VERIFICATION_SUMMARY)


# 내보내기 문서(MD/DOCX)에 붙일 섹션. DOCX 렌더러가 처리하는 요소(## 제목 + 문단)만 쓴다.
_MARKER = "검증 범위 및 한계"
DISCLAIMER_MD = f"\n\n## {_MARKER}\n\n{DISCLAIMER_TEXT}\n"


def append_disclaimer(markdown: str) -> str:
    """문서 markdown 끝에 한계 문구 섹션을 1회만 덧붙인다(이미 있으면 그대로 반환).

    내보내기(MD/DOCX) 경계에서만 호출한다 — final_draft 본문에 저장하면 /revise 재작성 시
    참고자료 재구성 과정에서 잘려나갈 수 있으므로, 파이프라인 밖(출력 시)에서 붙인다.
    """
    md = markdown or ""
    if _MARKER in md:
        return md
    return md.rstrip() + DISCLAIMER_MD
