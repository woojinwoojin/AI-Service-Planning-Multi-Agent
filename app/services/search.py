"""웹 검색 (Tavily) — Research Agent의 근거 확보용.

- TAVILY_API_KEY 가 없거나 오류가 나면 조용히 빈 결과를 반환한다.
  → Research Agent는 검색 없이 LLM 지식 기반으로 동작(관통 보장, 안전한 성능 저하).
- 검색이 되면 실제 출처 URL을 확보해 Research 결과의 sources(인용)로 넘긴다.
"""
from __future__ import annotations

import os
import sys
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()

# 출처 '유형' 표시용 라벨. 주의: 이는 '권위성 힌트'일 뿐 신뢰도/사실성 판정이 아니다.
# (정부 자료도 낡을 수 있고, 기업 자료는 편향, 언론이 항상 정확하지 않으며, 논문도 주제와
#  무관하면 좋은 근거가 아니다 — 그래서 '고신뢰'가 아니라 '유형'으로만 표기한다.)
SOURCE_TYPE_LABELS: dict[str, str] = {
    "government": "정부·공공기관",
    "academic": "학술·연구기관",
    "corporate": "기업 공식자료",
    "news": "언론",
    "community": "블로그·커뮤니티",
    "unknown": "기타",
}

# 규칙 기반 도메인 분류(LLM 불필요). 확신 있는 것만 태깅하고 나머지는 'unknown'으로 둔다
# — 무리한 추정(예: 임의 .com 을 '기업 공식자료'로 단정)이 잘못된 확신을 주는 것을 막기 위함.
_GOV_SUFFIXES = (".go.kr", ".gov", ".gov.kr", ".gov.uk", ".mil", ".europa.eu")
_ACADEMIC_SUFFIXES = (".ac.kr", ".re.kr", ".edu", ".ac.uk", ".edu.au")
_NEWS_HOSTS = frozenset({
    "yna.co.kr", "yonhapnews.co.kr", "chosun.com", "donga.com", "joongang.co.kr",
    "hani.co.kr", "khan.co.kr", "hankyung.com", "mk.co.kr", "mt.co.kr", "sedaily.com",
    "edaily.co.kr", "kbs.co.kr", "imbc.com", "sbs.co.kr", "ytn.co.kr", "newsis.com",
    "news1.kr", "hankookilbo.com", "reuters.com", "bloomberg.com", "nytimes.com",
    "wsj.com", "bbc.com", "bbc.co.uk", "cnn.com", "theguardian.com", "ft.com",
    "techcrunch.com", "wired.com", "cnbc.com",
})
_COMMUNITY_HOSTS = frozenset({
    "blog.naver.com", "cafe.naver.com", "tistory.com", "brunch.co.kr", "velog.io",
    "medium.com", "github.io", "wordpress.com", "blogspot.com", "reddit.com",
    "quora.com", "stackoverflow.com", "stackexchange.com", "namu.wiki",
    "wikipedia.org", "facebook.com", "twitter.com", "x.com", "instagram.com",
    "youtube.com", "threads.net", "disqus.com",
})


def classify_source_type(url: str) -> str:
    """URL 도메인으로 출처 '유형'을 규칙 분류한다(신뢰도 판정 아님, LLM 호출 없음).

    반환값은 SOURCE_TYPE_LABELS 의 키. 판단이 서지 않으면 'unknown'(기타)으로 남긴다.
    """
    host = (urlparse(url).hostname or "").lower().strip(".")
    if not host:
        return "unknown"
    if host.startswith("www."):
        host = host[4:]

    def _in(hosts: frozenset[str]) -> bool:
        return any(host == h or host.endswith("." + h) for h in hosts)

    if host.endswith(_GOV_SUFFIXES):
        return "government"
    if host.endswith(_ACADEMIC_SUFFIXES):
        return "academic"
    if _in(_NEWS_HOSTS):
        return "news"
    if _in(_COMMUNITY_HOSTS):
        return "community"
    return "unknown"


def build_source_objects(hits: list[dict]) -> list[dict]:
    """검색 히트를 구조화 출처 객체(제목/URL/스니펫/유형)로 변환한다(URL 기준 중복 제거).

    Research·Competitor 등 실제 검색을 쓰는 Agent가 공유한다 — 실제 출처만 담기므로
    최종 '참고자료' 인용과 배지 표시의 단일 근거가 된다(LLM이 지어낸 URL은 섞이지 않음).

    snippet 은 원문(full text)이 아니라 Tavily 검색 결과의 '요약문'이다. 뒤단 verifier 가
    이를 원문 사실 검증으로 오해하지 않도록 성격을 메타데이터로 함께 남긴다:
    - content_scope="search_snippet": 담긴 텍스트는 검색 요약문 수준임.
    - original_text_extracted=False: URL 원문을 추출·재확인하지 않았음.
    """
    seen: set[str] = set()
    objs: list[dict] = []
    for h in hits:
        url = (h.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        objs.append({
            "title": (h.get("title") or "").strip(),
            "url": url,
            "snippet": (h.get("content") or "").strip()[:300],
            "content_scope": "search_snippet",  # 원문 아님(검색 요약문)
            "original_text_extracted": False,   # URL 원문 추출·재확인 안 함
            "source_type": classify_source_type(url),  # 규칙 기반 유형(권위성 힌트)
        })
    return objs


def _key() -> str:
    return os.getenv("TAVILY_API_KEY", "").strip()


def search_enabled() -> bool:
    return bool(_key())


def web_search(query: str, max_results: int = 5, status: dict | None = None) -> list[dict]:
    """웹 검색 결과를 [{title, url, content}] 로 반환. 실패 시 빈 리스트.

    status(dict)를 주면 검색 '상태'를 기록한다 — 호출부가 '검색 실패'와 '결과 없음'을
    구분할 수 있게 하기 위함(정직한 run_status 표면화용):
      state = "disabled" | "no_results" | "ok" | "error", error = 사유 문자열.
    반환값(빈 리스트)만으로는 세 경우가 구분되지 않던 문제(외부 리뷰 P0-4)를 보완한다.
    """
    st = status if status is not None else {}
    if not search_enabled() or not query.strip():
        st["state"] = "disabled"
        return []
    try:
        from tavily import TavilyClient

        client = TavilyClient(api_key=_key())
        resp = client.search(query=query, max_results=max_results, search_depth="basic")
        out = []
        for r in resp.get("results", []):
            out.append({
                "title": (r.get("title") or "").strip(),
                "url": (r.get("url") or "").strip(),
                "content": (r.get("content") or "").strip(),
            })
        hits = [r for r in out if r["url"]]
        st["state"] = "ok" if hits else "no_results"
        return hits
    except Exception as exc:  # 네트워크/쿼터/패키지 문제 모두 안전하게 흡수(관통 보장)
        st["state"] = "error"
        st["error"] = f"{type(exc).__name__}: {exc}"
        print(f"[search] Tavily 검색 실패 → 검색 생략 ({type(exc).__name__}: {exc})", file=sys.stderr)
        return []
