"""Draft Writer Agent — 고정 서식 기획서 작성 및 1회 재작성 (6일 차 구현 예정).

draft(state): 초안 생성 → state["draft"]
revise(state): Reviewer 개선지시 + 사용자 수정요청 반영 재작성 → state["final_draft"]
"""
from __future__ import annotations

import json
import re

from app.prompts.templates import DRAFT_WRITER_SYSTEM, REVISER_SYSTEM
from app.schemas.state import ProjectState
from app.services import llm

# 실제 LLM은 종종 기획서 전체를 ```markdown ... ``` 로 감싸서 반환한다.
# 최종 .md 산출물에 코드펜스가 남지 않도록, 문서 전체를 감싼 펜스만 벗긴다.
_WRAPPING_FENCE = re.compile(r"^\s*```(?:markdown|md)?\s*\n(.*?)\n```\s*$", re.DOTALL)


def _strip_wrapping_fence(text: str) -> str:
    m = _WRAPPING_FENCE.match(text)
    return m.group(1).strip() if m else text.strip()


SECTIONS = [
    "프로젝트 개요", "추진 배경", "문제 정의", "목표 사용자",
    "시장 및 산업 분석", "PESTEL 분석", "제안 서비스", "핵심 기능",
    "차별성", "기대효과", "추진 계획", "위험요인 및 대응방안",
]


def _missing_sections(text: str) -> list[str]:
    """고정 서식 12개 섹션 중 `## {제목}` 제목이 빠진 것을 찾는다."""
    return [s for s in SECTIONS if f"## {s}" not in text]


def _generate(system: str, user: str, fallback: str, model: str) -> tuple[str, list[str]]:
    """기획서를 생성하고 서식(12섹션)을 검증한다.

    실제 모드에서 섹션이 누락되면 누락 목록을 명시해 1회만 교정 재호출한다(안정 생성).
    반환: (본문, 최종적으로 남은 누락 섹션 목록).
    """
    text = _strip_wrapping_fence(llm.complete_text(system, user, fallback=fallback, model=model))
    missing = _missing_sections(text)
    if missing and not llm.is_dummy():
        fix_user = (
            f"{user}\n\n[중요] 아래 섹션이 누락되었습니다. 12개 섹션 전체를 "
            f"고정 순서·제목(`## `)으로 빠짐없이 다시 작성하세요: {', '.join(missing)}"
        )
        text = _strip_wrapping_fence(llm.complete_text(system, fix_user, fallback=fallback, model=model))
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
    fallback = _dummy_draft(si, research, pestel)

    user = (
        "아래 정보를 바탕으로 고정 서식 기획서를 Markdown으로 작성하세요.\n"
        f"[입력]\n{json.dumps(si, ensure_ascii=False)}\n"
        f"[시장조사]\n{json.dumps(research, ensure_ascii=False)}\n"
        f"[PESTEL]\n{json.dumps(pestel, ensure_ascii=False)}"
    )
    text, missing = _generate(DRAFT_WRITER_SYSTEM, user, fallback, state.get("model", ""))

    mode = "더미" if llm.is_dummy() else f"실제 LLM·{llm.resolve_model(state.get('model', ''))}"
    note = "" if not missing else f" ⚠ 누락 섹션 {len(missing)}개: {', '.join(missing)}"
    logs = state.get("logs", []) + [f"[draft_writer] 초안 작성 완료 ({mode}){note}"]
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
    text, missing = _generate(REVISER_SYSTEM, user, fallback, state.get("model", ""))

    count = state.get("revision_count", 0) + 1
    note = "" if not missing else f" ⚠ 누락 섹션 {len(missing)}개"
    logs = state.get("logs", []) + [f"[draft_writer] 재작성 완료 (revision={count}){note}"]
    return {"final_draft": text, "revision_count": count, "logs": logs}
