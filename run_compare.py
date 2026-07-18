"""단일 LLM vs Multi-Agent 비교실험 실행 (10일 차, 발표 하이라이트).

주제 3개를 두 방식으로 생성 → 같은 심판으로 채점 → 점수표를 만든다.
결과 저장: docs/comparison_result.md (발표용 표) + outputs/comparison.json (원자료).

실행: python run_compare.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

from app.services import compare
from app.services.llm import default_model, is_dummy

# 로드맵: 주제 3개, 동일 평가 기준
TOPICS = [
    {
        "project_name": "AI 기반 대학생 진로 설계 서비스",
        "description": "전공·역량·관심 직무를 분석해 학습/취업 로드맵을 제공",
        "target_user": "진로를 고민하는 대학생",
        "problem": "자신의 역량과 진로에 맞는 준비 방법을 찾기 어렵다",
        "keywords": ["진로", "대학생", "취업"],
    },
    {
        "project_name": "소상공인 AI 재고관리 SaaS",
        "description": "판매 패턴을 학습해 발주 시점을 추천",
        "target_user": "동네 소매점 사장",
        "problem": "재고 과잉과 품절이 반복된다",
        "keywords": ["재고", "소상공인", "수요예측"],
    },
    {
        "project_name": "AI 시니어 복약 관리 알림",
        "description": "복약 시간을 음성으로 알리고 보호자에게 확인 전송",
        "target_user": "만성질환 고령자와 보호자",
        "problem": "복약 누락·중복으로 인한 건강 위험",
        "keywords": ["헬스케어", "고령자", "복약"],
    },
]


def _table_md(table: dict) -> str:
    lines = [
        "| 평가 항목 | 단일 Agent | Multi-Agent |",
        "|---|---|---|",
    ]
    for key, label in compare.CRITERIA.items():
        row = table[key]
        lines.append(f"| {label} | {row['single']} | {row['multi']} |")
    t = table["total"]
    lines.append(f"| **총점 (LLM 심판)** | **{t['single']}** | **{t['multi']}** |")
    c = table["citations"]
    lines.append(f"| **검증 가능한 실제 출처 수 (객관)** | **{c['single']}** | **{c['multi']}** |")
    return "\n".join(lines)


def main() -> None:
    model = "" if is_dummy() else default_model()
    print("=" * 64)
    print(f"단일 vs Multi-Agent 비교 · 주제 {len(TOPICS)}개 · 모델={model or '더미'}")
    print("=" * 64)

    results = []
    for i, topic in enumerate(TOPICS, 1):
        print(f"[{i}/{len(TOPICS)}] {topic['project_name']} 실행 중…")
        results.append(compare.run_topic(topic, model=model))

    table = compare.aggregate(results)
    table_md = _table_md(table)
    print("\n" + table_md)

    # 발표용 Markdown 저장
    docs = Path("docs")
    docs.mkdir(exist_ok=True)
    md = [
        "# 단일 LLM vs Multi-Agent 비교실험 결과\n",
        f"> 주제 {len(TOPICS)}개 · 모델 `{model or '더미'}` · 같은 심판(COMPARE_JUDGE)·같은 5개 기준으로 채점\n",
        "## 평균 점수표\n",
        table_md,
        "\n## 주제별 총점\n",
        "| 주제 | 단일 | Multi | 차이 |",
        "|---|---|---|---|",
    ]
    for r in results:
        s = r["single"]["judge"]["total"]
        m = r["multi"]["judge"]["total"]
        md.append(f"| {r['topic']} | {s} | {m} | {m - s:+d} |")
    md.append("\n## 심판 총평 (주제별)\n")
    for r in results:
        md.append(f"- **{r['topic']}**")
        md.append(f"  - 단일: {r['single']['judge']['comment']}")
        md.append(f"  - Multi: {r['multi']['judge']['comment']}")
    (docs / "comparison_result.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    # 원자료 JSON 저장
    out = Path("outputs")
    out.mkdir(exist_ok=True)
    (out / "comparison.json").write_text(
        json.dumps({"model": model, "table": table, "results": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\n저장: docs/comparison_result.md · outputs/comparison.json")
    delta = table["total"]["multi"] - table["total"]["single"]
    print(f"평균 총점 — 단일 {table['total']['single']} vs Multi {table['total']['multi']} (차이 {delta:+.1f})")
    print("=" * 64)


if __name__ == "__main__":
    main()
