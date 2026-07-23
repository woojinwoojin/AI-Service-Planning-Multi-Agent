"""Evidence Registry — 실제 검색 근거를 단일 레지스트리로 통합 (로드맵 v2 2-1).

지금까지 근거(실제 웹검색 출처)는 두 곳에 분산돼 있었다:
  - `research_result["source_objects"]`  (Research Agent)
  - `competitor_sources`                 (Competitor Agent, State 최상위)
둘 다 `search.build_source_objects()` 가 만든 같은 형식이지만 **어느 Agent가 어떤 쿼리로
확보했는지**는 남지 않았다. 이 모듈은 그것들을 하나의 목록으로 합쳐, 이후 단계
(2-3 Reviewer Issue 구조화·Phase 3 Tier 2 주장-근거 연결)가 `evidence_id` 로 특정 근거를
지목할 수 있게 하는 **기반 자료구조**다.

레지스트리 항목 스키마:
    {
      "evidence_id": "ev1",              # 실행 내 안정 id(URL 최초 등장 순서)
      "source_agents": ["research"],      # 이 근거를 확보한 Agent(들)
      "queries": ["... 시장 동향 ..."],    # 사용한 검색 쿼리(들)
      "url": "...", "title": "...", "snippet": "...",
      "source_type": "news",              # search.build_source_objects 메타(권위성 힌트)
      "content_scope": "search_snippet",  # 원문 아님(검색 요약문)
      "original_text_extracted": False,   # URL 원문 추출·재확인 안 함
      "used_by_claims": [],               # 이 근거를 인용하는 주장 id(2-3/Tier2에서 채움)
    }

설계 메모:
- `evidence_id` 는 **URL 최초 등장 순서**로 결정론적으로 매긴다(랜덤·시간 미사용 → 테스트 재현).
- 같은 URL 을 여러 Agent 가 찾으면 **하나의 항목으로 합치고** `source_agents`·`queries` 를 병합한다.
- 로드맵 스케치의 `source_agent`/`query`(단수) 대신 **리스트**로 둔다 — 한 URL 이 여러 Agent·
  쿼리에서 나올 수 있으므로 근거를 잃지 않기 위함.
- 실제 검색 출처만 담는다(LLM 이 지어낸 sources 문자열은 애초에 여기 오지 않는다).
"""
from __future__ import annotations

# build_source_objects 가 붙이는 메타 필드(그대로 레지스트리 항목에 실어 나른다).
_META_KEYS = ("title", "snippet", "source_type", "content_scope", "original_text_extracted")


def entries_from(source_agent: str, query: str, source_objects: list) -> list[dict]:
    """한 Agent 의 검색 출처(build_source_objects 결과)를 레지스트리 '원시 항목'으로 변환한다.

    아직 evidence_id 는 매기지 않는다(전역 중복 제거 후 normalize() 가 부여). 각 Agent 는
    자기 원시 항목만 반환하고, State reducer(operator.add)가 이를 누적한다.
    """
    agent = (source_agent or "").strip()
    q = (query or "").strip()
    out: list[dict] = []
    for o in source_objects or []:
        if not isinstance(o, dict):
            continue
        url = (o.get("url") or "").strip()
        if not url:
            continue
        item: dict = {"url": url}
        for k in _META_KEYS:
            if k in o:
                item[k] = o[k]
        item["source_agents"] = [agent] if agent else []
        item["queries"] = [q] if q else []
        item["used_by_claims"] = list(o.get("used_by_claims") or [])
        out.append(item)
    return out


def _merge_unique(base: list, extra: list) -> list:
    """순서를 보존하며 중복 없이 병합한다(문자열 리스트용)."""
    out = list(base)
    for x in extra:
        if x and x not in out:
            out.append(x)
    return out


def normalize(raw_entries: list) -> list[dict]:
    """누적된 원시 항목을 단일 레지스트리로 정규화한다.

    - URL 기준 중복 제거(최초 등장 항목을 대표로).
    - 같은 URL 의 `source_agents`·`queries`·`used_by_claims` 는 병합(근거 유실 방지).
    - `evidence_id` 를 최초 등장 순서로 매긴다(ev1, ev2, …) — 결정론적·재현 가능.

    입력이 이미 정규화된(evidence_id 가 있는) 목록이어도 안전하게 재정규화한다
    (id 를 다시 순서대로 부여하므로 finalize 를 여러 번 거쳐도 안정적).
    """
    by_url: dict[str, dict] = {}
    order: list[str] = []
    for e in raw_entries or []:
        if not isinstance(e, dict):
            continue
        url = (e.get("url") or "").strip()
        if not url:
            continue
        if url not in by_url:
            item = {"url": url}
            for k in _META_KEYS:
                if k in e:
                    item[k] = e[k]
            item["source_agents"] = list(e.get("source_agents") or [])
            item["queries"] = list(e.get("queries") or [])
            item["used_by_claims"] = list(e.get("used_by_claims") or [])
            by_url[url] = item
            order.append(url)
        else:
            item = by_url[url]
            item["source_agents"] = _merge_unique(item["source_agents"], e.get("source_agents") or [])
            item["queries"] = _merge_unique(item["queries"], e.get("queries") or [])
            item["used_by_claims"] = _merge_unique(item["used_by_claims"], e.get("used_by_claims") or [])
            # 대표 항목에 메타가 비어 있으면 뒤 항목 값으로 보완(첫 항목 우선, 빈 값만 채움).
            for k in _META_KEYS:
                if not item.get(k) and e.get(k):
                    item[k] = e[k]

    registry: list[dict] = []
    for i, url in enumerate(order, 1):
        item = by_url[url]
        item["evidence_id"] = f"ev{i}"
        registry.append(item)
    return registry
