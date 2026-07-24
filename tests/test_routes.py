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
    assert d["workflow_mode"] == "serial"                   # PR-1: 실행 구조 태깅(기본 직렬)
    assert "wall_time_ms" in d["usage"] and "llm_latency_sum_ms" in d["usage"]  # PR-1: 지연 지표 분리
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


def test_revise_reverifies_final_draft(client):
    """외부 리뷰 P0-1: /revise 는 수정본을 다시 검증(polish→재평가→verify→품질판정)해,

    옛 문서의 verification_result·run_status 가 수정본과 함께 남지 않게 한다.
    """
    run = client.post("/run", json={"project_name": "재검증", "problem": "P"}).json()
    pid = run["project_id"]
    rev = client.post("/revise", json={
        "project_name": "재검증", "draft": run["final_draft"],
        "revision_request": "더 구체적으로", "project_id": pid,
    }).json()
    assert rev["verification_result"]                       # 수정본에 대해 검증이 다시 수행됨
    assert "run_status" in rev                               # 실행 품질도 재판정
    # 이력에도 수정본 기준 검증 결과가 저장됨
    saved = client.get(f"/projects/{pid}").json()["state"]
    assert saved.get("verification_result")


def test_revise_without_project_id_saves_new(client):
    rev = client.post("/revise", json={
        "project_name": "새기획", "draft": "# 새기획 기획서\n내용",
        "revision_request": "보강",
    }).json()
    assert rev["project_id"] > 0                            # id 없으면 신규 저장
    assert rev["revision_count"] == 1


def test_run_and_history_include_verification_summary(client):
    """PR-D: /run 응답과 이력 조회 모두에 검증 범위·한계 문구가 포함된다."""
    from app.services import reliability
    d = client.post("/run", json={"project_name": "신뢰", "problem": "P"}).json()
    assert d["verification_summary"]["scope"] == "search_snippet_only"
    assert d["verification_summary"]["note"] == reliability.DISCLAIMER_TEXT
    state = client.get(f"/projects/{d['project_id']}").json()["state"]
    assert state["verification_summary"]["note"] == reliability.DISCLAIMER_TEXT


def test_legacy_project_without_summary_gets_default(client):
    """과거 레코드(verification_summary 없음)를 조회해도 안전 기본값이 채워진다."""
    from app.services import reliability, store
    pid = store.save_run({"structured_input": {"project_name": "옛프로젝트"},
                          "final_draft": "# 옛 기획서\n내용", "model": "gpt-4o-mini"})
    state = client.get(f"/projects/{pid}").json()["state"]
    assert state["verification_summary"]["scope"] == "search_snippet_only"
    assert state["verification_summary"]["note"] == reliability.DISCLAIMER_TEXT


def test_suggest_requires_project_name(client):
    r = client.post("/suggest", json={"project_name": ""})
    assert r.status_code == 400                             # 프로젝트명 필수


def test_suggest_returns_fields_in_dummy_mode(client):
    d = client.post("/suggest", json={"project_name": "AI 반려식물 케어"}).json()
    assert {"description", "target_user", "problem", "keywords"} <= set(d)
    assert isinstance(d["keywords"], list)
    assert d["description"]                                 # 더미라도 초안은 채워짐
    assert "meta" in d                                      # 추천 이유·확신도 메타 포함(§5)


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
    assert types[0] == "start"                            # SSE 계약: 첫 이벤트는 start
    assert "preprocess" in nodes and "verify" in nodes   # 실제 노드 순차 완료 이벤트
    assert types[-1] == "done"                            # 마지막은 done
    assert result and result["project_id"] > 0            # 결과 포함 + 이력 저장
    assert result["final_draft"]


def test_stream_start_event_carries_mode(client):
    import json
    first = None
    with client.stream("POST", "/run/stream",
                       json={"project_name": "시작", "problem": "P"}) as r:
        for line in r.iter_lines():
            if line and line.startswith("data: "):
                first = json.loads(line[6:])
                break
    assert first["type"] == "start"
    assert first["workflow_mode"] in ("serial", "parallel")


def test_stream_error_event_is_unified_and_hides_internals(client, monkeypatch):
    """스트림 도중 예외 → 통일 error 이벤트(HTTP 오류 봉투와 동일 구조), 내부 상세 미노출."""
    import json

    def boom_stream(*a, **k):
        yield {"type": "start", "workflow_mode": "serial"}
        raise RuntimeError("내부 비밀 스택")
    monkeypatch.setattr("app.api.routes.run_workflow_stream", boom_stream)

    events = []
    with client.stream("POST", "/run/stream",
                       json={"project_name": "터짐", "problem": "P"}) as r:
        assert r.status_code == 200                        # 스트림 자체는 정상 종료
        for line in r.iter_lines():
            if line and line.startswith("data: "):
                events.append(json.loads(line[6:]))
    last = events[-1]
    assert last["type"] == "error"
    assert last["error"]["code"] == "internal_error" and last["error"]["status"] == 500
    assert last["message"]                                 # UI 하위호환(ev.message)
    assert "내부 비밀" not in json.dumps(last, ensure_ascii=False)  # 내부 상세 미노출


def test_fallback_reasons_surface_to_api(client, monkeypatch):
    """호출 실패(혼잡)가 발생하면 노드별 원인이 응답에 표면화된다(‘[더미]’ 대신 정직한 안내용)."""
    from app.services import llm

    def boom(*a, **k):
        raise llm.LLMError("busy", reason="혼잡")

    monkeypatch.setattr(llm, "is_dummy", lambda: False)   # 실제 모드로 간주
    monkeypatch.setattr(llm, "_get_model", lambda model="": object())  # 환경(키/provider) 비의존
    monkeypatch.setattr(llm, "_invoke_with_retry", boom)  # 모든 LLM 호출이 혼잡으로 실패
    d = client.post("/run", json={"project_name": "혼잡", "problem": "P"}).json()
    assert d["run_status"] == "degraded"                  # 실패가 아니라 fallback로 흡수
    assert d["fallback_reasons"]                           # 노드별 원인 맵 존재
    assert "혼잡" in d["fallback_reasons"].values()        # 분류된 원인이 전달됨


def test_admin_page_served(client):
    """관리자·데모 도구 페이지가 /admin 으로 분리 제공된다(메인 UI에서 분리)."""
    r = client.get("/admin")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "관리자" in r.text and "demo_fail_nodes" in r.text


def test_demo_fail_injection_via_payload(client, monkeypatch):
    """관리자 페이지 데모 토글: demo_fail_nodes로 지정한 노드만 실패해 fallback_reasons에 표면화."""
    from app.services import llm

    class FakeResp:
        content = "{}"
        usage_metadata = {}

    monkeypatch.setattr(llm, "is_dummy", lambda: False)          # 실제 모드로 간주
    monkeypatch.setattr(llm, "_get_model", lambda model="": object())  # 환경(키/provider) 비의존
    monkeypatch.setattr(llm, "_invoke_with_retry", lambda *a, **k: FakeResp())  # 비대상 노드는 정상
    d = client.post("/run", json={
        "project_name": "데모", "problem": "P",
        "demo_fail_nodes": ["customer", "risk"], "demo_fail_reason": "형식",
    }).json()
    assert d["fallback_reasons"].get("customer") == "형식"       # 지정 노드만 실패
    assert d["fallback_reasons"].get("risk") == "형식"
    assert "pestel" not in d["fallback_reasons"]                 # 미지정 노드는 정상
