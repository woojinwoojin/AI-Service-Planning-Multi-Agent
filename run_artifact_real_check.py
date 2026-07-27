"""Artifact 읽기 전환의 실 LLM 검증 실행 (로드맵 2-2 PR 5f).

주제 N개 × 읽기 모드 3종을 **실제 LLM 으로** 돌려 다음을 확인한다.
  1) 실제 내용으로도 Dual Write 정합성(`artifact_parity`)이 유지되는가
  2) 실전에서 폴백이 정말 0 인가(`shadow_fallbacks` — 전환 판단의 실제 근거)
  3) 세 모드에서 품질·비용에 하락이 없는가(동일성 아님 — LLM 은 확률적이다)
  4) 같은 State 를 세 모드로 읽었을 때 **프롬프트가 정확히 같은가**(결정적·비용 0)

실행:
    python run_artifact_real_check.py --topics 3            # 3주제 × 3모드 = 9회
    python run_artifact_real_check.py --topics 1 --modes legacy    # 스모크 1회

이어하기: (주제, 모드)별로 즉시 저장하므로 중단돼도 진행분이 보존된다
(`outputs/artifact_real_partial.json`). 조건이 바뀌면(모델·주제 수·커밋) 재사용하지 않는다.
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

from app.services import artifact_real_check as arc
from app.services.eval_set import TOPICS
from app.services.llm import default_model, is_dummy
from app.services.parallel_bench import experiment_signature

_PARTIAL = Path("outputs/artifact_real_partial.json")


def _p(msg: str) -> None:
    print(msg, flush=True)


def _load_partial(sig: dict) -> dict:
    if not _PARTIAL.exists():
        return {"signature": sig, "rows": {}, "prompt_parity": {}}
    try:
        data = json.loads(_PARTIAL.read_text(encoding="utf-8"))
    except Exception:
        return {"signature": sig, "rows": {}, "prompt_parity": {}}
    if data.get("signature") != sig:
        _p("  (이전 진행분은 실험 조건이 달라 재사용하지 않습니다)")
        return {"signature": sig, "rows": {}, "prompt_parity": {}}
    return data


def _save_partial(data: dict) -> None:
    _PARTIAL.parent.mkdir(exist_ok=True)
    _PARTIAL.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_report(rep: dict, rows: list[dict], model: str, sig: dict, workflow_mode: str) -> None:
    docs, out = Path("docs"), Path("outputs")
    docs.mkdir(exist_ok=True)
    out.mkdir(exist_ok=True)
    md = [
        "# Artifact 읽기 전환 · 실 LLM 검증 리포트\n",
        f"> 모델 `{model or '더미'}` · 실행 구조 `{workflow_mode}` · "
        f"커밋 `{sig['git_commit']}` · 총 {rep['runs']}회 · 비용 ${rep['total_cost_usd']}\n",
        "> **실 LLM 은 확률적이라 모드 간 산출물 동일성을 쓸 수 없다.** 비교는 두 층이다 — "
        "품질·비용의 **비열등성**(아래 표)과, 같은 State 를 세 모드로 읽었을 때의 "
        "**프롬프트 동일성**(결정적·비용 0).\n",
        "> 표본이 작으므로 통계가 아니라 **스모크**다. 값은 평균이 아니라 실행별로 나열한다.\n",
        "\n> ⚠️ **`사실 검증률`은 모드 비교에 쓰지 말 것.** 이 지표는 verify 가 문서에서 주장을 "
        "**매번 새로 추출해** 판정하므로 주장 집합 자체가 흔들린다. 같은 문서·같은 프롬프트로 "
        "9회 재판정했을 때 0.2~0.9 로 벌어졌다(모드 내 반복 폭이 모드 간 차이만큼 크다). "
        "모드 간 차이가 보이더라도 **읽기 경로가 원인일 수 없다** — 아래 '프롬프트 동일성'이 "
        "같은 State 에서 세 모드의 프롬프트가 동일함을 결정적으로 보이기 때문이다.\n",
        "\n## 요약\n",
        *[f"- {ln}" for ln in arc.summary_lines(rep)],
        "\n## 모드별 비교\n",
        "| 항목 | " + " | ".join(arc.MODES) + " |",
        "|---|" + "---|" * len(arc.MODES),
    ]
    keys = [
        ("실행 수", "runs"), ("run_status", "run_status"),
        ("failed 노드 합", "failed_nodes_total"), ("fallback 노드 합", "fallback_nodes_total"),
        ("parity 전부 ok", "parity_ok_all"), ("parity 불일치 사유", "parity_reasons"),
        ("Artifact status", "artifact_statuses"),
        ("읽기 수", "reads_total"), ("그중 Artifact", "reads_from_artifact"),
        ("실제 폴백", "fallbacks"), ("폴백 사유", "fallback_reasons"),
        ("shadow 폴백", "shadow_fallbacks"), ("shadow 사유", "shadow_reasons"),
        ("14섹션 전부 완전", "sections_complete_all"), ("빈 섹션 합", "empty_sections_total"),
        ("고유 출처 URL", "unique_source_urls"), ("총점", "total_score"),
        ("사실 검증률", "fact_support_rate"),
        ("LLM 호출", "calls"), ("LLM fallback 합", "fallback_calls_total"),
        ("비용(USD)", "est_cost_usd"), ("wall(ms)", "wall_time_ms"),
    ]
    for label, key in keys:
        cells = []
        for m in arc.MODES:
            v = (rep["by_mode"].get(m) or {}).get(key, "—")
            cells.append(json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v))
        md.append(f"| {label} | " + " | ".join(cells) + " |")

    md += ["\n## 프롬프트 동일성(결정적·비용 0)\n",
           "실제 실행이 남긴 State 를 고정하고 세 모드로 각 소비자의 프롬프트를 만들어 대조한다. "
           "State 가 고정이면 프롬프트 생성은 결정적이므로 **정확히 같아야 한다** — "
           "읽기 경로가 갈리면 반드시 여기서 잡힌다.\n",
           "| 주제 | 소비자 수 | 결과 | 불일치 | 프롬프트 0건 |", "|---|---|---|---|---|"]
    for topic, v in rep["prompt_parity"].items():
        bad = json.dumps(v["mismatched"], ensure_ascii=False) if v["mismatched"] else "—"
        emp = ", ".join(v["empty"]) if v["empty"] else "—"
        md.append(f"| {topic} | {v['checked']} | {'✅ 동일' if v['ok'] else '❌ 불일치'} "
                  f"| {bad} | {emp} |")

    md += ["\n## 실행별 원자료\n",
           "| 주제 | 모드 | status | parity | 읽기(Artifact) | 폴백 | shadow | 섹션 | 점수 | $ |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        md.append(
            f"| {r['topic']} | {r['read_mode']} | {r['run_status']} | {r['parity_ok']} "
            f"| {r['reads_total']}({r['reads_from_artifact']}) | {r['fallbacks']} "
            f"| {r['shadow_fallbacks']} | {r['sections_present']}/14 | {r['total_score']} "
            f"| {r['est_cost_usd']} |")

    (docs / "artifact_real_check.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    (out / "artifact_real_check.json").write_text(
        json.dumps({"signature": sig, "workflow_mode": workflow_mode,
                    "report": rep, "rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="Artifact 읽기 전환 실 LLM 검증")
    ap.add_argument("--topics", type=int, default=3, help="사용할 주제 수(기본 3)")
    ap.add_argument("--modes", nargs="*", default=arc.MODES, help="읽기 모드(기본 3종 전부)")
    ap.add_argument("--workflow-mode", default="parallel", choices=["serial", "parallel"],
                    help="실행 구조(기본 parallel — fan-out 경계까지 함께 본다)")
    ap.add_argument("--model", default=None, help="모델(기본: 환경 기본값)")
    args = ap.parse_args()

    if is_dummy():
        _p("⚠ 더미 모드(USE_DUMMY=1 또는 키 없음)입니다 — 이 스크립트의 목적(실 LLM 확인)에 맞지 않습니다.")
    model = "" if is_dummy() else (args.model or default_model())
    topics = TOPICS[: args.topics]
    sig = experiment_signature(topics, 1, model)
    sig["read_modes"] = list(args.modes)
    sig["workflow_mode"] = args.workflow_mode

    total = len(topics) * len(args.modes)
    _p("=" * 70)
    _p(f"Artifact 실 LLM 검증 · 주제 {len(topics)} × 모드 {len(args.modes)} = {total}회 "
       f"· 모델={model or '더미'} · 구조={args.workflow_mode}")
    _p("=" * 70)

    data = _load_partial(sig)
    done = len(data["rows"])
    if done:
        _p(f"  이어하기: 이미 {done}/{total}회 완료됨")

    for topic in topics:
        tid = topic.get("id") or topic.get("project_name", "")
        for mode in args.modes:
            key = f"{tid}|{mode}"
            if key in data["rows"]:
                continue
            _p(f"  실행 {len(data['rows']) + 1}/{total}: {tid} · {mode}")
            row, state = arc.run_topic(topic, mode, args.workflow_mode)
            data["rows"][key] = row
            # 프롬프트 동일성은 주제당 1회면 충분하다(State 고정 검사라 모드마다 할 필요 없음).
            if tid not in data["prompt_parity"]:
                data["prompt_parity"][tid] = arc.prompt_parity(state)
                pp = data["prompt_parity"][tid]
                _p(f"    프롬프트 동일성: {'✅' if pp['ok'] else '❌ ' + json.dumps(pp, ensure_ascii=False)}")
            _save_partial(data)
            _p(f"    status={row['run_status']} parity={row['parity_ok']} "
               f"읽기={row['reads_total']}(Artifact {row['reads_from_artifact']}) "
               f"폴백={row['fallbacks']} shadow={row['shadow_fallbacks']} "
               f"점수={row['total_score']} ${row['est_cost_usd']}")

    rows = list(data["rows"].values())
    rep = arc.summarize(rows, data["prompt_parity"])
    _write_report(rep, rows, model, sig, args.workflow_mode)

    _p("\n" + "=" * 70)
    for ln in arc.summary_lines(rep):
        _p("  " + ln)
    _p("\n저장: docs/artifact_real_check.md · outputs/artifact_real_check.json")
    _p("=" * 70)


if __name__ == "__main__":
    main()
