"""프로젝트 이력 저장 (SQLite).

/run 실행 결과를 로컬 DB에 저장하고, 목록·상세 조회를 제공한다. 별도 ORM 없이
파이썬 내장 sqlite3만 사용한다. 전체 실행 상태는 JSON 블롭으로, 조회용 필드
(이름·모델·총점·시각)는 별도 컬럼으로 저장한다.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.services.markdown_export import _RUN_KEYS

DB_PATH = Path("data/projects.db")


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_name TEXT,
            model TEXT,
            total_score INTEGER,
            created_at TEXT,
            state_json TEXT
        )"""
    )
    return conn


def save_run(state: dict) -> int:
    """실행 상태를 저장하고 새 프로젝트 id를 반환."""
    si = state.get("structured_input") or {}
    name = si.get("project_name") or (state.get("user_input") or {}).get("project_name") or "제목 없는 프로젝트"
    # 이력의 총점은 실제 최종 문서 점수(final_review_result) 우선, 없으면 초안 점수로 대체
    total = (state.get("final_review_result") or state.get("review_result") or {}).get("total_score")
    created = datetime.now(timezone.utc).isoformat(timespec="seconds")
    payload = {k: state.get(k) for k in _RUN_KEYS}
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO projects (project_name, model, total_score, created_at, state_json) VALUES (?,?,?,?,?)",
            (name, state.get("model", ""), total, created, json.dumps(payload, ensure_ascii=False)),
        )
        return int(cur.lastrowid)


def list_projects(limit: int = 50) -> list[dict]:
    """최근 프로젝트 목록(상세 제외)."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, project_name, model, total_score, created_at "
            "FROM projects ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_project(project_id: int) -> dict | None:
    """단일 프로젝트 상세(state 포함). 없으면 None."""
    with _conn() as conn:
        row = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["state"] = json.loads(d.pop("state_json"))
    return d
