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


def test_update_run_replaces_state_and_uses_final_score(tmp_db):
    """item 4: 수정 결과가 같은 레코드에 반영되고 총점은 최종본 재평가 점수로 갱신."""
    pid = tmp_db.save_run(_state("서비스 A", 70))
    revised = _state("서비스 A", 70)
    revised["final_draft"] = "# 서비스 A 기획서\n수정 반영본"
    revised["final_review_result"] = {"total_score": 88}
    assert tmp_db.update_run(pid, revised) is True
    detail = tmp_db.get_project(pid)
    assert detail["state"]["final_draft"].endswith("수정 반영본")   # 수정본으로 교체
    assert detail["total_score"] == 88                              # 최종본 점수로 갱신
    assert len(tmp_db.list_projects()) == 1                         # 신규가 아닌 갱신


def test_update_run_missing_id_returns_false(tmp_db):
    assert tmp_db.update_run(999, _state("X", 50)) is False


def test_save_defaults_name_when_missing(tmp_db):
    pid = tmp_db.save_run({"logs": []})
    assert tmp_db.get_project(pid)["project_name"] == "제목 없는 프로젝트"
