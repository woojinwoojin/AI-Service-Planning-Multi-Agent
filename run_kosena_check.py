"""KOSENA 방법론 준수 현황 점검 (체크포인트 3).

워크플로를 1회 실행하고 `kosena.evaluate` 판정을 사람이 읽을 형태로 출력한다.
**판정만 한다 — 내용을 만들지 않는다.** 무엇이 빠졌는지 항목 단위로 보여 주는 것이 목적이다.

실행:
    python run_kosena_check.py            # 더미(무비용). 구조 점검용
    python run_kosena_check.py --real     # 실 LLM 1회(비용 ~$0.012). 출처 항목까지 실측
    python run_kosena_check.py --json out.json

더미에서는 웹검색을 하지 않으므로 '출처 명시'가 미충족으로 나온다 — 이는 파이프라인의 결함이
아니라 더미의 한계이고, `--real` 로 확인할 수 있다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass


def main() -> None:
    ap = argparse.ArgumentParser(description="KOSENA 준수 현황 점검")
    ap.add_argument("--real", action="store_true", help="실 LLM 사용(비용 발생). 기본은 더미")
    ap.add_argument("--topic", type=int, default=0, help="eval_set 주제 인덱스")
    ap.add_argument("--json", default="", help="판정 결과를 JSON 으로 저장할 경로")
    args = ap.parse_args()

    if not args.real:
        os.environ["USE_DUMMY"] = "1"

    from app.graph.workflow import run_workflow
    from app.services import kosena
    from app.services.eval_set import TOPICS
    from app.services.llm import default_model, is_dummy

    topic = dict(TOPICS[args.topic % len(TOPICS)])
    topic.pop("id", None)
    print("=" * 70)
    print(f"KOSENA 준수 점검 · {'실 LLM ' + (default_model() or '') if not is_dummy() else '더미(무비용)'}"
          f" · 주제={topic.get('project_name')}")
    print("=" * 70, flush=True)

    state = run_workflow(topic)
    result = state.get("kosena_compliance") or kosena.evaluate(state)

    for line in kosena.report_lines(result):
        print(line)

    print()
    print(f"모듈별: {json.dumps(result['by_module'], ensure_ascii=False)}")
    if not args.real:
        print("\n※ 더미라 '출처 명시'는 미충족으로 나온다(웹검색 미수행). --real 로 확인할 것.")
    if args.json:
        Path(args.json).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        print(f"\n저장: {args.json}")
    print("\n상세 매핑·보완 방향: docs/kosena-compliance.md")


if __name__ == "__main__":
    main()
