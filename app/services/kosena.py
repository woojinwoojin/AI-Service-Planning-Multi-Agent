"""KOSENA 방법론 준수 검사 (체크포인트 3).

과제의 체크포인트 3은 "제공된 기획 방법론 템플릿(KOSENA)을 준수하는가"다. KOSENA 는 문서
디자인 템플릿이 아니라 **반드시 거쳐야 하는 분석 프레임워크와 산출물 구조**를 규정한다
(`docs/KOSENA_AI_서비스기획.pdf`, 21쪽). 평가표(p20)도 프레임워크 정확도·Lean Canvas 일관성·
TAM/SAM/SOM 교차검증·VPC Fit·MVP/Epic-Story-AC/와이어프레임을 직접 본다.

이 모듈은 **판정만 한다. 내용을 만들지 않는다.**

  - LLM·검색 호출 없음. 같은 State 면 항상 같은 결과(결정적).
  - **실행을 실패시키지 않는다.** `quality_gate`·`check_parity` 와 같은 태도 — 미충족을
    숨기지 않고 State·문서에 표면화해 사람이 판단하게 한다.
  - **'없음'과 '있는데 규격 미달'을 구분한다**(`missing` vs `partial`). 예를 들어 PESTEL 6영역은
    있지만 Critical Uncertainties Top 3 이 없으면 그건 '없음'이 아니라 '부분 충족'이고, 둘을
    뭉뚱그리면 무엇을 더 해야 하는지 알 수 없다.

**왜 검사부터 만드는가.** 아직 Porter·Lean Canvas 등을 생성하지 않는 상태에서도 이 검사는
가치가 있다 — 평가표 기준으로 **무엇이 빠졌는지 정확히 아는 것**이, 빠진 줄 모르고 '표면적
적용'으로 남는 것보다 낫다. 생성 Agent 가 붙으면 같은 검사가 그대로 충족 여부를 말해 준다.

**정직 표기 — 구조적으로 채울 수 없는 항목이 있다.** 평가표의 '고객 이해' 우수 기준은
*1차 인터뷰 + 설문*(p20)이고 페르소나 정의도 *인터뷰 5명 + 설문 100명*을 예로 든다(p12).
AI 파이프라인은 이를 만들 수 없다. 지어내면 KOSENA 원칙(정량 주장은 1차 자료로 뒷받침, p4)에
정면으로 위배되므로, **가설형임을 명시**하는 것이 최선이고 그 표기 여부 자체를 검사한다.
"""
from __future__ import annotations

from app.schemas import artifact

# 본문 분량 추정용 — **줄 수 기준**이다. 글자 수로 세면 표가 많은 문서에서 크게 빗나간다:
# 표 한 행은 글자 수가 적어도 렌더링에서는 한 줄을 온전히 차지한다(실측한 KOSENA 산출물은
# 494줄 중 183줄이 표 행이었고, 글자 기준 8.1쪽 vs 줄 기준 11.0쪽으로 갈렸다).
# A4·11pt·1.15 줄간격에서 대략 45줄/쪽. 정확한 페이지 수는 렌더러가 정하므로 **추정치**이고,
# 판정 문구에도 '추정'이라고 밝힌다.
_LINES_PER_PAGE = 45

# 제출 형식(p4): 본문 A4 30~50쪽 · 발표 15~20쪽
DOC_PAGES_MIN, DOC_PAGES_MAX = 30, 50
DECK_PAGES_MIN, DECK_PAGES_MAX = 15, 20

OK, PARTIAL, MISSING = "ok", "partial", "missing"

# CJM 5단계(p12) — 표기 흔들림을 흡수하려고 대표어만 본다.
_CJM_STAGES = ("인지", "고려", "구매", "사용", "재사용")
_CJM_ITEMS = ("action", "emotion", "pain_point", "touchpoint")
# Lean Canvas 9블록(p10) — 작성 순서 1→9 그대로.
_LEAN_BLOCKS = ("problem", "customer_segments", "uvp", "solution", "channels",
                "revenue_streams", "cost_structure", "key_metrics", "unfair_advantage")
# 페르소나 필수 5항목(p12)
_PERSONA_FIELDS = ("demographics", "behavior", "goal", "pain_points", "expectations")
# VPC 6영역(p15)
_VPC_PROFILE = ("customer_jobs", "pains", "gains")
_VPC_MAP = ("products_services", "pain_relievers", "gain_creators")
# MOSCOW 4단계(p17) — Won't have 는 "이번 범위 제외"를 **명시**해야 하므로 비어 있어도 키는 필요.
_MOSCOW = ("must", "should", "could", "wont")
_KANO = ("basic", "performance", "excitement")


def _n(v) -> int:
    """리스트 길이. 리스트가 아니면 0."""
    return len(v) if isinstance(v, list) else 0


def _filled(d: dict, keys) -> list[str]:
    """dict 에서 값이 실제로 채워진 키만."""
    return [k for k in keys if isinstance(d, dict) and d.get(k)]


def _safe_read(state: dict, artifact_type: str) -> dict:
    """Artifact 를 읽되 **실패는 '없음'으로 본다.**

    `artifact_only` 모드에서 쓸 수 없는 Artifact 는 `ArtifactUnavailable` 을 던진다. 그건
    **소비자**(문서를 만드는 Agent)에게는 옳은 동작이다 — 빈 값으로 조용히 진행하면 '경쟁사
    분석을 안 보고 만든 SWOT'이 정상 산출물로 저장되기 때문이다. 하지만 이 모듈은 소비자가
    아니라 **관찰자**다. 관찰하다가 실행을 죽이면 안 되고, 못 읽었다는 사실은 그대로 '미충족'
    으로 보고하는 것이 정확하다.
    """
    try:
        return artifact.read(state, artifact_type)
    except Exception:
        return {}


def _ctx(state: dict) -> dict:
    """검사에 쓸 입력을 **한 번씩만** 읽어 둔다.

    같은 Artifact 를 여러 검사에서 반복해 읽으면 `prefer_artifact` 폴백 경고가 검사 횟수만큼
    나고 PR 5d 런타임 읽기 카운터도 부풀려진다(2-5 `research_gap` 에서 같은 이유로 1회 읽기를
    고정했다). 읽기는 selector 를 타므로 읽기 모드 설정을 그대로 따른다.
    """
    if not isinstance(state, dict):
        state = {}
    return {
        "state": state,
        "kosena": state.get("kosena") if isinstance(state.get("kosena"), dict) else {},
        "pestel": _safe_read(state, "pestel_analysis"),
        "swot": _safe_read(state, "swot_analysis"),
        "competitor": _safe_read(state, "competitor_analysis"),
        "customer": _safe_read(state, "customer_analysis"),
        "business_model": _safe_read(state, "business_model_analysis"),
        "research": _safe_read(state, "research_analysis"),
        "risk": _safe_read(state, "risk_analysis"),
        "draft": state.get("final_draft") or state.get("draft") or "",
    }


# ---- 개별 검사 ---------------------------------------------------------------
# 각 검사는 (status, detail) 을 돌려준다. detail 은 **무엇이 왜 부족한지** 사람이 읽을 문장.

def _c_pestel_6(c):
    n = len(c["pestel"]) if isinstance(c["pestel"], dict) else 0
    if n >= 6:
        return OK, f"{n}개 영역"
    return (PARTIAL, f"{n}/6 영역") if n else (MISSING, "PESTEL 결과 없음")


def _c_pestel_critical(c):
    top = c["kosena"].get("critical_uncertainties")
    if _n(top) >= 3:
        return OK, "Top 3 선별됨"
    if c["pestel"]:
        return PARTIAL, "PESTEL 6영역은 있으나 영향력·가능성 상위 3개 선별이 없음"
    return MISSING, "없음"


def _c_frameworks_2(c):
    have = []
    if c["swot"]:
        have.append("SWOT")
    if c["kosena"].get("porter"):
        have.append("Porter")
    if c["kosena"].get("value_chain"):
        have.append("Value Chain")
    if len(have) >= 2:
        return OK, " + ".join(have)
    return (PARTIAL, f"{have[0]} 만 있음(최소 2개 필요)") if have else (MISSING, "없음")


def _c_ksf_5(c):
    n = _n(c["kosena"].get("ksf"))
    return (OK, "5개") if n == 5 else (PARTIAL, f"{n}개(5개 필요)") if n else (MISSING, "없음")


def _c_implications_3(c):
    n = _n(c["kosena"].get("implications"))
    return (OK, "3개") if n >= 3 else (PARTIAL, f"{n}개(3개 필요)") if n else (MISSING, "없음")


def _c_hmw(c):
    k = c["kosena"]
    hmw, ideas = _n(k.get("hmw")), _n(k.get("ideas"))
    if hmw >= 5 and ideas >= 25 and k.get("selected_concept"):
        return OK, f"HMW {hmw}개 · 아이디어 {ideas}개 · 컨셉 선정"
    if hmw or ideas:
        return PARTIAL, f"HMW {hmw}/5 · 아이디어 {ideas}/25 · 컨셉 {'있음' if k.get('selected_concept') else '없음'}"
    return MISSING, "없음"


def _c_lean_canvas(c):
    lc = c["kosena"].get("lean_canvas")
    got = _filled(lc, _LEAN_BLOCKS)
    if len(got) == 9:
        return OK, "9블록"
    if got:
        return PARTIAL, f"{len(got)}/9 블록(누락: {', '.join(b for b in _LEAN_BLOCKS if b not in got)})"
    # 재료가 얼마나 있는지 함께 알려 준다 — '아예 없음'과 '조립하면 되는 상태'는 다르다.
    raw = _filled(c["business_model"], ("revenue_streams", "cost_structure", "key_metrics"))
    return MISSING, f"없음(현재 재료: business_model 의 {len(raw)}개 항목뿐)"


def _c_hypotheses_3(c):
    n = _n(c["kosena"].get("key_hypotheses"))
    return (OK, "3개") if n >= 3 else (PARTIAL, f"{n}개(3개 필요)") if n else (MISSING, "없음")


def _c_personas_2(c):
    ps = c["kosena"].get("personas")
    if _n(ps) >= 2 and all(len(_filled(p, _PERSONA_FIELDS)) >= 5 for p in ps[:2]):
        return OK, "2종 · 필수 5항목 충족"
    if _n(ps):
        return PARTIAL, f"{_n(ps)}종(2종 필요) 또는 필수 5항목 미충족"
    # 현재 customer_result 의 페르소나는 **문자열 1개**라 KOSENA 규격에 못 미친다.
    if c["customer"].get("target_persona"):
        return PARTIAL, "target_persona 문자열 1개만 있음(2종 × 필수 5항목 필요)"
    return MISSING, "없음"


def _c_cjm(c):
    cjm = c["kosena"].get("cjm")
    stages = cjm.get("stages") if isinstance(cjm, dict) else None
    if _n(stages) >= 5 and all(len(_filled(s, _CJM_ITEMS)) >= 4 for s in stages[:5]):
        return (OK, "5단계 × 4항목") if (cjm or {}).get("opportunities") else \
               (PARTIAL, "5단계 × 4항목은 있으나 Opportunity 도출 없음")
    return (PARTIAL, f"{_n(stages)}/5 단계") if _n(stages) else (MISSING, "없음")


def _c_tam_sam_som(c):
    s = c["kosena"].get("market_sizing")
    got = _filled(s, ("tam", "sam", "som"))
    return (OK, "TAM·SAM·SOM") if len(got) == 3 else \
           (PARTIAL, f"{len(got)}/3") if got else (MISSING, "없음")


def _c_sizing_cross(c):
    s = c["kosena"].get("market_sizing")
    got = _filled(s, ("top_down", "bottom_up"))
    if len(got) == 2:
        return (OK, "Top-down + Bottom-up") if (s or {}).get("gap_reason") else \
               (PARTIAL, "두 방식은 있으나 차이 사유 설명 없음")
    return (PARTIAL, f"{got[0]} 만 있음") if got else (MISSING, "없음")


def _c_competitors_321(c):
    g = c["kosena"].get("competitor_groups")
    if isinstance(g, dict) and _n(g.get("direct")) >= 3 and _n(g.get("indirect")) >= 2 \
            and _n(g.get("potential")) >= 1:
        return OK, "직접3 · 간접2 · 잠재1"
    n = _n(c["competitor"].get("competitors"))
    if isinstance(g, dict):
        return PARTIAL, (f"직접{_n(g.get('direct'))} · 간접{_n(g.get('indirect'))} · "
                         f"잠재{_n(g.get('potential'))}(3·2·1 필요)")
    return (PARTIAL, f"경쟁사 {n}개는 있으나 직접/간접/잠재 분류가 없음") if n else (MISSING, "없음")


def _c_comparison_10(c):
    n = _n(c["kosena"].get("comparison_criteria"))
    return (OK, f"{n}개 항목") if n >= 10 else (PARTIAL, f"{n}/10 항목") if n else (MISSING, "없음")


def _c_positioning_map(c):
    m = c["kosena"].get("positioning_map")
    if isinstance(m, dict) and m.get("x_axis") and m.get("y_axis") and _n(m.get("points")) >= 2:
        return OK, f"{m['x_axis']} × {m['y_axis']}"
    if c["competitor"].get("positioning"):
        return PARTIAL, "positioning 서술만 있음(2축 좌표 맵 필요)"
    return MISSING, "없음"


def _c_vpc(c):
    v = c["kosena"].get("vpc")
    prof, vmap = _filled((v or {}).get("customer_profile"), _VPC_PROFILE), \
        _filled((v or {}).get("value_map"), _VPC_MAP)
    if len(prof) == 3 and len(vmap) == 3:
        return (OK, "고객 프로필 3 + 가치 지도 3") if (v or {}).get("fit") else \
               (PARTIAL, "6영역은 있으나 Fit 검증 서술 없음")
    return (PARTIAL, f"프로필 {len(prof)}/3 · 가치지도 {len(vmap)}/3") if (prof or vmap) \
        else (MISSING, "없음")


def _c_core_features(c):
    n = _n(c["kosena"].get("core_features"))
    return (OK, f"{n}개") if 5 <= n <= 7 else (PARTIAL, f"{n}개(5~7개 필요)") if n else (MISSING, "없음")


def _c_use_cases_3(c):
    n = _n(c["kosena"].get("use_cases"))
    return (OK, "3종") if n >= 3 else (PARTIAL, f"{n}/3") if n else (MISSING, "없음")


def _c_moscow(c):
    m = c["kosena"].get("moscow")
    # Won't have 는 '이번 범위 제외'를 명시하는 칸이라 키 존재 자체가 요건이다(p17).
    if isinstance(m, dict) and all(k in m for k in _MOSCOW) and \
            any(m.get(k) for k in ("must", "should", "could")):
        return OK, "Must/Should/Could/Won't"
    return (PARTIAL, f"{len(_filled(m, _MOSCOW))}/4 구분") if isinstance(m, dict) else (MISSING, "없음")


def _c_kano(c):
    got = _filled(c["kosena"].get("kano"), _KANO)
    return (OK, "3분류") if len(got) == 3 else (PARTIAL, f"{len(got)}/3") if got else (MISSING, "없음")


def _c_mvp(c):
    return (OK, "범위 정의됨") if c["kosena"].get("mvp_scope") else (MISSING, "없음")


def _c_epic_story_ac(c):
    eps = c["kosena"].get("epics")
    if not _n(eps):
        return MISSING, "없음"
    stories = [s for e in eps if isinstance(e, dict) for s in (e.get("stories") or [])]
    gwt = [s for s in stories if isinstance(s, dict)
           and all(s.get(k) for k in ("given", "when", "then"))]
    if stories and len(gwt) == len(stories):
        return OK, f"Epic {_n(eps)} · Story {len(stories)} · AC 전부 Given-When-Then"
    return PARTIAL, f"Epic {_n(eps)} · Story {len(stories)} · GWT 형식 {len(gwt)}/{len(stories)}"


def _c_milestones_kpi(c):
    k = c["kosena"]
    got = [x for x in ("milestones", "kpis") if _n(k.get(x))]
    return (OK, "마일스톤 + KPI") if len(got) == 2 else \
           (PARTIAL, f"{got[0]} 만 있음") if got else (MISSING, "없음")


def _c_wireframe(c):
    n = _n(c["kosena"].get("wireframes"))
    return (OK, f"{n}개 화면") if n >= 2 else (PARTIAL, "1개 화면(2~3개 권장)") if n else (MISSING, "없음")


def _c_sources(c):
    n = len([e for e in (c["state"].get("evidence_registry") or []) if isinstance(e, dict) and e.get("url")])
    return (OK, f"출처 {n}건") if n else (MISSING, "인용된 출처 없음")


def _c_ai_log(c):
    n = _n(c["state"].get("ai_usage_log"))
    if n:
        return OK, f"{n}건"
    # 재료는 이미 있다 — Artifact 의 owner_agent·depends_on·status + reviewer 판정.
    return MISSING, f"전용 로그 없음(재료: artifacts {_n(c['state'].get('artifacts'))}건·logs 존재)"


def _submission_doc(c) -> str:
    """평가 대상이 되는 **제출 본문**.

    KOSENA 가 요구하는 본문은 7종 산출물 문서(`kosena_plan`)다. 기존 14섹션 기획서는 그 안에
    참고로 포함되는 일부이므로, KOSENA 문서가 없을 때만 대신 본다. 분량·표기 검사가 서로 다른
    문서를 보면 판정이 어긋난다.
    """
    return c["state"].get("kosena_plan") or c["draft"]


def _c_hypothesis_labeling(c):
    """정량 주장을 1차 자료로 뒷받침할 수 없으면 **가설임을 명시**해야 한다(p4·p20).

    이 프로젝트는 인터뷰·설문을 수행하지 않으므로 '충족'이 아니라 **정직한 표기**가 정답이다.
    """
    marks = ("가설", "추정", "미검증", "인터뷰로 검증되지 않")
    return (OK, "가설·추정 표기 있음") if any(m in _submission_doc(c) for m in marks) \
        else (MISSING, "가설/추정임을 밝히는 문구가 본문에 없음")


def _c_doc_length(c):
    """제출 본문의 분량(p4: A4 30~50쪽).

    `missing`/`partial` 은 **문서 존재 여부**로 가른다. 반올림한 쪽수로 가르면 1줄짜리 문서가
    0.0쪽 → '없음'으로 보고돼, '문서가 없다'와 '있는데 짧다'가 뭉개진다.
    """
    doc = _submission_doc(c)
    if not doc.strip():
        return MISSING, "제출 본문이 생성되지 않음"
    pages = round(len(doc.splitlines()) / _LINES_PER_PAGE, 1)
    if DOC_PAGES_MIN <= pages <= DOC_PAGES_MAX:
        return OK, f"약 {pages}쪽(추정)"
    return PARTIAL, f"약 {pages}쪽(추정) — {DOC_PAGES_MIN}~{DOC_PAGES_MAX}쪽 필요"


# 요구사항 명세 — 순서는 PDF 모듈 순. `page` 는 근거 쪽수(원문 대조용).
REQUIREMENTS: list[dict] = [
    {"id": "pestel_6", "module": "M1", "title": "PESTEL 6영역", "page": 7, "check": _c_pestel_6},
    {"id": "pestel_critical_top3", "module": "M1", "title": "Critical Uncertainties Top 3",
     "page": 7, "check": _c_pestel_critical},
    {"id": "frameworks_2", "module": "M1", "title": "Porter/SWOT/Value Chain 중 2개 이상",
     "page": 8, "check": _c_frameworks_2},
    {"id": "ksf_5", "module": "M1", "title": "KSF 5개", "page": 8, "check": _c_ksf_5},
    {"id": "implications_3", "module": "M1", "title": "설계 시사점 3가지", "page": 8,
     "check": _c_implications_3},
    {"id": "hmw_ideation", "module": "M1", "title": "HMW 5개 · 아이디어 25+ · 컨셉 선정",
     "page": 9, "check": _c_hmw},
    {"id": "lean_canvas_9", "module": "M1", "title": "Lean Canvas 9블록", "page": 10,
     "check": _c_lean_canvas},
    {"id": "key_hypotheses_3", "module": "M1", "title": "핵심 가설 3개", "page": 10,
     "check": _c_hypotheses_3},
    {"id": "personas_2", "module": "M2", "title": "페르소나 2종 × 필수 5항목", "page": 12,
     "check": _c_personas_2},
    {"id": "cjm", "module": "M2", "title": "CJM 5단계 × 4항목 + Opportunity", "page": 12,
     "check": _c_cjm},
    {"id": "tam_sam_som", "module": "M2", "title": "TAM·SAM·SOM", "page": 13,
     "check": _c_tam_sam_som},
    {"id": "sizing_cross_check", "module": "M2", "title": "Top-down · Bottom-up 교차검증",
     "page": 13, "check": _c_sizing_cross},
    {"id": "competitors_3_2_1", "module": "M2", "title": "경쟁사 직접3·간접2·잠재1", "page": 14,
     "check": _c_competitors_321},
    {"id": "comparison_criteria_10", "module": "M2", "title": "비교 항목 10개 이상", "page": 14,
     "check": _c_comparison_10},
    {"id": "positioning_map", "module": "M2", "title": "2축 포지셔닝 맵", "page": 14,
     "check": _c_positioning_map},
    {"id": "vpc", "module": "M2", "title": "VPC 6영역 + Fit", "page": 15, "check": _c_vpc},
    {"id": "core_features_5_7", "module": "M2", "title": "핵심 기능 5~7개", "page": 15,
     "check": _c_core_features},
    {"id": "use_cases_3", "module": "M2", "title": "Use Case 3종", "page": 15,
     "check": _c_use_cases_3},
    {"id": "moscow", "module": "M3", "title": "MOSCOW 4구분(Won't 명시)", "page": 17,
     "check": _c_moscow},
    {"id": "kano", "module": "M3", "title": "Kano 3분류", "page": 17, "check": _c_kano},
    {"id": "mvp_scope", "module": "M3", "title": "MVP 범위", "page": 17, "check": _c_mvp},
    {"id": "epic_story_ac", "module": "M3", "title": "Epic-Story-AC (Given-When-Then)",
     "page": 18, "check": _c_epic_story_ac},
    {"id": "milestones_kpi", "module": "M3", "title": "마일스톤 · KPI", "page": 3,
     "check": _c_milestones_kpi},
    {"id": "wireframes", "module": "M3", "title": "와이어프레임 2~3개 화면", "page": 5,
     "check": _c_wireframe},
    {"id": "sources_cited", "module": "공통", "title": "출처 명시", "page": 4, "check": _c_sources},
    {"id": "ai_usage_log", "module": "공통", "title": "AI 활용 로그(프롬프트·응답·채택 여부)",
     "page": 4, "check": _c_ai_log},
    {"id": "hypothesis_labeling", "module": "공통", "title": "정량 주장의 가설·추정 표기",
     "page": 4, "check": _c_hypothesis_labeling},
    {"id": "doc_length", "module": "공통", "title": "본문 A4 30~50쪽", "page": 4,
     "check": _c_doc_length},
]


def evaluate(state: dict) -> dict:
    """KOSENA 준수 여부를 판정한다(결정적·LLM 없음·실행을 실패시키지 않음).

    반환:
        {"total", "ok", "partial", "missing",
         "checks": [{id, module, title, page, status, detail}],
         "by_module": {"M1": {ok,partial,missing}, ...},
         "unmet": [id...],            # partial + missing (무엇을 더 해야 하는지)
         "summary": "..."}            # 문서·UI 에 그대로 실을 한 줄
    """
    c = _ctx(state)
    checks: list[dict] = []
    for spec in REQUIREMENTS:
        try:
            status, detail = spec["check"](c)
        except Exception as exc:                       # 검사 하나가 죽어도 리포트는 나와야 한다
            status, detail = MISSING, f"검사 오류: {type(exc).__name__}"
        checks.append({"id": spec["id"], "module": spec["module"], "title": spec["title"],
                       "page": spec["page"], "status": status, "detail": detail})

    counts = {s: sum(1 for x in checks if x["status"] == s) for s in (OK, PARTIAL, MISSING)}
    by_module: dict[str, dict] = {}
    for x in checks:
        m = by_module.setdefault(x["module"], {OK: 0, PARTIAL: 0, MISSING: 0})
        m[x["status"]] += 1
    return {
        "total": len(checks),
        "ok": counts[OK], "partial": counts[PARTIAL], "missing": counts[MISSING],
        "checks": checks,
        "by_module": by_module,
        "unmet": [x["id"] for x in checks if x["status"] != OK],
        "summary": (f"KOSENA 준수 {counts[OK]}/{len(checks)} 충족 · "
                    f"부분 {counts[PARTIAL]} · 미충족 {counts[MISSING]}"),
    }


def report_lines(result: dict) -> list[str]:
    """사람이 읽을 요약. 미충족 항목을 **감추지 않고** 근거 쪽수와 함께 나열한다."""
    icon = {OK: "✅", PARTIAL: "🟡", MISSING: "❌"}
    lines = [result["summary"], ""]
    for module in ("M1", "M2", "M3", "공통"):
        rows = [x for x in result["checks"] if x["module"] == module]
        if not rows:
            continue
        lines.append(f"[{module}]")
        lines += [f"  {icon[x['status']]} {x['title']} (p{x['page']}) — {x['detail']}" for x in rows]
    return lines
