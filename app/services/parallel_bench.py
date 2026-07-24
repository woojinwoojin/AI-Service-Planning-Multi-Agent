"""직렬 vs 병렬 워크플로 비교 측정 (PR-4).

같은 주제를 serial/parallel 두 구조로 실행하고 다음을 뽑는다.
- 성능: wall_time_ms(실제 대기시간, 주 지표) · llm_latency_sum_ms(호출 합)
- 비용/안정성: LLM 호출 수 · 토큰 · 추정 비용 · fallback 수 · run_status
- 품질(결정론적, LLM 심판 없이): 14섹션 존재/순서 · 빈 섹션 수 · PESTEL 표 · 고유 출처 URL 수 · 검증 분포

핵심 원칙(설계): Agent 입력·프롬프트·결과 구조는 동일하고 '실행 순서만' 다르므로,
지연 차이는 병렬화 때문이고 품질은 '비열등성(하락 없음)'을 확인하는 것이 목적이다.
"""
from __future__ import annotations

import hashlib
import re
import statistics
import subprocess

from app.agents.draft_writer import SECTIONS
from app.graph.workflow import run_workflow

# 실험 조건을 식별하는 버전. 그래프 구조/프롬프트/측정 방식이 바뀌면 올려서
# 과거 partial 캐시를 자동 무효화한다(다른 조건의 결과가 섞이지 않도록).
WORKFLOW_VERSION = "parallel-v1"

_TABLE_SEP = re.compile(r"^\s*\|[\s:|-]+\|\s*$", re.MULTILINE)


def experiment_signature(topics: list[dict], reps: int, model: str) -> dict:
    """실험 조건 지문 — git commit·모델·주제 구성·반복·버전. partial 재사용 판단에 쓴다.

    이 지문이 다르면(모델 변경·주제 변경·코드 변경 등) 이전 진행분을 재사용하지 않는다
    (외부 리뷰 #2: (topic,mode,rep) 키만으로는 다른 실험 결과가 섞일 수 있음).
    """
    names = "|".join(sorted(t.get("project_name", "") for t in topics))
    topics_hash = hashlib.sha256(names.encode("utf-8")).hexdigest()[:12]
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        commit = "unknown"
    return {"git_commit": commit, "model": model or "dummy", "topics_hash": topics_hash,
            "topic_count": len(topics), "reps": reps, "version": WORKFLOW_VERSION}


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


def structural_quality(state: dict) -> dict:
    """문서 '구조/건전성' 품질 지표(LLM 심판 없이 결정론적).

    주의(정직한 명칭): 이것은 14섹션 존재·순서·빈 섹션·PESTEL 표·출처 수·검증 분포 등
    '구조적 완성도'만 측정한다. 논리성·구체성·일관성 같은 '내용 품질'은 측정하지 않는다.
    병렬화는 품질 향상이 아니라 '구조적 비열등성(저하 없음)' 확인이 목적이므로 이 지표로 충분하되,
    내용 품질 동등성까지 보이려면 별도 소규모 블라인드 평가가 필요하다.
    """
    draft = state.get("final_draft") or state.get("draft") or ""
    present = [s for s in SECTIONS if f"## {s}" in draft]
    idxs = [draft.find(f"## {s}") for s in present]
    ordered = idxs == sorted(idxs)                       # 등장 순서가 고정 서식과 일치
    srcs = list((state.get("research_result") or {}).get("source_objects") or [])
    srcs += list(state.get("competitor_sources") or [])
    unique_urls = len({o.get("url") for o in srcs if isinstance(o, dict) and o.get("url")})
    vr = state.get("verification_result") or {}
    claims = vr.get("claims") or []
    # 근거 상태 분포(Tier 2): contradicted(반대 근거)·not_applicable(비-사실)까지 분리 집계.
    dist = {"supported": 0, "unsupported": 0, "contradicted": 0,
            "uncertain": 0, "not_applicable": 0}
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
        # Tier 2 신뢰도 지표: 사실 주장 검증률·근거 연결률(완료 게이트 리포트용).
        "fact_support_rate": vr.get("fact_support_rate"),
        "evidence_link_rate": vr.get("evidence_link_rate"),
    }


# 하위호환 별칭(옛 이름). 새 코드는 structural_quality 를 쓴다.
deterministic_quality = structural_quality


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
        "timing": state.get("timing") or {},          # 단계별 실행시간(병목 위치)
        "quality": structural_quality(state),
        # 재작성 계측(로드맵 2-4 PR-7): 섹션 단위 수정이 얼마나 자주·몇 섹션에 적용됐는지.
        "revision": _revision_metrics(state),
    }


def _revision_metrics(state: dict) -> dict:
    """실행의 재작성 전략을 벤치 지표로 요약(집계에서 섹션/전체/생략 비율 계산)."""
    strategy = state.get("revision_strategy", "none")
    return {
        "review_decision": "finalize" if strategy == "none" else "revise",
        "revision_executed": strategy in ("section", "full"),
        "revision_scope": strategy,                        # none / section / full
        "revised_sections": len(state.get("revised_section_ids") or []),
        "fallback_reason": state.get("revision_fallback_reason"),
    }


def _median(values: list[float]) -> float | None:
    nums = [v for v in values if isinstance(v, (int, float))]
    return round(statistics.median(nums), 1) if nums else None


def _mean_metric(rows: list[dict], key: str) -> float | None:
    """실행들의 quality[key] 평균(None 제외). Tier 2 지표(사실 검증률·근거 연결률)용."""
    vals = [(r.get("quality") or {}).get(key) for r in rows]
    nums = [v for v in vals if isinstance(v, (int, float))]
    return round(sum(nums) / len(nums), 3) if nums else None


def _stage_medians(rows: list[dict]) -> dict:
    """실행들의 단계별 wall time 중앙값. 어느 구간이 병목인지 한눈에 보기 위함."""
    stage_names: list[str] = []
    for r in rows:
        for k in ((r.get("timing") or {}).get("stages") or {}):
            if k not in stage_names:
                stage_names.append(k)
    out = {}
    for name in stage_names:
        vals = [(r.get("timing") or {}).get("stages", {}).get(name) for r in rows]
        out[name] = _median(vals)
    return out


def _percentile(values: list[float], pct: float) -> float | None:
    """작은 표본에서도 안전한 백분위(근사). n=1 이면 그 값, 없으면 None."""
    nums = sorted(v for v in values if isinstance(v, (int, float)))
    if not nums:
        return None
    k = (len(nums) - 1) * pct
    lo, hi = int(k), min(int(k) + 1, len(nums) - 1)
    return round(nums[lo] + (nums[hi] - nums[lo]) * (k - lo), 1)


def aggregate(runs: list[dict]) -> dict:
    """실행 기록을 mode별로 집계(중앙값·평균 품질). 성능 주 지표는 wall_time_ms 중앙값."""
    out: dict = {}
    for mode in ("serial", "parallel"):
        rows = [r for r in runs if r.get("mode") == mode]
        if not rows:
            continue
        n = len(rows)
        walls = [r["wall_time_ms"] for r in rows]
        statuses = [r.get("run_status") for r in rows]
        out[mode] = {
            "runs": n,
            "wall_time_ms_median": _median(walls),
            "wall_time_ms_p95": _percentile(walls, 0.95),          # 평균만 빠르고 꼬리가 느려지지 않았는지
            "wall_time_ms_max": round(max(v for v in walls if v is not None), 1) if any(walls) else None,
            "llm_latency_sum_ms_median": _median([r["llm_latency_sum_ms"] for r in rows]),
            "calls_mean": round(sum(r["calls"] or 0 for r in rows) / n, 1),
            "total_tokens_mean": round(sum(r["total_tokens"] or 0 for r in rows) / n, 1),
            "est_cost_usd_mean": round(sum(r["est_cost_usd"] or 0 for r in rows) / n, 6),
            "fallback_calls_total": sum(r["fallback_calls"] or 0 for r in rows),
            # 안정성: 실행 품질 분포(성공/저하/실패) — 병렬화가 실패·fallback 을 늘리지 않았는지
            "run_status": {s: statuses.count(s) for s in ("success", "degraded", "failed") if s in statuses},
            "sections_complete_rate": round(
                sum(1 for r in rows if r["quality"]["sections_complete"]) / n, 3),
            "sections_ordered_rate": round(
                sum(1 for r in rows if r["quality"]["sections_ordered"]) / n, 3),
            "pestel_table_rate": round(
                sum(1 for r in rows if r["quality"]["pestel_table"]) / n, 3),
            "empty_sections_mean": round(
                sum(r["quality"]["empty_sections"] for r in rows) / n, 2),
            "unique_source_urls_mean": round(
                sum(r["quality"]["unique_source_urls"] for r in rows) / n, 2),
            # 재작성 전략 분포(PR-7): 섹션 단위 수정 실행률·범위별 횟수·평균 수정 섹션 수.
            "revision": {
                "executed_rate": round(
                    sum(1 for r in rows if (r.get("revision") or {}).get("revision_executed")) / n, 3),
                "scope": {sc: sum(1 for r in rows
                                  if (r.get("revision") or {}).get("revision_scope") == sc)
                          for sc in ("none", "section", "full")},
                "revised_sections_mean": round(
                    sum((r.get("revision") or {}).get("revised_sections", 0) for r in rows) / n, 2),
            },
            # 신뢰도 Tier 2 지표 평균(사실 검증률·근거 연결률). 옛 실행엔 없을 수 있어 None 제외 평균.
            "fact_support_rate_mean": _mean_metric(rows, "fact_support_rate"),
            "evidence_link_rate_mean": _mean_metric(rows, "evidence_link_rate"),
            # 단계별 wall time 중앙값(병목 위치) + coverage 중앙값(측정이 전체를 얼마나 설명하는지)
            "stage_ms_median": _stage_medians(rows),
            "timing_coverage_median": _median(
                [(r.get("timing") or {}).get("coverage") for r in rows]),
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
