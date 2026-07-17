# 13일 압축 개발 로드맵

> 기준일: 2026-07-17 (1일 차) · 목표 제출: 약 2주 뒤
> 이 문서는 README(풀버전 12-Agent 기획)를 **발표에서 점수가 나오는 최소 코어**로 압축한 실행 계획입니다.

---

## 0. 최종 제출 목표

발표자료 제출 시점에 다음 네 가지가 준비되어 있으면 성공입니다.

1. 사용자가 사업 아이디어를 입력할 수 있음
2. 여러 AI Agent가 시장분석·PESTEL·기획서 작성을 **순차 수행**함
3. 검토 Agent가 기획서를 평가하고 **개선 의견 → 1회 재작성**을 수행함
4. **단일 LLM 방식 vs Multi-Agent 방식**의 결과를 비교한 자료가 있음

> 완성형 서비스보다 **Multi-Agent 구조를 쓴 이유와 실제 개선 결과**를 보여주는 것이 핵심.

---

## 1. 풀버전 기획 vs 13일 압축 로드맵 (1일 차 비교 근거)

`README.md` / `기획안1.md` / `기획안2.md`는 **모두 동일한 풀버전 문서**입니다.
따라서 "기획서 2종 비교"는 **풀버전(12-Agent) vs 압축버전(4-Agent)** 비교로 대체하며,
이 "왜 범위를 줄였는가"가 곧 발표 논리가 됩니다.

| 항목 | 풀버전 (README) | 13일 압축버전 | 판정 |
|---|---|---|---|
| Agent 수 | 12개 | **4개** (Research·PESTEL·Draft·Reviewer) | 축소 |
| 입력 구조화 | 별도 Agent | 전처리 함수 | 강등 |
| 웹 검색 | 필수 | **고정 자료로 시작** (시간 남으면 실검색) | 후순위 |
| 출처 검증 Agent | 필수 | 제외 | 삭제 |
| 경쟁사 분석 | 필수 | 제외 | 삭제 |
| DB (SQLite) | 4테이블 | State면 충분 | 후순위 |
| DOCX/PPTX | 필수/Should | Markdown 저장이면 충분 | 후순위 |
| CI/CD | 필수 | 제외 | 삭제 |
| **단일 vs 멀티 비교실험** | 없음 | **핵심 (10일 차)** | 신규 |

---

## 2. 구현할 4개 Agent

| 구성 | 역할 |
|---|---|
| Research Agent | 시장·산업 자료 조사 및 핵심 내용 정리 |
| PESTEL Agent | 조사 결과를 근거로 PESTEL 분석 |
| Draft Writer Agent | 분석 결과를 기획서 형식으로 작성 |
| Reviewer Agent | 평가 기준에 따라 검토하고 개선점 제시 |

입력 구조화는 별도 Agent가 아니라 **전처리 함수**로 처리.

### 실행 흐름

```text
사용자 아이디어 입력
  → 입력 구조화(함수)
  → Research Agent
  → PESTEL Agent
  → Draft Writer Agent
  → Reviewer Agent
  → (개선 지시 1회 반영) Draft Writer 재작성
  → 최종 기획서 출력
```

Human-in-the-Loop는 단순하게: **AI 초안 → 사용자 수정 요청 입력 → Draft Writer 재작성**.

---

## 3. 반드시 구현 / 제외 기능

**반드시:** 아이디어 입력 · 시장조사 · PESTEL · 기획서 초안 · 평가 · 1회 재작성 · 최종 화면 · Agent별 결과 확인 · 진행 상태(로그) 표시.

**제외:** 로그인/회원 · 협업 · 권한관리 · PPT 자동생성 · 고급 DOCX · 방법론 선택 · Jira/Notion 연동 · 버전관리 · 다중 LLM 자동비교 · 실시간 스트리밍 · 화려한 프론트.

---

## 4. 일자별 일정

| 일차 | 목표 | 완료 기준 (Definition of Done) |
|---|---|---|
| **1** | 범위 확정 | 풀↔압축 비교표, 핵심 1문장, MVP/제외 목록, 4-Agent 역할 확정 |
| **2** | PRD + 데이터 구조 | 2~3p PRD, `ProjectState`, Agent 입출력 명세 → `docs/PRD.md` |
| **3** | 프로젝트 골격 | 더미 데이터로 입력→Research→PESTEL→Writer→Reviewer→출력 관통 |
| **4** | Research Agent | 아이디어→시장조사 JSON 출력 (고정 자료 근거) |
| **5** | PESTEL Agent | Research 결과만 근거로 P/E/S/T/E/L 6항목 표 |
| **6** | Draft Writer Agent | 고정된 기획서 서식 1종 안정 생성 |
| **7** | Reviewer Agent | 5항목 100점 평가 + 개선지시 → **1차 마감선** |
| **8** | 전체 연결 | 실제 입력 1건이 처음~끝 관통 → **2차 마감선** |
| **9** | 최소 UI + 피드백 | 화면 3개(입력/결과/최종), MVP 고정(freeze) |
| **10** | 단일 vs 멀티 비교실험 | 주제 3개, 동일 기준 점수표 → **발표 하이라이트** |
| **11** | 테스트·오류 수정 | 예외처리, 신규 기능 금지 |
| **12** | 발표자료 제작 | 초안 완성 (화면 캡처 + Agent별 출력 예시 포함) |
| **13** | 최종 점검·제출 | 오탈자, 백업(영상/캡처/JSON), README, 제출 |

### 핵심 마감선
- **7일:** 4개 Agent 개별 동작
- **8일:** 전체 워크플로 관통 (안 되면 UI를 더 줄인다)
- **9일:** 기능 추가 중단, MVP 고정
- **10일:** 비교 실험 결과 준비
- **12일:** 발표자료 초안 완성
- **13일:** 오류 수정·제출만

---

## 5. Agent 입출력 명세 (요약)

**Research Agent** — 입력: 사업 아이디어·목표 사용자·목표 시장·키워드
```json
{ "market_overview": "", "industry_trends": [], "customer_needs": [],
  "competitors": [], "opportunities": [], "risks": [], "sources": [] }
```

**PESTEL Agent** — Research 결과만 근거. 항목별 [주요 내용·기회·위협·대응 방향].

**Draft Writer Agent** — 고정 서식:
프로젝트 개요 / 추진 배경 / 문제 정의 / 목표 사용자 / 시장·산업 분석 /
PESTEL 분석 / 제안 서비스 / 핵심 기능 / 차별성 / 기대효과 / 추진 계획 / 위험요인·대응.

**Reviewer Agent** — 평가 5항목 각 20점:
```json
{ "total_score": 0, "strengths": [], "weaknesses": [],
  "unsupported_claims": [], "revision_instructions": [], "section_scores": {} }
```
평가 항목: 문제정의 명확성 / 시장분석 타당성 / 해결방안 구체성 / 서비스 차별성 / 실행 가능성.
→ 수정 지시를 Draft Writer에 **1회만** 전달해 최종본 생성 (자동 반복 최대 1회).

---

## 6. State 구조

```python
class ProjectState(TypedDict):
    user_input: dict
    structured_input: dict
    research_result: dict
    pestel_result: dict
    draft: str
    review_result: dict
    final_draft: str
    revision_count: int
```

---

## 7. 10일 차 비교 실험 (발표 점수 핵심)

| 평가 항목 | 단일 Agent | Multi-Agent |
|---|---|---|
| 문제 정의 명확성 | | |
| 시장분석 구체성 | | |
| PESTEL 완성도 | | |
| 기획서 일관성 | | |
| 근거와 출처 | | |
| **총점** | | |

- 단일 Agent: 하나의 프롬프트로 기획서 전체 생성
- Multi-Agent: 시장조사 → PESTEL → 기획서 → 평가·수정
- 주제 3개, **동일 평가 기준**. 정밀 통계보다 "개선되었음"을 보여주는 것이 목적.

---

## 8. 시간이 부족할 때 삭제 순서

1. Markdown 다운로드
2. 사용자 수정 후 재작성
3. 실제 웹 검색
4. 경쟁사 분석
5. 출처 자동 정리
6. 프론트엔드 디자인

**끝까지 유지:** Research / PESTEL / Draft Writer / Reviewer 4개 · Agent 결과 연결 · 단일 vs 멀티 비교.

---

## 9. 현실적 성공 기준

> 하나의 사업 아이디어를 입력하면 4개 AI Agent가 시장조사·PESTEL·기획서 작성 및 평가를 수행하고,
> 기존 단일 LLM 방식보다 **구조적이고 구체적인 기획서**를 생성하는 프로토타입.
