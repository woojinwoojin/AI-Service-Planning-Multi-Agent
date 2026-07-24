"""섹션 파서/조립기 테스트 (로드맵 2-4 PR-7, LLM 호출 없음).

핵심 불변식 검증:
- 14섹션 파싱·정본 순서 valid 판정
- 파싱→조립 왕복 byte 동일
- 특정 섹션만 교체 시 나머지 섹션 byte 동일(계획 3의 테스트 2)
- 참고자료 등 14섹션 밖 블록 보존
"""
from __future__ import annotations

from app.services import sections


def make_draft(prefix: str = "") -> str:
    """유효한 14섹션 기획서 + 참고자료 섹션을 만든다."""
    lines = ["# 테스트 기획서", ""]
    for _, title in sections.SECTION_SPECS:
        lines += [f"## {title}", "", f"{prefix}{title} 본문 내용.", ""]
    lines += ["## 참고자료", "", "- 출처A — http://a.example", "- 출처B — http://b.example", ""]
    return "\n".join(lines)


def test_parse_recognizes_all_14_sections_in_order():
    p = sections.parse_sections(make_draft())
    assert p["valid"] is True
    assert p["reason"] is None
    assert p["order"] == sections.KNOWN_IDS
    assert set(p["sections"]) == set(sections.KNOWN_IDS)
    # 참고자료는 14섹션 밖 블록(section_id None)이지만 blocks 에는 남는다
    assert any(b["section_id"] is None and "참고자료" in b["title"] for b in p["blocks"])


def test_roundtrip_is_byte_identical_when_nothing_revised():
    md = make_draft()
    p = sections.parse_sections(md)
    assert sections.assemble(p, {}) == md          # 왕복 완전 일치


def test_assemble_changes_only_target_sections():
    md = make_draft()
    p = sections.parse_sections(md)
    new = sections.assemble(p, {"revenue_model": "완전히 새로운 수익 모델 본문."})
    np = sections.parse_sections(new)
    assert np["valid"] is True
    assert "완전히 새로운 수익 모델 본문." in sections.section_body(np, "revenue_model")
    # 나머지 13섹션 body 는 원문과 byte 동일
    for sid in sections.KNOWN_IDS:
        if sid == "revenue_model":
            continue
        assert sections.section_body(np, sid) == sections.section_body(p, sid)
    # 참고자료(14섹션 밖 블록)도 보존
    assert "http://a.example" in new and "http://b.example" in new


def test_missing_section_is_invalid():
    md = make_draft().replace("## 수익 모델\n", "")   # 한 섹션 제목 제거
    p = sections.parse_sections(md)
    assert p["valid"] is False
    assert p["reason"] == "missing"


def test_duplicate_section_is_invalid():
    md = make_draft() + "\n## 수익 모델\n\n중복 섹션.\n"
    p = sections.parse_sections(md)
    assert p["valid"] is False
    assert p["reason"] == "duplicate"


def test_out_of_order_is_invalid():
    # 첫 두 섹션 순서를 뒤집는다
    md = make_draft()
    swapped = md.replace("## 프로젝트 개요", "§TMP§").replace("## 추진 배경", "## 프로젝트 개요").replace("§TMP§", "## 추진 배경")
    p = sections.parse_sections(swapped)
    assert p["valid"] is False
    assert p["reason"] == "order"


def test_numbered_headings_still_map():
    """제목에 번호가 붙어도(`## 11. 수익 모델`) 정본 ID 로 매핑된다(견고성)."""
    md = make_draft().replace("## 수익 모델", "## 11. 수익 모델")
    p = sections.parse_sections(md)
    assert p["sections"].get("revenue_model") is not None
    assert p["valid"] is True


def test_no_headings_is_invalid():
    p = sections.parse_sections("제목 없는 평문")
    assert p["valid"] is False
    assert p["reason"] == "no_headings"
    assert p["preamble"] == "제목 없는 평문"       # 원문 보존
