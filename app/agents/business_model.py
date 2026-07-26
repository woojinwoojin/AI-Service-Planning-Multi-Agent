"""Business Model Agent — 수익 구조·가격·비용·핵심지표 제안."""
from __future__ import annotations

import json

from app.prompts.templates import BIZMODEL_SYSTEM
from app.schemas import artifact
from app.schemas.state import ProjectState
from app.services import llm


def _validate(result: dict, fallback: dict) -> dict:
    if not isinstance(result, dict):
        return dict(fallback)

    def _list(key):
        v = result.get(key)
        return [s.strip() for s in v if isinstance(s, str) and s.strip()] if isinstance(v, list) else []

    pricing = result.get("pricing") if isinstance(result.get("pricing"), str) else ""
    return {
        "revenue_streams": _list("revenue_streams"),
        "pricing": pricing,
        "cost_structure": _list("cost_structure"),
        "key_metrics": _list("key_metrics"),
    }


def _dummy(_: dict) -> dict:
    return {
        "revenue_streams": ["[더미] 구독 수익"],
        "pricing": "[더미] 월 구독제",
        "cost_structure": ["[더미] 인프라 비용"],
        "key_metrics": ["[더미] MAU"],
    }


def business_model(state: ProjectState) -> dict:
    # 앞 Agent(Research) 결과는 selector 로 읽는다(로드맵 2-2 PR 5c-2) — ARTIFACT_READ_MODE 를 따른다.
    research = artifact.read(state, "research_analysis")
    si = state.get("structured_input", {})
    fallback = _dummy(research)
    user = (
        "아래 정보를 근거로 비즈니스/수익 모델을 설계하세요.\n"
        f"[아이디어]\n{json.dumps(si, ensure_ascii=False)}\n"
        f"[시장조사]\n{json.dumps(research, ensure_ascii=False)}"
    )
    status: dict = {}
    raw = llm.complete_json(BIZMODEL_SYSTEM, user, fallback=fallback,
                            model=state.get("model", ""), status=status)
    result = _validate(raw, fallback)
    mode = llm.mode_label(status, state.get("model", ""))
    logs = [f"[business_model] 수익모델 설계 완료 ({mode})"]
    # Dual Write(로드맵 2-2 PR 4, 3묶음): 평면 결과와 같은 내용을 표준 Artifact 봉투로도 방출.
    # 이 묶음으로 7개 Agent 전부가 Artifact 를 직접 쓴다(파생 폴백은 옛 기록용으로만 남는다).
    return {"business_model_result": result, "logs": logs,
            "artifacts": [artifact.make_artifact("business_model_analysis", result)]}
