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


def _key() -> str:
    return os.getenv("TAVILY_API_KEY", "").strip()


def search_enabled() -> bool:
    return bool(_key())


def web_search(query: str, max_results: int = 5) -> list[dict]:
    """웹 검색 결과를 [{title, url, content}] 로 반환. 실패 시 빈 리스트."""
    if not search_enabled() or not query.strip():
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
        return [r for r in out if r["url"]]
    except Exception as exc:  # 네트워크/쿼터/패키지 문제 모두 안전하게 흡수
        print(f"[search] Tavily 검색 실패 → 검색 생략 ({type(exc).__name__}: {exc})", file=sys.stderr)
        return []
