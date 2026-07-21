"""정보 신뢰성 한계 문구(단일 소스) 테스트 — PR-D."""
from __future__ import annotations

from app.services import reliability
from app.services.docx_export import build_docx
from app.services.markdown_export import _RUN_KEYS


def test_verification_summary_shape():
    vs = reliability.VERIFICATION_SUMMARY
    assert vs["scope"] == "search_snippet_only"
    assert vs["original_document_checked"] is False
    assert vs["fact_check_completed"] is False
    assert vs["note"] == reliability.DISCLAIMER_TEXT      # UI 가 재사용하는 동일 문구


def test_append_disclaimer_adds_once_and_is_idempotent():
    md = "# 기획서\n## 개요\n내용"
    once = reliability.append_disclaimer(md)
    assert "검증 범위 및 한계" in once
    assert reliability.DISCLAIMER_TEXT in once
    assert once.count("검증 범위 및 한계") == 1
    # 이미 문구가 있으면 다시 붙이지 않는다(중복 방지)
    assert reliability.append_disclaimer(once) == once


def test_append_disclaimer_handles_empty():
    out = reliability.append_disclaimer("")
    assert reliability.DISCLAIMER_TEXT in out


def test_disclaimer_renders_into_docx():
    """DOCX 변환기가 한계 문구 섹션(## 제목 + 문단)을 실제로 렌더한다."""
    md = reliability.append_disclaimer("# 기획서\n## 개요\n내용")
    doc = build_docx(md)
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "검증 범위 및 한계" in text
    assert "원문 사실성은 재검증하지 않았습니다" in text


def test_run_json_includes_verification_summary_key():
    assert "verification_summary" in _RUN_KEYS


def test_summary_returns_isolated_copy():
    """공유 상수 오염 방지: summary()는 매번 독립 사본을 준다."""
    a = reliability.summary()
    a["scope"] = "MUTATED"
    a["note"] = "changed"
    assert reliability.VERIFICATION_SUMMARY["scope"] == "search_snippet_only"  # 원본 불변
    assert reliability.summary()["note"] == reliability.DISCLAIMER_TEXT
