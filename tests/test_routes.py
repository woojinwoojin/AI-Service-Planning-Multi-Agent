"""API 라우트 통합 테스트 (더미 모드·임시 DB, 실제 LLM 호출 없음).

item 4: /revise 결과가 이력에 반영되고 응답에 수정횟수·관측치·id가 포함되는지.
item 9: /run 응답에 실행 품질(run_status)이 표면화되는지.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import llm, store


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "projects.db")
    monkeypatch.setattr(llm, "is_dummy", lambda: True)      # LLM 호출 없이 관통
    return TestClient(app)


def test_run_persists_and_reports_quality(client):
    d = client.post("/run", json={"project_name": "테스트", "problem": "P"}).json()
    assert d["project_id"] > 0
    assert d["run_status"] == "degraded"                    # 더미 실행 → 정직하게 degraded
    assert d["final_review_result"]                         # 최종본 재평가 포함
    ids = [p["id"] for p in client.get("/projects").json()["projects"]]
    assert d["project_id"] in ids                           # 이력에 저장됨


def test_revise_updates_same_history_record(client, monkeypatch):
    from app.api import routes
    run = client.post("/run", json={"project_name": "수정대상", "problem": "P"}).json()
    pid = run["project_id"]

    # 재작성기가 마커가 있는 수정본을 내도록 대체(더미 fallback 텍스트 배치 아티팩트 회피)
    monkeypatch.setattr(routes.draft_writer, "revise", lambda state: {
        "final_draft": "# 수정대상 기획서\n확실히 바뀐 수정본", "revision_count": 1,
        "logs": ["[draft_writer] 재작성 완료 (revision=1)"]})

    rev = client.post("/revise", json={
        "project_name": "수정대상", "draft": run["final_draft"],
        "revision_request": "톤을 정리해줘", "project_id": pid,
    }).json()

    assert rev["project_id"] == pid                         # 같은 레코드 갱신(신규 아님)
    assert rev["revision_count"] == 1
    assert "usage" in rev and "final_review_result" in rev  # 응답 보강
    after = client.get(f"/projects/{pid}").json()["state"]["final_draft"]
    assert "확실히 바뀐 수정본" in after                    # 이력이 수정본으로 갱신됨
    assert len(client.get("/projects").json()["projects"]) == 1


def test_revise_without_project_id_saves_new(client):
    rev = client.post("/revise", json={
        "project_name": "새기획", "draft": "# 새기획 기획서\n내용",
        "revision_request": "보강",
    }).json()
    assert rev["project_id"] > 0                            # id 없으면 신규 저장
    assert rev["revision_count"] == 1


def test_suggest_requires_project_name(client):
    r = client.post("/suggest", json={"project_name": ""})
    assert r.status_code == 400                             # 프로젝트명 필수


def test_suggest_returns_fields_in_dummy_mode(client):
    d = client.post("/suggest", json={"project_name": "AI 반려식물 케어"}).json()
    assert set(d) == {"description", "target_user", "problem", "keywords"}
    assert isinstance(d["keywords"], list)
    assert d["description"]                                 # 더미라도 초안은 채워짐


def test_run_stream_emits_node_events_and_done(client):
    import json
    with client.stream("POST", "/run/stream",
                       json={"project_name": "스트림", "problem": "P"}) as r:
        assert r.status_code == 200
        assert "text/event-stream" in r.headers["content-type"]
        types, nodes, result = [], [], None
        for line in r.iter_lines():
            if not line or not line.startswith("data: "):
                continue
            ev = json.loads(line[6:])
            types.append(ev["type"])
            if ev["type"] == "node":
                nodes.append(ev["node"])
            elif ev["type"] == "done":
                result = ev["result"]
    assert "preprocess" in nodes and "verify" in nodes   # 실제 노드 순차 완료 이벤트
    assert types[-1] == "done"                            # 마지막은 done
    assert result and result["project_id"] > 0            # 결과 포함 + 이력 저장
    assert result["final_draft"]
