"""PR-8 조건부 Polish 품질 검증 실행 — Polish 생략이 읽기 품질을 해치는지 블라인드로 확인.

고정 평가 세트(eval_set.TOPICS) 앞 N개를 실행해, Polish 가 생략된 최종본과 그 문서에 Polish 를
적용한 편집본을 블라인드 A/B 로 비교하고, 결과를 n/N 로 리포트(docs/polish_quality_report.md)한다.

실행:
    python run_polish_eval.py --topics 4      # 실제 LLM(주제당 생성+편집+심판)

주의: 실제 LLM 을 호출한다. 더미 모드면 Polish 자체가 no-op 이라 비교가 무의미하므로 경고만 낸다.
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

from app.services import eval_set, polish_eval
from app.services.llm import default_model, is_dummy


def _p(msg: str) -> None:
    print(msg, flush=True)


def _write_report(rep: dict, model: str) -> None:
    docs, out = Path("docs"), Path("outputs")
    docs.mkdir(exist_ok=True)
    out.mkdir(exist_ok=True)
    md = [
        "# PR-8 조건부 Polish 품질 검증 (블라인드 A/B)\n",
        f"> 모델: `{model or '더미'}` · Polish 생략본 vs 편집본 블라인드 비교 · 표현 품질(일관성·흐름·중복)만\n",
        "> 편집본이 생략본을 꾸준히 이기지 못하면 = Polish 생략의 품질 손해가 작다(생략 안전).\n",
        "## 지표\n",
        *[f"- {ln}" for ln in polish_eval.summary_lines(rep)],
        "\n## 주제별 판정\n",
        "| topic | 실행 Polish | 비교 | 승자 | 사유 |",
        "|---|---|---|---|---|",
    ]
    for r in rep["results"]:
        md.append(f"| {r['topic']} | {'실행' if r['polish_applied_in_run'] else '생략'} | "
                  f"{'O' if r.get('compared') else '-'} | {r.get('winner', '-')} | {r.get('reason', '')} |")
    (docs / "polish_quality_report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    (out / "polish_quality.json").write_text(
        json.dumps({"model": model, "report": rep}, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="PR-8 조건부 Polish 품질 검증")
    ap.add_argument("--topics", type=int, default=4, help="앞에서부터 N개 주제(기본 4)")
    ap.add_argument("--model", default=None, help="생성·편집·심판 모델(기본: 환경 기본값)")
    args = ap.parse_args()

    if is_dummy():
        _p("⚠ 더미 모드: Polish 가 no-op 이라 비교가 무의미합니다. 실 LLM 키로 실행하세요.")
    model = "" if is_dummy() else (args.model or default_model())
    topics = eval_set.get_topics(args.topics)

    _p("=" * 60)
    _p(f"Polish 품질 검증 · 주제 {len(topics)}개 · 모델={model or '더미'}")
    _p("=" * 60)

    results = polish_eval.evaluate(topics, model=model)
    rep = polish_eval.report(results)
    _write_report(rep, model)

    for ln in polish_eval.summary_lines(rep):
        _p("  " + ln)
    _p("\n저장: docs/polish_quality_report.md · outputs/polish_quality.json")
    _p("=" * 60)


if __name__ == "__main__":
    main()
