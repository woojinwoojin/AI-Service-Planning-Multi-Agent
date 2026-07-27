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
# `retrieved_at`(조회 시점)·`published_date`(발행일, 검색이 줄 때만)도 그대로 실어 나른다 —
# 근거가 언제 조회된 것인지 없으면 몇 달 전 스냅샷을 최신 자료처럼 읽게 된다. 같은 URL 이
# 여러 번 나오면 `normalize` 가 최초 항목을 대표로 두므로 **처음 조회한 시점**이 남는다.
_META_KEYS = ("title", "snippet", "source_type", "content_scope", "original_text_extracted",
              "retrieved_at", "published_date")


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


# 정량 주장(시장 규모·정책·통계)의 근거로 **정부·학술 자료를 먼저 보게** 하는 순서다.
#
# ⚠️ **이건 '공식 출처를 우선 수집'하는 것이 아니다.** 수집은 일반 웹 검색이 하고, 여기서는
# 이미 모인 근거를 `classify_source_type` 의 도메인 규칙으로 분류해 **정렬만** 한다. 공식기관
# 도메인만 따로 검색하는 구조는 없다.
#
# ⚠️ `corporate` 는 `SOURCE_TYPE_LABELS` 에 라벨만 있고 분류기가 **반환하지 않는다**(기업 공식
# 사이트는 판단이 서지 않아 `unknown` 으로 남는다 — 과잉 분류를 피하는 의도적 선택). 순서에
# 남겨 둔 것은 나중에 분류가 늘어날 때를 위한 자리이고, 지금 실제로 나오는 값은
# government·academic·news·community·unknown 다섯 가지다.
_AUTHORITY_ORDER = ("government", "academic", "corporate", "news", "community", "unknown")


def authority_rank(source_type: str) -> int:
    """`_AUTHORITY_ORDER` 상의 순위(작을수록 공식). 모르는 유형은 맨 뒤."""
    try:
        return _AUTHORITY_ORDER.index((source_type or "unknown").strip())
    except ValueError:
        return len(_AUTHORITY_ORDER)


def for_prompt(registry: list) -> str:
    """정규화된 레지스트리를 verifier 프롬프트용 근거 목록 문자열로 만든다.

    각 줄에 evidence_id 를 앞세워, LLM 이 주장별로 '어느 근거가 뒷받침하는지'를
    evidence_id 로 지목(인용)할 수 있게 한다(2-3/Tier 2 의 주장-근거 연결 토대).

    **수집된 근거를 출처 유형으로 분류해 정부·학술 자료를 앞에 싣는다**(`authority_rank`).
    LLM 은 목록 앞쪽을 더 많이 인용하므로, 정부·학술 자료가 커뮤니티 글보다 앞에 오면 정량
    주장이 더 단단한 근거에 붙는다. **우선 수집이 아니라 우선 정렬이다** — 검색 자체는 공식기관
    도메인을 따로 겨냥하지 않는다.
    같은 유형 안에서는 **원래 순서(evidence_id 순)를 유지**해 판정이 결정적으로 남는다.

    조회 시점(`retrieved_at`)·발행일(`published_date`)을 각 줄에 함께 적는다 — 오래된 근거를
    최신 자료로 오인해 "현재 시장은…" 같은 주장을 붙이는 것을 막기 위함이다.
    """
    items = [e for e in (registry or []) if isinstance(e, dict)]
    ordered = sorted(enumerate(items),
                     key=lambda p: (authority_rank(p[1].get("source_type", "")), p[0]))
    lines: list[str] = []
    for _, e in ordered:
        eid = e.get("evidence_id", "")
        stype = e.get("source_type", "") or "unknown"
        title = (e.get("title") or "").strip()
        snippet = (e.get("snippet") or "").strip()
        when = " ".join(x for x in (
            f"발행 {e['published_date']}" if e.get("published_date") else "",
            f"조회 {str(e['retrieved_at'])[:10]}" if e.get("retrieved_at") else "",
        ) if x)
        head = f"[{eid}] ({stype}{'; ' + when if when else ''})"
        body = f"{title}: {snippet}" if title else snippet
        lines.append(f"{head} {body}".rstrip())
    return "\n".join(lines)


def search_basis_date(registry: list) -> str:
    """레지스트리에 기록된 **가장 늦은 조회 시점**의 날짜(YYYY-MM-DD). 없으면 빈 문자열.

    문서의 '검색 기준일'로 쓴다 — 근거가 어느 시점의 웹 스냅샷인지 독자가 알아야 한다.
    """
    stamps = [str(e.get("retrieved_at"))[:10] for e in (registry or [])
              if isinstance(e, dict) and e.get("retrieved_at")]
    return max(stamps) if stamps else ""


def link_claims(registry: list, claims: list) -> list[dict]:
    """verifier 주장의 evidence_ids 인용을 역인덱스로 뒤집어 각 근거의 used_by_claims 를 채운다.

    - claim `{id, evidence_ids: [ev1, ...]}` → 해당 근거 항목의 used_by_claims 에 claim id 추가.
    - 멱등: 매 호출마다 used_by_claims 를 재계산하므로 finalize/재작성을 여러 번 거쳐도 안정적.
    입력 registry(정규화됨)를 제자리 갱신해 반환한다.
    """
    by_id: dict[str, dict] = {e.get("evidence_id"): e for e in (registry or [])
                             if isinstance(e, dict) and e.get("evidence_id")}
    for e in by_id.values():
        e["used_by_claims"] = []
    for c in claims or []:
        if not isinstance(c, dict):
            continue
        cid = c.get("id")
        if not cid:
            continue
        for eid in c.get("evidence_ids") or []:
            e = by_id.get(eid)
            if e is not None and cid not in e["used_by_claims"]:
                e["used_by_claims"].append(cid)
    return list(registry or [])
