"""PR-8 조건부 Polish 품질 검증 — Polish 생략이 '읽기 품질'을 해치는지 블라인드로 확인.

방법: PR-8이 Polish 를 생략한 실제 최종본(=생략본)과, 그 문서에 Polish 를 적용한 버전(=편집본)을
**블라인드 A/B**로 심판에게 비교시킨다(어느 쪽이 편집본인지 모르고, 위치도 주제마다 교차). 편집본이
생략본을 꾸준히 이기지 못하면 = Polish 를 건너뛴 손해가 작다는 뜻(생략이 안전).

- 오직 표현 품질(일관성·흐름·중복)만 비교한다 — 두 문서는 내용이 사실상 같으므로.
- 실제 실행에서 Polish 가 '실행된' 주제는 생략 비교 대상이 아니므로 제외한다(compared=False).

주의: evaluate() 는 실제 LLM 을 호출한다(주제당 생성+편집+심판). 집계·블라인드 매핑은 무료로 테스트 가능.
"""
from __future__ import annotations

from app.agents import draft_writer
from app.graph.workflow import run_workflow
from app.prompts.templates import POLISH_JUDGE
from app.services import llm


def _force_polish(state: dict, model: str) -> str:
    """생략본에 Polish 를 강제 적용한 편집본을 만든다(revision_strategy=full 로 실행 강제)."""
    forced = dict(state)
    forced["revision_strategy"] = "full"        # 생략 조건을 무시하고 Polish 를 실행시킨다
    forced["model"] = model
    out = draft_writer.polish(forced)
    return out.get("final_draft") or state.get("final_draft", "")


def judge_pair(skipped: str, polished: str, model: str, swap: bool) -> dict:
    """생략본 vs 편집본을 블라인드로 비교. swap 으로 A/B 위치를 교차해 위치 편향을 제거한다.

    심판은 어느 쪽이 편집본인지 모른다. 반환 winner 는 'skipped'|'polished'|'tie' 로 되돌려 매핑한다.
    """
    if swap:
        doc_a, doc_b, amap = polished, skipped, {"A": "polished", "B": "skipped"}
    else:
        doc_a, doc_b, amap = skipped, polished, {"A": "skipped", "B": "polished"}
    user = f"[문서A]\n{doc_a}\n\n[문서B]\n{doc_b}"
    raw = llm.complete_json(POLISH_JUDGE, user, fallback={"winner": "tie"}, model=model)
    w = raw.get("winner") if isinstance(raw, dict) else "tie"
    winner = amap.get(w, "tie") if w in ("A", "B") else "tie"
    reason = raw.get("reason", "") if isinstance(raw, dict) else ""
    return {"winner": winner, "reason": reason}


def evaluate(topics: list[dict], model: str = "") -> list[dict]:
    """각 주제를 실행해 Polish 생략본과 편집본을 블라인드 비교한다. 실제 LLM 호출."""
    results: list[dict] = []
    for i, topic in enumerate(topics):
        state = run_workflow(dict(topic))
        applied = bool(state.get("polish_applied", True))
        row: dict = {"topic": topic.get("project_name", f"t{i}"),
                     "polish_applied_in_run": applied}
        if applied:
            row["compared"] = False        # 실제 실행에서 Polish 가 돌았음 → 생략 비교 대상 아님
        else:
            skipped = state.get("final_draft", "")
            polished = _force_polish(state, model)
            verdict = judge_pair(skipped, polished, model, swap=bool(i % 2))
            row.update({"compared": True, "winner": verdict["winner"], "reason": verdict["reason"]})
        results.append(row)
    return results


def report(results: list[dict]) -> dict:
    """블라인드 비교 결과를 n/N 로 집계. polished_wins 가 낮을수록 Polish 생략이 안전."""
    compared = [r for r in results if r.get("compared")]

    def frac(winner: str) -> str:
        return f"{sum(1 for r in compared if r['winner'] == winner)}/{len(compared)}"

    return {
        "n_total": len(results),
        "n_compared": len(compared),
        "polished_wins": frac("polished"),   # 편집본이 낫다 = Polish 생략이 손해
        "skipped_wins": frac("skipped"),      # 생략본이 낫다
        "ties": frac("tie"),                  # 차이 없음
        "results": results,
    }


def summary_lines(rep: dict) -> list[str]:
    return [
        f"비교 대상: {rep['n_compared']}/{rep['n_total']}건 (실행에서 Polish 가 생략된 것만 비교)",
        f"편집본 우세(=생략이 손해): {rep['polished_wins']}",
        f"생략본 우세: {rep['skipped_wins']}",
        f"차이 없음(tie): {rep['ties']}",
    ]
