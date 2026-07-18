"""다중 모델 비교실험 — 모델 등급별로 단일 vs Multi-Agent 격차를 측정.

핵심 설계: 생성 모델은 바꿔가며(gpt-4o-mini, gpt-4o …), 심판은 하나로 '고정'해
공정하게 비교한다. "모델이 강해져도 Multi-Agent 우위가 유지되는가?"를 본다.

- 주제 3개(비용/시간 감안) · 심판 고정(JUDGE_MODEL)
- 이어하기: (모델,주제)별로 즉시 저장 → 중단돼도 진행분 보존, 재실행 시 이어감
- 결과: docs/multimodel_result.md, outputs/multimodel.json

실행: python run_multimodel.py
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
from app.services.llm import is_dummy

# 생성 모델 목록(현재 provider의 허용 모델이어야 함). 심판은 아래 하나로 고정.
GEN_MODELS = ["gpt-4o-mini", "gpt-4o"]
JUDGE_MODEL = "gpt-4o-mini"

TOPICS = [
    {"project_name": "AI 기반 대학생 진로 설계 서비스",
     "description": "전공·역량·관심 직무를 분석해 학습/취업 로드맵을 제공",
     "target_user": "진로를 고민하는 대학생", "problem": "자신의 역량과 진로에 맞는 준비 방법을 찾기 어렵다",
     "keywords": ["진로", "대학생", "취업"]},
    {"project_name": "소상공인 AI 재고관리 SaaS",
     "description": "판매 패턴을 학습해 발주 시점을 추천", "target_user": "동네 소매점 사장",
     "problem": "재고 과잉과 품절이 반복된다", "keywords": ["재고", "소상공인", "수요예측"]},
    {"project_name": "AI 시니어 복약 관리 알림",
     "description": "복약 시간을 음성으로 알리고 보호자에게 확인 전송", "target_user": "만성질환 고령자와 보호자",
     "problem": "복약 누락·중복으로 인한 건강 위험", "keywords": ["헬스케어", "고령자", "복약"]},
]

_PARTIAL = Path("outputs/multimodel_partial.json")


def _p(msg: str) -> None:
    print(msg, flush=True)


def _avg(results: list[dict], side: str, field: str) -> float:
    return round(sum(r[side]["judge"][field] if field != "citations" else r[side]["citations"]
                     for r in results) / len(results), 1)


def main() -> None:
    if is_dummy():
        _p("더미 모드입니다. 실제 LLM 키가 필요합니다."); return

    _PARTIAL.parent.mkdir(exist_ok=True)
    cache = json.loads(_PARTIAL.read_text(encoding="utf-8")) if _PARTIAL.exists() else {}

    _p("=" * 64)
    _p(f"다중 모델 비교 · 생성 모델 {GEN_MODELS} · 심판(고정) {JUDGE_MODEL} · 주제 {len(TOPICS)}개")
    _p("=" * 64)

    for gen in GEN_MODELS:
        for topic in TOPICS:
            key = f"{gen}::{topic['project_name']}"
            if key in cache:
                _p(f"[{gen}] 이미 완료: {topic['project_name']}")
                continue
            _p(f"[{gen}] {topic['project_name']} 실행 중…")
            cache[key] = compare.run_topic(topic, model=gen, judge_model=JUDGE_MODEL)
            _PARTIAL.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
            _p(f"    저장 완료 ({len(cache)}/{len(GEN_MODELS) * len(TOPICS)})")

    total = len(GEN_MODELS) * len(TOPICS)
    if len(cache) < total:
        _p(f"\n부분 완료 {len(cache)}/{total} — 다시 실행하면 이어서 진행합니다.")
        return

    # 모델별 집계
    rows = []
    for gen in GEN_MODELS:
        res = [cache[f"{gen}::{t['project_name']}"] for t in TOPICS]
        s = _avg(res, "single", "total")
        m = _avg(res, "multi", "total")
        wins = sum(1 for r in res if r["multi"]["judge"]["total"] > r["single"]["judge"]["total"])
        rows.append({"model": gen, "single": s, "multi": m, "delta": round(m - s, 1),
                     "multi_citations": _avg(res, "multi", "citations"),
                     "single_citations": _avg(res, "single", "citations"),
                     "multi_wins": wins, "topics": len(res)})

    md = ["# 다중 모델 비교실험 결과\n",
          f"> 생성 모델별 단일 vs Multi-Agent · 심판 고정 `{JUDGE_MODEL}` · 주제 {len(TOPICS)}개 · 심판 {compare.JUDGE_SAMPLES}회 평균\n",
          "| 생성 모델 | 단일 총점 | Multi 총점 | 차이 | Multi 우위 | 출처 수(단일/Multi) |",
          "|---|---|---|---|---|---|"]
    for r in rows:
        md.append(f"| {r['model']} | {r['single']} | {r['multi']} | {r['delta']:+.1f} | "
                  f"{r['multi_wins']}/{r['topics']} | {r['single_citations']} / {r['multi_citations']} |")
    md.append("\n> 심판을 고정했으므로 행 간 비교가 공정합니다. 생성 모델이 강해질 때 단일-멀티 격차 변화를 확인하세요.\n")

    Path("docs").mkdir(exist_ok=True)
    (Path("docs") / "multimodel_result.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    (Path("outputs") / "multimodel.json").write_text(
        json.dumps({"judge_model": JUDGE_MODEL, "rows": rows, "raw": cache}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    _PARTIAL.unlink(missing_ok=True)

    _p("\n" + "\n".join(md))
    _p("\n저장: docs/multimodel_result.md · outputs/multimodel.json")
    _p("=" * 64)


if __name__ == "__main__":
    main()
