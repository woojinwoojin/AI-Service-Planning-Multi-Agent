"""신뢰도 Tier 2 — Ground Truth 스모크셋(균형 10건)으로 verifier 판정 품질 측정.

전략 문서(docs/정보신뢰성_전략.md §3 Tier 2-8)의 방침을 그대로 따른다:
- 대규모 GT(30~50건) 대신 **소규모 균형 세트**로 발표/데모 범위에 맞춘다.
- 결과는 백분율만 제시하지 않고 **n/N 형태**로 보고한다("허위 통과 1/4" 처럼).
- 세트를 근거 유형별로 **균형** 구성해, verifier 가 전부 uncertain 으로 도망가 점수를 따는
  문제까지 검출한다: supported 2 · unsupported 2 · contradicted 2 · uncertain 1 · 비-사실 3.

측정 지표(무엇을 신뢰할 수 있는지 정직하게):
- **false_pass(허위 통과)**: 근거가 없거나(unsupported) 반대인데(contradicted) supported 로 통과 → 가장 위험. 낮을수록 좋음.
- **contradicted_detected**: 반대 근거를 실제로 contradicted 로 잡아낸 비율(Tier 2 핵심 능력).
- **claim_type_accuracy**: 사실/추론/제안 유형 분류 정확도.
- **non_fact_not_verified**: 추론·제안을 근거 검증 대상에서 제외(not_applicable)했는지.

주의: evaluate() 는 실제 LLM 을 호출한다(비용). 세트·집계·리포트 로직은 무료로 테스트 가능하다.
"""
from __future__ import annotations

from app.agents import verifier
from app.services import evidence

# 근거는 search.build_source_objects 형식(title/url/snippet/source_type)으로 둔다.
# evaluate() 가 evidence.entries_from 으로 레지스트리 원시 항목으로 변환해 judge_claim 에 넘긴다.
GT_SET: list[dict] = [
    # ── 사실 · 근거로 뒷받침됨(supported) ────────────────────────────────
    {"id": "g1", "category": "supported", "claim": "국내 반려동물 양육 가구가 증가하는 추세다",
     "expected_claim_type": "fact", "expected_status": "supported",
     "evidence": [{"title": "반려동물 실태 보고", "url": "https://ex.go.kr/pet",
                   "snippet": "국내 반려동물 양육 가구가 매년 꾸준히 증가하고 있다", "source_type": "government"}]},
    {"id": "g2", "category": "supported", "claim": "전기차 판매량이 늘고 있다",
     "expected_claim_type": "fact", "expected_status": "supported",
     "evidence": [{"title": "전기차 시장 동향", "url": "https://ex.news/ev",
                   "snippet": "전기차 판매량이 전년 대비 크게 늘었다", "source_type": "news"}]},
    # ── 사실 · 근거에서 확인 안 됨(unsupported = 근거 미확인) ─────────────
    {"id": "g3", "category": "unsupported", "claim": "본 앱 사용자의 90%가 만족한다고 응답했다",
     "expected_claim_type": "fact", "expected_status": "unsupported",
     "evidence": []},                                    # 근거 자체가 없음
    {"id": "g4", "category": "unsupported", "claim": "이 시장에는 경쟁 서비스가 전혀 없다",
     "expected_claim_type": "fact", "expected_status": "unsupported",
     "evidence": [{"title": "시장 개요", "url": "https://ex.news/market",
                   "snippet": "시장이 성장하며 다양한 서비스가 등장하고 있다", "source_type": "news"}]},
    # ── 사실 · 근거가 주장과 반대(contradicted = 반대 근거) ───────────────
    {"id": "g5", "category": "contradicted", "claim": "해당 시장 규모는 매년 감소하고 있다",
     "expected_claim_type": "fact", "expected_status": "contradicted",
     "evidence": [{"title": "시장 규모 통계", "url": "https://ex.go.kr/size",
                   "snippet": "해당 시장 규모는 매년 성장하고 있다", "source_type": "government"}]},
    {"id": "g6", "category": "contradicted", "claim": "이 서비스의 사용자 이탈률은 업계 평균보다 낮다",
     "expected_claim_type": "fact", "expected_status": "contradicted",
     "evidence": [{"title": "이탈률 분석", "url": "https://ex.ac.kr/churn",
                   "snippet": "이 서비스의 이탈률은 업계 평균보다 높은 편이다", "source_type": "academic"}]},
    # ── 사실 · 근거 불충분으로 판단 불가(uncertain) ──────────────────────
    {"id": "g7", "category": "uncertain", "claim": "향후 관련 규제가 완화될 가능성이 있다",
     "expected_claim_type": "fact", "expected_status": "uncertain",
     "evidence": [{"title": "업계 뉴스", "url": "https://ex.news/reg",
                   "snippet": "규제 방향에 대한 논의가 진행 중이다", "source_type": "news"}]},
    # ── 비-사실(추론/제안) · 근거 검증 대상 아님(not_applicable) ──────────
    {"id": "g8", "category": "inference",
     "claim": "따라서 본 서비스는 시장에서 성공할 것으로 예상된다",
     "expected_claim_type": "inference", "expected_status": "not_applicable", "evidence": []},
    {"id": "g9", "category": "proposal", "claim": "본 서비스는 AI 기반 맞춤 추천 기능을 제공한다",
     "expected_claim_type": "proposal", "expected_status": "not_applicable", "evidence": []},
    {"id": "g10", "category": "proposal", "claim": "월 9,900원 구독 모델로 제공할 계획이다",
     "expected_claim_type": "proposal", "expected_status": "not_applicable", "evidence": []},
]


def evaluate(model: str = "", subset: list[dict] | None = None) -> list[dict]:
    """각 GT 항목을 verifier.judge_claim 으로 판정하고 (기대 vs 예측)을 모은다. 실제 LLM 호출."""
    items = subset if subset is not None else GT_SET
    results: list[dict] = []
    for it in items:
        reg = evidence.entries_from("gt", "", it.get("evidence") or [])
        pred = verifier.judge_claim(it["claim"], reg, model)
        results.append({
            "id": it["id"], "category": it["category"], "claim": it["claim"],
            "expected_claim_type": it["expected_claim_type"],
            "expected_status": it["expected_status"],
            "pred_claim_type": pred.get("claim_type"),
            "pred_status": pred.get("status"),
        })
    return results


def _frac(hit: list, total: list) -> str:
    """n/N 문자열(백분율 대신 표본 수까지 보이도록 — 전략 문서 요구)."""
    return f"{len(hit)}/{len(total)}"


def report(results: list[dict]) -> dict:
    """판정 결과를 신뢰도 지표로 집계한다. 비율은 전부 n/N 형태로 돌려준다."""
    facts = [r for r in results if r["expected_claim_type"] == "fact"]
    non_facts = [r for r in results if r["expected_claim_type"] != "fact"]
    should_block = [r for r in facts if r["expected_status"] in ("unsupported", "contradicted")]
    false_pass = [r for r in should_block if r["pred_status"] == "supported"]
    should_pass = [r for r in facts if r["expected_status"] == "supported"]
    correct_pass = [r for r in should_pass if r["pred_status"] == "supported"]
    contra = [r for r in facts if r["expected_status"] == "contradicted"]
    contra_detected = [r for r in contra if r["pred_status"] == "contradicted"]
    type_correct = [r for r in results if r["pred_claim_type"] == r["expected_claim_type"]]
    non_fact_ok = [r for r in non_facts if r["pred_status"] == "not_applicable"]
    return {
        "n": len(results),
        "false_pass": _frac(false_pass, should_block),           # 낮을수록 좋음(허위 통과)
        "correct_pass": _frac(correct_pass, should_pass),        # 근거 있는 주장 통과
        "contradicted_detected": _frac(contra_detected, contra),  # 반대 근거 탐지
        "claim_type_accuracy": _frac(type_correct, results),     # 유형 분류 정확도
        "non_fact_not_verified": _frac(non_fact_ok, non_facts),  # 추론·제안 검증 제외
        "results": results,
    }


def summary_lines(rep: dict) -> list[str]:
    """리포트를 사람이 읽는 n/N 요약 줄로 만든다(CLI·문서 공통)."""
    return [
        f"표본: {rep['n']}건 (균형 세트)",
        f"허위 통과(위험): {rep['false_pass']}  — 근거 없음·반대인데 supported 로 통과",
        f"반대 근거 탐지: {rep['contradicted_detected']}",
        f"근거 있는 주장 통과: {rep['correct_pass']}",
        f"주장 유형 분류 정확: {rep['claim_type_accuracy']}",
        f"추론·제안 검증 제외(not_applicable): {rep['non_fact_not_verified']}",
    ]
