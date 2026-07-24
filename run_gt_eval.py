"""신뢰도 Tier 2 — Ground Truth 스모크셋 평가 실행 (로드맵 Phase 3).

균형 GT 세트(app/services/gt_eval.GT_SET, 10건)로 verifier 판정 품질을 측정하고,
결과를 n/N 형태로 리포트(docs/gt_report.md)·원자료(outputs/gt.json)로 저장한다.

실행:
    python run_gt_eval.py                 # 실제 LLM(비용 소액) — 기본 모델
    python run_gt_eval.py --model gpt-4o-mini

주의: 실제 LLM 을 호출한다(주장 10건 × 1콜). 더미 모드(USE_DUMMY=1)면 전부 uncertain 으로
판정돼 의미 있는 수치가 나오지 않으므로 경고만 출력한다(세트·집계 검증은 테스트가 담당).
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

from app.services import gt_eval
from app.services.llm import default_model, is_dummy


def _p(msg: str) -> None:
    print(msg, flush=True)


def _write_report(rep: dict, model: str) -> None:
    docs, out = Path("docs"), Path("outputs")
    docs.mkdir(exist_ok=True)
    out.mkdir(exist_ok=True)
    md = [
        "# 신뢰도 Tier 2 · Ground Truth 스모크셋 리포트\n",
        f"> 모델: `{model or '더미'}` · 표본 {rep['n']}건(균형 세트) · 판정=verifier.judge_claim\n",
        "> 비율은 백분율이 아니라 n/N 로 보고한다(표본 수를 함께 봐야 오해가 없음).\n",
        "## 지표\n",
        *[f"- {ln}" for ln in gt_eval.summary_lines(rep)],
        "\n## 항목별 판정\n",
        "| id | 분류 | 기대(유형/상태) | 예측(유형/상태) | 주장 |",
        "|---|---|---|---|---|",
    ]
    for r in rep["results"]:
        exp = f"{r['expected_claim_type']}/{r['expected_status']}"
        pred = f"{r['pred_claim_type']}/{r['pred_status']}"
        md.append(f"| {r['id']} | {r['category']} | {exp} | {pred} | {r['claim']} |")
    (docs / "gt_report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    (out / "gt.json").write_text(
        json.dumps({"model": model, "report": rep}, ensure_ascii=False, indent=2),
        encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="신뢰도 Tier 2 GT 스모크셋 평가")
    ap.add_argument("--model", default=None, help="판정 모델(기본: 환경 기본값)")
    args = ap.parse_args()

    if is_dummy():
        _p("⚠ 더미 모드(USE_DUMMY=1 또는 키 없음): 전부 uncertain 판정 → 수치 무의미.")
        _p("  실측하려면 API 키를 설정하고 다시 실행하세요.")
    model = "" if is_dummy() else (args.model or default_model())

    _p("=" * 60)
    _p(f"GT 스모크셋 평가 · 표본 {len(gt_eval.GT_SET)}건 · 모델={model or '더미'}")
    _p("=" * 60)

    results = gt_eval.evaluate(model=model)
    rep = gt_eval.report(results)
    _write_report(rep, model)

    for ln in gt_eval.summary_lines(rep):
        _p("  " + ln)
    _p("\n저장: docs/gt_report.md · outputs/gt.json")
    _p("=" * 60)


if __name__ == "__main__":
    main()
