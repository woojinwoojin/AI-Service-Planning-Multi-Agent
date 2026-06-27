# AI-Service-Planning-Multi-Agent
# AI 서비스 기획 보조 Multi-Agent

> 시장분석 · PESTEL · 경쟁사 분석 · 기획서 초안 자동화 프로젝트

## 1. 프로젝트 개요

본 프로젝트는 사용자가 AI 서비스 아이디어 또는 기획서 초안을 입력하면,  
Multi-Agent Workflow를 통해 시장조사, PESTEL 분석, 경쟁사 비교, 기획서 초안 작성, 출처 검증, DOCX/PPTX 산출물 생성을 자동화하는 서비스입니다.

기존 AI 발표자료 제작 도구가 결과물 생성과 디자인 자동화에 집중한다면,  
본 프로젝트는 **서비스 기획 과정 자체를 구조화하고 자동화**하는 것을 목표로 합니다.

---

## 2. 해결하고 싶은 문제

AI 서비스 기획 과정에서는 다음과 같은 문제가 반복적으로 발생합니다.

- 시장조사, PESTEL 분석, 경쟁사 비교, 기획서 초안 작성에 많은 시간이 소요됨
- 최신 웹 정보 반영과 출처 관리가 수작업으로는 일관성을 확보하기 어려움
- 기획 단계별 산출물 형식이 표준화되지 않아 검토와 재사용이 비효율적임
- LLM이 생성한 내용의 사실성, 최신성, 출처 신뢰도를 사람이 직접 검증해야 함

---

## 3. 핵심 목표

- 사용자의 아이디어 입력을 구조화된 기획 템플릿으로 변환
- 웹 검색을 통해 최신 시장 정보와 출처 수집
- 출처의 신뢰도, 최신성, 중복 여부 검증
- 시장조사, PESTEL, 경쟁사 분석을 Agent별로 수행
- 분석 결과를 통합하여 기획서 초안 생성
- Fact-check / Critic Agent를 통해 환각 및 근거 부족 여부 평가
- DOCX/PPTX 형식의 산출물 생성
- FastAPI + LangGraph 기반 Multi-Agent Workflow 구현
- GitHub Actions 기반 CI/CD 파이프라인 구성

---

## 4. 전체 시스템 구조

```text
User
 ↓
Web UI
 ↓
FastAPI Server
 ↓
LangGraph Orchestrator
 ↓
Multi-Agent Workflow
 ↓
DB / Source Storage / Output Storage
 ↓
JSON Result + DOCX/PPTX Output
```

### 상세 구조

```text
[User]
  ↓
[Web UI]
  ↓
[FastAPI]
  ├─ Project API
  ├─ Workflow Run API
  ├─ Source API
  └─ Export API
  ↓
[LangGraph Orchestrator]
  ↓
[Input Structuring Agent]
  ↓
[Research Agent]
  ↓
[Source Verification Agent]
  ↓
 ┌───────────────────────────────┐
 │ Parallel Analysis Layer        │
 │                               │
 │  ├ Market Research Agent       │
 │  ├ PESTEL Analysis Agent       │
 │  ├ Competitor Analysis Agent   │
 │  └ Customer Problem Agent      │
 └───────────────────────────────┘
  ↓
[Synthesis Agent]
  ↓
[Proposal Writing Agent]
  ↓
[Fact-check / Critic Agent]
  ↓
[Export Agent]
  ↓
[DOCX / PPTX / JSON Output]
```

---

## 5. Multi-Agent를 사용하는 이유

단순히 여러 Agent를 순차적으로 호출하는 구조는 Single-Agent Chain과 큰 차이가 없을 수 있습니다.  
따라서 본 프로젝트에서는 Multi-Agent의 장점을 다음과 같이 활용합니다.

### 5.1 역할별 전문화

각 Agent는 하나의 명확한 역할을 담당합니다.

- 시장조사 Agent: 시장 현황, 성장성, 트렌드 분석
- PESTEL Agent: 정치, 경제, 사회, 기술, 환경, 법률 요인 분석
- 경쟁사 Agent: 직접/간접/잠재 경쟁사 비교
- Critic Agent: 출처 충실도, 논리성, 환각 여부 평가

### 5.2 병렬 분석 구조

Source Verification 이후에는 시장분석, PESTEL, 경쟁사 분석을 병렬적으로 수행할 수 있습니다.

```text
Verified Sources
 ├─ Market Research Agent
 ├─ PESTEL Analysis Agent
 ├─ Competitor Analysis Agent
 └─ Customer Problem Agent
```

### 5.3 검증 및 재실행 루프

Critic Agent가 특정 섹션의 품질이 낮다고 판단하면 전체 기획서를 다시 생성하는 것이 아니라,  
문제가 있는 Agent만 재실행할 수 있습니다.

```text
Critic Agent Evaluation
 ├─ 출처 부족 → Web Research Agent 재실행
 ├─ PESTEL 미흡 → PESTEL Agent 재실행
 ├─ 경쟁사 분류 오류 → Competitor Agent 재실행
 └─ 전체 기준 통과 → Export Agent 실행
```

---

## 6. Agent 역할 정의

| No | Agent | 역할 | Input | Output | MVP |
|---|---|---|---|---|---|
| 1 | Input Structuring Agent | 사용자 아이디어/기획서를 구조화하고 핵심 키워드 추출 | 사용자 입력 | Organized Template, Keywords | Must |
| 2 | Query Planning Agent | 시장조사, PESTEL, 경쟁사 분석에 필요한 검색 쿼리 생성 | Organized Template, Keywords | Search Queries | Must |
| 3 | Web Research Agent | 검색 쿼리 기반 최신 웹 자료 수집 | Search Queries | Search Results / Evidence Pool | Must |
| 4 | Source Verification Agent | 출처의 신뢰도, 최신성, 중복 여부 검증 | Search Results | Verified Sources | Must |
| 5 | Market Research Agent | 시장 규모, 성장 배경, 주요 트렌드, 고객 문제 분석 | Organized Template, Verified Sources | Market Research Result | Must |
| 6 | PESTEL Analysis Agent | Political, Economic, Social, Technological, Environmental, Legal 분석 | Market Result, Verified Sources | PESTEL Table | Must |
| 7 | Competitor Analysis Agent | 직접/간접/잠재 경쟁사 분석 및 비교표 생성 | Organized Template, Verified Sources | Competitor Comparison Table | Must |
| 8 | Customer Problem Agent | 타깃 사용자, Pain Point, 니즈 분석 | Organized Template, Verified Sources | Customer Problem Result | Should |
| 9 | Synthesis Agent | 분석 결과를 기획서 템플릿 구조로 통합 | Market, PESTEL, Competitor Result | Structured Planning Result | Must |
| 10 | Proposal Writing Agent | 구조화된 분석 결과를 바탕으로 기획서 초안 작성 | Structured Planning Result | Proposal Draft | Must |
| 11 | Fact-check / Critic Agent | 생성된 초안의 출처 일치성, 논리성, 환각 위험 평가 | Proposal Draft, Verified Sources | Evaluation Report | Must |
| 12 | Export Agent | 최종 기획서를 DOCX/PPTX 형식으로 변환 | Final Proposal | DOCX, PPTX | Should |

---

## 7. MVP 기능 범위

### Must Have

- 사용자 아이디어 입력
- 입력 내용 구조화 및 키워드 추출
- 웹 검색 연동
- 출처 저장 및 신뢰도/최신성 평가
- 시장조사 자동 생성
- PESTEL 분석 자동 생성
- 경쟁사 비교표 자동 생성
- 기획서 초안 생성
- Critic Agent 기반 평가
- 결과 JSON 출력
- DOCX Export

### Should Have

- PPTX Export
- Agent 실행 로그 저장
- 섹션별 재생성
- 평가 점수 시각화
- 간단한 웹 UI

### Could Have

- TAM/SAM/SOM 자동 계산
- Persona / Customer Journey Map 생성
- Lean Canvas 자동 생성
- PRD 초안 자동 생성
- 템플릿 선택 기능

### Won't Have

- 회원가입/로그인
- 결제 시스템
- 실시간 공동 편집
- 고급 PPT 디자인 편집
- 완전 자동 사업 타당성 판단

---

## 8. 주요 산출물

- 시장조사 결과
- PESTEL 분석표
- 경쟁사 비교표
- 고객 문제 및 Pain Point 분석
- 서비스 기획서 초안
- Fact-check / Critic 평가 결과
- 출처 목록
- DOCX 파일
- PPTX 파일
- Agent 실행 로그
- 최종 발표자료

---

## 9. 기술 스택

| 영역 | 기술 |
|---|---|
| Backend | FastAPI |
| Agent Workflow | LangGraph |
| LLM | CLOVA Studio 또는 OpenAI API |
| Web Search | Tavily / Serper / Brave Search API |
| DB | SQLite |
| Vector DB / RAG | ChromaDB 또는 FAISS |
| Export | python-docx, python-pptx |
| Frontend | Streamlit 또는 React |
| CI/CD | GitHub Actions |
| Deployment | Render / Railway / EC2 |

---

## 10. FastAPI API 설계 초안

| Method | Endpoint | 설명 |
|---|---|---|
| POST | `/projects` | 새 기획 프로젝트 생성 |
| GET | `/projects` | 프로젝트 목록 조회 |
| GET | `/projects/{project_id}` | 프로젝트 상세 조회 |
| POST | `/projects/{project_id}/run` | Multi-Agent Workflow 실행 |
| GET | `/projects/{project_id}/status` | 현재 실행 상태 조회 |
| GET | `/projects/{project_id}/sources` | 출처 목록 조회 |
| GET | `/projects/{project_id}/sections` | 생성된 기획서 섹션 조회 |
| POST | `/projects/{project_id}/evaluate` | Critic Agent 평가 실행 |
| GET | `/projects/{project_id}/export/docx` | DOCX 파일 다운로드 |
| GET | `/projects/{project_id}/export/pptx` | PPTX 파일 다운로드 |

---

## 11. State 설계 초안

LangGraph에서는 전체 workflow 상태를 하나의 State로 관리합니다.

```python
class PlanningState(TypedDict):
    user_input: str

    organized_template: dict
    keywords: list[str]

    search_queries: list[str]
    raw_sources: list[dict]
    verified_sources: list[dict]

    market_result: dict
    pestel_result: dict
    competitor_result: dict
    customer_problem_result: dict

    structured_planning_result: dict
    proposal_draft: str

    evaluation_report: dict
    final_proposal: str

    export_files: dict
```

---

## 12. 데이터베이스 설계 초안

### Project

| Field | Type | Description |
|---|---|---|
| id | int | 프로젝트 ID |
| title | string | 프로젝트명 |
| topic | string | 사용자 입력 주제 |
| target_market | string | 대상 시장 |
| status | string | 진행 상태 |
| created_at | datetime | 생성일 |
| updated_at | datetime | 수정일 |

### Source

| Field | Type | Description |
|---|---|---|
| id | int | 출처 ID |
| project_id | int | 연결 프로젝트 |
| title | string | 출처 제목 |
| url | string | URL |
| source_type | string | 공식자료/언론/리포트/블로그 |
| published_date | date | 발행일 |
| retrieved_at | datetime | 조회일 |
| reliability_score | int | 신뢰도 점수 |
| freshness_score | int | 최신성 점수 |
| used_in_section | string | 사용된 섹션 |

### AgentRun

| Field | Type | Description |
|---|---|---|
| id | int | 실행 ID |
| project_id | int | 연결 프로젝트 |
| agent_name | string | Agent 이름 |
| input | text | 입력값 |
| output | text | 출력값 |
| status | string | 성공/실패 |
| created_at | datetime | 실행 시간 |

### DraftSection

| Field | Type | Description |
|---|---|---|
| id | int | 섹션 ID |
| project_id | int | 연결 프로젝트 |
| section_name | string | 섹션명 |
| content | text | 생성 내용 |
| source_ids | list | 연결 출처 |
| evaluation_score | int | 평가 점수 |
| feedback | text | 개선 피드백 |

---

## 13. 5주 개발 일정

| 주차 | 목표 | 핵심 산출물 |
|---|---|---|
| 1주차 | 기획 확정, 아키텍처 설계, FastAPI/LangGraph 세팅 | 요구사항 정의서, Agent 설계표, API 명세 |
| 2주차 | 웹 검색, 출처 저장, Source Verification 구현 | 검색 Agent, Source DB, Verified Sources |
| 3주차 | 시장분석, PESTEL, 경쟁사 분석 Agent 구현 | Market Result, PESTEL Table, Competitor Table |
| 4주차 | 기획서 초안 생성 및 DOCX/PPTX Export 구현 | Proposal Draft, DOCX, PPTX |
| 5주차 | 평가, 버그 수정, 배포, 최종 발표 준비 | Demo, Evaluation Result, Final Presentation |

---

## 14. 평가 기준

| 평가 항목 | 설명 |
|---|---|
| 분석 깊이 | 시장분석과 PESTEL이 표면적이지 않은가 |
| 출처 충실도 | 주요 주장에 출처가 연결되어 있는가 |
| 최신성 | 최신 웹 정보가 반영되었는가 |
| 경쟁사 분석력 | 직접/간접/잠재 경쟁사를 구분했는가 |
| 기획서 구조성 | 문제 정의 → 시장성 → 해결책 → 기능 → 로드맵 흐름이 자연스러운가 |
| 환각 위험 | 출처 없는 수치, 기업명, 시장 정보가 생성되지 않았는가 |
| 문서 완성도 | DOCX/PPTX 결과물이 제출 가능한 수준인가 |
| Multi-Agent 효과 | Single-Agent 대비 분석 누락 감소와 품질 향상이 있는가 |

---

## 15. GitHub Actions 기반 CI/CD 계획

### CI

GitHub에 코드가 push되면 자동으로 다음 작업을 수행합니다.

```text
push / pull request
 ↓
Python 환경 세팅
 ↓
requirements.txt 설치
 ↓
pytest 실행
 ↓
FastAPI import 및 기본 테스트
```

### CD

CI가 성공하면 배포 서버에 최신 코드를 반영합니다.

```text
GitHub Repository
 ↓
GitHub Actions
 ↓
Render / Railway / EC2
 ↓
Uvicorn으로 FastAPI 실행
 ↓
사용자 접속
```

MVP에서는 우선 CI를 먼저 구현하고, 시간이 남으면 CD까지 연결합니다.

---

## 16. 프로젝트 차별점

| 기존 AI 서비스 | 본 프로젝트 |
|---|---|
| 발표자료 생성 중심 | 서비스 기획 과정 자동화 중심 |
| 디자인과 문서 생성에 집중 | 시장조사, PESTEL, 경쟁사 분석 포함 |
| 출처 관리 약함 | 출처 신뢰도와 최신성 검증 |
| 사용자가 직접 검증 | Fact-check / Critic Agent가 평가 |
| 범용 프롬프트 기반 | KOSENA 기획 방법론 기반 템플릿 사용 |
| 결과물 중심 | 중간 산출물, 실행 로그, 평가 결과까지 관리 |

---

## 17. 멘토링 질문 리스트

### Multi-Agent 구조 관련

- 이 프로젝트에서 Multi-Agent의 핵심을 병렬성으로 봐야 하는지, 역할별 전문화와 검증 루프로 봐야 하는지 궁금합니다.
- 시장분석, PESTEL, 경쟁사 분석을 병렬적으로 실행하는 구조가 기업체에서 의도한 방향과 맞는지 확인하고 싶습니다.
- 5주 MVP 기준으로 실제 병렬 실행까지 구현하는 것이 중요한지, Agent별 역할 분리와 중간 산출물 저장이 더 중요한지 궁금합니다.

### 기획 범위 관련

- KOSENA 템플릿 전체를 자동화하는 것이 좋은지, 핵심 섹션만 자동화하는 것이 좋은지 궁금합니다.
- DOCX와 PPTX 중 어떤 산출물을 더 우선해야 하는지 확인하고 싶습니다.
- TAM/SAM/SOM, Persona, PRD까지 MVP에 포함해야 할지 궁금합니다.

### 기술 구현 관련

- FastAPI + LangGraph 구조가 적절한지 확인하고 싶습니다.
- Web Search API는 어떤 도구를 사용하는 것이 현실적인지 궁금합니다.
- RAG를 MVP에 포함하는 것이 필요한지, 우선 Source DB만 구현해도 되는지 궁금합니다.
- GitHub Actions 기반 CI/CD는 어느 수준까지 구현하는 것이 적절한지 궁금합니다.

---

## 18. 실행 예시

### 사용자 입력

```text
AI 기반 회의록 자동화 서비스 기획서를 만들어줘.
```

### 예상 결과

- 시장조사 요약
- PESTEL 분석표
- 경쟁사 비교표
- 고객 Pain Point
- 서비스 컨셉
- 기획서 초안
- 출처 목록
- 평가 결과
- DOCX/PPTX 파일

---

## 19. 실행 명령 예시

### Local Development

```bash
uvicorn app.main:app --reload
```

### API Docs

```text
http://localhost:8000/docs
```

---

## 20. Repository 구조 초안

```text
project/
 ├─ app/
 │   ├─ main.py
 │   ├─ api/
 │   │   ├─ project.py
 │   │   ├─ run.py
 │   │   └─ export.py
 │   ├─ agents/
 │   │   ├─ input_structuring.py
 │   │   ├─ web_research.py
 │   │   ├─ source_verification.py
 │   │   ├─ market_research.py
 │   │   ├─ pestel_analysis.py
 │   │   ├─ competitor_analysis.py
 │   │   ├─ synthesis.py
 │   │   ├─ proposal_writing.py
 │   │   └─ critic.py
 │   ├─ graph/
 │   │   └─ workflow.py
 │   ├─ services/
 │   │   ├─ search_service.py
 │   │   ├─ source_service.py
 │   │   ├─ docx_exporter.py
 │   │   └─ pptx_exporter.py
 │   ├─ db/
 │   │   ├─ models.py
 │   │   └─ database.py
 │   └─ schemas/
 │       └─ project.py
 ├─ outputs/
 │   ├─ docx/
 │   └─ pptx/
 ├─ tests/
 ├─ requirements.txt
 ├─ .env.example
 └─ README.md
```
