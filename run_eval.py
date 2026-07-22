"""서비스 품질 평가 실행 (로드맵 v2 · Phase 1 — 개편 前 평가 기준선).

고정 평가 세트(eval_set.TOPICS)를 멀티 워크플로로 생성 → 8기준 루브릭 채점(N회, 변동성 측정)
+ 결정론적 구조 검사 → 비용/latency/실험 서명과 함께 리포트로 저장.

이후 모든 개편(Phase 2~)은 이 리포트와 '동일 세트·동일 루브릭'으로 전후 비교한다.

실행:
    python run_eval.py                      # 전체 세트, 심판 3회
    python run_eval.py --topics 5 --samples 2
    python run_eval.py --fresh              # 이어하기 캐시 무시하고 새로
    python run_eval.py --human data/human_plans.json   # 사람 기획서 보정 채점 포함

이어하기: 중단돼도 outputs/eval_partial.json 에 주제별로 저장. 실험 서명이 다르면 자동 폐기.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

from app.services import evaluation, eval_set
from app.services.llm import default_model, is_dummy

_PARTIAL = Path("outputs/eval_partial.json")


def _p(msg: str) -> None:
    print(msg, flush=True)


def _load_partial(sig: dict) -> list[dict]:
    """이어하기 캐시 로드 — 실험 서명이 일치할 때만(다른 조건 결과 혼입 방지)."""
    if not _PARTIAL.exists():
        return []
    try:
        cached = json.loads(_PARTIAL.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    if cached.get("signature") != sig:
        _p("  (이전 진행분이 다른 실험 조건 → 폐기하고 새로 시작)")
        return []
    return cached.get("results", [])


def _rubric_table(agg: dict) -> str:
    lines = ["| 기준 | 평균(20점) | 변동성(σ) |", "|---|---|---|"]
    for key, meta in eval_set.RUBRIC.items():
        c = agg["per_criterion"][key]
        lines.append(f"| {meta['label']} | {c['mean']} | {c['stdev_mean']} |")
    lines.append(f"| **총점(100환산)** | **{agg['total_100_mean']}** | {agg['judge_variability_mean']} |")
    return "\n".join(lines)


def _write_report(results: list[dict], agg: dict, sig: dict,
                  human: list[dict] | None) -> None:
    docs, out = Path("docs"), Path("outputs")
    docs.mkdir(exist_ok=True)
    out.mkdir(exist_ok=True)

    st = agg["structural"]
    cost = agg["cost"]
    md = [
        "# 서비스 품질 평가 리포트 (Phase 1 · 개편 前 기준선)\n",
        f"> 실험 서명: `{json.dumps(sig, ensure_ascii=False)}`\n",
        f"> 주제 {agg['topics']}개 · 심판 {sig['judge_samples']}회 평균 · 루브릭 `{sig['rubric_version']}`\n",
        "## 루브릭 평균 점수\n",
        _rubric_table(agg),
        "\n> 변동성(σ)은 같은 문서를 여러 번 채점했을 때의 표준편차. 클수록 평가가 불안정 → '90점 절대 신뢰 금지'.\n",
        "## 구조 품질(결정론적)\n",
        f"- 14섹션 완비율: {st['sections_complete_rate']} · 순서 정합률: {st['sections_ordered_rate']}",
        f"- PESTEL 표 포함률: {st['pestel_table_rate']} · 빈 섹션 평균: {st['empty_sections_mean']}",
        f"- 고유 출처 URL 평균: {st['unique_source_urls_mean']}\n",
        "## 비용·지연\n",
        f"- 평균 wall time: {cost['wall_time_ms_mean']} ms · 평균 토큰: {cost['total_tokens_mean']}",
        f"- 평균 비용: ${cost['est_cost_usd_mean']} · fallback 합: {cost['fallback_calls_total']}",
        f"- 실행 품질 분포: {json.dumps(agg['run_status'], ensure_ascii=False)}\n",
        "## 주제별 총점\n",
        "| id | 주제 | 총점(100) | 총점σ | 상태 |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        rb = r["rubric"]
        md.append(f"| {r['id']} | {r['topic']} | {rb['total_100']} | {rb['total_stdev']} | {r['run_status']} |")

    if human:
        md.append("\n## 사람 기준선 보정\n")
        md.append("| id | 사람 총점(100) | 모델 총점(100) | 차이(모델-사람) |")
        md.append("|---|---|---|---|")
        model_by_id = {r["id"]: r["rubric"]["total_100"] for r in results}
        for h in human:
            hv = h["rubric"]["total_100"]
            mv = model_by_id.get(h["id"])
            diff = f"{mv - hv:+.1f}" if isinstance(mv, (int, float)) else "—"
            md.append(f"| {h['id']} | {hv} | {mv if mv is not None else '—'} | {diff} |")
        md.append("\n> 사람이 쓴 좋은 기획서 대비 모델 점수가 과대/과소한지 확인해 임계값을 보정한다.\n")

    (docs / "eval_report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    (out / "eval.json").write_text(
        json.dumps({"signature": sig, "aggregate": agg, "results": results,
                    "human": human or []}, ensure_ascii=False, indent=2),
        encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="서비스 품질 평가(Phase 1)")
    ap.add_argument("--topics", type=int, default=None, help="앞에서부터 N개만(기본 전체)")
    ap.add_argument("--samples", type=int, default=evaluation.JUDGE_SAMPLES, help="플랜당 심판 횟수")
    ap.add_argument("--model", default=None, help="생성·채점 모델(기본: 환경 기본값)")
    ap.add_argument("--fresh", action="store_true", help="이어하기 캐시 무시")
    ap.add_argument("--human", default=None, help="사람 기획서 JSON([{id,plan}...]) 경로")
    args = ap.parse_args()

    model = "" if is_dummy() else (args.model or default_model())
    topics = eval_set.get_topics(args.topics)
    sig = evaluation.experiment_signature(topics, args.samples, model)

    _p("=" * 64)
    _p(f"서비스 품질 평가 · 주제 {len(topics)}개 · 심판 {args.samples}회 · 모델={model or '더미'}")
    _p(f"루브릭={sig['rubric_version']} · commit={sig['git_commit']}")
    _p("=" * 64)

    _PARTIAL.parent.mkdir(exist_ok=True)
    if args.fresh:
        _PARTIAL.unlink(missing_ok=True)
    results = _load_partial(sig)
    done = {r["id"] for r in results}

    for i, topic in enumerate(topics, 1):
        tid = topic["id"]
        if tid in done:
            _p(f"[{i}/{len(topics)}] 이미 완료: {tid}")
            continue
        _p(f"[{i}/{len(topics)}] {topic['project_name']} 평가 중…")
        results.append(evaluation.evaluate_topic(topic, model=model, samples=args.samples))
        _PARTIAL.write_text(
            json.dumps({"signature": sig, "results": results}, ensure_ascii=False, indent=2),
            encoding="utf-8")

    # 순서 안정화(고정 세트 순서대로)
    order = {t["id"]: n for n, t in enumerate(topics)}
    results.sort(key=lambda r: order.get(r["id"], 1_000))

    human = None
    if args.human:
        human_plans = json.loads(Path(args.human).read_text(encoding="utf-8"))
        _p(f"사람 기획서 {len(human_plans)}건 보정 채점 중…")
        human = evaluation.calibrate_against_human(human_plans, model=model, samples=args.samples)

    agg = evaluation.aggregate(results)
    _write_report(results, agg, sig, human)
    _PARTIAL.unlink(missing_ok=True)

    _p(f"\n총점(100환산) 평균: {agg['total_100_mean']} · 심판 변동성(σ): {agg['judge_variability_mean']}")
    _p(f"구조 완비율: {agg['structural']['sections_complete_rate']} · 평균 비용: ${agg['cost']['est_cost_usd_mean']}")
    _p("저장: docs/eval_report.md · outputs/eval.json")
    _p("=" * 64)


if __name__ == "__main__":
    main()
