"""프로젝트 이력 저장(SQLite) 테스트 — 임시 DB 사용, 서버·LLM 불필요."""
from __future__ import annotations

import pytest

from app.services import store


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "projects.db")
    return store


def _state(name, score):
    return {
        "structured_input": {"project_name": name},
        "model": "gpt-4o-mini",
        "research_result": {"market_overview": "M"},
        "review_result": {"total_score": score},
        "final_draft": f"# {name} 기획서\n내용",
        "logs": ["[research] ok"],
    }


def test_save_list_get_roundtrip(tmp_db):
    id1 = tmp_db.save_run(_state("서비스 A", 82))
    id2 = tmp_db.save_run(_state("서비스 B", 91))
    assert id2 > id1

    items = tmp_db.list_projects()
    assert len(items) == 2
    assert items[0]["id"] == id2                     # 최신순
    assert items[0]["project_name"] == "서비스 B" and items[0]["total_score"] == 91

    detail = tmp_db.get_project(id1)
    assert detail["project_name"] == "서비스 A"
    assert detail["state"]["final_draft"].startswith("# 서비스 A")
    assert detail["state"]["review_result"]["total_score"] == 82


def test_get_missing_returns_none(tmp_db):
    assert tmp_db.get_project(999) is None


def test_save_defaults_name_when_missing(tmp_db):
    pid = tmp_db.save_run({"logs": []})
    assert tmp_db.get_project(pid)["project_name"] == "제목 없는 프로젝트"
