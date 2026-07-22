"""직렬 vs 병렬 워크플로 비교 측정 (PR-4).

같은 주제를 serial/parallel 두 구조로 실행하고 다음을 뽑는다.
- 성능: wall_time_ms(실제 대기시간, 주 지표) · llm_latency_sum_ms(호출 합)
- 비용/안정성: LLM 호출 수 · 토큰 · 추정 비용 · fallback 수 · run_status
- 품질(결정론적, LLM 심판 없이): 14섹션 존재/순서 · 빈 섹션 수 · PESTEL 표 · 고유 출처 URL 수 · 검증 분포

핵심 원칙(설계): Agent 입력·프롬프트·결과 구조는 동일하고 '실행 순서만' 다르므로,
지연 차이는 병렬화 때문이고 품질은 '비열등성(하락 없음)'을 확인하는 것이 목적이다.
"""
from __future__ import annotations

import re
import statistics

from app.agents.draft_writer import SECTIONS
from app.graph.workflow import run_workflow

_TABLE_SEP = re.compile(r"^\s*\|[\s:|-]+\|\s*$", re.MULTILINE)


def _empty_sections(draft: str) -> list[str]:
    """제목은 있으나 본문이 비어 있는 섹션 목록."""
    empty = []
    for s in SECTIONS:
        # 제목 줄 뒤 '한 줄바꿈'까지만 먹고 본문을 캡처한다([ \t]*: 빈 줄을 삼켜 다음 섹션을
        # 본문으로 오인하지 않도록 줄바꿈은 제외). 본문이 공백뿐이면 '빈 섹션'.
        m = re.search(rf"##\s+{re.escape(s)}[ \t]*\n(.*?)(?=\n##\s|\Z)", draft, re.DOTALL)
        if m is not None and not m.group(1).strip():
            empty.append(s)
    return empty


def deterministic_quality(state: dict) -> dict:
    """LLM 심판 없이 문서 구조로만 판정하는 품질 지표(재현 가능·결정론적)."""
    draft = state.get("final_draft") or state.get("draft") or ""
    present = [s for s in SECTIONS if f"## {s}" in draft]
    idxs = [draft.find(f"## {s}") for s in present]
    ordered = idxs == sorted(idxs)                       # 등장 순서가 고정 서식과 일치
    srcs = list((state.get("research_result") or {}).get("source_objects") or [])
    srcs += list(state.get("competitor_sources") or [])
    unique_urls = len({o.get("url") for o in srcs if isinstance(o, dict) and o.get("url")})
    claims = (state.get("verification_result") or {}).get("claims") or []
    dist = {"supported": 0, "unsupported": 0, "uncertain": 0}
    for c in claims:
        st = c.get("status") if isinstance(c, dict) else None
        if st in dist:
            dist[st] += 1
    return {
        "sections_present": len(present),
        "sections_total": len(SECTIONS),
        "sections_complete": len(present) == len(SECTIONS),
        "sections_ordered": ordered,
        "empty_sections": len(_empty_sections(draft)),
        "pestel_table": bool(_TABLE_SEP.search(draft)),
        "unique_source_urls": unique_urls,
        "verification": dist,
    }


def run_once(topic: dict, mode: str) -> dict:
    """주제 1개를 주어진 구조(mode)로 1회 실행하고 지표를 반환."""
    state = run_workflow(dict(topic), workflow_mode=mode)
    u = state.get("usage") or {}
    return {
        "topic": topic.get("project_name", ""),
        "mode": state.get("workflow_mode", mode),
        "wall_time_ms": u.get("wall_time_ms"),
        "llm_latency_sum_ms": u.get("llm_latency_sum_ms"),
        "calls": u.get("calls"),
        "total_tokens": u.get("total_tokens"),
        "est_cost_usd": u.get("est_cost_usd"),
        "fallback_calls": u.get("fallback_calls"),
        "run_status": state.get("run_status"),
        "quality": deterministic_quality(state),
    }


def _median(values: list[float]) -> float | None:
    nums = [v for v in values if isinstance(v, (int, float))]
    return round(statistics.median(nums), 1) if nums else None


def aggregate(runs: list[dict]) -> dict:
    """실행 기록을 mode별로 집계(중앙값·평균 품질). 성능 주 지표는 wall_time_ms 중앙값."""
    out: dict = {}
    for mode in ("serial", "parallel"):
        rows = [r for r in runs if r.get("mode") == mode]
        if not rows:
            continue
        n = len(rows)
        out[mode] = {
            "runs": n,
            "wall_time_ms_median": _median([r["wall_time_ms"] for r in rows]),
            "llm_latency_sum_ms_median": _median([r["llm_latency_sum_ms"] for r in rows]),
            "total_tokens_mean": round(sum(r["total_tokens"] or 0 for r in rows) / n, 1),
            "est_cost_usd_mean": round(sum(r["est_cost_usd"] or 0 for r in rows) / n, 6),
            "fallback_calls_total": sum(r["fallback_calls"] or 0 for r in rows),
            "sections_complete_rate": round(
                sum(1 for r in rows if r["quality"]["sections_complete"]) / n, 3),
            "empty_sections_mean": round(
                sum(r["quality"]["empty_sections"] for r in rows) / n, 2),
            "unique_source_urls_mean": round(
                sum(r["quality"]["unique_source_urls"] for r in rows) / n, 2),
        }
    # 병렬화 성능 요약(직렬 대비): wall time 감소율
    if "serial" in out and "parallel" in out:
        s = out["serial"]["wall_time_ms_median"]
        p = out["parallel"]["wall_time_ms_median"]
        out["summary"] = {
            "wall_time_reduction_pct": round((s - p) / s * 100, 1) if s else None,
            "token_diff_pct": round(
                (out["parallel"]["total_tokens_mean"] - out["serial"]["total_tokens_mean"])
                / out["serial"]["total_tokens_mean"] * 100, 1) if out["serial"]["total_tokens_mean"] else None,
        }
    return out
