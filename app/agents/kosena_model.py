"""KOSENA M1 비즈니스 모델 Agent — HMW · Lean Canvas 9블록 · 핵심 가설 (체크포인트 3).

KOSENA 는 아이디어를 **발산 → 수렴**으로 만들고(HMW 5개 → 아이디어 25개 이상 → 3개 압축 →
최종 컨셉 1개, PDF p9), 비즈니스 모델을 **Lean Canvas 9블록**으로 정확한 순서·정의에 맞춰
작성하되 각 블록을 **가설 형태**로 쓰고 핵심 가설 3개에는 검증 계획을 붙이라고 규정한다(p10).
평가표도 "Lean Canvas 일관성 + 검증 가설 명확"을 직접 본다(p20).

현재 `business_model_result` 는 `{revenue_streams, pricing, cost_structure, key_metrics}` 뿐이라
**9블록 중 6개(Problem·Customer Segment·UVP·Solution·Channels·Unfair Advantage)는 재료 자체가
없다.** 그래서 이 단계는 재조립이 아니라 새 분석이다.

`kosena_industry` 뒤에 온다 — HMW 는 **KSF + 시장 Gap 을 결합**해 만들어야 하므로(p9)
KSF 가 먼저 나와 있어야 한다.
"""
from __future__ import annotations

import json

from app.prompts.templates import KOSENA_MODEL_SYSTEM
from app.schemas import artifact
from app.schemas.state import ProjectState
from app.services import llm

# Lean Canvas 9블록(p10) — 작성 순서 1→9 그대로. 개수·이름이 평가 대상이라 코드에서 강제한다.
LEAN_BLOCKS = ("problem", "customer_segments", "uvp", "solution", "channels",
               "revenue_streams", "cost_structure", "key_metrics", "unfair_advantage")
HMW_COUNT = 5
IDEA_MIN = 25
HYPOTHESIS_COUNT = 3


def _strs(v, limit: int | None = None) -> list[str]:
    out = [s.strip() for s in v if isinstance(s, str) and s.strip()] if isinstance(v, list) else []
    return out[:limit] if limit else out


def _validate(result: dict, fallback: dict) -> dict:
    """스키마 강제. `kosena_industry` 와 같은 이유로 **모자란 개수를 지어내 채우지 않는다.**"""
    if not isinstance(result, dict):
        return dict(fallback)

    lc_raw = result.get("lean_canvas") if isinstance(result.get("lean_canvas"), dict) else {}
    lean_canvas = {k: str(lc_raw[k]).strip() for k in LEAN_BLOCKS
                   if isinstance(lc_raw.get(k), str) and lc_raw[k].strip()}

    hyps = []
    for item in (result.get("key_hypotheses") or [])[:HYPOTHESIS_COUNT]:
        if isinstance(item, dict) and item.get("hypothesis"):
            hyps.append({"hypothesis": str(item["hypothesis"]),
                         "validation": str(item.get("validation", "")),
                         "metric": str(item.get("metric", ""))})

    concept = result.get("selected_concept")
    out = {
        "hmw": _strs(result.get("hmw"), HMW_COUNT),
        "ideas": _strs(result.get("ideas")),
        "selected_concept": concept.strip() if isinstance(concept, str) else "",
        "lean_canvas": lean_canvas,
        "key_hypotheses": hyps,
    }
    return out if any(out.values()) else dict(fallback)


def _dummy() -> dict:
    return {
        "hmw": [f"[더미] 어떻게 하면 사용자가 상황 {i}에서 목표를 달성할 수 있을까?"
                for i in range(1, HMW_COUNT + 1)],
        "ideas": [f"[더미] 아이디어 {i}" for i in range(1, IDEA_MIN + 1)],
        "selected_concept": "[더미] 실현가능성·시장성·차별성 기준으로 선정한 최종 컨셉",
        "lean_canvas": {k: f"[더미] {k} 가설" for k in LEAN_BLOCKS},
        "key_hypotheses": [
            {"hypothesis": f"[더미] 핵심 가설 {i}", "validation": "[더미] 검증 방법",
             "metric": "[더미] 판단 지표"} for i in range(1, HYPOTHESIS_COUNT + 1)],
    }


def kosena_model(state: ProjectState) -> dict:
    """조사·고객·수익모델 + 앞 단계 KSF 를 근거로 HMW·Lean Canvas·핵심 가설을 만든다."""
    research = artifact.read(state, "research_analysis")
    customer = artifact.read(state, "customer_analysis")
    business_model = artifact.read(state, "business_model_analysis")
    # KSF·시사점은 같은 실행의 앞 KOSENA 노드가 넣어 둔 값이다(Artifact 가 아니라 state["kosena"]).
    prior = state.get("kosena") if isinstance(state.get("kosena"), dict) else {}

    fallback = _dummy()
    si = state.get("structured_input", {})
    user = (
        "아래 결과를 근거로 아이디어 발산·수렴과 Lean Canvas 를 작성하세요.\n"
        f"[아이디어]\n{json.dumps(si, ensure_ascii=False)}\n\n"
        f"[시장조사]\n{json.dumps(research, ensure_ascii=False)}\n\n"
        f"[고객 분석]\n{json.dumps(customer, ensure_ascii=False)}\n\n"
        f"[수익 모델]\n{json.dumps(business_model, ensure_ascii=False)}\n\n"
        f"[KSF]\n{json.dumps(prior.get('ksf') or [], ensure_ascii=False)}\n\n"
        f"[설계 시사점]\n{json.dumps(prior.get('implications') or [], ensure_ascii=False)}"
    )
    status: dict = {}
    raw = llm.complete_json(KOSENA_MODEL_SYSTEM, user, fallback=fallback,
                            model=state.get("model", ""), status=status)
    result = _validate(raw, fallback)

    mode = llm.mode_label(status, state.get("model", ""))
    logs = [f"[kosena_model] 비즈니스 모델 설계 완료 ({mode}, Lean Canvas "
            f"{len(result['lean_canvas'])}/9 · HMW {len(result['hmw'])}/{HMW_COUNT} · "
            f"아이디어 {len(result['ideas'])}/{IDEA_MIN}+)"]
    return {"kosena": result, "logs": logs}
