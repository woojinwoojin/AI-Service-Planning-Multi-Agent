"""KOSENA 준수 검사 (체크포인트 3) — 실 LLM 호출 없음.

이 모듈은 **판정 도구**다. 조용히 다 통과시키면 리포트의 ✅ 가 아무 의미가 없고, 반대로
다 미충족으로 떨어뜨리면 개선이 반영돼도 알 수 없다. 그래서 양방향을 고정한다:

  1) 요구 수치를 **실제로 강제하는가**(KSF 4개는 ok 가 아니어야 한다)
  2) 채우면 **정말 ok 로 바뀌는가**
  3) '없음'과 '있는데 규격 미달'을 **구분하는가**(missing vs partial) — 이게 이 검사의 핵심
"""
from __future__ import annotations

import pytest

from app.schemas import artifact
from app.services import kosena


def _by_id(result: dict, check_id: str) -> dict:
    return next(x for x in result["checks"] if x["id"] == check_id)


def _status(state: dict, check_id: str) -> str:
    return _by_id(kosena.evaluate(state), check_id)["status"]


# ---- 기본 동작 ----

def test_empty_state_is_all_unmet_but_does_not_crash():
    """빈 State 여도 죽지 않고, 전부 미충족으로 정직하게 보고한다."""
    r = kosena.evaluate({})
    assert r["total"] == len(kosena.REQUIREMENTS) >= 25
    assert r["ok"] == 0 and r["missing"] > 0
    assert len(r["unmet"]) == r["total"]
    assert r["ok"] + r["partial"] + r["missing"] == r["total"]


def test_non_dict_state_is_safe():
    assert kosena.evaluate(None)["ok"] == 0        # type: ignore[arg-type]


def test_a_broken_check_does_not_kill_the_report(monkeypatch):
    """검사 하나가 예외를 던져도 나머지 판정은 나와야 한다(리포트가 통째로 날아가면 안 된다)."""
    def boom(_c):
        raise RuntimeError("고장")

    monkeypatch.setitem(kosena.REQUIREMENTS[0], "check", boom)
    r = kosena.evaluate({})
    assert r["total"] == len(kosena.REQUIREMENTS)
    assert "검사 오류" in _by_id(r, kosena.REQUIREMENTS[0]["id"])["detail"]


def test_every_requirement_cites_a_page():
    """근거 쪽수가 없으면 '왜 이게 요구사항인지' 원문 대조가 불가능하다."""
    for spec in kosena.REQUIREMENTS:
        assert isinstance(spec["page"], int) and spec["page"] > 0, spec["id"]
        assert spec["module"] in ("M1", "M2", "M3", "공통"), spec["id"]


# ---- 핵심: 없음 vs 규격 미달을 구분하는가 ----

def test_partial_distinguishes_missing_from_undersized():
    """PESTEL 이 있는데 Top3 만 없으면 '없음'이 아니라 '부분 충족'이다."""
    empty = kosena.evaluate({})
    assert _by_id(empty, "pestel_critical_top3")["status"] == kosena.MISSING

    with_pestel = {"pestel_result": {f"f{i}": {"content": "c"} for i in range(6)}}
    r = kosena.evaluate(with_pestel)
    assert _by_id(r, "pestel_6")["status"] == kosena.OK
    assert _by_id(r, "pestel_critical_top3")["status"] == kosena.PARTIAL


def test_string_persona_is_partial_not_ok():
    """현재 customer_result 의 페르소나는 **문자열 1개**다 — KOSENA 는 2종 × 5항목을 요구한다.

    이걸 ok 로 세면 '페르소나 있음'으로 오판한다.
    """
    st = {"customer_result": {"target_persona": "30대 직장인"}}
    assert _status(st, "personas_2") == kosena.PARTIAL


def test_competitors_without_classification_is_partial():
    st = {"competitor_result": {"competitors": [{"name": f"A{i}"} for i in range(5)]}}
    assert _status(st, "competitors_3_2_1") == kosena.PARTIAL


# ---- 요구 수치를 실제로 강제하는가 ----

@pytest.mark.parametrize(("count", "expected"), [
    (0, kosena.MISSING), (4, kosena.PARTIAL), (5, kosena.OK), (6, kosena.PARTIAL),
])
def test_ksf_requires_exactly_five(count, expected):
    """KSF 는 '5개'다(p8). 4개도 6개도 충족이 아니다."""
    assert _status({"kosena": {"ksf": ["k"] * count}}, "ksf_5") == expected


@pytest.mark.parametrize(("count", "expected"), [
    (4, kosena.PARTIAL), (5, kosena.OK), (7, kosena.OK), (8, kosena.PARTIAL),
])
def test_core_features_require_five_to_seven(count, expected):
    """핵심 기능은 5~7개(p15) — 범위 밖은 부분 충족."""
    assert _status({"kosena": {"core_features": ["f"] * count}}, "core_features_5_7") == expected


def test_lean_canvas_requires_all_nine_blocks():
    eight = {b: "v" for b in kosena._LEAN_BLOCKS[:8]}
    assert _status({"kosena": {"lean_canvas": eight}}, "lean_canvas_9") == kosena.PARTIAL
    nine = {b: "v" for b in kosena._LEAN_BLOCKS}
    r = kosena.evaluate({"kosena": {"lean_canvas": nine}})
    assert _by_id(r, "lean_canvas_9")["status"] == kosena.OK


def test_moscow_requires_wont_have_key_even_if_empty():
    """Won't have 는 '이번 범위 제외'를 **명시**하는 칸이라, 키가 없으면 충족이 아니다(p17)."""
    without = {"must": ["a"], "should": ["b"], "could": ["c"]}
    assert _status({"kosena": {"moscow": without}}, "moscow") == kosena.PARTIAL
    with_wont = {**without, "wont": []}
    assert _status({"kosena": {"moscow": with_wont}}, "moscow") == kosena.OK


def test_sizing_cross_check_needs_both_methods_and_gap_reason():
    """Top-down·Bottom-up 병행이 요건이고(p13), 두 값이 다르면 사유가 있어야 의미가 있다."""
    one = {"tam": 1, "sam": 1, "som": 1, "top_down": 100}
    assert _status({"kosena": {"market_sizing": one}}, "sizing_cross_check") == kosena.PARTIAL
    both = {**one, "bottom_up": 80}
    assert _status({"kosena": {"market_sizing": both}}, "sizing_cross_check") == kosena.PARTIAL
    full = {**both, "gap_reason": "채널 가정 차이"}
    assert _status({"kosena": {"market_sizing": full}}, "sizing_cross_check") == kosena.OK


def test_acceptance_criteria_must_be_given_when_then():
    """AC 는 Given-When-Then 형식이어야 한다(p18) — 서술형은 부분 충족."""
    loose = {"epics": [{"stories": [{"story": "로그인하고 싶다"}]}]}
    assert _status({"kosena": loose}, "epic_story_ac") == kosena.PARTIAL
    gwt = {"epics": [{"stories": [{"given": "g", "when": "w", "then": "t"}]}]}
    assert _status({"kosena": gwt}, "epic_story_ac") == kosena.OK


def test_hypothesis_labeling_checks_the_document_text():
    """인터뷰·설문을 못 하므로 **가설임을 명시**하는지 자체를 검사한다(p4·p20)."""
    assert _status({"final_draft": "시장 규모는 1조원이다."}, "hypothesis_labeling") == kosena.MISSING
    labeled = {"final_draft": "본 페르소나는 웹 리서치 기반 가설이며 인터뷰로 검증되지 않았다."}
    assert _status(labeled, "hypothesis_labeling") == kosena.OK


def test_doc_length_reports_estimated_pages():
    r = kosena.evaluate({"final_draft": "가" * (kosena._CHARS_PER_PAGE * 35)})
    assert _by_id(r, "doc_length")["status"] == kosena.OK
    short = kosena.evaluate({"final_draft": "가" * 500})
    assert _by_id(short, "doc_length")["status"] == kosena.PARTIAL
    assert "쪽(추정)" in _by_id(short, "doc_length")["detail"]   # 추정치임을 표기


# ---- Artifact selector 를 타는가 ----

def test_reads_through_artifact_selector(monkeypatch):
    """검사도 다른 소비자와 같은 창구(`artifact.read`)로 읽어야 읽기 모드가 일관된다(2-2)."""
    monkeypatch.setenv(artifact.READ_MODE_ENV, artifact.READ_PREFER_ARTIFACT)
    st = {
        "pestel_result": {},                              # 평면 키는 비어 있고
        "artifacts": [artifact.make_artifact(              # Artifact 에만 6영역이 있다
            "pestel_analysis", {f"f{i}": {"content": "c"} for i in range(6)})],
    }
    assert _status(st, "pestel_6") == kosena.OK


def test_unreadable_artifact_does_not_break_the_run(monkeypatch):
    """`artifact_only` 에서 못 쓰는 Artifact 는 `ArtifactUnavailable` 을 던진다.

    그건 **소비자**에게는 옳은 동작이지만, 이 검사는 **관찰자**다 — 관찰하다 실행을 죽이면
    안 되고 '미충족'으로 보고해야 한다. (실제로 이걸 빠뜨려 옛 기록 회귀 3건이 깨졌었다.)
    """
    monkeypatch.setenv(artifact.READ_MODE_ENV, artifact.READ_ARTIFACT_ONLY)
    r = kosena.evaluate({"pestel_result": {"a": {"content": "c"}}})   # Artifact 는 없음
    assert r["total"] == len(kosena.REQUIREMENTS)
    assert _by_id(r, "pestel_6")["status"] == kosena.MISSING


def test_each_artifact_is_read_once(monkeypatch):
    """같은 Artifact 를 검사마다 다시 읽으면 폴백 경고와 PR 5d 읽기 카운터가 부풀려진다."""
    seen: list[str] = []
    real = artifact.read
    monkeypatch.setattr(artifact, "read", lambda st, t: (seen.append(t), real(st, t))[1])
    kosena.evaluate({"pestel_result": {"a": {}}, "swot_result": {"strengths": []}})
    assert len(seen) == len(set(seen)), f"중복 읽기: {seen}"


# ---- 리포트 ----

def test_report_lines_show_unmet_with_page_refs():
    lines = "\n".join(kosena.report_lines(kosena.evaluate({})))
    assert "KOSENA 준수" in lines and "❌" in lines
    assert "(p10)" in lines          # 근거 쪽수가 보여야 원문 대조가 된다
    for module in ("M1", "M2", "M3", "공통"):
        assert f"[{module}]" in lines


# ---- 워크플로 통합 ----

def test_run_records_kosena_compliance(monkeypatch):
    from app.graph.workflow import run_workflow
    from app.services import llm

    monkeypatch.setattr(llm, "is_dummy", lambda: True)
    state = run_workflow({"project_name": "KOSENA", "problem": "P"})
    r = state["kosena_compliance"]
    assert r["total"] == len(kosena.REQUIREMENTS)
    assert _by_id(r, "pestel_6")["status"] == kosena.OK
    # KOSENA M1 Agent 도입 후 Lean Canvas 는 충족으로 바뀐다(같은 검사가 진척을 그대로 보고).
    assert _by_id(r, "lean_canvas_9")["status"] == kosena.OK
    # 반면 M2·M3 는 아직 생성 계층이 없어 미충족이어야 한다 — 검사가 조용히 다 통과시키면 안 된다.
    assert _by_id(r, "cjm")["status"] == kosena.MISSING
    assert _by_id(r, "epic_story_ac")["status"] == kosena.MISSING


def test_old_record_gets_compliance_on_read(tmp_path, monkeypatch):
    """옛 기록도 재조회 시 판정이 소급된다('판정 없음'과 '미충족'을 구분하기 위해)."""
    from app.services import store

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "p.db")
    pid = store.save_run({"structured_input": {"project_name": "옛"},
                          "pestel_result": {f"f{i}": {"content": "c"} for i in range(6)},
                          "final_draft": "# 옛 기획서"})
    st = store.get_project(pid)["state"]
    assert st["kosena_compliance"]["total"] == len(kosena.REQUIREMENTS)
