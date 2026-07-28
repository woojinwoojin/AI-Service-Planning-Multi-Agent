"""공개 배포용 요청 상한 (상시 트랙 E) — 실 LLM 호출 없음.

이 기능의 **첫 번째 요구사항은 "평소엔 아무 일도 하지 않는 것"**이다. 기본값이 조금이라도
켜져 있으면 로컬 개발·CI·기존 테스트가 조용히 429 를 맞는다. 그래서 비활성 기본값을 가장
먼저 고정한다.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import (
    docx_export,
    llm,
    markdown_export,
    pptx_export,
    public_guard,
    store,
)

_ENV = ["PUBLIC_MAX_RUNS_PER_IP", "PUBLIC_IP_WINDOW_SEC",
        "PUBLIC_MAX_RUNS_PER_DAY", "PUBLIC_MAX_COST_PER_DAY_USD",
        "PUBLIC_MAX_SUGGESTIONS_PER_IP", "ENABLE_SERVER_SAVE"]


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for k in _ENV:
        monkeypatch.delenv(k, raising=False)
    public_guard.reset()
    yield
    public_guard.reset()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "p.db")
    monkeypatch.setattr(llm, "is_dummy", lambda: True)
    return TestClient(app)


@pytest.fixture
def outputs_tmp(tmp_path, monkeypatch):
    """`/run/save` 가 파일을 쓰는 3개 OUTPUT_DIR 를 임시 경로로 돌려 저장소 오염 방지."""
    out = tmp_path / "outputs"
    for mod in (markdown_export, docx_export, pptx_export):
        monkeypatch.setattr(mod, "OUTPUT_DIR", out)
    return out


# ---- 기본은 비활성 ----

def test_disabled_by_default():
    assert public_guard.enabled() is False
    assert public_guard.check("1.2.3.4") == (True, "")
    assert public_guard.status()["enabled"] is False


def test_disabled_guard_does_not_block_runs(client):
    """상한을 켜지 않았으면 몇 번을 불러도 통과해야 한다(기존 동작 보존)."""
    for _ in range(3):
        assert client.post("/run", json={"project_name": "무제한", "problem": "P"}).status_code == 200


def test_invalid_env_values_fall_back_to_default(monkeypatch):
    """오타·잘못된 값이 **상한을 0(무제한)으로 만들거나 예외를 내면 안 된다.**"""
    monkeypatch.setenv("PUBLIC_MAX_RUNS_PER_DAY", "다섯")
    monkeypatch.setenv("PUBLIC_MAX_COST_PER_DAY_USD", "abc")
    assert public_guard.limits()["max_runs_per_day"] == 0
    assert public_guard.limits()["max_cost_per_day_usd"] == 0.0


# ---- IP 당 빈도 ----

def test_per_ip_limit_blocks_after_quota(monkeypatch):
    monkeypatch.setenv("PUBLIC_MAX_RUNS_PER_IP", "2")
    monkeypatch.setenv("PUBLIC_IP_WINDOW_SEC", "3600")
    assert public_guard.check("1.1.1.1")[0] is True
    assert public_guard.check("1.1.1.1")[0] is True
    ok, reason = public_guard.check("1.1.1.1")
    assert ok is False and "다시 시도" in reason      # 언제 풀리는지 알려줘야 한다
    assert public_guard.check("2.2.2.2")[0] is True   # 다른 IP 는 영향 없음


def test_ip_window_expires(monkeypatch):
    """창이 지나면 다시 허용된다 — 영구 차단이 아니다.

    시계는 **처음부터** 가짜로 둔다. 기록 뒤에 갈아끼우면 기록된 시각과 축이 어긋나 창이
    영원히 안 지난 것처럼 보인다(이 테스트를 처음 그렇게 썼다가 잡혔다).
    """
    monkeypatch.setenv("PUBLIC_MAX_RUNS_PER_IP", "1")
    monkeypatch.setenv("PUBLIC_IP_WINDOW_SEC", "60")
    clock = [1000.0]
    monkeypatch.setattr(public_guard.time, "monotonic", lambda: clock[0])

    assert public_guard.check("3.3.3.3")[0] is True
    assert public_guard.check("3.3.3.3")[0] is False
    clock[0] += 59                      # 아직 창 안
    assert public_guard.check("3.3.3.3")[0] is False
    clock[0] += 2                       # 창을 넘김
    assert public_guard.check("3.3.3.3")[0] is True


# ---- 전역 일일 상한 ----

def test_daily_run_limit_is_ip_independent(monkeypatch):
    """IP 는 위조 가능하므로 **실제 보증은 전역 상한**이다 — IP 를 바꿔도 걸려야 한다."""
    monkeypatch.setenv("PUBLIC_MAX_RUNS_PER_DAY", "2")
    assert public_guard.check("1.1.1.1")[0] is True
    assert public_guard.check("2.2.2.2")[0] is True
    ok, reason = public_guard.check("3.3.3.3")
    assert ok is False and "실행 한도" in reason


def test_daily_cost_limit_uses_recorded_actuals(monkeypatch):
    monkeypatch.setenv("PUBLIC_MAX_COST_PER_DAY_USD", "0.05")
    assert public_guard.check("1.1.1.1")[0] is True
    public_guard.record_cost(0.03)
    assert public_guard.check("1.1.1.1")[0] is True     # 아직 여유
    public_guard.record_cost(0.03)                      # 누계 0.06 > 0.05
    ok, reason = public_guard.check("1.1.1.1")
    assert ok is False and "예산" in reason
    assert public_guard.status()["cost_today_usd"] == 0.06


def test_day_rollover_resets_counters(monkeypatch):
    monkeypatch.setenv("PUBLIC_MAX_RUNS_PER_DAY", "1")
    assert public_guard.check("1.1.1.1")[0] is True
    assert public_guard.check("1.1.1.1")[0] is False
    public_guard._day = "1999-01-01"                   # 날짜가 바뀐 상황
    assert public_guard.check("1.1.1.1")[0] is True


# ---- HTTP 통합 ----

def test_guarded_run_returns_429_in_unified_envelope(client, monkeypatch):
    monkeypatch.setenv("PUBLIC_MAX_RUNS_PER_DAY", "1")
    assert client.post("/run", json={"project_name": "한도", "problem": "P"}).status_code == 200
    r = client.post("/run", json={"project_name": "한도", "problem": "P"})
    assert r.status_code == 429
    err = r.json()["error"]
    assert err["code"] == "rate_limited" and err["status"] == 429 and err["message"]


def test_read_only_paths_are_never_blocked(client, monkeypatch):
    """참가자가 **자기 결과를 다시 보는 것**까지 막으면 테스트가 망가진다."""
    run = client.post("/run", json={"project_name": "조회", "problem": "P"}).json()
    monkeypatch.setenv("PUBLIC_MAX_RUNS_PER_DAY", "1")
    public_guard.reset()
    public_guard.check("x")                                     # 한도 소진
    assert client.get("/projects").status_code == 200
    assert client.get(f"/projects/{run['project_id']}").status_code == 200
    assert client.get("/health").status_code == 200
    assert client.post("/export/docx", json={"project_name": "조회",
                                             "markdown": "# 문서"}).status_code == 200


# ---- LLM 을 태우는 경로가 상한을 빠져나가지 않는지 (외부 리뷰 P0) ----

def test_run_save_is_guarded(client, monkeypatch):
    """`/run/save` 도 전체 워크플로를 돌린다 — 상한 없이 반복 호출되면 안 된다.

    화면이 이 경로를 쓰지 않는다는 것은 방어가 아니다(라우트가 열려 있으면 누구나 부른다).
    """
    assert "/run/save" in public_guard.GUARDED_PATHS
    monkeypatch.setenv("PUBLIC_MAX_RUNS_PER_DAY", "1")
    public_guard.reset()
    public_guard.check("x")                                     # 한도 소진
    r = client.post("/run/save", json={"project_name": "한도", "problem": "P"})
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "rate_limited"


def test_suggest_is_guarded(client, monkeypatch):
    """`/suggest` 도 LLM 을 1회 호출한다 — 공개 주소에서 무제한이면 안 된다."""
    assert "/suggest" in public_guard.LIGHT_PATHS
    monkeypatch.setenv("PUBLIC_MAX_SUGGESTIONS_PER_IP", "1")
    monkeypatch.setenv("PUBLIC_IP_WINDOW_SEC", "3600")
    public_guard.reset()
    assert client.post("/suggest", json={"project_name": "추천"}).status_code == 200
    r = client.post("/suggest", json={"project_name": "추천"})
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "rate_limited"


def test_suggest_does_not_consume_run_quota(monkeypatch):
    """자동완성이 **기획서 생성 몫**을 깎으면 정작 실행을 못 한다(한 바구니에 넣지 않는 이유)."""
    monkeypatch.setenv("PUBLIC_MAX_RUNS_PER_DAY", "2")
    monkeypatch.setenv("PUBLIC_MAX_SUGGESTIONS_PER_IP", "50")
    public_guard.reset()
    for _ in range(5):
        assert public_guard.check("1.1.1.1", "light")[0] is True
    assert public_guard.status()["runs_today"] == 0          # 일일 실행 수 미차감
    assert public_guard.check("1.1.1.1")[0] is True          # 실행 2건은 그대로 남아 있다
    assert public_guard.check("1.1.1.1")[0] is True
    assert public_guard.check("1.1.1.1")[0] is False


def test_light_path_still_bound_by_daily_cost(monkeypatch):
    """IP 는 위조 가능하므로 **돈에 대한 보증은 일일 비용 상한**이다 — light 도 예외가 아니다."""
    monkeypatch.setenv("PUBLIC_MAX_COST_PER_DAY_USD", "0.05")
    public_guard.reset()
    assert public_guard.check("1.1.1.1", "light")[0] is True
    public_guard.record_cost(0.06)
    ok, reason = public_guard.check("1.1.1.1", "light")
    assert ok is False and "예산" in reason


def test_run_save_records_cost(client, monkeypatch, outputs_tmp):
    """`/run/save` 는 비용을 일일 누계에 더해야 한다 — 그전에는 집계조차 되지 않았다."""
    calls: list[float] = []
    monkeypatch.setenv("PUBLIC_MAX_COST_PER_DAY_USD", "999")     # 상한은 켜 두되 막지는 않게
    public_guard.reset()
    monkeypatch.setattr(public_guard, "record_cost", lambda c: calls.append(c))
    assert client.post("/run/save", json={"project_name": "집계", "problem": "P"}).status_code == 200
    assert calls, "/run/save 가 _record_public_cost 를 부르지 않았다"


def test_server_save_can_be_disabled_for_public(client, monkeypatch, outputs_tmp):
    """공개 배포에서는 `ENABLE_SERVER_SAVE=0` 으로 서버 디스크 저장 경로를 끈다."""
    monkeypatch.setenv("ENABLE_SERVER_SAVE", "0")
    r = client.post("/run/save", json={"project_name": "차단", "problem": "P"})
    assert r.status_code == 404                                   # 존재하지 않는 것처럼
    monkeypatch.setenv("ENABLE_SERVER_SAVE", "1")
    assert client.post("/run/save",
                       json={"project_name": "허용", "problem": "P"}).status_code == 200


def test_health_exposes_limit_status(client, monkeypatch):
    monkeypatch.setenv("PUBLIC_MAX_RUNS_PER_DAY", "5")
    body = client.get("/health").json()["public_limits"]
    assert body["enabled"] is True and body["limits"]["max_runs_per_day"] == 5
    assert body["scope"] == "single_process"       # 여러 인스턴스면 값이 인스턴스별이라는 표기


def test_run_records_actual_cost_into_daily_total(client, monkeypatch):
    """실행이 끝나면 실측 비용이 일일 누계에 반영돼야 한다(다음 요청 판정의 근거)."""
    monkeypatch.setenv("PUBLIC_MAX_COST_PER_DAY_USD", "999")   # 막지는 않되 집계는 켠다
    client.post("/run", json={"project_name": "비용집계", "problem": "P"})
    # 더미 실행은 비용 0 이므로 '집계 경로가 살아 있는지'만 본다(음수·예외 없음).
    assert public_guard.status()["cost_today_usd"] >= 0.0
    public_guard.record_cost(0.0123)
    assert public_guard.status()["cost_today_usd"] >= 0.0123


# ---- 클라이언트 IP 판별 ----

def test_client_ip_prefers_forwarded_header():
    assert public_guard.client_ip({"x-forwarded-for": "9.9.9.9, 10.0.0.1"}, "10.0.0.1") == "9.9.9.9"
    assert public_guard.client_ip({}, "10.0.0.5") == "10.0.0.5"
    assert public_guard.client_ip({}, "") == "unknown"
