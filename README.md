# AI 서비스 기획 보조 Multi-Agent

> 아이디어 한 줄을 입력하면 여러 AI Agent가 **시장조사 → 경쟁사 분석 → 고객 문제 → PESTEL → SWOT → 수익모델 → 리스크 → 기획서 작성 → 심사 → 일관성 편집 → 근거 일치성 검증**을 순차 수행해 근거 있는 서비스 기획서를 만들어 주는 도구입니다.

FastAPI + LangGraph 기반의 Multi-Agent 워크플로로, **실제 웹 검색으로 근거를 확보하고 그 출처를 기획서에 인용**하는 것이 핵심 차별점입니다.

> 이 문서는 **실제 구현된 현재 상태**를 설명합니다. 초기 12-Agent 풀버전 구상과 13일 압축 계획, 잘라낸 범위는 [`ROADMAP.md`](ROADMAP.md)를, 상세 명세는 [`docs/PRD.md`](docs/PRD.md)를 참고하세요.

---

## 1. 무엇을 하나

- 사용자가 아이디어(프로젝트명·설명·타깃·문제·키워드)를 입력
- 여러 Agent가 순차적으로 분석을 쌓아 올림 (각 Agent는 앞 단계 결과를 근거로 삼음)
- 실제 웹 검색(Tavily)으로 시장 근거를 수집하고 **출처 URL을 최종 기획서에 인용**
- 심사 Agent가 5항목 100점으로 평가하고, 미달 시 **1회 자동 재작성**
- 일관성 편집 패스가 **섹션 간 중복 제거·연결 보강**을 수행
- 재작성·편집이 끝난 **최종본을 다시 채점**해, 화면 점수가 실제 최종 문서와 일치(초안 → 최종 변화 표시)
- 마지막에 검증 Agent가 기획서 주장을 근거와 대조(근거 확인율 산출)
- 사용자가 결과를 보고 **직접 수정 요청**(Human-in-the-Loop)으로 재작성 가능
- 실행 결과는 **SQLite 이력에 자동 저장**되고, 실행당 **토큰·추정 비용·지연**(관측성)을 함께 표시
- 최종 기획서를 **Markdown / JSON / Word(.docx) / PowerPoint(.pptx)**로 저장·다운로드

---

## 2. 아키텍처 (12-노드 Multi-Agent)

```text
사용자 입력
  → preprocess(입력 구조화 함수)
  → Research Agent        (웹 검색으로 시장조사 + 출처 수집)
  → Competitor Agent      (경쟁사 강점/약점/포지셔닝/차별화)
  → Customer Agent        (페르소나 · Pain · 니즈 · JTBD)
  → PESTEL Agent          (6요인 × 4항목 표)
  → SWOT Agent            (강점/약점/기회/위협)
  → Business Model Agent  (수익원/가격/비용/핵심지표)
  → Risk Agent            (유형별 리스크 + 가능성/영향/대응)
  → Draft Writer Agent    (고정 14섹션 기획서 작성, 실제 출처 인용)
  → Reviewer Agent        (5항목 100점 평가 + 개선지시)
  → (총점 < 90 이면) Draft Writer 재작성 1회
  → Polish                (섹션 간 중복 제거·연결 문장 보강, 구조·표·참고자료 유지)
  → Final Reviewer        (재작성·편집 후 최종본 재평가 → 화면 표시 점수)
  → Verify (근거 일치성)   (기획서 주장 ↔ 수집된 조사 결과 대조, 근거 확인율 산출)
  → 최종 기획서 + Agent별 산출물 + 실행 관측치
```

- **오케스트레이션**: LangGraph `StateGraph`. 모든 노드는 `_safe()`로 감싸 한 Agent가 실패해도 파이프라인이 **처음부터 끝까지 완주**합니다.
- **안정성**: LLM 호출 실패(레이트리밋/네트워크)는 재시도 후 해당 단계 fallback으로 흡수하고, 로그에 정직하게 `fallback`으로 표기합니다.
- **스키마 강제**: 각 Agent는 `_validate()`로 출력 스키마를 강제하고, 누락/타입오류는 중립값으로 채워 다음 Agent가 항상 온전한 입력을 받습니다.
- **관측성**: 실행마다 LLM 호출 수·입출력 토큰·추정 비용(USD)·총 지연(실제 대기시간 `wall_time_ms` / LLM 호출시간 합계 `llm_latency_sum_ms`)·fallback 수를 집계해 결과에 포함합니다.
- **실행 구조 선택(실험)**: `WORKFLOW_MODE=serial|parallel`. 병렬 모드는 Research 이후 서로 독립인 분석 4분기(Competitor→SWOT / Customer / PESTEL→Risk / Business Model)를 동시에 실행하고 Draft에서 합류합니다. **Agent 입력·프롬프트·결과 구조는 직렬과 동일**하고 실행 순서만 달라, 병렬화로 인한 지연 감소를 직렬과 공정하게 비교할 수 있습니다(기본값은 `serial`).
- **단계별 실행시간 계측**: 각 노드를 감싸 stage별 wall time·critical path·coverage를 집계(`timing`). 병렬 `analysis_block`은 노드 duration의 합이 아니라 실제 대기시간(겹침 반영)이라, 어느 구간이 병목인지 정량 확인할 수 있습니다.

---

## 3. Multi-Agent를 쓰는 이유 — 단일 LLM과의 비교

동일한 주제·동일한 서식·동일한 심판으로 **단일 프롬프트 1회 생성**과 **Multi-Agent 파이프라인**을 비교했습니다 (`run_compare.py`).

**6개 주제 · 플랜당 심판 3회 평균 · gpt-4o-mini** 기준:

| 평가 항목 | 단일 프롬프트 | Multi-Agent |
|---|---|---|
| 문제 정의 명확성 | 18.0 | 18.0 |
| 시장분석 구체성 | 16.7 | 17.1 |
| PESTEL 완성도 | 18.9 | 19.3 |
| 기획서 일관성 | 15.6 | 16.1 |
| 근거와 출처 | 15.7 | 16.1 |
| **총점 (LLM 심판)** | **84.9** | **86.5 (+1.6)** |
| **기획서에 포함된 고유 출처 URL 수 (객관)** | **0** | **5** |

- **Multi-Agent가 6주제 중 5개에서 우위**(나머지 1개 주제는 -0.3점 근소 열세).
- 정직한 관찰: **LLM 심판 점수만으로는 격차가 크지 않습니다.** 작은 LLM 심판은 "유창하지만 근거 없는 글"과 "실제 인용된 글"을 잘 구분하지 못합니다.
- **결정적 차이는 객관 지표**: Multi-Agent는 기획서에 실제 출처 URL을 평균 5건 인용하고(독자가 직접 확인 가능), 단일 LLM은 0건입니다(검색을 하지 않으므로). 이 지표는 URL의 **포함 여부**를 셀 뿐 내용의 사실성까지 검증하지는 않지만, 단일 LLM은 URL 자체가 없습니다. Multi-Agent의 가치는 점수가 아니라 **추적 가능한 근거**에 있습니다.
- **정직한 범위**: 이 실험은 **단일 프롬프트 1회 vs 근거 기반 Multi-Agent 파이프라인 전체**(웹검색 + 단계별 분석·검토 포함)를 비교합니다. 따라서 "역할을 여러 Agent로 분업한 효과"와 "웹검색 grounding 효과"가 함께 측정됩니다 — 분업 효과만 따로 증명하는 것은 아닙니다. 순수 분업 효과를 분리하려면 `단일 Agent + 동일 웹검색 자료` 기준선을 추가해야 합니다(향후 과제).

전체 원자료와 주제별 결과는 [`docs/comparison_result.md`](docs/comparison_result.md) 참고.

### 다중 모델 비교

심판을 `gpt-4o-mini`로 고정하고 **생성 모델만 바꿔** 단일 vs Multi 격차가 어떻게 변하는지 확인했습니다 (`run_multimodel.py`, 주제 3개·심판 3회 평균):

| 생성 모델 | 단일 총점 | Multi 총점 | 차이 | Multi 우위 | 출처 수(단일/Multi) |
|---|---|---|---|---|---|
| gpt-4o-mini | 84.8 | 86.6 | +1.8 | 3/3 | 0 / 5 |
| gpt-4o | 84.7 | 86.3 | +1.6 | 3/3 | 0 / 5 |

전체 결과는 [`docs/multimodel_result.md`](docs/multimodel_result.md) 참고.

---

## 4. 주요 기능

- **실제 웹 검색 grounding** — Research/Competitor Agent가 Tavily로 검색해 근거 확보
- **출처 인용** — Research·Competitor가 실제 웹검색으로 확보한 URL만 기획서 `참고자료` 섹션에 명시(LLM이 지어낸 출처는 인용에서 제외, 검색이 없으면 참고자료도 비움)
- **고객 문제 분석** — 페르소나 · Pain point · 니즈 · JTBD(Jobs To Be Done)
- **고정 14섹션 서식** — 프로젝트 개요 … PESTEL · SWOT · 수익 모델 … 위험요인 (PESTEL은 표로 렌더)
- **평가·자동 재작성** — Reviewer 5항목 평가, 총점 90 미만 시 1회 재작성
- **일관성 편집(Polish)** — 섹션 간 중복 제거·연결 문장 보강(구조·표·참고자료는 유지)
- **최종본 재평가(Final Reviewer)** — 재작성·편집 후 최종본을 다시 채점해 표시 점수가 실제 문서와 일치(초안 → 최종 변화 표시)
- **근거 일치성 검증** — 최종 기획서의 주장이 앞 단계에서 수집한 조사 결과(시장조사 + 경쟁사 검색 근거)와 일치하는지 검토해 근거 확인율 산출 (URL 원문 접속이 아닌 근거 텍스트 대조)
- **Human-in-the-Loop** — 사용자가 수정 요청을 넣어 재작성(수정 결과도 이력에 반영·재평가)
- **프로젝트 이력(SQLite)** — 실행 결과를 로컬 DB에 저장, 목록·상세 조회
- **관측성** — 실행당 LLM 호출 수·토큰·추정 비용·지연·fallback 표시
- **실행 품질 표면화** — fallback/더미/실패 노드를 `run_status`로 판정해 UI 배너로 경고(정상/일부 fallback/실패), 신뢰도 낮은 결과의 DOCX 다운로드 시 확인
- **산출물** — Markdown / 전체 결과 JSON / Word(.docx) / PowerPoint(.pptx, `##` 섹션별 슬라이드·표 렌더·내용 넘침 시 자동 분할)
- **입력 자동완성** — 프로젝트명(+기존 입력)으로 **비어 있는 항목만** AI가 채움. 사용자가 이미 쓴 값은 보존·문맥으로만 활용하고, AI가 채운 필드는 `AI 추천` 배지로 표시(수정 시 `AI 추천 수정됨`). **AI 제안과 비교** 모드에서는 4개 항목 모두에 제안을 받아 `내 값 vs AI`를 나란히 보고 항목별로 기존 유지·AI 적용·합치기를 선택(입력은 확인 전까지 그대로)
- **최소 웹 UI** — 입력 / 결과(Agent별) / 최종 기획서 / 이력 4화면 (FastAPI가 서빙하는 자체완결 HTML)
- **비교 harness** — 단일 vs 멀티(`run_compare.py`), 다중 모델(`run_multimodel.py`), 직렬 vs 병렬(`run_parallel_bench.py`) 재현 가능한 실험
- **회귀 테스트** — `pytest` 136개 (LLM 호출 없이 검증 로직·라우트 커버) · `ruff` 정적 검사 통과

---

## 5. 기술 스택

| 영역 | 사용 |
|---|---|
| Backend | FastAPI |
| Agent 오케스트레이션 | LangGraph |
| LLM | OpenAI · Anthropic (provider/모델 선택 가능, 키 없으면 더미 모드) |
| 웹 검색 | Tavily (키 없으면 검색 생략하고 LLM 지식 기반) |
| 이력 저장 | SQLite (python 내장 sqlite3, `data/projects.db`) |
| 관측성 | 자체 usage 집계 (토큰·추정 비용·지연) |
| 산출물 | python-docx (.docx), python-pptx (.pptx), Markdown, JSON |
| Frontend | 자체완결 HTML (인라인 CSS/JS, 외부 CDN 없음) |
| 테스트 | pytest |

---

## 6. API

| Method | Endpoint | 설명 |
|---|---|---|
| GET | `/` | 최소 UI(입력/결과/최종/이력 4화면) |
| GET | `/admin` | 관리자·데모 도구(임시) — 특정 Agent를 일부러 실패시켜 정직한 미완성 안내 시연 |
| GET | `/health` | 상태 · 더미 여부 · provider · 기본 모델 |
| GET | `/models` | 현재 provider에서 선택 가능한 모델 목록 |
| GET | `/projects` | 저장된 프로젝트 이력 목록(최신순) |
| GET | `/projects/{id}` | 저장된 프로젝트 상세(전체 실행 결과) |
| POST | `/run` | 아이디어 입력 → 전체 워크플로 실행, Agent별 결과 + 관측치 + 실행 품질(run_status) 반환 (이력 자동 저장) |
| POST | `/revise` | Human-in-the-Loop 수동 수정요청 반영 재작성. `project_id`를 주면 저장된 상태를 근거로 삼아 이력을 갱신하고, 수정횟수·관측치·재평가 점수를 반환 |
| POST | `/run/save` | 실행 후 `.md` + `.json` + `.docx` + `.pptx` 저장 |
| POST | `/export/docx` | 기획서 Markdown → Word(.docx) 다운로드 |
| POST | `/export/pptx` | 기획서 Markdown → PowerPoint(.pptx) 다운로드 |

Swagger 문서: `http://localhost:8000/docs`

---

## 7. 실행 방법

```bash
# 1) 의존성 설치
pip install -r requirements.txt
#   (발표/재현용으로 버전을 고정하려면: pip install -r requirements-lock.txt)

# 2) 환경 변수 설정
cp .env.example .env
#   .env 에서:
#   LLM_PROVIDER=openai        # 또는 anthropic
#   USE_DUMMY=0                # 실제 LLM 사용 (키 없으면 자동 더미 모드)
#   OPENAI_API_KEY=sk-...      # 또는 ANTHROPIC_API_KEY
#   TAVILY_API_KEY=tvly-...    # (선택) 웹 검색. 없으면 검색 생략

# 3) 서버 실행
uvicorn app.main:app --reload
#   → 브라우저에서 http://localhost:8000/

# 4) 비교실험 (선택)
python run_compare.py         # 단일 vs Multi → docs/comparison_result.md, outputs/comparison.json
python run_multimodel.py      # 생성 모델별 비교 → docs/multimodel_result.md
python run_parallel_bench.py --topics 3 --reps 1   # 직렬 vs 병렬(WORKFLOW_MODE) wall time·품질·비용 비교(스모크)

# 5) 관통 데모 (선택)
python run_demo.py            # 파이프라인 처음~끝 흐름 확인

# 6) 테스트
pytest -q
```

> 키가 없거나 `USE_DUMMY=1`이면 **더미 모드**로 동작해 실제 호출 없이 전체 파이프라인 흐름을 검증할 수 있습니다.

---

## 8. 저장소 구조

```text
app/
 ├─ main.py                 # FastAPI 진입점 + UI 서빙
 ├─ api/routes.py           # API 엔드포인트
 ├─ graph/workflow.py       # LangGraph 워크플로 (노드·엣지·_safe·관측 집계)
 ├─ agents/
 │   ├─ preprocess.py       # 입력 구조화(함수)
 │   ├─ research.py         # 시장조사 (웹 검색 grounding)
 │   ├─ competitor.py       # 경쟁사 분석
 │   ├─ customer.py         # 고객 문제(페르소나·Pain·니즈·JTBD)
 │   ├─ pestel.py           # PESTEL 6요인
 │   ├─ swot.py             # SWOT
 │   ├─ business_model.py   # 수익모델
 │   ├─ risk.py             # 리스크
 │   ├─ draft_writer.py     # 기획서 작성 + 재작성 + polish(일관성 편집)
 │   ├─ reviewer.py         # 평가
 │   ├─ verifier.py         # 근거 일치성 검증(주장↔조사결과 대조)
 │   └─ single_agent.py     # 단일 LLM 기준선(비교용)
 ├─ services/
 │   ├─ llm.py              # LLM 래퍼 (provider/모델·재시도·fallback·관측 record)
 │   ├─ search.py           # Tavily 웹 검색
 │   ├─ compare.py          # 단일 vs 멀티 비교·채점
 │   ├─ parallel_bench.py   # 직렬 vs 병렬 비교 측정(wall time·결정론 품질·비용)
 │   ├─ store.py            # 프로젝트 이력 저장 (SQLite)
 │   ├─ usage.py            # 실행 관측성 (토큰·추정 비용·지연 집계)
 │   ├─ timing.py           # 단계별 실행시간 계측 (stage wall·critical path·coverage)
 │   ├─ markdown_export.py  # .md / 실행결과 .json 저장
 │   ├─ docx_export.py      # Markdown → .docx
 │   └─ pptx_export.py      # Markdown → .pptx (섹션별 슬라이드)
 ├─ prompts/templates.py    # 프롬프트 템플릿
 ├─ schemas/state.py        # State·입출력 스키마
 └─ static/index.html       # 최소 UI(입력/결과/최종/이력)
tests/                      # pytest 136개 (LLM 호출 없이 검증 로직·라우트 테스트)
run_compare.py              # 단일 vs 멀티 비교실험 CLI
run_multimodel.py           # 생성 모델별 비교실험 CLI
run_parallel_bench.py       # 직렬 vs 병렬 비교실험 CLI (wall time·품질·비용)
run_demo.py                 # 파이프라인 관통 데모 CLI
data/projects.db            # 프로젝트 이력 DB (실행 시 생성)
docs/                       # PRD, 로드맵, 비교결과
```

---

## 9. 현재 범위와 향후

원래 12-Agent 풀버전 구상에서 다음은 **의도적으로 제외**했습니다(범위 관리): RAG · CI/CD · 로그인. 이력 저장(SQLite)·관측성·다중 모델 비교·일관성 편집·고객 문제 분석·PPTX 산출은 이후 복원·추가했습니다.

향후 후보: 관측성 per-Agent 분해, UI에서 단일 vs 멀티 나란히 보기, 섹션 간 연결성 추가 개선. 자세한 배경은 [`ROADMAP.md`](ROADMAP.md) 참고.
