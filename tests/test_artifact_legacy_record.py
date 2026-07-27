"""옛 기록(Artifact 없이 저장된 v2 레코드)의 읽기 전환 검증 (로드맵 2-2 PR 5e).

PR 5a~5d 의 검증은 전부 **새로 실행한** State 기준이었다. 그런데 운영에서 `prefer_artifact`
를 켜면 그날부터 열리는 기록의 대부분은 **그 전에 저장된 것**이다. 그 기록에는 `artifacts`
키가 아예 없고, `migrate.upgrade_state` 가 평면 결과에서 파생해 채운다. 이 경로가 세 모드에서
어떻게 되는지는 지금까지 확인된 적이 없다.

검증 대상 체인: **옛 v2 기록 → migrate → 세 모드 → `/revise` → 재검증 → 저장 → 재조회**.

실 LLM·검색 호출 없음(더미).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas import artifact
from app.services import llm, sections, store

MODES = [artifact.READ_LEGACY, artifact.READ_PREFER_ARTIFACT, artifact.READ_ARTIFACT_ONLY]

DRAFT = "# 옛기록 기획서\n" + "\n".join(f"## {t}\n내용입니다." for t in sections.SECTION_TITLES)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "old.db")
    monkeypatch.setattr(llm, "is_dummy", lambda: True)
    return TestClient(app)


def old_v2_record(**over) -> dict:
    """`artifacts`·`artifact_parity` 가 없던 시절의 기록(state_version=2)."""
    st = {
        "user_input": {"project_name": "옛기록", "problem": "P"},
        "structured_input": {"project_name": "옛기록"},
        "research_result": {"market_overview": "옛 시장", "competitors": ["A사"]},
        "competitor_result": {"competitors": [{"name": "A사"}], "positioning": "p",
                              "differentiation": ["d"]},
        "customer_result": {"personas": [{"name": "김철수"}]},
        "pestel_result": {"political": ["p1"]},
        "swot_result": {"strengths": ["s"]},
        "business_model_result": {"revenue_streams": ["r"]},
        "risk_result": {"risks": [{"name": "위험"}]},
        "draft": DRAFT, "final_draft": DRAFT,
        "review_result": {"total_score": 80},
        "initial_review_result": {"total_score": 80},
        "final_review_result": {"total_score": 80},
        "verification_result": {"claims": []},
        "revision_count": 0,
        "state_version": 2,
    }
    st.update(over)
    return st


def _revise(client, pid: int | None = None):
    body = {"project_name": "옛기록", "draft": DRAFT, "revision_request": "톤을 정리해줘"}
    if pid is not None:
        body["project_id"] = pid
    return client.post("/revise", json=body)


def _state(client, pid: int) -> dict:
    return client.get(f"/projects/{pid}").json()["state"]


# ---- migrate 가 옛 기록에 Artifact 를 채우는가 ----

def test_old_record_gets_artifacts_on_read(client):
    """저장 당시엔 없던 `artifacts` 가 재조회 시 평면 결과에서 파생돼 7개 채워진다."""
    pid = store.save_run(old_v2_record())
    st = _state(client, pid)
    assert st["state_version"] == 3
    assert len(st["artifacts"]) == len(artifact.LEGACY_ARTIFACT_SPECS)
    assert {a["status"] for a in st["artifacts"]} == {artifact.STATUS_COMPLETE}
    assert st["artifact_parity"]["ok"]
    # 파생본임이 기록에 남아야 이행 진행도를 볼 수 있다.
    assert {(a["metadata"] or {}).get("source") for a in st["artifacts"]} == \
           {artifact.SOURCE_LEGACY}


# ---- 옛 기록을 세 모드에서 수정할 수 있는가 ----

@pytest.mark.parametrize("mode", MODES)
def test_old_record_revise_completes_in_every_mode(client, monkeypatch, mode):
    """옛 기록 → 수정 → 재검증 → 저장 → 재조회가 세 모드 모두에서 완주한다."""
    monkeypatch.setenv(artifact.READ_MODE_ENV, mode)
    pid = store.save_run(old_v2_record())

    r = _revise(client, pid)
    assert r.status_code == 200, mode
    body = r.json()
    assert body["project_id"] == pid              # 같은 레코드 갱신(이력 쪼개짐 없음)
    assert body["final_draft"] and not body["failed_nodes"], mode

    after = _state(client, pid)                   # 저장 → 재조회까지
    assert after["state_version"] == 3
    assert after["artifact_parity"]["ok"], mode
    assert len(after["artifacts"]) == len(artifact.LEGACY_ARTIFACT_SPECS)


def test_old_record_revise_reads_through_artifacts(client, monkeypatch):
    """`artifact_only` 에서 옛 기록 수정이 **평면 키 없이** Artifact 만으로 돌아간다.

    파생 Artifact 가 원본 평면 키를 제대로 대신하는지가 이 모드의 질문이다.
    """
    monkeypatch.setenv(artifact.READ_MODE_ENV, artifact.READ_ARTIFACT_ONLY)
    pid = store.save_run(old_v2_record())
    assert _revise(client, pid).status_code == 200

    rt = _state(client, pid)["artifact_read"]["runtime"]
    assert rt["measured"] and rt["total"] > 0
    assert rt["from_artifact"] == rt["total"] and rt["fallbacks"] == 0


def test_revise_reads_only_research_and_competitor(client, monkeypatch):
    """**`/revise` 는 7개 중 2개만 읽는다** — 이 사실을 고정해 둔다.

    아래 '결손 옛 기록' 테스트들이 통과하는 이유가 '결손 Artifact 를 잘 견뎌서'가 아니라
    **애초에 그 Artifact 를 읽지 않아서**이기 때문이다. 읽는 범위가 넓어지면(예: PR 6 의
    선택적 재실행) 그 테스트들의 의미가 달라지므로 여기서 함께 깨지게 한다.
    """
    monkeypatch.setenv(artifact.READ_MODE_ENV, artifact.READ_PREFER_ARTIFACT)
    pid = store.save_run(old_v2_record())
    _revise(client, pid)

    rt = _state(client, pid)["artifact_read"]["runtime"]
    assert {t["artifact_type"] for t in rt["by_type"]} == {"research_analysis",
                                                           "competitor_analysis"}


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(("label", "over"), [
    ("결과 키 결손", {"pestel_result": {}}),          # 초기 버전·중간 실패로 일부만 저장된 기록
    ("failed 노드 기록", {"failed_nodes": ["swot"]}),  # 파생 Artifact status=failed 가 됨
])
def test_degraded_old_record_still_revisable(client, monkeypatch, mode, label, over):
    """결손·실패가 기록된 옛 기록도 수정할 수 있어야 한다(이력이 열리지 않으면 안 된다).

    ⚠️ 단, 이는 **`/revise` 가 그 Artifact 를 읽지 않기 때문**이기도 하다
    (`test_revise_reads_only_research_and_competitor` 참고). "결손 Artifact 를 안전하게
    소비한다"는 진술이 아니다.
    """
    monkeypatch.setenv(artifact.READ_MODE_ENV, mode)
    pid = store.save_run(old_v2_record(**over))
    r = _revise(client, pid)
    assert r.status_code == 200, (label, mode)
    assert r.json()["final_draft"], (label, mode)


# ---- base 없는 /revise — 세 모드가 갈리는 유일한 지점 ----

def test_revise_without_base_degrades_gracefully_except_artifact_only(client, monkeypatch):
    """`project_id` 없는 수정은 **State 에 Artifact 도 평면 결과도 없다.**

    - `legacy`·`prefer_artifact`: 폴백해서 **같은 문서로 완주**한다.
    - `artifact_only`: 폴백이 없으므로 `revise`·`verify` 가 실패하고 최종본이 비어 나온다.
      의도된 동작이지만(검증 전용 모드), **`artifact_only` 를 운영에 켜면 안 되는 구체적
      근거**이므로 고정해 둔다. HTTP 는 200 인 채 내용만 비므로 더 눈에 안 띈다.
    """
    outs = {}
    for mode in MODES:
        monkeypatch.setenv(artifact.READ_MODE_ENV, mode)
        r = _revise(client)
        assert r.status_code == 200, mode
        outs[mode] = r.json()

    ok = [outs[artifact.READ_LEGACY], outs[artifact.READ_PREFER_ARTIFACT]]
    for b in ok:
        assert b["final_draft"] and b["run_status"] == "degraded"
        assert not b["failed_nodes"]
    # 폴백이 산출물을 바꾸지 않는다 — prefer_artifact 는 legacy 와 같은 문서를 낸다.
    assert ok[1]["final_draft"] == ok[0]["final_draft"]

    only = outs[artifact.READ_ARTIFACT_ONLY]
    assert only["run_status"] == "failed"
    assert set(only["failed_nodes"]) == {"revise", "verify"}
    assert not only["final_draft"]


def test_shadow_prediction_holds_on_a_real_request(client, monkeypatch):
    """PR 5d 의 shadow 지표가 **실제 요청 경로**에서도 실제 폴백과 일치하는가.

    5d 는 단위 수준(손으로 만든 State)에서만 이 성질을 고정했다. 여기서는 폴백이 실제로
    발생하는 경로(base 없는 `/revise`)로 확인한다 — 지금까지 관통 실행의 폴백은 늘 0 이라
    이 지표가 0 이 아닌 값을 옳게 세는지 볼 기회가 없었다.
    """
    monkeypatch.setenv(artifact.READ_MODE_ENV, artifact.READ_LEGACY)
    shadow = _state(client, _revise(client).json()["project_id"])["artifact_read"]["runtime"]

    monkeypatch.setenv(artifact.READ_MODE_ENV, artifact.READ_PREFER_ARTIFACT)
    actual = _state(client, _revise(client).json()["project_id"])["artifact_read"]["runtime"]

    assert shadow["shadow_fallbacks"] > 0                      # 공허하지 않은 측정
    assert shadow["shadow_fallbacks"] == actual["fallbacks"]
    assert shadow["shadow_reasons"] == actual["fallback_reasons"] == {"missing": 3}


# ---- 저장 계층: 없는 키를 None 으로 굳히지 않는다 ----

def test_absent_keys_are_not_stored_as_none(client):
    """`_RUN_KEYS` 중 State 에 **없던** 키는 저장하지 않는다(None 으로 채우지 않는다).

    None 으로 저장하면 재조회 쪽의 `state.get("user_input", {})` 가 기본값이 아니라 None 을
    받아 `{**None}` → 500 이 된다. 정상 실행 기록에서는 재현되지 않지만, 일부 키만 있는
    기록(외부 도구·수기 이관·과거 실행)이 들어오면 이력이 열리지 않는다.
    """
    pid = store.save_run({"structured_input": {"project_name": "부분"}, "draft": DRAFT,
                          "final_draft": DRAFT, "revision_fallback_reason": None})
    st = _state(client, pid)
    # 없던 키는 **키 자체가 없거나** 기본값이어야 한다 — None 으로 굳어 있으면 안 된다.
    for key in ("user_input", "review_result", "initial_review_result", "revision_count"):
        assert st.get(key) is not None or key not in st, key
    # 명시적으로 None 인 값은 그대로 보존된다(없는 키와 구분).
    assert "revision_fallback_reason" in st and st["revision_fallback_reason"] is None


def test_partial_record_is_revisable(client):
    """일부 키만 있는 기록도 이력에서 수정할 수 있다(위 저장 규칙의 실제 효과)."""
    pid = store.save_run({"structured_input": {"project_name": "부분"},
                          "draft": DRAFT, "final_draft": DRAFT})
    r = _revise(client, pid)
    assert r.status_code == 200
    assert r.json()["final_draft"]
