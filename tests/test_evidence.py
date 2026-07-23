"""Evidence Registry(로드맵 2-1) 단위 테스트 — LLM 호출 없음, 결정론적."""
from __future__ import annotations

from app.services import evidence


def _obj(url: str, title: str = "", stype: str = "news") -> dict:
    """search.build_source_objects 형식의 출처 객체."""
    return {
        "title": title,
        "url": url,
        "snippet": f"{title} 요약",
        "content_scope": "search_snippet",
        "original_text_extracted": False,
        "source_type": stype,
    }


def test_entries_from_wraps_agent_and_query():
    objs = [_obj("https://a.com", "A", "government")]
    out = evidence.entries_from("research", "시장 동향", objs)
    assert len(out) == 1
    e = out[0]
    assert e["url"] == "https://a.com"
    assert e["source_agents"] == ["research"]
    assert e["queries"] == ["시장 동향"]
    assert e["used_by_claims"] == []
    # build_source_objects 메타는 그대로 실려 나른다
    assert e["source_type"] == "government"
    assert e["content_scope"] == "search_snippet"
    assert e["original_text_extracted"] is False
    # evidence_id 는 아직 없음(normalize 가 부여)
    assert "evidence_id" not in e


def test_entries_from_skips_urlless_and_nondict():
    objs = [_obj("https://a.com"), {"title": "no url"}, "쓰레기", {"url": "  "}]
    out = evidence.entries_from("research", "q", objs)
    assert [e["url"] for e in out] == ["https://a.com"]


def test_normalize_assigns_stable_ids_in_first_seen_order():
    raw = (
        evidence.entries_from("research", "q1", [_obj("https://a.com", "A"), _obj("https://b.com", "B")])
        + evidence.entries_from("competitor", "q2", [_obj("https://c.com", "C")])
    )
    reg = evidence.normalize(raw)
    assert [e["evidence_id"] for e in reg] == ["ev1", "ev2", "ev3"]
    assert [e["url"] for e in reg] == ["https://a.com", "https://b.com", "https://c.com"]


def test_normalize_is_deterministic():
    raw = evidence.entries_from("research", "q", [_obj("https://a.com"), _obj("https://b.com")])
    assert evidence.normalize(raw) == evidence.normalize(list(raw))


def test_normalize_merges_same_url_across_agents():
    raw = (
        evidence.entries_from("research", "시장", [_obj("https://shared.com", "Shared")])
        + evidence.entries_from("competitor", "경쟁", [_obj("https://shared.com", "Shared")])
    )
    reg = evidence.normalize(raw)
    assert len(reg) == 1
    e = reg[0]
    assert e["evidence_id"] == "ev1"
    assert e["source_agents"] == ["research", "competitor"]
    assert e["queries"] == ["시장", "경쟁"]


def test_normalize_fills_empty_meta_from_later_entry():
    # 첫 항목의 title 이 비어 있으면(빈 문자열) 뒤 항목 값으로 보완한다.
    first = {"url": "https://a.com", "title": "", "source_type": "news",
             "source_agents": ["research"], "queries": ["q"], "used_by_claims": []}
    second = {"url": "https://a.com", "title": "제목", "source_type": "news",
              "source_agents": ["competitor"], "queries": ["q2"], "used_by_claims": []}
    reg = evidence.normalize([first, second])
    assert len(reg) == 1
    assert reg[0]["title"] == "제목"


def test_normalize_keeps_first_nonempty_meta():
    # 비어 있지 않은 메타는 첫 항목이 유지된다(같은 URL 이면 분류값도 같으므로 first-wins).
    first = {"url": "https://a.com", "title": "먼저", "source_type": "government",
             "source_agents": ["research"], "queries": ["q"], "used_by_claims": []}
    second = {"url": "https://a.com", "title": "나중", "source_type": "news",
              "source_agents": ["competitor"], "queries": ["q2"], "used_by_claims": []}
    reg = evidence.normalize([first, second])
    assert reg[0]["title"] == "먼저"
    assert reg[0]["source_type"] == "government"


def test_normalize_merges_used_by_claims():
    a = {"url": "https://a.com", "source_agents": ["research"], "queries": ["q"],
         "used_by_claims": ["c1"]}
    b = {"url": "https://a.com", "source_agents": ["research"], "queries": ["q"],
         "used_by_claims": ["c2", "c1"]}
    reg = evidence.normalize([a, b])
    assert reg[0]["used_by_claims"] == ["c1", "c2"]


def test_normalize_renormalizes_already_normalized_list():
    """finalize 를 여러 번 거쳐도(정규화된 목록 재입력) id 가 안정적으로 다시 부여된다."""
    raw = evidence.entries_from("research", "q", [_obj("https://a.com"), _obj("https://b.com")])
    once = evidence.normalize(raw)
    twice = evidence.normalize(once)
    assert twice == once


def test_normalize_empty_and_garbage():
    assert evidence.normalize([]) == []
    assert evidence.normalize(None) == []
    assert evidence.normalize(["x", 3, {"no": "url"}]) == []


# --- for_prompt / link_claims (2-1b: 주장-근거 연결) ---

def test_for_prompt_labels_each_evidence_with_id():
    reg = evidence.normalize(
        evidence.entries_from("research", "q", [_obj("https://a.com", "A", "news")])
    )
    text = evidence.for_prompt(reg)
    assert text.startswith("[ev1]")
    assert "(news)" in text and "A" in text


def test_link_claims_fills_used_by_claims():
    reg = evidence.normalize(
        evidence.entries_from("research", "q", [_obj("https://a.com"), _obj("https://b.com")])
    )
    claims = [
        {"id": "c1", "evidence_ids": ["ev1"]},
        {"id": "c2", "evidence_ids": ["ev1", "ev2"]},
    ]
    out = evidence.link_claims(reg, claims)
    by_id = {e["evidence_id"]: e for e in out}
    assert by_id["ev1"]["used_by_claims"] == ["c1", "c2"]
    assert by_id["ev2"]["used_by_claims"] == ["c2"]


def test_link_claims_ignores_unknown_id_and_is_idempotent():
    reg = evidence.normalize(evidence.entries_from("research", "q", [_obj("https://a.com")]))
    claims = [{"id": "c1", "evidence_ids": ["ev1", "ev99"]}]  # ev99 없음
    once = evidence.link_claims(reg, claims)
    assert once[0]["used_by_claims"] == ["c1"]               # ev99 무시
    twice = evidence.link_claims(once, claims)               # 재실행해도 중복 안 쌓임
    assert twice[0]["used_by_claims"] == ["c1"]


def test_link_claims_resets_when_no_claims():
    reg = evidence.normalize(evidence.entries_from("research", "q", [_obj("https://a.com")]))
    evidence.link_claims(reg, [{"id": "c1", "evidence_ids": ["ev1"]}])
    out = evidence.link_claims(reg, [])                      # 주장 없으면 비움
    assert out[0]["used_by_claims"] == []


# --- 통합: Agent 방출 → State reducer 누적 → finalize 정규화 ---

def _patch_search(monkeypatch, module, hits: list[dict]):
    """해당 Agent 모듈의 검색을 가짜 히트로 대체하고 LLM 은 fallback 로만 동작시킨다."""
    monkeypatch.setattr(module.llm, "is_dummy", lambda: False)
    monkeypatch.setattr(module.search, "web_search",
                        lambda *a, **k: [dict(h) for h in hits])
    # LLM 은 호출해도 fallback 을 그대로 반환(실 API 미사용) → 검색 출처 경로만 검증
    monkeypatch.setattr(module.llm, "complete_json",
                        lambda system, user, fallback, **k: fallback)
    monkeypatch.setattr(module.llm, "mode_label", lambda *a, **k: "fallback·검색")


def test_research_emits_evidence_registry(monkeypatch):
    from app.agents import research
    hits = [{"title": "리포트", "url": "https://gov.kr/r", "content": "시장 성장"}]
    _patch_search(monkeypatch, research, hits)
    out = research.research({"structured_input": {"project_name": "P", "keywords": ["AI"]}})
    reg = out["evidence_registry"]
    assert len(reg) == 1
    assert reg[0]["url"] == "https://gov.kr/r"
    assert reg[0]["source_agents"] == ["research"]
    assert reg[0]["queries"][0]  # 실제 쿼리가 실렸다
    assert "evidence_id" not in reg[0]  # 원시 항목(id 는 finalize 에서)


def test_full_run_builds_normalized_registry(monkeypatch):
    """전체 실행: research/competitor 근거가 하나의 레지스트리로 통합되고 id 가 매겨진다."""
    from app.services import llm, search
    from app.graph.workflow import run_workflow

    shared = {"title": "공용", "url": "https://shared.com", "content": "공용 근거"}
    # research/competitor 는 같은 search 모듈을 공유하므로 쿼리로 분기해 다른 히트를 준다.
    def _dispatch(query, *a, **k):
        if "경쟁" in query and "비교" in query:  # competitor 쿼리
            return [{"title": "C", "url": "https://c.com", "content": "경쟁"}, dict(shared)]
        return [{"title": "R", "url": "https://r.com", "content": "연구"}, dict(shared)]

    monkeypatch.setattr(llm, "is_dummy", lambda: False)
    monkeypatch.setattr(search, "web_search", _dispatch)
    monkeypatch.setattr(llm, "complete_json", lambda system, user, fallback, **k: fallback)
    monkeypatch.setattr(llm, "mode_label", lambda *a, **k: "fallback·검색")

    state = run_workflow({"project_name": "통합", "problem": "P"}, workflow_mode="serial")
    reg = state["evidence_registry"]
    urls = [e["url"] for e in reg]
    # 3개 고유 URL(공용은 1개로 병합), id 는 최초 등장 순서
    assert urls == ["https://r.com", "https://shared.com", "https://c.com"]
    assert [e["evidence_id"] for e in reg] == ["ev1", "ev2", "ev3"]
    shared_e = next(e for e in reg if e["url"] == "https://shared.com")
    assert shared_e["source_agents"] == ["research", "competitor"]  # 두 Agent 병합
    assert all(e["used_by_claims"] == [] for e in reg)              # 2-1 단계에선 빈 값
