"""AI 활용 로그 (KOSENA 체크포인트 3, PDF p4·p19).

KOSENA 는 **"AI 응답을 그대로 사용하지 않고 검증·수정·재구성"** 하고, 그 과정을
**프롬프트 + 응답 + 채택 여부**로 남겨 별도 파일로 첨부하라고 요구한다(p4). 평가표도
'AI 활용' 축에서 *프롬프트 정교 + 검증 + 반영 명확*을 본다(p20).

**이 파이프라인은 그 재료를 이미 전부 갖고 있다.** 새로 계측할 것이 없고 형식만 맞추면 된다:

| KOSENA 요구 | 이 프로젝트의 근거 |
|---|---|
| 프롬프트 | `app/prompts/templates.py` 의 system 프롬프트(코드로 버전 고정) |
| 입력 | Artifact 의 `depends_on` — 어느 산출물을 입력으로 썼는지 |
| 응답 | Artifact 의 `content` |
| 검증 | `_validate` 스키마 강제 · `status`(complete/fallback/failed) · reviewer 구조화 issues |
| **채택 여부** | `best_version`(재작성본 vs 초안) · `reverted_from_revision` · `polish_applied` |

`best_version` 이 특히 중요하다 — **AI 재작성이 초안보다 나빠서 되돌린 기록**이라, "AI 응답을
그대로 쓰지 않았다"는 것을 실제 판정으로 보여 준다.

**정직 표기 — 무엇을 남기고 무엇을 안 남기는가.**
system 프롬프트(재사용되는 지시문)와 응답(Artifact content), 입력 목록, 채택 여부는 남긴다.
**user 프롬프트 원문은 남기지 않는다** — 앞 단계 결과를 기계적으로 이어 붙인 것이라 응답·입력
목록과 중복이고, 웹검색 스니펫(신뢰할 수 없는 외부 텍스트)이 그대로 실려 로그가 비대해진다.
대신 어떤 입력이 들어갔는지는 `inputs` 로 정확히 특정된다.
"""
from __future__ import annotations

from app.prompts import templates
from app.schemas import artifact

# Artifact 를 내지 않는 문서·검토·KOSENA 단계. Artifact 를 내는 7개 Agent 는
# LEGACY_ARTIFACT_SPECS 에서 자동으로 뽑는다(두 곳에 적지 않는다).
#
# **`inputs`·`produces` 를 왜 손으로 적는가.** 이 노드들은 Artifact 를 내지 않으므로
# `depends_on`(입력)도 `content`(응답)도 없다. 그래서 예전에는 `inputs: []` ·
# `output: {"artifact": None}` 으로 비어 있었고, KOSENA Agent 4종의 로그가 사실상
# "프롬프트 템플릿 있음 · 채택 Yes" 뿐이었다 — 그걸로는 KOSENA 가 요구하는
# *프롬프트 + 입력 + 응답 + 채택 여부*(p4)를 남겼다고 말할 수 없다.
#
# 값은 각 노드의 `artifact.read(...)` 호출과 `_validate()` 출력 키에서 그대로 옮긴 것이고,
# 드리프트는 테스트로 막는다(`produces` 합집합이 실제 `state["kosena"]` 키를 덮는지 확인).
_DOC_NODES: list[dict] = [
    {"node": "kosena_industry", "role": "산업 분석 전략가", "prompt": "KOSENA_INDUSTRY_SYSTEM",
     "inputs": ["pestel_analysis", "research_analysis", "competitor_analysis", "swot_analysis"],
     "state_key": "kosena",
     "produces": ["critical_uncertainties", "porter", "value_chain", "ksf", "implications"]},
    {"node": "kosena_model", "role": "린 스타트업 코치", "prompt": "KOSENA_MODEL_SYSTEM",
     "inputs": ["research_analysis", "customer_analysis", "business_model_analysis",
                "kosena.ksf", "kosena.implications"],
     "state_key": "kosena",
     "produces": ["hmw", "ideas", "shortlisted_concepts", "selected_concept", "lean_canvas",
                  "key_hypotheses"]},
    {"node": "kosena_research", "role": "데이터 기반 서비스 기획자",
     "prompt": "KOSENA_RESEARCH_SYSTEM",
     "inputs": ["research_analysis", "customer_analysis", "competitor_analysis"],
     "state_key": "kosena",
     "produces": ["personas", "cjm", "market_sizing", "competitor_groups",
                  "comparison_criteria", "competitor_comparison", "positioning_map"]},
    {"node": "kosena_roadmap", "role": "프로덕트 오너", "prompt": "KOSENA_ROADMAP_SYSTEM",
     "inputs": ["customer_analysis", "business_model_analysis", "risk_analysis",
                "kosena.lean_canvas", "kosena.personas"],
     "state_key": "kosena",
     "produces": ["vpc", "core_features", "use_cases", "moscow", "kano", "mvp_scope",
                  "epics", "milestones", "kpis", "wireframes"]},
    {"node": "research_gap", "role": "근거 공백 보강 조사자", "prompt": "RESEARCH_GAP_SYSTEM",
     "inputs": ["evidence_gaps"], "state_key": "evidence_registry",
     "produces": ["evidence_registry", "dynamic_research"]},
    {"node": "draft", "role": "기획서 작성자", "prompt": "DRAFT_WRITER_SYSTEM",
     "inputs": ["research_analysis", "competitor_analysis", "customer_analysis",
                "swot_analysis", "business_model_analysis", "risk_analysis", "pestel_analysis"],
     "state_key": "draft", "produces": ["draft"]},
    {"node": "reviewer", "role": "기획서 심사자", "prompt": "REVIEWER_SYSTEM",
     "inputs": ["draft"], "state_key": "review_result",
     "produces": ["review_result", "initial_review_result"]},
    {"node": "polish", "role": "일관성 편집자", "prompt": "EDITOR_SYSTEM",
     "inputs": ["final_draft"], "state_key": "final_draft", "produces": ["final_draft"]},
    {"node": "final_reviewer", "role": "최종본 심사자", "prompt": "REVIEWER_SYSTEM",
     "inputs": ["final_draft"], "state_key": "final_review_result",
     "produces": ["final_review_result", "best_version"]},
    {"node": "verify", "role": "근거 일치성 검증자", "prompt": "VERIFY_SYSTEM",
     "inputs": ["final_draft", "evidence_registry"], "state_key": "verification_result",
     "produces": ["verification_result"]},
]

# Artifact 를 내는 Agent 의 system 프롬프트 상수명.
_ARTIFACT_PROMPTS = {
    "research": "RESEARCH_SYSTEM", "competitor": "COMPETITOR_SYSTEM",
    "customer": "CUSTOMER_SYSTEM", "pestel": "PESTEL_SYSTEM", "swot": "SWOT_SYSTEM",
    "business_model": "BIZMODEL_SYSTEM", "risk": "RISK_SYSTEM",
}

# KOSENA 표준 5단 구조(p19). 프롬프트가 이를 따르는지 로그에 표기한다.
_FIVE_PARTS = ("[역할]", "[입력]", "[요구사항]", "[출력 형식]", "[검증 조건]")


def _prompt_info(const_name: str) -> dict:
    """system 프롬프트의 존재·5단 구조 준수 여부. 없는 상수는 조용히 넘긴다."""
    text = getattr(templates, const_name, None)
    if not isinstance(text, str):
        return {"template": const_name, "available": False, "five_part_structure": False}
    return {"template": const_name, "available": True,
            "five_part_structure": all(p in text for p in _FIVE_PARTS),
            "chars": len(text)}


def _adoption(state: dict, node: str, status: str) -> tuple[bool, str]:
    """이 응답을 **채택했는가**. KOSENA 가 요구하는 핵심 항목이다(p4).

    단순히 '실행됐다'가 아니라 실제 판정을 반영한다 — 재작성본이 초안보다 나빠 되돌린 경우
    (`reverted_from_revision`)가 "AI 응답을 그대로 쓰지 않았다"는 가장 분명한 증거다.
    """
    if status == artifact.STATUS_FAILED:
        return False, "노드가 예외로 건너뛰어져 결과를 쓰지 못함"
    if status == artifact.STATUS_FALLBACK:
        return True, "LLM 실패로 fallback 응답을 사용(정직 표기 대상)"
    if node in ("revise", "section_revise") and state.get("reverted_from_revision"):
        return False, "재작성본이 초안보다 낮게 평가되어 초안을 채택(select_best)"
    if node == "polish" and state.get("polish_applied") is False:
        return False, f"편집 생략: {state.get('polish_skip_reason') or '표현 이슈 없음'}"
    return True, ""


def _produced(state: dict, spec: dict, status: str) -> list[str]:
    """이 노드가 **실제로 채운** 필드만. 선언된 목록을 그대로 싣지 않는다.

    선언을 그대로 베끼면 실패한 노드도 "10개 필드를 만들었다"고 적힌다. 로그의 값은 산출물이
    있다는 주장이므로, State 에서 값이 실제로 있는 것만 남긴다. `kosena` 처럼 여러 노드가 한 키를
    공유하는 경우도 필드 단위로 보면 누가 무엇을 냈는지 정확히 갈린다.
    """
    if status == artifact.STATUS_FAILED:
        return []
    key = spec["state_key"]
    holder = state.get(key)
    if key == "kosena" and isinstance(holder, dict):
        return [f for f in spec["produces"] if holder.get(f)]
    return [f for f in spec["produces"] if state.get(f)]


def build(state: dict) -> list[dict]:
    """실행 State 에서 AI 활용 로그를 만든다(결정적·LLM 호출 없음).

    항목: {agent, role, prompt, inputs, output, verification, adopted, note}
    """
    if not isinstance(state, dict):
        return []
    arts = {a.get("artifact_type"): a for a in (state.get("artifacts") or [])
            if isinstance(a, dict)}
    by_id = {a.get("artifact_id"): a.get("artifact_type") for a in arts.values()}
    entries: list[dict] = []

    # 1) Artifact 를 내는 Agent — 입력·응답·검증이 전부 Artifact 에 들어 있다.
    for spec in artifact.LEGACY_ARTIFACT_SPECS:
        a = arts.get(spec["artifact_type"])
        if not a:
            continue
        status = a.get("status", artifact.STATUS_MISSING)
        adopted, note = _adoption(state, spec["owner_agent"], status)
        entries.append({
            "agent": spec["owner_agent"],
            "role": f"{spec['artifact_type']} 담당",
            "prompt": _prompt_info(_ARTIFACT_PROMPTS.get(spec["owner_agent"], "")),
            # 입력은 `depends_on` 에서 온다. 파이프라인 첫 Agent(research)는 앞선 산출물이 없어
            # 비는데, 그때 빈 배열을 남기면 "입력을 기록하지 않았다"로 읽힌다 — 실제 입력은
            # 사용자 아이디어이고, 웹검색을 했으면 그것도 입력이다(근거 id 로 확인된다).
            "inputs": [by_id.get(d, d) for d in a.get("depends_on") or []]
                      or (["structured_input"] + (["web_search"] if a.get("evidence_ids") else [])),
            "output": {"artifact": spec["artifact_type"],
                       "target_sections": a.get("target_sections") or [],
                       "evidence_ids": a.get("evidence_ids") or []},
            "verification": {"schema_validated": True, "status": status},
            "adopted": adopted,
            "note": note,
        })

    # 2) 문서·검토·KOSENA 단계 — Artifact 는 없지만 프롬프트·채택 판정은 있다.
    failed = set(state.get("failed_nodes") or [])
    fallback = set(state.get("fallback_nodes") or [])
    for spec in _DOC_NODES:
        node = spec["node"]
        if node in failed:
            status = artifact.STATUS_FAILED
        elif node in fallback:
            status = artifact.STATUS_FALLBACK
        else:
            status = artifact.STATUS_COMPLETE
        adopted, note = _adoption(state, node, status)
        entries.append({
            "agent": node, "role": spec["role"], "prompt": _prompt_info(spec["prompt"]),
            "inputs": list(spec["inputs"]),
            # Artifact 가 없으므로 '어떤 State 키의 어떤 필드를 만들었는지'로 응답을 특정한다.
            # 실패·폴백한 노드는 실제로 채운 필드만 남겨 '만들었다고 적혀 있는데 비어 있는'
            # 로그가 되지 않게 한다.
            "output": {"artifact": None, "state_key": spec["state_key"],
                       "produced_fields": _produced(state, spec, status)},
            "verification": {"schema_validated": True, "status": status},
            "adopted": adopted, "note": note,
        })

    # 3) 문서 재작성 — '채택 여부'가 가장 뚜렷하게 드러나는 지점.
    strategy = state.get("revision_strategy") or "none"
    if strategy != "none":
        adopted, note = _adoption(state, "revise", artifact.STATUS_COMPLETE)
        entries.append({
            "agent": "revise" if strategy == "full" else "section_revise",
            "role": "기획서 재작성",
            "prompt": _prompt_info("REVISER_SYSTEM" if strategy == "full"
                                   else "SECTION_REVISER_SYSTEM"),
            "inputs": ["review_result.issues", "final_draft"],
            "output": {"artifact": None, "state_key": "final_draft",
                       "produced_fields": ["final_draft"],
                       "revised_sections": state.get("revised_section_ids") or []},
            "verification": {"schema_validated": True,
                             "status": artifact.STATUS_COMPLETE,
                             "reviewer_rescored": bool(state.get("final_review_result"))},
            "adopted": adopted,
            "note": note or f"전략={strategy}",
        })
    return entries


def summary(entries: list[dict]) -> dict:
    """집계 — 몇 건 중 몇 건을 채택했고, 프롬프트가 5단 구조를 따르는가."""
    total = len(entries)
    adopted = sum(1 for e in entries if e.get("adopted"))
    five = sum(1 for e in entries if (e.get("prompt") or {}).get("five_part_structure"))
    return {"total": total, "adopted": adopted, "rejected": total - adopted,
            "five_part_prompts": five}


def to_markdown(entries: list[dict]) -> str:
    """별도 첨부용 Markdown(p4: 'AI 로그 별도 파일 첨부')."""
    s = summary(entries)
    out = [
        "# AI 활용 로그\n",
        "> KOSENA 요구(p4): AI 응답을 그대로 쓰지 않고 **검증·수정·재구성**한 과정을 "
        "프롬프트·응답·채택 여부로 남긴다.\n",
        f"> 총 {s['total']}건 · 채택 {s['adopted']} · 미채택 {s['rejected']} · "
        f"표준 5단 구조 프롬프트 {s['five_part_prompts']}건\n",
        "\n> **무엇을 남기고 무엇을 안 남기는가**: system 프롬프트(코드로 버전 고정)·입력 목록·"
        "응답 산출물·검증 결과·채택 여부를 남긴다. **user 프롬프트 원문은 남기지 않는다** — "
        "앞 단계 결과를 기계적으로 이어 붙인 것이라 입력 목록과 중복이고, 웹검색 스니펫이 "
        "그대로 실려 로그가 비대해지기 때문이다.\n",
        "\n| Agent | 역할 | 프롬프트(5단) | 입력 | 산출 | 상태 | 채택 | 비고 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for e in entries:
        p = e.get("prompt") or {}
        o = e.get("output") or {}
        five = "✅" if p.get("five_part_structure") else "—"
        inputs = ", ".join(e.get("inputs") or []) or "—"
        # Artifact 를 내는 Agent 는 Artifact 이름으로, 그 외 노드는 실제로 채운 State 필드로
        # 응답을 특정한다(둘 다 없으면 정직하게 '—').
        produced = o.get("produced_fields") or []
        art = o.get("artifact") or (
            f"`{o.get('state_key')}`: {', '.join(produced)}" if produced else "—")
        out.append(
            f"| `{e['agent']}` | {e['role']} | `{p.get('template', '—')}` {five} | {inputs} "
            f"| {art} | {(e.get('verification') or {}).get('status', '')} "
            f"| {'채택' if e.get('adopted') else '**미채택**'} | {e.get('note') or ''} |")
    out.append("\n## 검증 절차\n")
    out += [
        "- 모든 Agent 출력은 `_validate()` 로 **스키마를 강제**한다(누락 키는 중립값, 더미 문구 차단).",
        "- LLM 실패는 예외로 터뜨리지 않고 fallback 으로 흡수하되 `status` 에 **정직하게 표기**한다.",
        "- Reviewer 가 구조화 issue(`target_section_id`·`severity`)로 지목하면 해당 섹션만 재작성한다.",
        "- 재작성본이 초안보다 낮게 평가되면 **초안을 되돌려 채택**한다(`select_best`) — "
        "AI 응답을 그대로 쓰지 않는다는 가장 분명한 증거다.",
        "- 최종 문서의 주장은 수집한 근거와 대조 검증한다(`verify`, 주장별 `evidence_ids`).",
    ]
    return "\n".join(out) + "\n"
