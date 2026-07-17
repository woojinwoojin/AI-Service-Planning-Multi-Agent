"""3일 차 완료 기준 검증용 CLI.

더미 데이터로 입력 → Research → PESTEL → Writer → Reviewer → 출력 전체 관통 확인.
실행: python run_demo.py
"""
from __future__ import annotations

import sys

# Windows 콘솔(cp949)에서 한글/기호 출력 깨짐 방지
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

from app.graph.workflow import run_workflow

SAMPLE_INPUT = {
    "project_name": "AI 기반 대학생 진로 설계 서비스",
    "description": "사용자의 전공·역량·관심 직무를 분석하고 학습 및 취업 준비 로드맵을 제공한다.",
    "target_user": "전공과 진로를 고민하는 대학생",
    "problem": "대학생들이 자신의 역량과 진로에 맞는 준비 방법을 찾기 어렵다.",
    "keywords": ["진로", "대학생", "취업", "역량 분석"],
}


def main() -> None:
    print("=" * 60)
    print("Multi-Agent 워크플로 실행 (더미 모드 가능)")
    print("=" * 60)

    state = run_workflow(SAMPLE_INPUT)

    print("\n--- 실행 로그 ---")
    for line in state.get("logs", []):
        print(" ", line)

    print("\n--- Research 결과(요약) ---")
    print(" ", state["research_result"].get("market_overview", ""))

    print("\n--- PESTEL 결과(키 확인) ---")
    print(" ", list(state["pestel_result"].keys()))

    print("\n--- Reviewer 평가 ---")
    print("  총점:", state["review_result"].get("total_score"))
    print("  재작성 횟수:", state.get("revision_count"))

    print("\n--- 최종 기획서(첫 400자) ---")
    print(state["final_draft"][:400])

    # 6개 산출물이 모두 존재하는지 확인
    required = ["structured_input", "research_result", "pestel_result",
               "draft", "review_result", "final_draft"]
    missing = [k for k in required if not state.get(k)]
    print("\n" + "=" * 60)
    if missing:
        print("❌ 누락된 산출물:", missing)
    else:
        print("✅ 전체 파이프라인 관통 성공 — 6개 산출물 모두 생성됨")
    print("=" * 60)


if __name__ == "__main__":
    main()
