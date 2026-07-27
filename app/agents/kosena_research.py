"""KOSENA M2 고객·시장 Agent — 페르소나 2종 · CJM · TAM/SAM/SOM · 포지셔닝 (체크포인트 3).

KOSENA M2 는 정량·정성 리서치를 통합해 **의사결정 가능한 인물상**을 만들고(p12), 시장 규모를
**Top-down 과 Bottom-up 을 병행**해 추정 신뢰도를 확보하며(p13), 경쟁사를 **직접3·간접2·잠재1**
로 분류해 **10개 이상 항목**으로 비교하고 2축 포지셔닝 맵을 그리라고 규정한다(p14).

현재 `customer_result` 의 페르소나는 **문자열 1개**(`target_persona`)라 규격에 한참 못 미친다.
그래서 이 단계는 재조립이 아니라 새 분석이다.

**⚠️ 구조적 한계를 그대로 안고 간다.** 평가표의 '고객 이해' 우수 기준은 *1차 인터뷰 + 설문*
(p20)이고 페르소나 정의도 *인터뷰 5명 + 설문 100명*을 예로 든다(p12). AI 파이프라인은 이를
만들 수 없다. 지어내면 KOSENA 원칙(*정량 주장은 1차 자료로 뒷받침*, p4)에 정면으로 위배되므로
**프롬프트가 '가설·미검증'을 명시하도록 요구**하고, 검사(`hypothesis_labeling`)가 그 표기를 본다.
"""
from __future__ import annotations

import json

from app.prompts.templates import KOSENA_RESEARCH_SYSTEM
from app.schemas import artifact
from app.schemas.state import ProjectState
from app.services import llm

PERSONA_COUNT = 2
PERSONA_FIELDS = ("demographics", "behavior", "goal", "pain_points", "expectations")
CJM_STAGES = ("인지", "고려", "구매", "사용", "재사용/이탈")
CJM_ITEMS = ("action", "emotion", "pain_point", "touchpoint")
CRITERIA_MIN = 10
GROUP_SIZES = {"direct": 3, "indirect": 2, "potential": 1}


def _strs(v, limit: int | None = None) -> list[str]:
    out = [s.strip() for s in v if isinstance(s, str) and s.strip()] if isinstance(v, list) else []
    return out[:limit] if limit else out


def _dicts(v, keys, limit: int | None = None) -> list[dict]:
    """dict 리스트에서 지정 키만 남긴다. **하나라도 채워진 항목만** 살린다."""
    out = []
    for item in v if isinstance(v, list) else []:
        if not isinstance(item, dict):
            continue
        row = {k: str(item.get(k, "")).strip() for k in keys}
        if any(row.values()):
            out.append(row)
    return out[:limit] if limit else out


def _validate(result: dict, fallback: dict) -> dict:
    """스키마 강제. 다른 KOSENA Agent 와 같이 **모자란 개수를 지어내 채우지 않는다.**"""
    if not isinstance(result, dict):
        return dict(fallback)

    cjm_raw = result.get("cjm") if isinstance(result.get("cjm"), dict) else {}
    cjm = {
        "stages": _dicts(cjm_raw.get("stages"), ("stage", *CJM_ITEMS), len(CJM_STAGES)),
        "opportunities": _strs(cjm_raw.get("opportunities")),
    }

    ms_raw = result.get("market_sizing") if isinstance(result.get("market_sizing"), dict) else {}
    market_sizing = {k: str(ms_raw[k]).strip() for k in
                     ("tam", "sam", "som", "top_down", "bottom_up", "gap_reason")
                     if isinstance(ms_raw.get(k), (str, int, float)) and str(ms_raw[k]).strip()}
    if _strs(ms_raw.get("assumptions")):
        market_sizing["assumptions"] = _strs(ms_raw.get("assumptions"))

    cg_raw = result.get("competitor_groups") if isinstance(result.get("competitor_groups"), dict) else {}
    competitor_groups = {g: _strs(cg_raw.get(g), n) for g, n in GROUP_SIZES.items()
                         if _strs(cg_raw.get(g))}

    pm_raw = result.get("positioning_map") if isinstance(result.get("positioning_map"), dict) else {}
    points = []
    for p in pm_raw.get("points") if isinstance(pm_raw.get("points"), list) else []:
        if isinstance(p, dict) and p.get("name"):
            try:
                points.append({"name": str(p["name"]), "x": float(p.get("x", 0)),
                               "y": float(p.get("y", 0))})
            except (TypeError, ValueError):
                continue
    positioning_map = {}
    if pm_raw.get("x_axis") and pm_raw.get("y_axis") and points:
        positioning_map = {"x_axis": str(pm_raw["x_axis"]), "y_axis": str(pm_raw["y_axis"]),
                           "points": points}

    out = {
        "personas": _dicts(result.get("personas"), ("name", *PERSONA_FIELDS), PERSONA_COUNT),
        "cjm": cjm if cjm["stages"] else {},
        "market_sizing": market_sizing,
        "competitor_groups": competitor_groups,
        "comparison_criteria": _strs(result.get("comparison_criteria")),
        "positioning_map": positioning_map,
    }
    return out if any(out.values()) else dict(fallback)


def _dummy() -> dict:
    return {
        "personas": [
            {"name": f"[더미] 페르소나 {i}", "demographics": "[더미] 30대·직장인",
             "behavior": "[더미] 모바일 중심", "goal": "[더미] 시간 절약",
             "pain_points": "[더미] 정보 탐색 비용", "expectations": "[더미] 간편함(가설·미검증)"}
            for i in range(1, PERSONA_COUNT + 1)],
        "cjm": {"stages": [{"stage": s, "action": "[더미] 행동", "emotion": "[더미] 감정",
                            "pain_point": "[더미] 불편", "touchpoint": "[더미] 접점"}
                           for s in CJM_STAGES],
                "opportunities": ["[더미] 기회 1", "[더미] 기회 2"]},
        "market_sizing": {"tam": "[더미] 추정 1조원", "sam": "[더미] 추정 2000억원",
                          "som": "[더미] 추정 60억원(가정 3%)", "top_down": "[더미] 전체시장×점유율",
                          "bottom_up": "[더미] 고객수×단가×빈도",
                          "gap_reason": "[더미] 채널 가정 차이", "assumptions": ["[더미] 가정"]},
        "competitor_groups": {"direct": ["[더미] 직접1", "[더미] 직접2", "[더미] 직접3"],
                              "indirect": ["[더미] 간접1", "[더미] 간접2"],
                              "potential": ["[더미] 잠재1"]},
        "comparison_criteria": [f"[더미] 비교항목 {i}" for i in range(1, CRITERIA_MIN + 1)],
        "positioning_map": {"x_axis": "가격", "y_axis": "기능",
                            "points": [{"name": "자사", "x": 4.0, "y": 8.0},
                                       {"name": "[더미] A", "x": 7.0, "y": 6.0}]},
    }


def kosena_research(state: ProjectState) -> dict:
    """조사·고객·경쟁사 결과로 페르소나 2종·CJM·시장 사이징·포지셔닝을 만든다."""
    research = artifact.read(state, "research_analysis")
    customer = artifact.read(state, "customer_analysis")
    competitor = artifact.read(state, "competitor_analysis")

    fallback = _dummy()
    user = (
        "아래 결과를 근거로 고객 리서치와 시장 사이징을 수행하세요.\n"
        f"[아이디어]\n{json.dumps(state.get('structured_input', {}), ensure_ascii=False)}\n\n"
        f"[시장조사]\n{json.dumps(research, ensure_ascii=False)}\n\n"
        f"[고객 분석]\n{json.dumps(customer, ensure_ascii=False)}\n\n"
        f"[경쟁사]\n{json.dumps(competitor, ensure_ascii=False)}"
    )
    status: dict = {}
    raw = llm.complete_json(KOSENA_RESEARCH_SYSTEM, user, fallback=fallback,
                            model=state.get("model", ""), status=status)
    result = _validate(raw, fallback)

    mode = llm.mode_label(status, state.get("model", ""))
    logs = [f"[kosena_research] 고객·시장 분석 완료 ({mode}, 페르소나 "
            f"{len(result['personas'])}/{PERSONA_COUNT} · CJM "
            f"{len(result['cjm'].get('stages', []))}/5 · 비교항목 "
            f"{len(result['comparison_criteria'])}/{CRITERIA_MIN}+)"]
    return {"kosena": result, "logs": logs}
