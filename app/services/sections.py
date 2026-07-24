"""기획서 Markdown ↔ 섹션 객체 파서/조립기 (로드맵 v2 2-4, PR-7).

전체 재작성 대신 '문제 있는 섹션만' 담당 Agent가 보완할 수 있도록, 고정 서식 기획서
Markdown 을 14개 섹션 블록으로 나누고 다시 합치는 기반 자료구조다.

핵심 불변식:
- **미수정 섹션은 byte 동일**. 조립은 원문 슬라이스(raw)를 그대로 이어붙이고, 수정 대상
  섹션의 body 만 교체한다. `## 참고자료`·한계 문구 등 14섹션 밖 블록도 원문 그대로 보존한다.
- 파싱→(수정 없이)조립 시 원문과 정확히 일치한다(왕복 안전).
- `section_id` 는 자유 문자열이 아니라 14섹션 내부 ID(예: `revenue_model`). 표시 제목과 분리.

섹션 ID ↔ 표시 제목은 `SECTION_SPECS` 단일 진실원천이며, `draft_writer.SECTIONS` 가 이를
파생한다(제목 드리프트 방지).
"""
from __future__ import annotations

import re

# (section_id, 표시 제목) — 고정 서식 14섹션의 정본 순서. draft_writer.SECTIONS 가 이를 파생.
SECTION_SPECS: list[tuple[str, str]] = [
    ("overview", "프로젝트 개요"),
    ("background", "추진 배경"),
    ("problem", "문제 정의"),
    ("target_user", "목표 사용자"),
    ("market_analysis", "시장 및 산업 분석"),
    ("pestel", "PESTEL 분석"),
    ("swot", "SWOT 분석"),
    ("service", "제안 서비스"),
    ("features", "핵심 기능"),
    ("differentiation", "차별성"),
    ("revenue_model", "수익 모델"),
    ("expected_effect", "기대효과"),
    ("plan", "추진 계획"),
    ("risk", "위험요인 및 대응방안"),
]

SECTION_TITLES: list[str] = [title for _, title in SECTION_SPECS]
KNOWN_IDS: list[str] = [sid for sid, _ in SECTION_SPECS]
_TITLE_TO_ID: dict[str, str] = {title: sid for sid, title in SECTION_SPECS}
ID_TO_TITLE: dict[str, str] = dict(SECTION_SPECS)

# `## 제목` 2단계 제목 한 줄. 앞뒤 공백을 허용하되 제목 텍스트만 캡처한다.
_HEADING_RE = re.compile(r"^##[ \t]+(.+?)[ \t]*$", re.MULTILINE)
# 제목 앞에 붙는 번호("11. ", "3) ")를 제거해 정본 제목과 매칭한다(견고성).
_NUM_PREFIX_RE = re.compile(r"^\d+[.)]\s*")


def _title_to_id(title: str) -> str | None:
    t = _NUM_PREFIX_RE.sub("", (title or "").strip()).strip()
    return _TITLE_TO_ID.get(t)


def parse_sections(md: str) -> dict:
    """기획서 Markdown 을 섹션 블록으로 파싱한다.

    반환:
      {
        "preamble": str,            # 첫 `## ` 이전(문서 제목 `# ...` 등) — 원문 그대로
        "blocks": [                 # 등장 순서의 모든 `## ` 블록(14섹션 + 참고자료 등)
            {"title", "section_id"|None, "heading_line", "body", "raw"}
        ],
        "sections": {section_id: block_index},  # 14섹션 중 인식된 것
        "order": [section_id, ...],             # 인식된 14섹션의 등장 순서
        "valid": bool,              # 14섹션 모두·중복 없이·정본 순서로 존재
        "reason": str | None,       # valid=False 사유(missing/duplicate/order)
      }

    `preamble + 각 block["raw"] 이어붙임 == md` 를 보장한다(왕복 byte 동일).
    """
    matches = list(_HEADING_RE.finditer(md or ""))
    if not matches:
        return {"preamble": md or "", "blocks": [], "sections": {},
                "order": [], "valid": False, "reason": "no_headings"}

    preamble = md[: matches[0].start()]
    blocks: list[dict] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(md)
        raw = md[start:end]
        heading_line = m.group(0)
        title = m.group(1).strip()
        body = raw[len(heading_line):]  # 제목 줄 이후 전체(선행 개행 포함) — raw 의 나머지
        blocks.append({
            "title": title,
            "section_id": _title_to_id(title),
            "heading_line": heading_line,
            "body": body,
            "raw": raw,
        })

    sections: dict[str, int] = {}
    order: list[str] = []
    duplicate = False
    for idx, b in enumerate(blocks):
        sid = b["section_id"]
        if not sid:
            continue
        if sid in sections:
            duplicate = True
            continue
        sections[sid] = idx
        order.append(sid)

    if duplicate:
        valid, reason = False, "duplicate"
    elif set(order) != set(KNOWN_IDS):
        valid, reason = False, "missing"
    elif order != KNOWN_IDS:
        valid, reason = False, "order"
    else:
        valid, reason = True, None

    return {"preamble": preamble, "blocks": blocks, "sections": sections,
            "order": order, "valid": valid, "reason": reason}


def section_body(parsed: dict, section_id: str) -> str:
    """파싱 결과에서 특정 섹션의 body(제목 줄 제외 본문)를 반환한다. 없으면 빈 문자열."""
    idx = parsed.get("sections", {}).get(section_id)
    if idx is None:
        return ""
    return parsed["blocks"][idx]["body"].strip()


def assemble(parsed: dict, revised: dict[str, str]) -> str:
    """파싱 결과를 다시 Markdown 으로 조립하되, revised 에 있는 섹션의 body 만 교체한다.

    - revised = {section_id: 새 본문(제목 줄 제외)}.
    - 미수정 블록은 원문 raw 를 그대로 사용 → **byte 동일**.
    - 수정 블록은 기존 제목 줄을 재사용하고(제목 드리프트 방지) 본문만 교체한다.
    """
    parts = [parsed.get("preamble", "")]
    for b in parsed.get("blocks", []):
        sid = b["section_id"]
        if sid and sid in revised:
            content = (revised[sid] or "").strip()
            parts.append(f"{b['heading_line']}\n\n{content}\n\n")
        else:
            parts.append(b["raw"])
    # 미수정 블록은 원문 raw 를 그대로 쓰므로, revised 가 비면 결과가 원문과 정확히 일치한다.
    return "".join(parts)
