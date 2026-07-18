"""웹 검색 (Tavily) — Research Agent의 근거 확보용.

- TAVILY_API_KEY 가 없거나 오류가 나면 조용히 빈 결과를 반환한다.
  → Research Agent는 검색 없이 LLM 지식 기반으로 동작(관통 보장, 안전한 성능 저하).
- 검색이 되면 실제 출처 URL을 확보해 Research 결과의 sources(인용)로 넘긴다.
"""
from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

load_dotenv()


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
