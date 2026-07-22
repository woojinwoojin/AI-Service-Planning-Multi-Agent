"""입력 자동완성(/suggest) — 사용자 입력 보존 + 빈 항목만 채우기 테스트 (더미/mock)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import llm, suggest

_ALL = {"description", "target_user", "problem", "keywords"}


@pytest.fixture
def dummy(monkeypatch):
    monkeypatch.setattr(llm, "is_dummy", lambda: True)


def test_no_existing_fills_all(dummy):
    out = suggest.suggest_fields("반려식물 케어")
    assert _ALL <= set(out)
    assert all(out[k] for k in _ALL)                          # 빈 항목 전부 채움
    assert isinstance(out["keywords"], list)


def test_existing_fields_are_preserved_as_none(dummy):
    """사용자가 입력한 항목은 응답에서 None(=프론트가 건드리지 않음), 빈 항목만 채움."""
    out = suggest.suggest_fields(
        "시니어 복약 관리",
        existing={"target_user": "혼자 사는 65세 이상", "problem": "복약 혼동"})
    assert out["target_user"] is None and out["problem"] is None   # 보존(추천 안 함)
    assert out["description"] and out["keywords"]                    # 빈 항목만 채움


def test_all_filled_returns_all_none(dummy):
    out = suggest.suggest_fields("완성", existing={
        "description": "d", "target_user": "u", "problem": "p", "keywords": ["k"]})
    assert all(out[k] is None for k in _ALL)                   # 채울 게 없음 → 전부 보존


def test_existing_used_as_context_in_prompt(monkeypatch):
    """빈 항목 생성 시 사용자 입력을 문맥으로 프롬프트에 전달한다."""
    seen = {}
    monkeypatch.setattr(llm, "is_dummy", lambda: False)

    def fake(system, user, **k):
        seen["user"] = user
        return {"description": "생성됨", "keywords": ["a", "b"]}

    monkeypatch.setattr(llm, "complete_json", fake)
    suggest.suggest_fields("복약 관리", existing={"target_user": "고령층 독거"})
    assert "고령층 독거" in seen["user"]                       # 기존 입력이 문맥으로 포함됨
    assert "변경하지 말고" in seen["user"] or "변경" in seen["user"]


def test_validate_ignores_values_for_filled_fields(dummy):
    """빈 항목이 아닌데 LLM이 값을 줘도 무시(사용자 입력 덮어쓰기 방지)."""
    out = suggest._validate(
        {"description": "덮어쓰기 시도", "problem": "빈칸 채움"},
        fallback={"description": None, "target_user": None, "problem": "fb", "keywords": []},
        empty=["problem"])
    assert out["description"] is None                          # 빈 항목 아님 → 무시
    assert out["problem"] == "빈칸 채움"


def test_route_preserves_existing(dummy):
    c = TestClient(app)
    d = c.post("/suggest", json={
        "project_name": "시니어 복약", "existing": {"target_user": "독거 고령층"}}).json()
    assert d["target_user"] is None                           # 사용자 입력 보존
    assert d["description"]                                    # 빈 항목만 채움


def test_compare_mode_suggests_all_fields(dummy):
    """비교 모드: 사용자가 일부 입력했어도 4개 항목 '모두' 제안(None 없음)."""
    out = suggest.suggest_fields("복약 관리", existing={"target_user": "고령층"}, compare=True)
    assert _ALL <= set(out)
    assert all(out[k] is not None for k in _ALL)              # 채워진 항목도 제안 생성
    assert isinstance(out["keywords"], list)


def test_compare_mode_does_not_mutate_existing_arg(dummy):
    """비교 제안 생성이 넘겨받은 existing 을 변경하지 않는다(순수)."""
    existing = {"target_user": "고령층", "keywords": ["복약"]}
    suggest.suggest_fields("복약", existing=existing, compare=True)
    assert existing == {"target_user": "고령층", "keywords": ["복약"]}


def test_compare_mode_uses_existing_as_context(monkeypatch):
    seen = {}
    monkeypatch.setattr(llm, "is_dummy", lambda: False)

    def fake(system, user, **k):
        seen["system"] = system
        seen["user"] = user
        return {"description": "d", "target_user": "t", "problem": "p", "keywords": ["a"]}

    monkeypatch.setattr(llm, "complete_json", fake)
    from app.prompts import templates
    suggest.suggest_fields("복약", existing={"problem": "복약 혼동"}, compare=True)
    assert seen["system"] == templates.SUGGEST_COMPARE_SYSTEM  # 비교 전용 프롬프트 사용
    assert "복약 혼동" in seen["user"]                          # 사용자 현재 값이 문맥에 포함


def test_compare_mode_route(dummy):
    c = TestClient(app)
    d = c.post("/suggest", json={
        "project_name": "복약", "existing": {"target_user": "고령층"}, "compare": True}).json()
    assert all(d[k] is not None for k in _ALL)                # 라우트에서도 전 항목 제안


def test_meta_reason_confidence_based_on(monkeypatch):
    """§5: 각 추천 필드에 이유·확신도·참고 입력(meta)이 담기고, 확신도는 정규화된다."""
    monkeypatch.setattr(llm, "is_dummy", lambda: False)
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: {
        "description": {"value": "설명", "reason": "문제 기반 요약", "confidence": "high",
                        "based_on": ["problem", "존재하지않음"]},
        "keywords": {"value": ["a", "b"], "reason": "도메인", "confidence": "이상값", "based_on": []},
    })
    out = suggest.suggest_fields("P", existing={"problem": "복약 혼동"})
    m = out["meta"]["description"]
    assert m["reason"] == "문제 기반 요약" and m["confidence"] == "high"
    assert m["based_on"] == ["problem"]                       # 알 수 없는 필드 id 제거
    assert out["meta"]["keywords"]["confidence"] == "medium"  # 잘못된 확신도 → medium 정규화


def test_meta_only_for_suggested_fields(dummy):
    """사용자 입력 필드(보존)에는 meta 를 만들지 않는다."""
    out = suggest.suggest_fields("P", existing={"target_user": "U"})
    assert "target_user" not in out["meta"]                   # 보존 필드는 meta 없음
    assert "description" in out["meta"]                        # 채운 필드만 meta
