# 2차 코드리뷰 대응 계획 (보류 — 나중에 진행)

> 작성 2026-07-19 · 상태 **보류(未착수)** · 발표자료 제작 ~2026-07-29(D-10)
> 배경: 1차 리뷰(item 2~12)는 PR #23~#28로 완료([[code-review-response]]). 이 문서는 **2차 리뷰** 대응 계획.
> 파일명 영문화: 2.2 피드백대로 이 문서는 영문명(`review2-action-plan.md`)으로 생성.

---

## 0. 사실 확인 결과 (착수 전 검증 완료)

- **git 추적 상태**: `data/`·`outputs/`·`__pycache__`·`.pytest_cache`·`.ruff_cache`는 이미 `.gitignore`로 제외됨. **git에 추적된 나쁜 파일 없음.** → 2.1은 `git archive`만으로 해결(코드변경 불필요).
- **lock 파일**: `requirements-lock.txt`에 torch/pygame/PyQt5 등 노이즈 25개 확인 → 2.3은 **내 실수**(전체환경 freeze), 재생성 필요.
- **revise 초기화**: `app/api/routes.py`의 `/revise`가 `revision_count: 0`, `logs: []`로 리셋 → 2.4 실버그 확정.

---

## 1. 잘 반영된 부분 (1차 리뷰 결과, 유지)

①최종 기획서 재평가(initial/final 분리) ②/revise 참고자료 유지+회귀테스트 ③HITL 이력 저장 ④실행 품질(run_status) ⑤"근거 일치성 검증" 명칭 ⑥검색 프롬프트 인젝션 방어 ⑦비교 실험 한계 명시 ⑧다중 모델 비교. → 발표에서 "리뷰 반영해 개선"으로 어필 가능.

---

## 2. 배치 A — 제출환경 정리 + 남은 버그 (착수 시 반나절, 코드)

| # | 항목 | 처리 방법 | 규모 |
|---|---|---|---|
| 2.3 | lock이 전체환경 freeze(노이즈 25개) | 깨끗한 venv(`python -m venv .venv-clean`)에 `requirements.txt`만 설치 후 freeze → 프로젝트 전용 lock 재생성 | 소 |
| 2.4 | /revise가 revision_count·logs 초기화 | `revision_count: base.get("revision_count",0)`, `logs: list(base.get("logs",[]))`로 승계. (더 정확히는 auto/manual revision_count 분리) | 소 |
| 2.5 | update_run이 메타 미갱신 | `update_run()`에서 `project_name`·`model`도 갱신, `updated_at` 컬럼 추가(ALTER TABLE 방어 마이그레이션). `created_at`은 최초값 유지 | 중 |
| 2.6 | 비교 심판 temperature 0.3 | 심판 호출만 temperature=0(결정론)으로 분리. `complete_json`→`_get_model`에 temperature 파라미터 전달 | 중 |
| 2.2 | 한글 파일명 이식성 | `docs/내일_할일.md`→`docs/next_tasks.md`, `docs/발표용_비교정리.md`→`docs/presentation_comparison.md` 등 영문화 + 참조 갱신 | 소 |
| 2.1 | 제출 ZIP 정리 | 코드변경 없이 `git archive --format=zip HEAD -o release.zip`로 추적 파일만 패키징. README/스크립트에 명시 | 소 |

**완료 기준**: pytest 통과 · ruff 통과 · `git status --short` 비어있음 · ZIP에 `.env`/`.git`/DB/cache 없음.

---

## 3. 배치 B·C — 전략적 강화 (D-10 발표 준비와 병행)

리뷰어 §3의 10일 일정과 우리 D-10 계획이 정합. 새 Agent 추가보다 **안정성·실험 신뢰도·발표 증거** 강화에 집중.

- **2.7 통합 테스트 + CI**: `@pytest.mark.integration`(API 키 있을 때만), GitHub Actions(pytest+ruff). 실제 LLM 1회·Tavily 1회·전체 워크플로 1주제·DOCX 열기·/revise 연속 2회.
- **2.8 출처 품질 지표**: URL 개수 외에 **표본 10개 수동 검증표**(접속 가능·주장 관련성·공공/연구기관 비율·중복 도메인·최신성·존재하지 않는 출처). 자동 검증 Agent보다 빠르고 신뢰도 높음.
- **A/B/C 3단계 비교(가장 추천)**: A(단일 프롬프트) · B(단일+동일 검색자료) · C(전체 Multi-Agent). **A→B=웹검색 효과, B→C=분업·검토 효과** 분리. 연구적 완성도 최대 상승.
- **비교 확대**: 6→8~10주제(분야 비중복), 심판 temp=0 고정.
- **사람 평가(블라인드)**: 2명 이상에게 A/B/C 익명 평가(5항목×20점), LLM 평가와 방향 비교. 어려우면 본인 블라인드 + 한계 명시.
- **UI 발표력**: 초안→최종 점수 변화, 수정 전후 diff, 클릭 가능한 출처 카드, Agent 타임라인/토큰, degraded 강조, 단일 vs 멀티 좌우 비교.
- **속도·비용 + Ablation**: 느린 Agent 탐지, `전체 / -Polish / -Reviewer / -Competitor` 품질 비교로 기여 낮은 단계는 데모모드에서 제외. 재작성 임계값 90 적정성 점검.
- **발표자료·데모영상**: 아키텍처 그림·LangGraph 흐름도·UI 캡처·비교 그래프·점수 변화·출처 연결 화면·1~2분 데모영상.

---

## 4. D-10(~2026-07-29) 제출 체크리스트 (리뷰어 10일차)

- [ ] `pytest -q` · `ruff check .` 통과
- [ ] 실제 모드 1회 전체 실행 / DOCX 다운로드 / 이력 조회 / 사용자 수정 2회 / degraded 경고 / 더미 모드(키 없음) 확인
- [ ] README 순서대로 **새 환경에서** 설치·실행 검증
- [ ] 발표자료 숫자 ↔ JSON 원자료 일치
- [ ] 제출 ZIP 내부 확인(app/tests/docs/README/ROADMAP/requirements·lock/run_*.py/.env.example/.gitattributes; **DB·키·캐시·개인 실행결과 제외**)
- [ ] **API 키 rotate**(채팅 노출분) — 크레딧 보호

---

## 참고
- 1차 리뷰 대응: [[code-review-response]] (PR #23~#28)
- 발표 비교 프레이밍: [[comparison-experiment-decision]] · `docs/발표용_비교정리.md`
- 아키텍처/ADR: `docs/ARCHITECTURE.md`
