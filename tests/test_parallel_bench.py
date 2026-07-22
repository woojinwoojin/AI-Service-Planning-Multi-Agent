"""직렬 vs 병렬 비교 측정 도구 (PR-4) 테스트 — 더미/합성 데이터, 실제 API 호출 없음."""
from __future__ import annotations

from app.agents.draft_writer import SECTIONS
from app.services import parallel_bench


def _draft(empty_section: str | None = None) -> str:
    parts = ["# 테스트 기획서\n"]
    for s in SECTIONS:
        parts.append(f"## {s}")
        if s == empty_section:
            parts.append("")                                   # 본문 비움
        elif s == "PESTEL 분석":
            parts.append("| 요인 | 내용 |\n|---|---|\n| Political | x |")
        else:
            parts.append("내용 문장.")
        parts.append("")
    return "\n".join(parts)


def _state(draft: str) -> dict:
    return {
        "final_draft": draft,
        "research_result": {"source_objects": [{"url": "https://a"}, {"url": "https://b"}]},
        "competitor_sources": [{"url": "https://a"}, {"url": "https://c"}],   # a 는 중복
        "verification_result": {"claims": [{"status": "supported"}, {"status": "unsupported"},
                                           {"status": "supported"}]},
    }


def test_deterministic_quality_full_doc():
    q = parallel_bench.deterministic_quality(_state(_draft()))
    assert q["sections_present"] == len(SECTIONS) and q["sections_complete"]
    assert q["sections_ordered"] is True
    assert q["empty_sections"] == 0
    assert q["pestel_table"] is True
    assert q["unique_source_urls"] == 3                        # a,b,c (a 중복 제거)
    assert q["verification"] == {"supported": 2, "unsupported": 1, "uncertain": 0}


def test_deterministic_quality_detects_empty_section():
    q = parallel_bench.deterministic_quality(_state(_draft(empty_section="기대효과")))
    assert q["sections_present"] == len(SECTIONS)              # 제목은 존재
    assert q["empty_sections"] == 1                            # 본문이 빈 섹션 1개


def test_run_once_dummy_returns_metrics(monkeypatch):
    from app.services import llm
    monkeypatch.setattr(llm, "is_dummy", lambda: True)         # 무료·결정론
    rec = parallel_bench.run_once({"project_name": "P", "problem": "x"}, "parallel")
    assert rec["mode"] == "parallel"
    assert rec["wall_time_ms"] is not None and rec["wall_time_ms"] >= 0
    assert "quality" in rec and rec["quality"]["sections_total"] == len(SECTIONS)


def test_serial_and_parallel_quality_equal_dummy(monkeypatch):
    """비열등성: 같은 입력에서 직렬/병렬 결정론 품질 지표가 동일해야 한다."""
    from app.services import llm
    monkeypatch.setattr(llm, "is_dummy", lambda: True)
    topic = {"project_name": "동등", "problem": "P", "target_user": "U", "description": "D"}
    s = parallel_bench.run_once(topic, "serial")["quality"]
    p = parallel_bench.run_once(topic, "parallel")["quality"]
    assert s == p


def test_aggregate_medians_and_reduction():
    def _run(mode, wall, tokens):
        return {"topic": "t", "mode": mode, "rep": 0, "wall_time_ms": wall,
                "llm_latency_sum_ms": 2000, "calls": 10, "total_tokens": tokens,
                "est_cost_usd": 0.01, "fallback_calls": 0, "run_status": "success",
                "quality": {"sections_complete": True, "empty_sections": 0, "unique_source_urls": 5}}
    runs = [_run("serial", 1000, 100), _run("serial", 1200, 100),
            _run("parallel", 600, 100), _run("parallel", 640, 100)]
    agg = parallel_bench.aggregate(runs)
    assert agg["serial"]["wall_time_ms_median"] == 1100.0      # median(1000,1200)
    assert agg["parallel"]["wall_time_ms_median"] == 620.0
    assert agg["serial"]["sections_complete_rate"] == 1.0
    assert agg["summary"]["wall_time_reduction_pct"] == round((1100 - 620) / 1100 * 100, 1)
    assert agg["summary"]["token_diff_pct"] == 0.0            # 병렬화는 토큰을 바꾸지 않음
