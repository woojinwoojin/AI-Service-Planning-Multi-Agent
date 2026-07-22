"""입력 구조화 (별도 Agent가 아닌 전처리 함수 — ROADMAP 2번).

사용자 입력을 뒤 Agent들이 쓰기 좋은 형태로 정리하고, 비어 있는 필드에 기본값을 채운다.
"""
from __future__ import annotations

from app.schemas.state import ProjectState


def preprocess(state: ProjectState) -> dict:
    ui = state.get("user_input", {}) or {}

    keywords = ui.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [k.strip() for k in keywords.split(",") if k.strip()]

    structured = {
        "project_name": (ui.get("project_name") or "제목 없는 프로젝트").strip(),
        "description": (ui.get("description") or "").strip(),
        "target_user": (ui.get("target_user") or "미지정").strip(),
        "problem": (ui.get("problem") or "").strip(),
        "keywords": keywords,
        # 목표 시장: 명시값이 없으면 목표 사용자로 대체
        "target_market": (ui.get("target_market") or ui.get("target_user") or "미지정").strip(),
    }

    logs = ["[preprocess] 입력 구조화 완료"]
    return {"structured_input": structured, "logs": logs, "revision_count": 0}
