"""직렬 vs 병렬 워크플로 비교 실험 CLI (PR-4).

같은 주제를 serial/parallel 두 구조로 실행해 wall_time·품질(결정론)·비용을 비교한다.
- 순서 효과 완화: rep 마다 AB/BA 교차(짝수 rep=serial→parallel, 홀수=parallel→serial).
- 이어하기: (주제, mode, rep)별로 즉시 저장 → 중단돼도 진행분 보존.
- 스모크: --topics 3 --reps 1 (6회) → 정상 동작·wall time 감소만 먼저 확인.
- 본 실험: --topics 6 --reps 2 (24회) → 중앙값 비교(유료: 실제 LLM 모드에서 비용 발생).

실행 예: python run_parallel_bench.py --topics 3 --reps 1
        python run_parallel_bench.py --topics 6 --reps 2 --shuffle --seed 7
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

from app.services import parallel_bench
from app.services.llm import default_model, is_dummy
from run_compare import TOPICS

_PARTIAL = Path("outputs/parallel_bench_partial.json")


def _p(msg: str) -> None:
    print(msg, flush=True)


def _order(rep: int) -> list[str]:
    """AB/BA 교차: 짝수 rep 은 serial→parallel, 홀수 rep 은 parallel→serial."""
    return ["serial", "parallel"] if rep % 2 == 0 else ["parallel", "serial"]


def _table_md(agg: dict) -> str:
    s, p = agg.get("serial", {}), agg.get("parallel", {})
    rows = [
        ("실행 횟수", s.get("runs"), p.get("runs")),
        ("wall time 중앙값(ms) ↓", s.get("wall_time_ms_median"), p.get("wall_time_ms_median")),
        ("wall time p95(ms)", s.get("wall_time_ms_p95"), p.get("wall_time_ms_p95")),
        ("wall time 최대(ms)", s.get("wall_time_ms_max"), p.get("wall_time_ms_max")),
        ("LLM 호출시간 합 중앙값(ms)", s.get("llm_latency_sum_ms_median"), p.get("llm_latency_sum_ms_median")),
        ("평균 LLM 호출 수", s.get("calls_mean"), p.get("calls_mean")),
        ("평균 토큰", s.get("total_tokens_mean"), p.get("total_tokens_mean")),
        ("평균 비용(USD)", s.get("est_cost_usd_mean"), p.get("est_cost_usd_mean")),
        ("실행 품질 분포", s.get("run_status"), p.get("run_status")),
        ("14섹션 완성률", s.get("sections_complete_rate"), p.get("sections_complete_rate")),
        ("섹션 순서 정상률", s.get("sections_ordered_rate"), p.get("sections_ordered_rate")),
        ("PESTEL 표 정상률", s.get("pestel_table_rate"), p.get("pestel_table_rate")),
        ("평균 빈 섹션 수", s.get("empty_sections_mean"), p.get("empty_sections_mean")),
        ("평균 고유 출처 URL 수", s.get("unique_source_urls_mean"), p.get("unique_source_urls_mean")),
        ("fallback 총계", s.get("fallback_calls_total"), p.get("fallback_calls_total")),
    ]
    lines = ["| 지표 | 직렬 | 병렬 |", "|---|---|---|"]
    lines += [f"| {name} | {sv} | {pv} |" for name, sv, pv in rows]
    return "\n".join(lines)


def _stage_table_md(agg: dict) -> str:
    """단계별 wall time 중앙값(ms) — 병목이 어느 구간인지. 병렬 효과는 analysis_block 에서 보인다."""
    s = agg.get("serial", {}).get("stage_ms_median", {}) or {}
    p = agg.get("parallel", {}).get("stage_ms_median", {}) or {}
    order = ["preprocess", "research", "analysis_block", "draft", "initial_review",
             "revise_or_finalize", "polish", "final_review", "verify"]
    names = [n for n in order if n in s or n in p]
    lines = ["| 단계 | 직렬(ms) | 병렬(ms) |", "|---|---|---|"]
    lines += [f"| {n} | {s.get(n)} | {p.get(n)} |" for n in names]
    cov_s = agg.get("serial", {}).get("timing_coverage_median")
    cov_p = agg.get("parallel", {}).get("timing_coverage_median")
    lines.append(f"| _coverage(대wall)_ | {cov_s} | {cov_p} |")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="직렬 vs 병렬 워크플로 비교 실험")
    ap.add_argument("--topics", type=int, default=3, help="사용할 주제 수(스모크=3, 본실험=6)")
    ap.add_argument("--reps", type=int, default=1, help="주제당 반복 횟수(AB/BA 교차)")
    ap.add_argument("--shuffle", action="store_true", help="주제 순서를 무작위화")
    ap.add_argument("--seed", type=int, default=0, help="무작위 시드(재현용)")
    ap.add_argument("--fresh", action="store_true", help="이전 partial 무시하고 처음부터")
    args = ap.parse_args()

    topics = list(TOPICS[: max(1, min(args.topics, len(TOPICS)))])
    if args.shuffle:
        random.Random(args.seed).shuffle(topics)
    model = "" if is_dummy() else default_model()
    total = len(topics) * args.reps * 2

    _p("=" * 64)
    _p(f"직렬 vs 병렬 비교 · 주제 {len(topics)}개 · 반복 {args.reps} · 총 {total}회 · 모델={model or '더미'}")
    if not is_dummy():
        _p("⚠ 실제 LLM 모드 — 이 실행은 API 비용이 발생합니다.")
    _p("=" * 64)

    # 이어하기 캐시는 '같은 실험 조건'일 때만 재사용한다(모델·주제·코드·반복이 바뀌면 무효).
    sig = parallel_bench.experiment_signature(topics, args.reps, model)
    _PARTIAL.parent.mkdir(exist_ok=True)
    if args.fresh:
        _PARTIAL.unlink(missing_ok=True)
    cached = json.loads(_PARTIAL.read_text(encoding="utf-8")) if _PARTIAL.exists() else None
    if cached and cached.get("signature") == sig:
        runs = cached.get("runs", [])
        _p(f"이어하기: 이전 진행분 {len(runs)}회 재사용(같은 실험 조건).")
    else:
        if cached:
            _p("⚠ 이전 partial 의 실험 조건이 달라 재사용하지 않고 새로 시작합니다(--fresh 로 강제 초기화 가능).")
        runs = []
    done = {(r["topic"], r["mode"], r["rep"]) for r in runs}

    i = 0
    for rep in range(args.reps):
        for topic in topics:
            for mode in _order(rep):
                i += 1
                key = (topic["project_name"], mode, rep)
                if key in done:
                    _p(f"[{i}/{total}] 이미 완료: {topic['project_name']} · {mode} · rep{rep}")
                    continue
                _p(f"[{i}/{total}] {topic['project_name']} · {mode} · rep{rep} 실행 중…")
                rec = parallel_bench.run_once(topic, mode)
                rec["rep"] = rep
                runs.append(rec)
                _PARTIAL.write_text(
                    json.dumps({"signature": sig, "runs": runs}, ensure_ascii=False, indent=2),
                    encoding="utf-8")

    agg = parallel_bench.aggregate(runs)
    table_md = _table_md(agg)
    stage_md = _stage_table_md(agg)
    _p("\n" + table_md)
    _p("\n[단계별 wall time 중앙값 — 병목 위치]\n" + stage_md)
    summary = agg.get("summary", {})
    if summary.get("wall_time_reduction_pct") is not None:
        _p(f"\nwall time 감소율(직렬→병렬): {summary['wall_time_reduction_pct']}%  ·  "
           f"토큰 차이: {summary.get('token_diff_pct')}%")

    docs = Path("docs")
    docs.mkdir(exist_ok=True)
    md = [
        "# 직렬 vs 병렬 워크플로 비교 결과\n",
        f"> 주제 {len(topics)}개 · 반복 {args.reps}(AB/BA 교차) · 모델 `{model or '더미'}` · 총 {len(runs)}회 실행\n",
        "> Agent 입력·프롬프트·결과 구조는 동일하고 실행 순서만 다름 — 지연 차이는 병렬화 효과,"
        " 품질은 '비열등성(하락 없음)' 확인이 목적. 품질 지표는 LLM 심판이 아닌 결정론적 구조 검사.\n",
        "## 요약표\n",
        table_md,
        "\n## 단계별 wall time 중앙값 (병목 위치)\n",
        "> node duration 과 다름: analysis_block 은 분석 4분기의 실제 대기시간(겹침 반영). "
        "coverage 는 측정 단계가 전체 wall 을 설명하는 비율(프레임워크 오버헤드 제외 시 ≥95%).\n",
        stage_md,
    ]
    if summary:
        md.append(f"\n> **wall time 감소율(직렬→병렬): {summary.get('wall_time_reduction_pct')}%** · "
                  f"토큰 차이 {summary.get('token_diff_pct')}% (병렬화는 호출 수를 줄이지 않으므로 토큰·비용은 유사해야 정상)\n")
    (docs / "parallel_bench_result.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    out = Path("outputs")
    out.mkdir(exist_ok=True)
    (out / "parallel_bench.json").write_text(
        json.dumps({"signature": sig, "model": model, "aggregate": agg, "runs": runs},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    _PARTIAL.unlink(missing_ok=True)
    _p("\n저장: docs/parallel_bench_result.md · outputs/parallel_bench.json")
    _p("=" * 64)


if __name__ == "__main__":
    main()
