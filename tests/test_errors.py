"""통일 오류 응답 형식(로드맵 Phase 5) 테스트 — 서버·LLM 불필요(더미)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import llm, store


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "p.db")
    monkeypatch.setattr(llm, "is_dummy", lambda: True)     # LLM 미호출
    return TestClient(app)


def _assert_envelope(body: dict, status: int, code: str):
    assert set(body) == {"error"}
    err = body["error"]
    assert err["status"] == status
    assert err["code"] == code
    assert isinstance(err["message"], str) and err["message"]


def test_404_unified(client):
    r = client.get("/projects/999999")
    assert r.status_code == 404
    _assert_envelope(r.json(), 404, "not_found")
    assert "찾을 수 없습니다" in r.json()["error"]["message"]


def test_400_unified(client):
    # SuggestInput.project_name 은 필수지만 공백이면 라우트가 400을 던진다
    r = client.post("/suggest", json={"project_name": "   "})
    assert r.status_code == 400
    _assert_envelope(r.json(), 400, "bad_request")


def test_422_validation_unified_with_details(client):
    # /run 은 project_name 필수 → 누락 시 검증 오류
    r = client.post("/run", json={})
    assert r.status_code == 422
    body = r.json()
    _assert_envelope(body, 422, "validation_error")
    details = body["error"]["details"]
    assert isinstance(details, list) and details
    assert any("project_name" in d.get("field", "") for d in details)


def test_500_unified_hides_internals(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "p.db")
    monkeypatch.setattr(llm, "is_dummy", lambda: True)

    def boom(*a, **k):
        raise RuntimeError("내부 비밀 스택 정보")
    monkeypatch.setattr("app.api.routes.run_workflow", boom)
    # raise_server_exceptions=False → 핸들러 응답을 그대로 받는다
    c = TestClient(app, raise_server_exceptions=False)
    r = c.post("/run", json={"project_name": "터짐", "problem": "P"})
    assert r.status_code == 500
    _assert_envelope(r.json(), 500, "internal_error")
    assert "내부 비밀" not in r.json()["error"]["message"]   # 내부 상세 미노출


def test_error_payload_helper():
    from app.api import errors
    p = errors.error_payload(404, "없음")
    assert p == {"error": {"code": "not_found", "message": "없음", "status": 404}}
    p2 = errors.error_payload(422, "검증", details=[{"field": "x", "message": "m"}])
    assert p2["error"]["details"] == [{"field": "x", "message": "m"}]
    assert errors.error_payload(418, "teapot")["error"]["code"] == "error"   # 미매핑 4xx
