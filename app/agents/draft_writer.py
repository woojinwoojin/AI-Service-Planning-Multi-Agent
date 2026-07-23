"""Draft Writer Agent — 고정 서식 기획서 작성/재작성 + 일관성 편집.

draft(state): 초안 생성 → state["draft"]
revise(state): Reviewer 개선지시 + 사용자 수정요청 반영 재작성 → state["final_draft"]
polish(state): 완성본의 섹션 간 중복 제거·연결 문장 보강(일관성) → state["final_draft"]
"""
from __future__ import annotations

import json
import re

from app.prompts.templates import DRAFT_WRITER_SYSTEM, EDITOR_SYSTEM, REVISER_SYSTEM
from app.schemas.state import ProjectState
from app.services import evidence, llm

# 실제 LLM은 종종 기획서 전체를 ```markdown ... ``` 로 감싸서 반환한다.
# 최종 .md 산출물에 코드펜스가 남지 않도록, 문서 전체를 감싼 펜스만 벗긴다.
_WRAPPING_FENCE = re.compile(r"^\s*```(?:markdown|md)?\s*\n(.*?)\n```\s*$", re.DOTALL)


def _strip_wrapping_fence(text: str) -> str:
    m = _WRAPPING_FENCE.match(text)
    return m.group(1).strip() if m else text.strip()


SECTIONS = [
    "프로젝트 개요", "추진 배경", "문제 정의", "목표 사용자",
    "시장 및 산업 분석", "PESTEL 분석", "SWOT 분석", "제안 서비스", "핵심 기능",
    "차별성", "수익 모델", "기대효과", "추진 계획", "위험요인 및 대응방안",
]


def _missing_sections(text: str) -> list[str]:
    """고정 서식 14개 섹션 중 `## {제목}` 제목이 빠진 것을 찾는다."""
    return [s for s in SECTIONS if f"## {s}" not in text]


_REF_HEADER = "## 참고자료"
_MAX_REFS = 8  # 참고자료 과다 나열 방지(문서 균형)


def _real_sources(state: ProjectState) -> list[str]:
    """참고자료로 인용할 '실제 검색 출처'만 모은다(LLM이 지어낸 sources 는 제외).

    통합 근거 레지스트리(evidence_registry)를 우선 사용한다(로드맵 2-1: 단일 출처 소스).
    레지스트리가 없으면(옛 프로젝트/재작성 등) 기존 source_objects+competitor_sources 로 fallback.
    어느 경로든 실제 웹검색 출처(제목/URL)만 담기므로, 참고자료에 LLM 생성 URL이 섞여
    실제 출처와 구분되지 않던 문제(외부 리뷰 P0-2/P0-3)를 막는다.
    """
    reg = state.get("evidence_registry") or []
    if reg:
        objs = evidence.normalize(reg)  # URL 중복 제거 + 안정 id(제목/URL 보존)
    else:
        objs = list((state.get("research_result") or {}).get("source_objects") or [])
        objs += list(state.get("competitor_sources") or [])
    seen: set[str] = set()
    lines: list[str] = []
    for o in objs:
        if not isinstance(o, dict):
            continue
        url = (o.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        title = (o.get("title") or "").strip()
        lines.append(f"{title} — {url}" if title else url)
    return lines


def _existing_ref_lines(text: str) -> list[str]:
    """본문에 이미 있는 '## 참고자료' 섹션의 항목(- ...)을 추출한다.

    실제 검색 출처 정보가 없는 재작성/편집(/revise·polish, 특히 옛 프로젝트 재조회) 시,
    이전 실행에서 인용했던 참고자료를 잃지 않도록 보존용으로 쓴다.
    """
    idx = text.find(_REF_HEADER)
    if idx == -1:
        return []
    out = []
    for ln in text[idx + len(_REF_HEADER):].splitlines():
        ln = ln.strip()
        if ln.startswith("- "):
            item = ln[2:].strip()
            if item:
                out.append(item)
    return out


def _append_references(text: str, sources: list, preserve_when_empty: bool = True) -> str:
    """실제 출처(제목 — URL 문자열)를 '참고자료' 섹션으로 최종 문서에 인용한다.

    새 출처가 있으면 기존 참고자료 섹션을 중복 없이 제거하고 다시 붙인다(최대 _MAX_REFS개).

    sources 가 비었을 때:
    - preserve_when_empty=True(기본): 본문을 그대로 반환(기존 '## 참고자료'를 지우지 않음).
      재작성/편집 경로에서 이전 인용을 보존하기 위함(회귀 버그 #2).
    - preserve_when_empty=False: 기존 참고자료 섹션을 제거한다. 실제 검색 출처가 없는데
      LLM이 지어낸 참고자료가 초안에 남는 것을 막기 위함(draft 최초 생성 경로).
    """
    real = [s.strip() for s in (sources or []) if isinstance(s, str) and s.strip()][:_MAX_REFS]
    idx = text.find(_REF_HEADER)
    if not real:
        if preserve_when_empty:
            return text.rstrip()
        return text[:idx].rstrip() if idx != -1 else text.rstrip()
    if idx != -1:
        text = text[:idx].rstrip()
    return "\n".join([text.rstrip(), "", _REF_HEADER, ""] + [f"- {s}" for s in real])


def _generate(system: str, user: str, fallback: str, model: str,
              status: dict | None = None) -> tuple[str, list[str]]:
    """기획서를 생성하고 서식(14섹션)을 검증한다.

    실제 모드에서 섹션이 누락되면 누락 목록을 명시해 1회만 교정 재호출한다(안정 생성).
    LLM 오류로 fallback되면 status['fallback']=True 로 알린다.
    반환: (본문, 최종적으로 남은 누락 섹션 목록).
    """
    text = _strip_wrapping_fence(
        llm.complete_text(system, user, fallback=fallback, model=model, status=status)
    )
    missing = _missing_sections(text)
    if missing and not llm.is_dummy():
        fix_user = (
            f"{user}\n\n[중요] 아래 섹션이 누락되었습니다. 14개 섹션 전체를 "
            f"고정 순서·제목(`## `)으로 빠짐없이 다시 작성하세요: {', '.join(missing)}"
        )
        text = _strip_wrapping_fence(
            llm.complete_text(system, fix_user, fallback=fallback, model=model, status=status)
        )
        missing = _missing_sections(text)
    return text, missing


def _dummy_draft(si: dict, research: dict, pestel: dict) -> str:
    name = si.get("project_name", "프로젝트")
    lines = [f"# {name} 기획서\n"]
    for sec in SECTIONS:
        lines.append(f"## {sec}\n")
        if sec == "문제 정의":
            lines.append(f"{si.get('problem', '(미입력)')}\n")
        elif sec == "목표 사용자":
            lines.append(f"{si.get('target_user', '(미입력)')}\n")
        elif sec == "시장 및 산업 분석":
            lines.append(f"- 시장 개요: {research.get('market_overview', '')}")
            lines.append(f"- 트렌드: {', '.join(research.get('industry_trends', []))}\n")
        elif sec == "PESTEL 분석":
            lines.append("| 요인 | 주요 내용 | 기회 | 위협 | 대응 |")
            lines.append("|---|---|---|---|---|")
            for factor, v in pestel.items():
                lines.append(
                    f"| {factor} | {v.get('content', '')} | {v.get('opportunity', '')} "
                    f"| {v.get('threat', '')} | {v.get('response', '')} |"
                )
            lines.append("")
        elif sec == "제안 서비스":
            lines.append(f"{si.get('description', '(미입력)')}\n")
        else:
            lines.append(f"[더미] {sec} 내용이 여기에 작성됩니다.\n")
    return "\n".join(lines)


def draft(state: ProjectState) -> dict:
    si = state.get("structured_input", {})
    research = state.get("research_result", {})
    pestel = state.get("pestel_result", {})
    comp = state.get("competitor_result", {})
    cust = state.get("customer_result", {})
    swot = state.get("swot_result", {})
    bizmodel = state.get("business_model_result", {})
    risks = state.get("risk_result", {})
    fallback = _dummy_draft(si, research, pestel)

    user = (
        "아래 정보를 바탕으로 고정 서식 기획서를 Markdown으로 작성하세요.\n"
        "'문제 정의'와 '목표 사용자'는 고객 문제 분석, '차별성'은 경쟁사 분석, 'SWOT 분석'은 SWOT 결과,\n"
        "'수익 모델'은 비즈니스 모델 결과, '위험요인 및 대응방안'은 리스크 분석 결과를 근거로 구체적으로 작성하세요.\n"
        f"[입력]\n{json.dumps(si, ensure_ascii=False)}\n"
        f"[시장조사]\n{json.dumps(research, ensure_ascii=False)}\n"
        f"[고객 문제]\n{json.dumps(cust, ensure_ascii=False)}\n"
        f"[경쟁사 분석]\n{json.dumps(comp, ensure_ascii=False)}\n"
        f"[SWOT]\n{json.dumps(swot, ensure_ascii=False)}\n"
        f"[비즈니스 모델]\n{json.dumps(bizmodel, ensure_ascii=False)}\n"
        f"[리스크]\n{json.dumps(risks, ensure_ascii=False)}\n"
        f"[PESTEL]\n{json.dumps(pestel, ensure_ascii=False)}"
    )
    status: dict = {}
    text, missing = _generate(DRAFT_WRITER_SYSTEM, user, fallback, state.get("model", ""), status)
    # 실제 검색 출처만 인용한다. 검색 근거가 없으면 LLM이 지어낸 참고자료를 남기지 않는다.
    text = _append_references(text, _real_sources(state), preserve_when_empty=False)

    mode = llm.mode_label(status, state.get("model", ""))
    note = "" if not missing else f" ⚠ 누락 섹션 {len(missing)}개: {', '.join(missing)}"
    logs = [f"[draft_writer] 초안 작성 완료 ({mode}){note}"]
    return {"draft": text, "logs": logs}


def revise(state: ProjectState) -> dict:
    """Reviewer 개선지시(및 사용자 수정요청)를 1회 반영해 최종본 생성."""
    current = state.get("draft", "")
    review = state.get("review_result", {})
    instructions = review.get("revision_instructions", [])
    user_request = state.get("user_input", {}).get("revision_request", "")

    fallback = (
        current
        + "\n\n---\n\n> [더미 재작성] 반영한 개선 지시:\n"
        + "\n".join(f"> - {i}" for i in instructions)
        + (f"\n> 사용자 수정요청: {user_request}" if user_request else "")
    )

    user = (
        f"[기존 초안]\n{current}\n\n"
        f"[Reviewer 수정 지시]\n{json.dumps(instructions, ensure_ascii=False)}\n"
        + (f"[사용자 수정요청]\n{user_request}\n" if user_request else "")
        + "위 지시를 반영해 기획서를 1회 재작성하세요."
    )
    status: dict = {}
    text, missing = _generate(REVISER_SYSTEM, user, fallback, state.get("model", ""), status)
    # 실제 검색 출처 우선. 없으면(옛 상태 등) 재작성 결과에 이미 있던 참고자료를 보존한다.
    text = _append_references(text, _real_sources(state) or _existing_ref_lines(text))

    count = state.get("revision_count", 0) + 1
    mode = llm.mode_label(status, state.get("model", ""))
    note = "" if not missing else f" ⚠ 누락 섹션 {len(missing)}개"
    logs = [f"[draft_writer] 재작성 완료 (revision={count}, {mode}){note}"]
    return {"final_draft": text, "revision_count": count, "logs": logs}


def polish(state: ProjectState) -> dict:
    """완성본의 섹션 간 중복 제거·연결 문장 보강(일관성 편집). 구조·표·참고자료는 유지.

    편집기가 URL을 훼손하지 않도록 참고자료는 떼고 본문만 편집한 뒤 다시 붙인다.
    편집 결과가 14섹션을 유지하지 못하면 원본 본문을 그대로 쓴다(안전).
    """
    text = state.get("final_draft", "") or state.get("draft", "")
    if llm.is_dummy() or not text.strip():
        return {}

    # 실제 검색 출처 우선. 없으면 편집 전 본문에 있던 참고자료를 보존한다(인용 유실 방지).
    sources = _real_sources(state) or _existing_ref_lines(text)
    body = text.split(f"\n{_REF_HEADER}")[0].rstrip()  # 참고자료 분리

    status: dict = {}
    edited = _strip_wrapping_fence(
        llm.complete_text(EDITOR_SYSTEM, body, fallback=body, model=state.get("model", ""), status=status)
    )
    if _missing_sections(edited):  # 편집이 구조를 깨면 원본 유지
        edited = body
    final = _append_references(edited, sources)

    mode = llm.mode_label(status, state.get("model", ""))
    logs = [f"[polish] 일관성 편집 완료 ({mode})"]
    return {"final_draft": final, "logs": logs}
