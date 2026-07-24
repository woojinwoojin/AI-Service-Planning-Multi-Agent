"""신뢰도 Tier 2 GT 스모크셋 — 세트 균형·집계·judge_claim 테스트 (LLM 호출 없음).

evaluate() 는 실제 LLM 을 호출하지만, 세트 구성·리포트 수학·judge_claim 스키마는
mock 으로 결정론적으로 검증한다(무료).
"""
from __future__ import annotations

from collections import Counter

from app.agents import verifier
from app.services import evidence, gt_eval


def test_gt_set_is_balanced():
    """전략 문서 요구 균형: supported 2·unsupported 2·contradicted 2·uncertain 1·비-사실 3."""
    cats = Counter(it["category"] for it in gt_eval.GT_SET)
    assert cats["supported"] == 2
    assert cats["unsupported"] == 2
    assert cats["contradicted"] == 2
    assert cats["uncertain"] == 1
    assert cats["inference"] + cats["proposal"] == 3
    assert len(gt_eval.GT_SET) == 10
    # 비-사실 항목은 기대 상태가 not_applicable, 사실 항목은 아님
    for it in gt_eval.GT_SET:
        if it["expected_claim_type"] == "fact":
            assert it["expected_status"] in ("supported", "unsupported", "contradicted", "uncertain")
        else:
            assert it["expected_status"] == "not_applicable"


def _perfect_results() -> list[dict]:
    """모든 예측이 기대와 정확히 일치하는 합성 결과."""
    return [{"id": it["id"], "category": it["category"], "claim": it["claim"],
             "expected_claim_type": it["expected_claim_type"],
             "expected_status": it["expected_status"],
             "pred_claim_type": it["expected_claim_type"],
             "pred_status": it["expected_status"]} for it in gt_eval.GT_SET]


def test_report_perfect_predictions():
    rep = gt_eval.report(_perfect_results())
    assert rep["n"] == 10
    assert rep["false_pass"] == "0/4"                 # 차단해야 할 4건(unsupported2+contradicted2) 중 0건 오통과
    assert rep["correct_pass"] == "2/2"               # supported 2건 정상 통과
    assert rep["contradicted_detected"] == "2/2"      # 반대 근거 2건 모두 탐지
    assert rep["claim_type_accuracy"] == "10/10"
    assert rep["non_fact_not_verified"] == "3/3"      # 추론·제안 3건 모두 검증 제외


def test_report_detects_false_pass():
    """근거 없는/반대 주장을 supported 로 통과시키면 false_pass 로 잡힌다."""
    results = _perfect_results()
    # g3(unsupported)와 g5(contradicted)를 잘못 supported 로 예측
    for r in results:
        if r["id"] in ("g3", "g5"):
            r["pred_status"] = "supported"
    rep = gt_eval.report(results)
    assert rep["false_pass"] == "2/4"                 # 4건 중 2건 허위 통과
    assert rep["contradicted_detected"] == "1/2"      # g5 를 놓침


def test_judge_claim_returns_single_validated_claim(monkeypatch):
    """judge_claim 은 VERIFY_SYSTEM·_validate 를 재사용해 단일 판정 dict 를 돌려준다."""
    reg = evidence.entries_from("gt", "", [
        {"url": "https://a", "title": "t", "snippet": "s", "source_type": "news"}])

    def fake(system, user, **k):
        assert "단일 주장" in user                    # 단일 주장 프롬프트 사용
        return {"claims": [{"claim": "시장이 감소한다", "claim_type": "fact",
                            "status": "contradicted", "evidence_ids": ["ev1"]}]}
    monkeypatch.setattr(verifier.llm, "complete_json", fake)
    out = verifier.judge_claim("시장이 감소한다", reg, model="m")
    assert out["claim_type"] == "fact"
    assert out["status"] == "contradicted"
    assert out["evidence_ids"] == ["ev1"]             # 유효 evidence_id 유지


def test_judge_claim_filters_invented_evidence_ids(monkeypatch):
    """레지스트리에 없는 evidence_id 는 걸러진다(judge 경로에서도 지어내기 차단)."""
    reg = evidence.entries_from("gt", "", [
        {"url": "https://a", "title": "t", "snippet": "s", "source_type": "news"}])  # ev1 만 유효

    monkeypatch.setattr(verifier.llm, "complete_json", lambda *a, **k: {
        "claims": [{"claim": "x", "claim_type": "fact", "status": "supported",
                    "evidence_ids": ["ev1", "ev9"]}]})                      # ev9 는 지어냄
    out = verifier.judge_claim("x", reg)
    assert out["evidence_ids"] == ["ev1"]


def test_evaluate_integrates_judge(monkeypatch):
    """evaluate 는 judge_claim 예측을 기대와 나란히 모아 report 가 집계할 수 있게 한다."""
    # judge_claim 을 '항상 기대대로 맞추는' 오라클로 대체(집계 배선만 검증)
    def oracle(claim, reg, model=""):
        it = next(x for x in gt_eval.GT_SET if x["claim"] == claim)
        return {"id": "c1", "claim": claim, "claim_type": it["expected_claim_type"],
                "status": it["expected_status"], "basis": "", "evidence_ids": []}
    monkeypatch.setattr(gt_eval.verifier, "judge_claim", oracle)
    results = gt_eval.evaluate(model="")
    assert len(results) == 10
    rep = gt_eval.report(results)
    assert rep["false_pass"] == "0/4" and rep["claim_type_accuracy"] == "10/10"


def test_summary_lines_use_fractions():
    rep = gt_eval.report(_perfect_results())
    text = "\n".join(gt_eval.summary_lines(rep))
    assert "0/4" in text and "표본: 10건" in text          # n/N 형태로 보고(백분율 단독 금지)
