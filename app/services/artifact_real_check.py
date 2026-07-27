"""Artifact 읽기 전환의 **실 LLM 검증** (로드맵 2-2 PR 5f).

PR 5a~5e 의 검증은 전부 **더미 기준**이었다. 더미는 Agent 결과가 짧은 placeholder 이고
LLM 이 늘 성공하므로, 다음 둘을 확인할 수 없었다:

1. **실제 내용으로도 Dual Write 가 정확한가** — `artifact_parity` 는 평면 결과와 Artifact
   content 의 동등성을 보는데, 더미 값은 작고 단순해 직렬화·중첩·유니코드에서 어긋날 여지가
   거의 없었다.
2. **실전에서 폴백이 정말 0 인가** — 실 LLM 은 빈 응답·fallback 을 낸다. 그러면
   `status=fallback`/content 가 빈 Artifact 가 생기고, 그때 처음으로 `shadow_fallbacks` 가
   0 이 아니게 된다. 전환 판단의 근거는 더미의 0 이 아니라 **이 숫자**다.

**측정 설계 — 실 LLM 에서는 산출물 동일성을 쓸 수 없다.**
LLM 이 확률적이라 같은 모드로 두 번 돌려도 문서가 다르다. 그래서 모드 간 비교는 두 층으로
나눈다:

- **결정적 층(비용 0)**: 실제 실행이 남긴 State 를 고정해 두고, 세 모드에서 각 Agent 가
  만드는 **프롬프트를 대조**한다. State 가 고정이면 프롬프트 생성은 결정적이므로 **정확히
  같아야 한다**. 읽기 경로가 갈리면 반드시 여기서 잡힌다. LLM 을 부르지 않아 추가 비용이 없다.
- **비열등성 층(유료)**: 모드별 실행의 구조 품질·점수·폴백·비용을 나란히 본다. **동일성이
  아니라 하락이 없는지**를 본다(n 이 작아 통계가 아니라 스모크다).
"""
from __future__ import annotations

import json
import os
from typing import Callable

from app.agents import (
    business_model,
    competitor,
    customer,
    draft_writer,
    pestel,
    risk,
    swot,
    verifier,
)
from app.graph.workflow import run_workflow
from app.schemas import artifact
from app.services import llm, parallel_bench, search

MODES = [artifact.READ_LEGACY, artifact.READ_PREFER_ARTIFACT, artifact.READ_ARTIFACT_ONLY]

# 프롬프트를 대조할 소비자. Agent 간 읽기 6곳 + 문서 생성·검증 2곳 = selector 를 타는 전부.
CONSUMERS: dict[str, Callable] = {
    "competitor": competitor.competitor,
    "customer": customer.customer,
    "pestel": pestel.pestel,
    "swot": swot.swot,
    "business_model": business_model.business_model,
    "risk": risk.risk,
    "draft_writer": draft_writer.draft,
    "verifier": verifier.verify,
}


def _run_metrics(state: dict, mode: str, topic_id: str) -> dict:
    """실행 하나에서 비교에 쓸 지표만 뽑는다(원본 State 는 크므로 싣지 않는다)."""
    u = state.get("usage") or {}
    parity = state.get("artifact_parity") or {}
    read = state.get("artifact_read") or {}
    runtime = read.get("runtime") or {}
    q = parallel_bench.structural_quality(state)
    arts = [a for a in (state.get("artifacts") or []) if isinstance(a, dict)]
    return {
        "topic": topic_id,
        "read_mode": mode,
        "workflow_mode": state.get("workflow_mode"),
        "run_status": state.get("run_status"),
        "failed_nodes": state.get("failed_nodes") or [],
        "fallback_nodes": state.get("fallback_nodes") or [],
        # 비용·안정성
        "calls": u.get("calls"),
        "total_tokens": u.get("total_tokens"),
        "est_cost_usd": u.get("est_cost_usd"),
        "fallback_calls": u.get("fallback_calls"),
        "wall_time_ms": u.get("wall_time_ms"),
        # Artifact 정합성 — 실제 내용으로 처음 확인하는 부분
        "parity_ok": parity.get("ok"),
        "parity_matched": parity.get("matched"),
        "parity_reasons": sorted({m.get("reason") for m in parity.get("mismatched") or []}),
        "artifact_statuses": {s: sum(1 for a in arts if a.get("status") == s)
                              for s in sorted({a.get("status") for a in arts})},
        # 런타임 읽기 계측(PR 5d) — 전환 판단의 실제 근거
        "reads_total": runtime.get("total"),
        "reads_from_artifact": runtime.get("from_artifact"),
        "fallbacks": runtime.get("fallbacks"),
        "fallback_reasons": runtime.get("fallback_reasons"),
        "shadow_fallbacks": runtime.get("shadow_fallbacks"),
        "shadow_reasons": runtime.get("shadow_reasons"),
        # 품질(구조 결정론 지표 + 최종 점수)
        "sections_present": q["sections_present"],
        "sections_complete": q["sections_complete"],
        "empty_sections": q["empty_sections"],
        "unique_source_urls": q["unique_source_urls"],
        "fact_support_rate": q["fact_support_rate"],
        "evidence_link_rate": q["evidence_link_rate"],
        "total_score": (state.get("final_review_result") or {}).get("total_score"),
    }


def run_topic(topic: dict, mode: str, workflow_mode: str = "parallel") -> tuple[dict, dict]:
    """주제 1개를 한 읽기 모드로 실행. `(지표, State)` 를 돌려준다."""
    os.environ[artifact.READ_MODE_ENV] = mode
    state = run_workflow(dict(topic), workflow_mode=workflow_mode)
    return _run_metrics(state, mode, topic.get("id") or topic.get("project_name", "")), state


# ---- 결정적 층: 같은 State 를 세 모드로 읽었을 때 프롬프트가 같은가 ----

def _capture_prompts(agent_fn: Callable, state: dict, mode: str) -> list[str]:
    """Agent 를 **LLM 호출 없이** 돌려 프롬프트만 모은다(추가 비용 0).

    `complete_json`/`complete_text`/`_generate` 는 fallback 을 그대로 돌려주도록 바꾼다
    (더미 모드와 같은 자리). 검색도 막는다 — 외부 호출 비용을 안 쓰기 위해서이기도 하고,
    검색 결과가 실행마다 달라지면 모드 간 비교가 그 차이에 오염되기 때문이다.
    """
    seen: list[str] = []

    def cap_json(system, user, fallback=None, **kw):
        seen.append(user)
        return fallback if fallback is not None else {}

    def cap_text(system, user, fallback="", **kw):
        seen.append(user)
        return fallback

    def cap_generate(system, user, fallback, model, status):
        seen.append(user)
        return fallback, []

    saved = (llm.complete_json, llm.complete_text, draft_writer._generate, search.web_search)
    llm.complete_json, llm.complete_text = cap_json, cap_text
    draft_writer._generate = cap_generate
    search.web_search = lambda *a, **k: []
    os.environ[artifact.READ_MODE_ENV] = mode
    try:
        agent_fn(dict(state))
    except artifact.ArtifactUnavailable as e:
        # artifact_only 에서 의존이 없으면 여기서 멈추는 게 정상 — 사유를 비교 대상에 남긴다.
        seen.append(f"<ArtifactUnavailable: {e}>")
    finally:
        llm.complete_json, llm.complete_text, draft_writer._generate, search.web_search = saved
    return seen


def prompt_parity(state: dict) -> dict:
    """실제 실행 State 를 세 모드로 읽어 소비자별 프롬프트가 같은지 대조한다.

    반환: `{"ok": bool, "checked": n, "mismatched": [{consumer, mode, ...}], "empty": [...]}`
    `empty` = 프롬프트가 하나도 안 잡힌 소비자(그 항목은 아무것도 검증하지 못한다 —
    조용히 ok 로 세면 검증했다는 착각이 된다).
    """
    mismatched: list[dict] = []
    empty: list[str] = []
    for name, fn in CONSUMERS.items():
        base = _capture_prompts(fn, state, artifact.READ_LEGACY)
        if not base:
            empty.append(name)
        for mode in MODES[1:]:
            got = _capture_prompts(fn, state, mode)
            if got != base:
                mismatched.append({
                    "consumer": name, "mode": mode,
                    "legacy_len": [len(p) for p in base],
                    "other_len": [len(p) for p in got],
                })
    return {"ok": not mismatched and not empty,
            "checked": len(CONSUMERS), "mismatched": mismatched, "empty": empty}


# ---- 집계 ----

def summarize(rows: list[dict], parities: dict) -> dict:
    """모드별로 묶어 비교표를 만든다. 실 LLM 이라 평균이 아니라 값을 그대로 나열한다(n 이 작다)."""
    by_mode: dict[str, list[dict]] = {m: [r for r in rows if r["read_mode"] == m] for m in MODES}

    def agg(rs: list[dict]) -> dict:
        if not rs:
            return {}
        def s(key):
            return [r.get(key) for r in rs]
        return {
            "runs": len(rs),
            "run_status": sorted({r["run_status"] for r in rs}),
            "failed_nodes_total": sum(len(r["failed_nodes"]) for r in rs),
            "fallback_nodes_total": sum(len(r["fallback_nodes"]) for r in rs),
            "parity_ok_all": all(r["parity_ok"] for r in rs),
            "parity_reasons": sorted({x for r in rs for x in r["parity_reasons"]}),
            "artifact_statuses": _merge_counts([r["artifact_statuses"] for r in rs]),
            "reads_total": s("reads_total"),
            "reads_from_artifact": s("reads_from_artifact"),
            "fallbacks": s("fallbacks"),
            "fallback_reasons": _merge_counts([r["fallback_reasons"] or {} for r in rs]),
            "shadow_fallbacks": s("shadow_fallbacks"),
            "shadow_reasons": _merge_counts([r["shadow_reasons"] or {} for r in rs]),
            "sections_complete_all": all(r["sections_complete"] for r in rs),
            "empty_sections_total": sum(r["empty_sections"] or 0 for r in rs),
            "unique_source_urls": s("unique_source_urls"),
            "total_score": s("total_score"),
            "fact_support_rate": s("fact_support_rate"),
            "calls": s("calls"),
            "fallback_calls_total": sum(r["fallback_calls"] or 0 for r in rs),
            "est_cost_usd": round(sum(r["est_cost_usd"] or 0 for r in rs), 4),
            "wall_time_ms": s("wall_time_ms"),
        }

    return {
        "by_mode": {m: agg(rs) for m, rs in by_mode.items()},
        "prompt_parity": parities,
        "total_cost_usd": round(sum(r["est_cost_usd"] or 0 for r in rows), 4),
        "runs": len(rows),
    }


def _merge_counts(dicts: list[dict]) -> dict:
    out: dict[str, int] = {}
    for d in dicts:
        for k, v in (d or {}).items():
            out[k] = out.get(k, 0) + int(v or 0)
    return {k: out[k] for k in sorted(out)}


def summary_lines(rep: dict) -> list[str]:
    """사람이 읽을 요약. 판정 근거를 문장으로 남긴다."""
    lines = []
    for mode in MODES:
        a = rep["by_mode"].get(mode) or {}
        if not a:
            continue
        lines.append(
            f"{mode}: {a['runs']}회 · run_status={a['run_status']} · "
            f"failed {a['failed_nodes_total']} · parity_ok={a['parity_ok_all']} · "
            f"14섹션 완전={a['sections_complete_all']} · 점수 {a['total_score']} · "
            f"읽기 {a['reads_total']}(Artifact {a['reads_from_artifact']}) · "
            f"폴백 {a['fallbacks']}{a['fallback_reasons'] or ''} · "
            f"shadow {a['shadow_fallbacks']}{a['shadow_reasons'] or ''} · "
            f"${a['est_cost_usd']}")
    pp = rep["prompt_parity"]
    bad = [t for t, v in pp.items() if not v["ok"]]
    lines.append(f"프롬프트 동일성(결정적): {len(pp)}주제 중 불일치 {len(bad)}건 "
                 f"{'· ' + json.dumps(bad, ensure_ascii=False) if bad else ''}")
    lines.append(f"총 비용 ${rep['total_cost_usd']} / {rep['runs']}회")
    return lines
