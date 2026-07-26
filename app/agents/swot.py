"""SWOT Agent — Research·경쟁사 결과 근거로 강점/약점/기회/위협 도출."""
from __future__ import annotations

import json

from app.prompts.templates import SWOT_SYSTEM
from app.schemas import artifact
from app.schemas.state import ProjectState
from app.services import llm

_KEYS = ["strengths", "weaknesses", "opportunities", "threats"]


def _validate(result: dict, fallback: dict) -> dict:
    if not isinstance(result, dict):
        return dict(fallback)
    out = {}
    for k in _KEYS:
        v = result.get(k)
        out[k] = [s.strip() for s in v if isinstance(s, str) and s.strip()] if isinstance(v, list) else []
    return out


def _dummy(_: dict) -> dict:
    return {k: [f"[더미] {k}"] for k in _KEYS}


def swot(state: ProjectState) -> dict:
    # 앞 Agent 결과는 selector 로 읽는다(로드맵 2-2 PR 5c-3). **의존이 2개인 첫 전환**이므로,
    # 여기서 읽는 유형 집합이 명세의 depends_on(artifact-research + artifact-competitor)과
    # 일치하는지가 테스트로 고정된다. 기본 모드 legacy 에서는 평면 키를 그대로 읽는다.
    research = artifact.read(state, "research_analysis")
    comp = artifact.read(state, "competitor_analysis")
    fallback = _dummy(research)
    user = (
        "아래 결과를 근거로 SWOT 분석을 수행하세요.\n"
        f"[시장조사]\n{json.dumps(research, ensure_ascii=False)}\n"
        f"[경쟁사]\n{json.dumps(comp, ensure_ascii=False)}"
    )
    status: dict = {}
    raw = llm.complete_json(SWOT_SYSTEM, user, fallback=fallback,
                            model=state.get("model", ""), status=status)
    result = _validate(raw, fallback)
    mode = llm.mode_label(status, state.get("model", ""))
    logs = [f"[swot] SWOT 분석 완료 ({mode})"]
    # Dual Write(로드맵 2-2 PR 4, 3묶음): 평면 결과와 같은 내용을 표준 Artifact 봉투로도 방출.
    # depends_on 은 research + competitor 2개(위 두 결과를 실제로 읽는다) — 명세가 자동으로 싣는다.
    return {"swot_result": result, "logs": logs,
            "artifacts": [artifact.make_artifact("swot_analysis", result)]}
