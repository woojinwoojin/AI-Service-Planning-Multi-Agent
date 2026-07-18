"""Draft 서식 유틸(_missing_sections/_strip_wrapping_fence)과 preprocess 엣지 테스트."""
from __future__ import annotations

from app.agents import draft_writer, preprocess
from app.agents.pestel import _dummy as pestel_dummy


def test_dummy_draft_has_all_sections_and_pestel_table():
    d = draft_writer._dummy_draft(
        {"project_name": "테스트", "problem": "P", "target_user": "U", "description": "D"},
        {"market_overview": "M", "industry_trends": ["t1"]},
        pestel_dummy({}),
    )
    assert draft_writer._missing_sections(d) == []
    assert "| 요인 | 주요 내용 | 기회 | 위협 | 대응 |" in d      # PESTEL 표 헤더
    assert d.count("| Political |") == 1


def test_missing_sections_detects_gaps():
    partial = "# X 기획서\n## 프로젝트 개요\n내용\n## 차별성\n내용"
    m = draft_writer._missing_sections(partial)
    assert "문제 정의" in m and "PESTEL 분석" in m
    assert "프로젝트 개요" not in m


def test_strip_wrapping_fence():
    s = draft_writer._strip_wrapping_fence
    assert s("```markdown\n# 제목\n내용\n```") == "# 제목\n내용"
    assert s("```\n# 제목\n```") == "# 제목"
    assert s("# 펜스없음\n내용") == "# 펜스없음\n내용"
    mid = "# 제목\n```python\ncode\n```\n끝"                       # 문서 중간 코드블록 보존
    assert s(mid) == mid


def test_append_references_adds_and_dedups():
    body = "# P 기획서\n## 프로젝트 개요\n내용"
    once = draft_writer._append_references(body, ["제목 — https://a.io", "https://b.io"])
    assert "## 참고자료" in once
    assert "https://a.io" in once and "https://b.io" in once
    # 재적용해도 참고자료 섹션이 중복되지 않음
    twice = draft_writer._append_references(once, ["https://c.io"])
    assert twice.count("## 참고자료") == 1
    assert "https://c.io" in twice and "https://a.io" not in twice  # 새 출처로 교체
    # 출처 없으면 원문 유지(섹션 미추가)
    assert "## 참고자료" not in draft_writer._append_references(body, [])
    # 참고자료 개수 상한
    many = [f"https://ex.io/{i}" for i in range(20)]
    capped = draft_writer._append_references(body, many)
    assert capped.count("- https://ex.io/") == draft_writer._MAX_REFS


def test_preprocess_keyword_string_and_defaults():
    out = preprocess.preprocess({"user_input": {"project_name": "  P  ", "keywords": "a, b ,c"}})
    si = out["structured_input"]
    assert si["project_name"] == "P"
    assert si["keywords"] == ["a", "b", "c"]          # 문자열 → 리스트 분해
    assert si["target_user"] == "미지정"              # 빈 값 기본치
    assert out["revision_count"] == 0


def test_preprocess_empty_input():
    out = preprocess.preprocess({})
    assert out["structured_input"]["project_name"] == "제목 없는 프로젝트"
