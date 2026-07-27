"""KOSENA 7종 산출물 문서 조립 (체크포인트 3, PDF p5).

KOSENA 는 팀 산출물을 **7종**으로 규정한다(p5) — 산업·서비스 분석 보고서 / Lean Canvas /
고객 리서치 패키지 / 시장·경쟁사 분석 / 서비스 컨셉·기능 정의서 / 개발 로드맵·PRD /
최종 발표자료. 제출 형식은 본문 **A4 30~50쪽**, 발표 **15~20쪽**이다(p4).

**설계 판단 — 기존 14섹션 기획서를 재구성하지 않고 별도 문서를 추가한다.**

기존 `final_draft`(14섹션)를 KOSENA 구조로 갈아엎으면 다음이 한꺼번에 깨진다:
`sections.py` 의 왕복 byte 동일 불변식 · `section_revise`(PR-7) 의 섹션 ID 의존 ·
`quality_gate` 의 '서식 정상(14섹션)' 체크 · `parallel_bench.structural_quality` · 관련 테스트 다수.

반면 `docx_export.docx_bytes(markdown)` / `pptx_export.pptx_bytes(markdown, title)` 는
**markdown 문자열만 받는 순수 함수**라 기존 문서와 아무 결합이 없다. 그래서 KOSENA 산출물을
markdown 으로 조립해 같은 함수에 넣으면 **DOCX/PPTX 가 그대로 따라오고**, 기존 경로는 한 줄도
건드리지 않는다.

    final_draft (14섹션)   → 그대로 유지 (기존 불변식 전부 무사)
    kosena_plan (7종)      → 새로 조립 → 같은 익스포터로 DOCX/PPTX

**정직 표기는 문서에 직접 박는다.** 인터뷰·설문을 수행하지 않았으므로 페르소나·시장 규모는
가설·추정이고, 그 사실을 각 산출물 머리에 명시한다(p4 '정량 주장은 1차 자료로 뒷받침',
p20 '고객 이해' 평가축). 이는 PDF 결론과도 맞는다 — *"완벽한 기획서가 아니라 의사결정 가능한
가설을 만든다"*(p21).
"""
from __future__ import annotations

from app.services import ai_log, kosena

# 산출물 7종의 제목(p5). 발표자료(7번)는 PPTX 로 따로 만들므로 본문에는 요약만 싣는다.
DELIVERABLES = [
    "산업·서비스 분석 보고서", "Lean Canvas", "고객 리서치 패키지", "시장·경쟁사 분석",
    "서비스 컨셉·기능 정의서", "개발 로드맵·PRD", "발표 요약",
]

_DISCLAIMER = (
    "> ⚠️ **본 문서의 정량 주장은 웹 리서치에 기반한 가설·추정입니다.** 인터뷰·설문 등 1차 자료로 "
    "검증되지 않았으며, 페르소나는 **가설형 페르소나**이고 TAM/SAM/SOM 은 명시한 가정 위의 "
    "**추정치**입니다. 실제 시장 점유율·고객 반응을 의미하지 않습니다.\n>\n"
    "> KOSENA 의 목표대로 *\"완벽한 기획서가 아니라 의사결정 가능한 가설\"* 을 만드는 것이 "
    "이 문서의 성격입니다. 각 가설은 시장·고객을 만나며 갱신되어야 합니다."
)


def _bullets(items, key: str | None = None) -> str:
    rows = []
    for i in items or []:
        text = i.get(key, "") if (key and isinstance(i, dict)) else i
        if isinstance(text, str) and text.strip():
            rows.append(f"- {text.strip()}")
        elif isinstance(i, dict) and not key:
            rows.append("- " + " · ".join(f"{k}: {v}" for k, v in i.items() if v))
    return "\n".join(rows) or "- (생성되지 않음)"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "(생성되지 않음)"
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(str(c).replace("\n", " ") for c in r) + " |" for r in rows]
    return "\n".join(out)


def _kv_table(d: dict, labels: dict[str, str]) -> str:
    rows = [[labels.get(k, k), str(v)] for k, v in (d or {}).items() if v and k in labels]
    return _table(["항목", "내용"], rows)


def _industry(k: dict) -> str:
    porter_rows = [[f, v.get("level", ""), v.get("rationale", "")]
                   for f, v in (k.get("porter") or {}).items()]
    cu_rows = [[c.get("factor", ""), c.get("why", ""), c.get("impact", "")]
               for c in k.get("critical_uncertainties") or []]
    vc = k.get("value_chain") or {}
    return "\n\n".join([
        "### Critical Uncertainties (영향력·가능성 상위 3)",
        _table(["요인", "왜 중요한가", "서비스 영향"], cu_rows),
        "### Porter's Five Forces",
        _table(["Force", "강도", "근거"], porter_rows),
        "### Value Chain",
        _table(["활동", "내용"], [[a, t] for a, t in vc.items()]),
        "### KSF (핵심 성공요인 5)", _bullets(k.get("ksf")),
        "### 서비스 설계 시사점 3", _bullets(k.get("implications")),
    ])


def _lean_canvas(k: dict) -> str:
    labels = {"problem": "1. Problem", "customer_segments": "2. Customer Segments",
              "uvp": "3. UVP", "solution": "4. Solution", "channels": "5. Channels",
              "revenue_streams": "6. Revenue Streams", "cost_structure": "7. Cost Structure",
              "key_metrics": "8. Key Metrics", "unfair_advantage": "9. Unfair Advantage"}
    hyp_rows = [[h.get("hypothesis", ""), h.get("validation", ""), h.get("metric", "")]
                for h in k.get("key_hypotheses") or []]
    return "\n\n".join([
        "### 9개 블록 (작성 순서 1→9)",
        _kv_table(k.get("lean_canvas") or {}, labels),
        "### 핵심 가설 3개와 검증 계획",
        _table(["가설", "검증 방법", "판단 지표"], hyp_rows),
        "### 아이디어 발산 → 수렴",
        "**HMW 질문**\n" + _bullets(k.get("hmw")),
        f"**발산한 아이디어 {len(k.get('ideas') or [])}개**\n" + _bullets((k.get("ideas") or [])[:25]),
        "**최종 컨셉**\n\n" + (k.get("selected_concept") or "(생성되지 않음)"),
    ])


def _customer(k: dict) -> str:
    parts = ["### 페르소나 (가설형 — 인터뷰 미검증)"]
    for p in k.get("personas") or []:
        parts.append(f"#### {p.get('name', '페르소나')}\n" + _kv_table(p, {
            "demographics": "인구통계", "behavior": "행동 패턴", "goal": "Goal·동기",
            "pain_points": "Pain Point", "expectations": "기대·거부 요인"}))
    cjm = k.get("cjm") or {}
    rows = [[s.get("stage", ""), s.get("action", ""), s.get("emotion", ""),
             s.get("pain_point", ""), s.get("touchpoint", "")] for s in cjm.get("stages") or []]
    parts += ["### Customer Journey Map",
              _table(["단계", "Action", "Emotion", "Pain Point", "Touchpoint"], rows),
              "**Opportunity**\n" + _bullets(cjm.get("opportunities"))]
    return "\n\n".join(parts)


def _market(k: dict) -> str:
    ms = k.get("market_sizing") or {}
    cg = k.get("competitor_groups") or {}
    pm = k.get("positioning_map") or {}
    pm_rows = [[p.get("name", ""), p.get("x", ""), p.get("y", "")] for p in pm.get("points") or []]
    return "\n\n".join([
        "### TAM · SAM · SOM (추정치 — 명시한 가정 위)",
        _kv_table(ms, {"tam": "TAM", "sam": "SAM", "som": "SOM",
                       "top_down": "Top-down 추정", "bottom_up": "Bottom-up 추정",
                       "gap_reason": "두 값이 다른 이유"}),
        "**사용한 가정**\n" + _bullets(ms.get("assumptions")),
        "### 경쟁사 분류",
        _table(["구분", "대상"], [["직접 경쟁자 (3)", ", ".join(cg.get("direct") or [])],
                                ["간접 경쟁자 (2)", ", ".join(cg.get("indirect") or [])],
                                ["잠재 경쟁자 (1)", ", ".join(cg.get("potential") or [])]]),
        f"### 비교 항목 ({len(k.get('comparison_criteria') or [])}개)",
        _bullets(k.get("comparison_criteria")),
        f"### 포지셔닝 맵 ({pm.get('x_axis', '')} × {pm.get('y_axis', '')})",
        _table([pm.get("x_axis", "X") and "대상", f"{pm.get('x_axis', 'X')} (0~10)",
                f"{pm.get('y_axis', 'Y')} (0~10)"], pm_rows),
    ])


def _concept(k: dict) -> str:
    vpc = k.get("vpc") or {}
    prof, vmap = vpc.get("customer_profile") or {}, vpc.get("value_map") or {}
    feat_rows = [[f.get("name", ""), f.get("impact", ""), f.get("feasibility", "")]
                 for f in k.get("core_features") or []]
    uc_rows = [[u.get("actor", ""), u.get("scenario", ""), u.get("expected_result", "")]
               for u in k.get("use_cases") or []]
    return "\n\n".join([
        "### Value Proposition Canvas",
        "**고객 프로필**\n" + _kv_table({k2: ", ".join(v) for k2, v in prof.items()},
                                     {"customer_jobs": "Customer Jobs", "pains": "Pains",
                                      "gains": "Gains"}),
        "**가치 지도**\n" + _kv_table({k2: ", ".join(v) for k2, v in vmap.items()},
                                   {"products_services": "Products & Services",
                                    "pain_relievers": "Pain Relievers",
                                    "gain_creators": "Gain Creators"}),
        "**Fit 검증**\n\n" + (vpc.get("fit") or "(생성되지 않음)"),
        f"### 핵심 기능 ({len(k.get('core_features') or [])}개)",
        _table(["기능", "사용자 임팩트", "실현가능성"], feat_rows),
        "### Use Case 3종",
        _table(["Actor", "시나리오", "기대 결과"], uc_rows),
    ])


def _roadmap(k: dict) -> str:
    mo, kano = k.get("moscow") or {}, k.get("kano") or {}
    parts = [
        "### MVP 범위\n\n" + (k.get("mvp_scope") or "(생성되지 않음)"),
        "### MOSCOW",
        _table(["구분", "기능"], [["Must have", ", ".join(mo.get("must") or []) or "—"],
                                ["Should have", ", ".join(mo.get("should") or []) or "—"],
                                ["Could have", ", ".join(mo.get("could") or []) or "—"],
                                ["Won't have (이번 범위 제외)", ", ".join(mo.get("wont") or []) or "—"]]),
        "### Kano Model",
        _table(["분류", "기능"], [["Basic (당연)", ", ".join(kano.get("basic") or []) or "—"],
                                ["Performance (성능)", ", ".join(kano.get("performance") or []) or "—"],
                                ["Excitement (매력)", ", ".join(kano.get("excitement") or []) or "—"]]),
        "### Epic – User Story – Acceptance Criteria (INVEST · Given-When-Then)",
    ]
    for e in k.get("epics") or []:
        parts.append(f"#### Epic: {e.get('name', '')}")
        for s in e.get("stories") or []:
            parts.append(
                f"- **Story**: {s.get('story', '')}\n"
                f"  - **Given** {s.get('given', '')}\n"
                f"  - **When** {s.get('when', '')}\n"
                f"  - **Then** {s.get('then', '')}")
    parts += [
        "### 마일스톤",
        _table(["마일스톤", "기간", "목표"],
               [[m.get("name", ""), m.get("period", ""), m.get("goal", "")]
                for m in k.get("milestones") or []]),
        "### KPI",
        _table(["지표", "목표(가정 포함)"],
               [[x.get("name", ""), x.get("target", "")] for x in k.get("kpis") or []]),
        "### 와이어프레임 (구조 스케치 — 실제 디자인 아님)",
    ]
    for w in k.get("wireframes") or []:
        parts.append(f"**{w.get('screen', '')}**\n\n```\n{w.get('layout', '')}\n```")
    return "\n\n".join(parts)


def build(state: dict) -> str:
    """State 에서 KOSENA 7종 산출물 Markdown 을 조립한다(결정적·LLM 호출 없음)."""
    if not isinstance(state, dict):
        return ""
    k = state.get("kosena") if isinstance(state.get("kosena"), dict) else {}
    si = state.get("structured_input") or {}
    name = si.get("project_name") or "서비스 기획안"
    comp = state.get("kosena_compliance") or kosena.evaluate(state)

    parts = [
        f"# {name} — 서비스 기획안 (KOSENA)",
        _DISCLAIMER,
        "## 개요",
        _kv_table(si, {"project_name": "프로젝트명", "description": "설명",
                       "target_user": "목표 사용자", "problem": "문제"}),
        f"## 1. {DELIVERABLES[0]}", _industry(k),
        f"## 2. {DELIVERABLES[1]}", _lean_canvas(k),
        f"## 3. {DELIVERABLES[2]}", _customer(k),
        f"## 4. {DELIVERABLES[3]}", _market(k),
        f"## 5. {DELIVERABLES[4]}", _concept(k),
        f"## 6. {DELIVERABLES[5]}", _roadmap(k),
        f"## 7. {DELIVERABLES[6]}",
        "본 기획안의 핵심은 다음과 같다.\n\n" + _bullets(k.get("implications")),
        "## 참고 — 기존 14섹션 기획서",
        "아래는 동일 실행에서 생성된 서술형 기획서다(KOSENA 산출물과 중복 서술 포함).",
        state.get("final_draft") or state.get("draft") or "(생성되지 않음)",
        "## AI 활용 로그",
        ai_log.to_markdown(state.get("ai_usage_log") or ai_log.build(state)),
        "## KOSENA 준수 현황 (자체 판정)",
        f"{comp.get('summary', '')}\n",
        _table(["모듈", "항목", "상태", "근거(쪽)", "비고"],
               [[c["module"], c["title"],
                 {"ok": "충족", "partial": "부분", "missing": "미충족"}[c["status"]],
                 f"p{c['page']}", c["detail"]] for c in comp.get("checks") or []]),
    ]
    return "\n\n".join(p for p in parts if p) + "\n"


def build_deck(state: dict) -> str:
    """발표용 Markdown(p4: 15~20쪽). `##` 하나가 슬라이드 하나가 된다(pptx_export 규칙)."""
    if not isinstance(state, dict):
        return ""
    k = state.get("kosena") if isinstance(state.get("kosena"), dict) else {}
    si = state.get("structured_input") or {}
    ms = k.get("market_sizing") or {}
    mo = k.get("moscow") or {}
    comp = state.get("kosena_compliance") or {}
    slides = [
        f"# {si.get('project_name', '서비스 기획안')}",
        "## 문제 정의\n\n" + (si.get("problem") or "—"),
        "## 목표 사용자\n\n" + (si.get("target_user") or "—"),
        "## 거시환경 — Critical Uncertainties\n\n" + _bullets(k.get("critical_uncertainties"), "factor"),
        "## 산업 구조 — Porter's Five Forces\n\n"
        + _table(["Force", "강도"], [[f, v.get("level", "")] for f, v in (k.get("porter") or {}).items()]),
        "## 핵심 성공요인 (KSF)\n\n" + _bullets(k.get("ksf")),
        "## 설계 시사점\n\n" + _bullets(k.get("implications")),
        "## 서비스 컨셉\n\n" + (k.get("selected_concept") or "—"),
        "## Lean Canvas 핵심\n\n"
        + _kv_table(k.get("lean_canvas") or {},
                    {"problem": "Problem", "customer_segments": "Customer", "uvp": "UVP",
                     "solution": "Solution", "revenue_streams": "Revenue"}),
        "## 페르소나\n\n" + _bullets(k.get("personas"), "name"),
        "## 고객 여정 (CJM)\n\n"
        + _table(["단계", "Pain Point"],
                 [[s.get("stage", ""), s.get("pain_point", "")]
                  for s in (k.get("cjm") or {}).get("stages") or []]),
        "## 시장 규모 (추정)\n\n"
        + _kv_table(ms, {"tam": "TAM", "sam": "SAM", "som": "SOM",
                         "top_down": "Top-down", "bottom_up": "Bottom-up"}),
        "## 경쟁 포지셔닝\n\n"
        + _table(["대상", "X", "Y"],
                 [[p.get("name", ""), p.get("x", ""), p.get("y", "")]
                  for p in (k.get("positioning_map") or {}).get("points") or []]),
        "## 가치 제안 (VPC Fit)\n\n" + ((k.get("vpc") or {}).get("fit") or "—"),
        "## 핵심 기능\n\n" + _bullets(k.get("core_features"), "name"),
        "## MVP 범위\n\n" + (k.get("mvp_scope") or "—"),
        "## 우선순위 (MOSCOW)\n\n"
        + _table(["구분", "기능"], [["Must", ", ".join(mo.get("must") or []) or "—"],
                                  ["Won't", ", ".join(mo.get("wont") or []) or "—"]]),
        "## 개발 로드맵\n\n"
        + _table(["마일스톤", "기간", "목표"],
                 [[m.get("name", ""), m.get("period", ""), m.get("goal", "")]
                  for m in k.get("milestones") or []]),
        "## KPI\n\n"
        + _table(["지표", "목표"], [[x.get("name", ""), x.get("target", "")]
                                  for x in k.get("kpis") or []]),
        "## 한계와 다음 단계\n\n"
        "- 정량 주장은 웹 리서치 기반 **가설·추정**이며 인터뷰·설문으로 검증되지 않았습니다.\n"
        f"- KOSENA 자체 판정: {comp.get('summary', '(미판정)')}\n"
        "- 다음 단계: 페르소나 인터뷰, 시장 규모 1차 자료 확인, MVP 사용성 테스트",
    ]
    return "\n\n".join(slides) + "\n"
