"""OpenAPI 문서화(로드맵 Phase 5 마무리) 테스트 — 스키마 생성만, 서버·LLM 불필요."""
from __future__ import annotations

from app.main import app

_SPEC = app.openapi()


def test_version_and_tags_metadata():
    assert _SPEC["info"]["version"] == "0.2.0"
    tag_names = {t["name"] for t in _SPEC.get("tags", [])}
    assert {"실행", "이력", "입력 보조", "내보내기", "시스템"} <= tag_names


def test_error_response_schema_documented():
    assert "ErrorResponse" in _SPEC["components"]["schemas"]
    # 공통 오류 응답(422·500)이 라우트에 노출됨
    run = _SPEC["paths"]["/run"]["post"]["responses"]
    assert "422" in run and "500" in run


def test_key_routes_have_tags_and_summaries():
    expected = {
        ("/run", "post"): "실행",
        ("/run/stream", "post"): "실행",
        ("/revise", "post"): "실행",
        ("/projects", "get"): "이력",
        ("/projects/{project_id}", "get"): "이력",
        ("/suggest", "post"): "입력 보조",
        ("/export/docx", "post"): "내보내기",
        ("/health", "get"): "시스템",
    }
    for (path, method), tag in expected.items():
        op = _SPEC["paths"][path][method]
        assert tag in op.get("tags", []), f"{method} {path} 태그 누락"
        assert op.get("summary"), f"{method} {path} summary 누락"


def test_project_detail_documents_404():
    assert "404" in _SPEC["paths"]["/projects/{project_id}"]["get"]["responses"]


def test_stream_documents_event_stream_content():
    content = _SPEC["paths"]["/run/stream"]["post"]["responses"]["200"]["content"]
    assert "text/event-stream" in content
