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
    """분량은 **줄 수**로 잰다 — 표가 많은 문서에서 글자 수 기준은 크게 빗나간다."""
    long_doc = "\n".join(["본문"] * (kosena._LINES_PER_PAGE * 35))
    assert _by_id(kosena.evaluate({"final_draft": long_doc}), "doc_length")["status"] == kosena.OK
    short = kosena.evaluate({"final_draft": "짧은 문서"})
    assert _by_id(short, "doc_length")["status"] == kosena.PARTIAL
    assert "쪽(추정)" in _by_id(short, "doc_length")["detail"]   # 추정치임을 표기


def test_doc_length_measures_the_kosena_deliverable_not_the_draft():
    """제출 본문은 KOSENA 7종 산출물이다 — 14섹션 초안은 그 안의 일부일 뿐이다."""
    st = {"final_draft": "짧음", "kosena_plan": "\n".join(["줄"] * (kosena._LINES_PER_PAGE * 35))}
    assert _by_id(kosena.evaluate(st), "doc_length")["status"] == kosena.OK


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

    # 생성 Agent 가 붙은 모듈(M1·M2·M3)은 전부 충족이어야 한다.
    for module in ("M1", "M2", "M3"):
        rows = [c for c in r["checks"] if c["module"] == module]
        assert all(c["status"] == kosena.OK for c in rows), \
            (module, [c["title"] for c in rows if c["status"] != kosena.OK])

    # 남은 미충족을 **집합으로 고정**한다. 항목별 단언을 늘어놓으면 진척이 생길 때마다
    # 테스트가 깨져서 매번 손봐야 한다(실제로 두 번 겪었다). 여기서는
    # '무엇이 아직 안 됐는지'가 바뀌는 순간에만 실패하게 둔다.
    # sources_cited 는 더미가 웹검색을 하지 않아 미충족이다(실 LLM 18회 실측에서는 충족).
    # doc_length 는 자동 생성 본문이 약 11쪽이라 30~50쪽 요건에 미달한다(정직 표기).
    assert set(r["unmet"]) == {"sources_cited", "doc_length"}


def test_old_record_gets_compliance_on_read(tmp_path, monkeypatch):
    """옛 기록도 재조회 시 판정이 소급된다('판정 없음'과 '미충족'을 구분하기 위해)."""
    from app.services import store

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "p.db")
    pid = store.save_run({"structured_input": {"project_name": "옛"},
                          "pestel_result": {f"f{i}": {"content": "c"} for i in range(6)},
                          "final_draft": "# 옛 기획서"})
    st = store.get_project(pid)["state"]
    assert st["kosena_compliance"]["total"] == len(kosena.REQUIREMENTS)


# ---- 실 LLM 실패가 준수율을 채우지 않는가 (허위 충족 차단) ----

def test_real_mode_llm_failure_leaves_outputs_empty(force_real_llm):
    """실모드에서 호출이 실패하면 산출물은 **비어야** 한다(키는 유지).

    각 Agent 의 `_dummy()` 는 검사를 통과할 만큼 구조가 완전하다(HMW 5개 · 아이디어 25개 ·
    Lean Canvas 9블록). 그게 실모드 폴백으로 들어가면 구조를 보는 준수 검사가 **충족**으로
    판정한다 — 실 LLM 이 실패했는데 "방법론 28개 항목을 지켰다"고 보고하는 셈이다.
    """
    from app.agents.kosena_model import LEAN_BLOCKS, kosena_model

    out = kosena_model({"structured_input": {}, "kosena": {}})["kosena"]
    assert not any(out.values()), out                  # 더미 내용이 새어 나오지 않는다
    assert "hmw" in out and "lean_canvas" in out       # 키는 유지 — 호출부 로그가 접근한다
    assert not out["lean_canvas"] and len(LEAN_BLOCKS) == 9


def test_real_mode_fallback_never_reports_ok(force_real_llm, monkeypatch):
    """리뷰 요구: 실 LLM 폴백이 발생한 KOSENA 항목은 ok 로 판정되지 않는다.

    `force_real_llm` 은 LLM 만 막는다 — 검색은 그대로 나가므로 여기서 함께 끊는다(테스트가
    Tavily 쿼터를 쓰면 안 되고, 네트워크 상태에 따라 결과가 흔들려서도 안 된다).
    """
    from app.graph.workflow import run_workflow
    from app.services import search

    monkeypatch.setattr(search, "search_enabled", lambda: False)

    st = run_workflow({"project_name": "폴백", "problem": "P"})
    r = st["kosena_compliance"]
    kosena_ids = {x["id"] for x in r["checks"] if x["module"] in ("M1", "M2", "M3")}
    assert kosena_ids <= set(r["unmet"]), sorted(kosena_ids - set(r["unmet"]))
    assert r["data_source"] == "fallback"
    assert "폴백된 단계" in r["summary"]


def test_dummy_run_stamps_its_provenance_on_the_verdict(monkeypatch):
    """더미 실행의 준수율은 캡처만 보면 실행 성과처럼 읽힌다 → 판정에 출처를 박는다."""
    from app.graph.workflow import run_workflow
    from app.services import llm

    monkeypatch.setattr(llm, "is_dummy", lambda: True)
    st = run_workflow({"project_name": "더미", "problem": "P"})
    r = st["kosena_compliance"]
    assert r["data_source"] == "dummy"
    assert "더미 데이터 기준" in r["summary"]
    # 문서·발표자료도 같은 경고를 실어야 한다(캡처가 문서에서 나오기 때문).
    assert "더미 데이터 기준" in st["kosena_plan"] and "더미 데이터 기준" in st["kosena_deck"]


def test_provenance_is_read_from_content_not_process_mode():
    """저장된 기록을 나중에 실모드 프로세스에서 다시 판정해도 같은 답이어야 한다."""
    assert kosena.evaluate({"kosena": {"ksf": ["[더미] 요인"]}})["data_source"] == "dummy"
    assert kosena.evaluate({"kosena": {"ksf": ["실제 요인"]}})["data_source"] == "real"


# ---- 발산 → 수렴 (25개 이상 → 3개 → 1개, p9) ----

def _ideation(**over) -> dict:
    base = {"hmw": [f"q{i}" for i in range(5)], "ideas": [f"i{i}" for i in range(25)],
            "shortlisted_concepts": [{"concept": f"c{i}"} for i in range(3)],
            "selected_concept": "최종"}
    return {"kosena": {**base, **over}}


def test_ideation_requires_the_shortlist_step():
    """25개에서 곧바로 1개로 건너뛴 것은 KOSENA 의 수렴 과정이 아니다(무엇을 왜 버렸는지 없음)."""
    assert _status(_ideation(), "hmw_ideation") == kosena.OK
    assert _status(_ideation(shortlisted_concepts=[]), "hmw_ideation") == kosena.PARTIAL


def test_ideation_shortlist_must_be_exactly_three():
    assert _status(_ideation(shortlisted_concepts=[{"concept": "c"}] * 2), "hmw_ideation") \
        == kosena.PARTIAL
    assert _status(_ideation(shortlisted_concepts=[{"concept": "c"}] * 4), "hmw_ideation") \
        == kosena.PARTIAL


def test_shortlist_is_not_padded_to_meet_the_count():
    """개수가 모자라도 복제해 채우지 않는다 — 부분 충족으로 보고하는 편이 정직하다."""
    from app.agents import kosena_model as mdl

    out = mdl._validate({"shortlisted_concepts": [{"concept": "하나뿐"}]}, mdl._dummy())
    assert len(out["shortlisted_concepts"]) == 1
    assert out["shortlisted_concepts"][0]["concept"] == "하나뿐"


def test_shortlist_appears_in_the_document(monkeypatch):
    """압축 후보가 본문에 실려야 평가자가 수렴 근거를 볼 수 있다."""
    from app.services import kosena_doc

    plan = kosena_doc.build(_ideation(shortlisted_concepts=[
        {"concept": "후보A", "feasibility": "높음", "marketability": "중간",
         "differentiation": "차별점", "selection_reason": "남긴 이유"}]))
    for text in ("압축 후보", "후보A", "남긴 이유"):
        assert text in plan, text


# ---- 경쟁사 비교표 (기준 목록 ≠ 비교표) ----

def test_criteria_list_alone_is_not_a_comparison_table():
    """기준 10개만 나열하고 경쟁사별 값이 없으면 비교표가 아니다(과제 기대 결과물에 명시)."""
    criteria_only = {"kosena": {"comparison_criteria": [f"기준{i}" for i in range(10)]}}
    assert _status(criteria_only, "comparison_criteria_10") == kosena.PARTIAL
    assert "값 없음" in _by_id(kosena.evaluate(criteria_only), "comparison_criteria_10")["detail"]


def test_comparison_table_with_criteria_is_ok():
    st = {"kosena": {"comparison_criteria": [f"기준{i}" for i in range(10)],
                     "competitor_comparison": [{"name": "A사", "type": "direct", "price": "월 1만"},
                                               {"name": "자사", "type": "self", "price": "미확인"}]}}
    r = _by_id(kosena.evaluate(st), "comparison_criteria_10")
    assert r["status"] == kosena.OK and "비교표 2행" in r["detail"]


def test_comparison_table_is_split_into_two_tables_in_the_document():
    """9열을 한 표에 밀어 넣으면 DOCX 가 열 폭을 줄여 표가 읽히지 않는다 → 두 표로 나눈다."""
    from app.services import kosena_doc

    plan = kosena_doc.build({"kosena": {"competitor_comparison": [
        {"name": "A사", "type": "direct", "features": "기능", "price": "월 1만",
         "ux": "단순", "target_user": "직장인", "revenue_model": "구독",
         "strength": "브랜드", "weakness": "가격"}]}})
    assert "경쟁사 비교표 (1행)" in plan
    assert "① 제품·시장" in plan and "② 강점·약점" in plan
    assert "A사 (직접)" in plan and "브랜드" in plan


def test_comparison_table_absence_is_stated_not_hidden():
    from app.services import kosena_doc

    plan = kosena_doc.build({"kosena": {"comparison_criteria": ["기능"]}})
    assert "경쟁사별 값이 없음" in plan


def test_comparison_rows_are_not_truncated():
    """경쟁사가 6곳이면 6행 + 자사 1행이 다 필요하다 — 개수를 자르면 비교가 성립하지 않는다."""
    from app.agents import kosena_research as rsc

    rows = [{"name": f"C{i}", "type": "direct", "price": "가격"} for i in range(7)]
    out = rsc._validate({"competitor_comparison": rows}, rsc._dummy())
    assert len(out["competitor_comparison"]) == 7
