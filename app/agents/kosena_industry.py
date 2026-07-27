"""KOSENA M1 산업 분석 Agent — Porter · Value Chain · KSF · 시사점 (체크포인트 3).

KOSENA 는 PESTEL 결과를 **입력으로** Porter/SWOT/Value Chain 중 최소 2개를 적용하고,
거기서 **KSF 5개**와 **설계 시사점 3가지**를 도출하라고 규정한다(PDF p8). PESTEL 자체도
6영역 서술로 끝나는 게 아니라 **영향력·가능성이 모두 높은 Top 3**를 골라 다음 단계 입력으로
넘겨야 한다(p7).

    거시환경(PESTEL) → 산업환경(Porter) → 자사환경(SWOT) → KSF 도출        (p7)

이 체인이 이 프로젝트의 Artifact 의존 모델과 그대로 대응한다 — 그래서 새 Agent 를 하나
얹는 것으로 M1 의 빈칸 대부분이 메워진다. 앞 단계 결과는 평면 키가 아니라
`artifact.read`(selector)로 읽어 2-2 의 읽기 모드를 그대로 따른다.

**결과는 `state["kosena"]` 에 자기 키만 넣는다**(reducer 가 병합). 7개 평면 결과 키와
`LEGACY_ARTIFACT_SPECS` 는 건드리지 않는다 — 거기에 항목을 더하면 `check_parity` 의
`expected == 7` 전제와 기존 정합성 테스트가 함께 깨진다.
"""
from __future__ import annotations

import json

from app.prompts.templates import KOSENA_INDUSTRY_SYSTEM
from app.schemas import artifact
from app.schemas.state import ProjectState
from app.services import llm

# Porter 5 Forces 키(고정) — 개수·이름이 평가 대상이라 코드에서 강제한다(p8).
FORCES = ("rivalry", "new_entrants", "substitutes", "buyer_power", "supplier_power")
# Value Chain 주활동 5 + 지원활동 4(p8).
VALUE_CHAIN_KEYS = ("inbound", "operations", "outbound", "marketing", "service",
                    "infrastructure", "hr", "technology", "procurement")
KSF_COUNT = 5
IMPLICATION_COUNT = 3
CU_COUNT = 3


def _strs(v, limit: int | None = None) -> list[str]:
    out = [s.strip() for s in v if isinstance(s, str) and s.strip()] if isinstance(v, list) else []
    return out[:limit] if limit else out


def _validate(result: dict, fallback: dict) -> dict:
    """스키마를 강제한다. **모자란 개수를 지어내 채우지 않는다.**

    KSF 가 4개면 4개 그대로 두고 `kosena_compliance` 가 '부분 충족'으로 보고하게 한다 —
    빈 문자열로 5개를 맞추면 검사는 통과하는데 문서에는 빈칸이 남는, 가장 나쁜 결과가 된다.
    """
    if not isinstance(result, dict):
        return dict(fallback)

    cu = []
    for item in (result.get("critical_uncertainties") or [])[:CU_COUNT]:
        if isinstance(item, dict) and (item.get("factor") or item.get("why")):
            cu.append({"factor": str(item.get("factor", "")), "why": str(item.get("why", "")),
                       "impact": str(item.get("impact", ""))})

    porter_raw = result.get("porter") if isinstance(result.get("porter"), dict) else {}
    porter = {}
    for f in FORCES:
        v = porter_raw.get(f)
        if isinstance(v, dict) and (v.get("level") or v.get("rationale")):
            porter[f] = {"level": str(v.get("level", "")), "rationale": str(v.get("rationale", ""))}

    vc_raw = result.get("value_chain") if isinstance(result.get("value_chain"), dict) else {}
    value_chain = {k: str(vc_raw[k]).strip() for k in VALUE_CHAIN_KEYS
                   if isinstance(vc_raw.get(k), str) and vc_raw[k].strip()}

    out = {
        "critical_uncertainties": cu,
        "porter": porter,
        "value_chain": value_chain,
        "ksf": _strs(result.get("ksf"), KSF_COUNT),
        "implications": _strs(result.get("implications"), IMPLICATION_COUNT),
    }
    # 부분적으로라도 나왔으면 그대로 살린다(있는 만큼이 정직하다).
    if any(out.values()):
        return out
    # 실모드 폴백은 **비어 있다**(`llm.dummy_fallback`) — 실패한 호출의 더미 구조가 KOSENA 준수
    # 검사를 충족으로 통과시키지 않도록. 그때는 `{}` 대신 **키를 갖춘 빈 결과**를 돌려준다.
    # `{}` 를 내보내면 호출부 로그의 result[...] 접근이 KeyError 로 노드를 실패시킨다.
    return dict(fallback) if fallback else out


def _dummy() -> dict:
    """구조는 완전하되 내용은 [더미] — 키 없이도 배선·검사를 관통 확인할 수 있어야 한다."""
    return {
        "critical_uncertainties": [
            {"factor": f"[더미] 요인 {i}", "why": "[더미] 영향력·가능성 모두 높음",
             "impact": "[더미] 서비스 설계에 직접 영향"} for i in range(1, CU_COUNT + 1)],
        "porter": {f: {"level": "중간", "rationale": "[더미] 근거"} for f in FORCES},
        "value_chain": {k: "[더미] 활동 서술" for k in VALUE_CHAIN_KEYS},
        "ksf": [f"[더미] 핵심 성공요인 {i}" for i in range(1, KSF_COUNT + 1)],
        "implications": [f"[더미] 설계 시사점 {i}" for i in range(1, IMPLICATION_COUNT + 1)],
    }


def kosena_industry(state: ProjectState) -> dict:
    """PESTEL·조사·경쟁사·SWOT 를 입력으로 Porter·Value Chain·KSF·시사점을 도출한다."""
    # 앞 Agent 결과는 selector 로 읽는다(2-2). 각 유형을 **한 번씩만** 읽어 폴백 경고·
    # 런타임 읽기 카운터가 부풀지 않게 한다.
    pestel = artifact.read(state, "pestel_analysis")
    research = artifact.read(state, "research_analysis")
    competitor = artifact.read(state, "competitor_analysis")
    swot = artifact.read(state, "swot_analysis")

    fallback = llm.dummy_fallback(_dummy())
    user = (
        "아래 앞 단계 분석 결과를 근거로 산업 분석을 수행하세요.\n"
        f"[PESTEL]\n{json.dumps(pestel, ensure_ascii=False)}\n\n"
        f"[시장조사]\n{json.dumps(research, ensure_ascii=False)}\n\n"
        f"[경쟁사]\n{json.dumps(competitor, ensure_ascii=False)}\n\n"
        f"[SWOT]\n{json.dumps(swot, ensure_ascii=False)}"
    )
    status: dict = {}
    raw = llm.complete_json(KOSENA_INDUSTRY_SYSTEM, user, fallback=fallback,
                            model=state.get("model", ""), status=status)
    result = _validate(raw, fallback)

    mode = llm.mode_label(status, state.get("model", ""))
    logs = [f"[kosena_industry] 산업 분석 완료 ({mode}, Porter {len(result['porter'])}/5 · "
            f"KSF {len(result['ksf'])}/{KSF_COUNT} · 시사점 {len(result['implications'])}/"
            f"{IMPLICATION_COUNT})"]
    return {"kosena": result, "logs": logs}
