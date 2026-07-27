"""KOSENA M2·M3 제품·로드맵 Agent — VPC · MVP · Epic-Story-AC · 와이어프레임 (체크포인트 3).

KOSENA 는 고객 프로필과 가치 지도의 **적합성(Fit)** 을 검증해 핵심 기능 5~7개와 Use Case 3종을
뽑고(p15), MOSCOW·Kano 로 MVP 범위를 확정하며(p17), Epic–Story–Acceptance Criteria 를 INVEST
원칙과 **Given-When-Then** 형식으로 쓰라고 규정한다(p18). 제출물에는 **와이어프레임**도 포함된다(p4·p5).

앞 두 KOSENA 노드 뒤에 온다 — VPC 고객 프로필은 페르소나·CJM(`kosena_research`)에서,
Solution·UVP 는 Lean Canvas(`kosena_model`)에서 이어받아야 일관성이 생긴다(평가표의
"Lean Canvas 블록 간 일관성"·"VPC Fit 검증"이 이 연결을 본다, p20).

와이어프레임은 Figma 수준의 디자인이 아니라 **고정폭 ASCII 박스 구조 스케치**다. 문서·PPT 에
코드블록으로 실어 '와이어프레임 없음'을 '기본 구조 제시'로 바꾸는 것이 목적이다.
"""
from __future__ import annotations

import json

from app.prompts.templates import KOSENA_ROADMAP_SYSTEM
from app.schemas import artifact
from app.schemas.state import ProjectState
from app.services import llm

FEATURE_MIN, FEATURE_MAX = 5, 7
USE_CASE_COUNT = 3
WIREFRAME_COUNT = 3
MOSCOW_KEYS = ("must", "should", "could", "wont")
KANO_KEYS = ("basic", "performance", "excitement")
VPC_PROFILE = ("customer_jobs", "pains", "gains")
VPC_MAP = ("products_services", "pain_relievers", "gain_creators")


def _strs(v, limit: int | None = None) -> list[str]:
    out = [s.strip() for s in v if isinstance(s, str) and s.strip()] if isinstance(v, list) else []
    return out[:limit] if limit else out


def _dicts(v, keys, limit: int | None = None) -> list[dict]:
    out = []
    for item in v if isinstance(v, list) else []:
        if not isinstance(item, dict):
            continue
        row = {k: str(item.get(k, "")).strip() for k in keys}
        if any(row.values()):
            out.append(row)
    return out[:limit] if limit else out


def _validate(result: dict, fallback: dict) -> dict:
    """스키마 강제. 개수 미달은 **그대로 두고** 검사가 부분 충족을 말하게 한다."""
    if not isinstance(result, dict):
        return dict(fallback)

    vpc_raw = result.get("vpc") if isinstance(result.get("vpc"), dict) else {}
    prof_raw = vpc_raw.get("customer_profile") if isinstance(vpc_raw.get("customer_profile"), dict) else {}
    map_raw = vpc_raw.get("value_map") if isinstance(vpc_raw.get("value_map"), dict) else {}
    profile = {k: _strs(prof_raw.get(k)) for k in VPC_PROFILE if _strs(prof_raw.get(k))}
    value_map = {k: _strs(map_raw.get(k)) for k in VPC_MAP if _strs(map_raw.get(k))}
    vpc = {}
    if profile or value_map:
        vpc = {"customer_profile": profile, "value_map": value_map,
               "fit": str(vpc_raw.get("fit", "")).strip()}

    moscow_raw = result.get("moscow") if isinstance(result.get("moscow"), dict) else {}
    # wont 는 '이번 범위 제외'를 명시하는 칸이라 **비어 있어도 키를 남긴다**(p17).
    moscow = {k: _strs(moscow_raw.get(k)) for k in MOSCOW_KEYS} if moscow_raw else {}

    kano_raw = result.get("kano") if isinstance(result.get("kano"), dict) else {}
    kano = {k: _strs(kano_raw.get(k)) for k in KANO_KEYS if _strs(kano_raw.get(k))}

    epics = []
    for e in result.get("epics") if isinstance(result.get("epics"), list) else []:
        if not isinstance(e, dict):
            continue
        stories = _dicts(e.get("stories"), ("story", "given", "when", "then"))
        if e.get("name") or stories:
            epics.append({"name": str(e.get("name", "")).strip(), "stories": stories})

    out = {
        "vpc": vpc,
        "core_features": _dicts(result.get("core_features"),
                                ("name", "impact", "feasibility"), FEATURE_MAX),
        "use_cases": _dicts(result.get("use_cases"),
                            ("actor", "scenario", "expected_result"), USE_CASE_COUNT),
        "moscow": moscow,
        "kano": kano,
        "mvp_scope": str(result.get("mvp_scope", "")).strip(),
        "epics": epics,
        "milestones": _dicts(result.get("milestones"), ("name", "period", "goal")),
        "kpis": _dicts(result.get("kpis"), ("name", "target")),
        "wireframes": _dicts(result.get("wireframes"), ("screen", "layout"), WIREFRAME_COUNT),
    }
    if any(out.values()):
        return out
    # 실모드 폴백은 **비어 있다**(`llm.dummy_fallback`) — 실패한 호출의 더미 구조가 KOSENA 준수
    # 검사를 충족으로 통과시키지 않도록. 그때는 `{}` 대신 **키를 갖춘 빈 결과**를 돌려준다.
    # `{}` 를 내보내면 호출부 로그의 result[...] 접근이 KeyError 로 노드를 실패시킨다.
    return dict(fallback) if fallback else out


_WIRE = ("┌──────────────────────────┐\n"
         "│ 서비스명            메뉴 │\n"
         "├──────────────────────────┤\n"
         "│ [더미] 주요 영역          │\n"
         "│ [카드 1]      [카드 2]    │\n"
         "├──────────────────────────┤\n"
         "│ 홈    검색    저장    MY │\n"
         "└──────────────────────────┘")


def _dummy() -> dict:
    return {
        "vpc": {"customer_profile": {k: [f"[더미] {k} 1", f"[더미] {k} 2"] for k in VPC_PROFILE},
                "value_map": {k: [f"[더미] {k} 1", f"[더미] {k} 2"] for k in VPC_MAP},
                "fit": "[더미] Pain Reliever 가 상위 Pain 2건을 직접 해소한다(가설)"},
        "core_features": [{"name": f"[더미] 기능 {i}", "impact": "[더미] 높음",
                           "feasibility": "[더미] 중간"} for i in range(1, FEATURE_MIN + 1)],
        "use_cases": [{"actor": "[더미] 사용자", "scenario": f"[더미] 시나리오 {i}",
                       "expected_result": "[더미] 기대 결과"} for i in range(1, USE_CASE_COUNT + 1)],
        "moscow": {"must": ["[더미] 필수 기능"], "should": ["[더미] 중요 기능"],
                   "could": ["[더미] 선택 기능"], "wont": ["[더미] 이번 범위 제외"]},
        "kano": {"basic": ["[더미] 당연 품질"], "performance": ["[더미] 성능 품질"],
                 "excitement": ["[더미] 매력 품질"]},
        "mvp_scope": "[더미] Must have + Performance 핵심 + Excitement 1개로 범위 확정",
        "epics": [{"name": "[더미] Epic 1", "stories": [
            {"story": "As a 사용자, I want ~, So that ~", "given": "[더미] 전제",
             "when": "[더미] 조건", "then": "[더미] 결과"}]}],
        "milestones": [{"name": f"[더미] M{i}", "period": "[더미] 1개월", "goal": "[더미] 목표"}
                       for i in range(1, 4)],
        "kpis": [{"name": f"[더미] 지표 {i}", "target": "[더미] 목표치(가정)"} for i in range(1, 4)],
        "wireframes": [{"screen": s, "layout": _WIRE}
                       for s in ("메인 화면", "핵심 기능 화면", "결과·완료 화면")],
    }


def kosena_roadmap(state: ProjectState) -> dict:
    """VPC·기능·Use Case·MOSCOW·Kano·MVP·Epic-Story-AC·마일스톤·와이어프레임을 만든다."""
    customer = artifact.read(state, "customer_analysis")
    business_model = artifact.read(state, "business_model_analysis")
    risk = artifact.read(state, "risk_analysis")
    prior = state.get("kosena") if isinstance(state.get("kosena"), dict) else {}

    fallback = llm.dummy_fallback(_dummy())
    user = (
        "아래 결과를 근거로 가치 제안과 개발 로드맵을 설계하세요.\n"
        f"[아이디어]\n{json.dumps(state.get('structured_input', {}), ensure_ascii=False)}\n\n"
        f"[고객 분석]\n{json.dumps(customer, ensure_ascii=False)}\n\n"
        f"[페르소나]\n{json.dumps(prior.get('personas') or [], ensure_ascii=False)}\n\n"
        f"[CJM]\n{json.dumps(prior.get('cjm') or {}, ensure_ascii=False)}\n\n"
        f"[Lean Canvas]\n{json.dumps(prior.get('lean_canvas') or {}, ensure_ascii=False)}\n\n"
        f"[수익 모델]\n{json.dumps(business_model, ensure_ascii=False)}\n\n"
        f"[리스크]\n{json.dumps(risk, ensure_ascii=False)}"
    )
    status: dict = {}
    raw = llm.complete_json(KOSENA_ROADMAP_SYSTEM, user, fallback=fallback,
                            model=state.get("model", ""), status=status)
    result = _validate(raw, fallback)

    mode = llm.mode_label(status, state.get("model", ""))
    stories = sum(len(e.get("stories") or []) for e in result["epics"])
    logs = [f"[kosena_roadmap] 로드맵 설계 완료 ({mode}, 기능 {len(result['core_features'])}"
            f"/{FEATURE_MIN}~{FEATURE_MAX} · Use Case {len(result['use_cases'])}"
            f"/{USE_CASE_COUNT} · Story {stories} · 와이어프레임 {len(result['wireframes'])})"]
    return {"kosena": result, "logs": logs}
