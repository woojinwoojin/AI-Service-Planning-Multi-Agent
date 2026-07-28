# AI 서비스 기획 보조 Multi-Agent

> 아이디어 한 줄을 입력하면 여러 AI Agent가 **시장조사 → 경쟁사 분석 → 고객 문제 → PESTEL → SWOT → 수익모델 → 리스크 → KOSENA 방법론 분석(Porter·Lean Canvas·CJM·TAM/SAM/SOM·VPC·MVP·Epic-Story) → 기획서 작성 → 심사 → 일관성 편집 → 근거 일치성 검증**을 수행해 근거 있는 서비스 기획서를 만들어 주는 도구입니다.

FastAPI + LangGraph 기반의 Multi-Agent 워크플로로, 핵심 차별점은 두 가지입니다:
**① 실제 웹 검색으로 근거를 확보하고 그 출처를 기획서에 인용**하고,
**② 기획 방법론(KOSENA)이 요구하는 프레임워크를 실제로 거쳤는지 코드로 결정적 점검**해 결과를
문서·화면에 표면화합니다(28개 요구항목).

> 이 문서는 **실제 구현된 현재 상태**를 설명합니다. 초기 12-Agent 풀버전 구상과 13일 압축 계획, 잘라낸 범위는 [`ROADMAP.md`](ROADMAP.md)를, 상세 명세는 [`docs/PRD.md`](docs/PRD.md)를 참고하세요.

---

## 1. 무엇을 하나

- 사용자가 아이디어(프로젝트명·설명·타깃·문제·키워드)를 입력
- 여러 Agent가 순차적으로 분석을 쌓아 올림 (각 Agent는 앞 단계 결과를 근거로 삼음)
- 실제 웹 검색(Tavily)으로 시장 근거를 수집하고 **출처 URL을 최종 기획서에 인용**
- 심사 Agent가 5항목 100점으로 평가하고, 미달 시 **1회 자동 재작성**
- 일관성 편집 패스가 **섹션 간 중복 제거·연결 보강**을 수행
- 재작성·편집이 끝난 **최종본을 다시 채점**해, 화면 점수가 실제 최종 문서와 일치(초안 → 최종 변화 표시)
- 마지막에 검증 Agent가 기획서 주장을 근거와 대조(근거 확인율 산출 — **검색 요약 기준**이며 URL 원문은 재검증하지 않음)
- KOSENA 방법론 Agent 4종이 **7종 산출물**(산업 분석·Lean Canvas·고객 리서치·시장/경쟁사·컨셉/기능·로드맵·발표)을 만들고, **28개 요구항목 준수 여부를 자체 점검**해 문서와 화면에 표시
- Agent별 **프롬프트·입력·산출·검증·채택 여부**를 AI 활용 로그로 남겨 별도 파일로 첨부
- 사용자가 결과를 보고 **직접 수정 요청**(Human-in-the-Loop)으로 재작성 가능
- 실행 결과는 **SQLite 이력에 자동 저장**되고(⚠️ 컨테이너 재시작 시 소실), 실행당 **토큰·추정 비용·지연**(관측성)을 함께 표시
- 최종 기획서와 KOSENA 산출물을 **Markdown / JSON / Word(.docx) / PowerPoint(.pptx)**로 저장·다운로드

---

## 2. 아키텍처 (22-노드 Multi-Agent)

```text
사용자 입력
  → preprocess(입력 구조화 함수)
  → Research Agent        (웹 검색으로 시장조사 + 출처 수집 + 근거 공백 보고)
  → Research Gap          (보고된 근거 공백에만 추가 검색 — 없으면 호출 0, 로드맵 2-5)
  → Competitor Agent      (경쟁사 강점/약점/포지셔닝/차별화)
  → Customer Agent        (페르소나 · Pain · 니즈 · JTBD)
  → PESTEL Agent          (6요인 × 4항목 표)
  → SWOT Agent            (강점/약점/기회/위협)
  → Business Model Agent  (수익원/가격/비용/핵심지표)
  → Risk Agent            (유형별 리스크 + 가능성/영향/대응)
  → KOSENA Industry Agent (Porter · Value Chain · KSF 5 · 설계 시사점 — 체크포인트 3)
  → KOSENA Model Agent    (HMW 5 → 아이디어 25+ → 압축 3 → 컨셉 1 · Lean Canvas 9블록 · 핵심 가설 3)
  → KOSENA Research Agent (페르소나 2종 · CJM · TAM/SAM/SOM 교차검증 · 경쟁사 3·2·1 + 비교표 · 포지셔닝 맵)
  → KOSENA Roadmap Agent  (VPC · 기능 5~7 · Use Case 3 · MOSCOW · Kano · MVP · Epic-Story-AC · 와이어프레임)
  → Draft Writer Agent    (고정 14섹션 기획서 작성, 실제 출처 인용)
  → Reviewer Agent        (5항목 100점 평가 + 개선지시)
  → (총점 < 90 이면) 섹션 단위 보완 또는 전체 재작성 1회
  → Polish                (섹션 간 중복 제거·연결 문장 보강 — 표현 이슈 없으면 조건부 생략)
  → Final Reviewer        (재작성·편집 후 최종본 재평가 → 화면 표시 점수)
  → Select Best           (재작성본이 초안보다 낮으면 초안으로 되돌림)
  → Verify (근거 일치성)   (기획서 주장 ↔ 수집된 조사 결과 대조, 근거 확인율 산출)
  → 최종 기획서 + KOSENA 7종 산출물 + AI 활용 로그 + 준수 자체점검 + 실행 관측치
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
- **입력 자동완성** — 프로젝트명(+기존 입력)으로 **비어 있는 항목만** AI가 채움. 사용자가 이미 쓴 값은 보존·문맥으로만 활용하고, AI가 채운 필드는 `AI 추천` 배지로 표시(수정 시 `AI 추천 수정됨`). **AI 제안과 비교** 모드에서는 4개 항목 모두에 제안을 받아 `내 값 vs AI`를 나란히 보고 항목별로 기존 유지·AI 적용·합치기를 선택(입력은 확인 전까지 그대로). 각 AI 추천에는 **추천 이유·확신도(높음/보통/낮음)·참고한 기존 입력**을 함께 표시
- **최소 웹 UI** — 입력 / 결과(Agent별) / 최종 기획서 / 이력 4화면 (FastAPI가 서빙하는 자체완결 HTML)
- **KOSENA 방법론 산출물** — 기획 방법론 템플릿(KOSENA)이 요구하는 프레임워크를 4개 Agent가 생성하고
  **7종 산출물 문서 + 발표자료(20쪽)**로 조립. 28개 요구항목을 코드로 결정적 점검하고 결과를 문서·화면에
  표면화(`app/services/kosena.py`). 상세 = [`docs/kosena-compliance.md`](docs/kosena-compliance.md)
- **AI 활용 로그** — Agent별 프롬프트·입력·산출·검증·**채택 여부**를 별도 파일로 첨부. 재작성본이 초안보다
  낮아 되돌린 기록(`best_version`)이 "AI 응답을 그대로 쓰지 않았다"는 증거가 된다
- **비교 harness** — 단일 vs 멀티(`run_compare.py`), 다중 모델(`run_multimodel.py`), 직렬 vs 병렬(`run_parallel_bench.py`) 재현 가능한 실험
- **회귀 테스트** — `pytest` **658개** (LLM 호출 없이 검증 로직·라우트 커버) · `ruff` 정적 검사 통과 · CI 4게이트(ruff+pytest·gitleaks·pip-audit·docker build) · 커버리지 하한 90%

---

## 5. 기술 스택

| 영역 | 사용 |
|---|---|
| Backend | FastAPI |
| Agent 오케스트레이션 | LangGraph |
| LLM | OpenAI · Anthropic (provider/모델 선택 가능, 키 없으면 더미 모드). **실측에 사용한 모델 ID = `gpt-4o-mini`** |
| 웹 검색 | Tavily (키 없으면 검색 생략하고 LLM 지식 기반) |
| 이력 저장 | SQLite (python 내장 sqlite3, `data/projects.db`) — ⚠️ **컨테이너 재시작 시 사라진다**(Cloud Run 공개 배포에서는 이력 비영속. 산출물 다운로드로 대응) |
| 관측성 | 자체 usage 집계 (토큰·추정 비용·지연) |
| 산출물 | python-docx (.docx), python-pptx (.pptx), Markdown, JSON |
| Frontend | 자체완결 HTML (인라인 CSS/JS). **외부 CDN·빌드 도구를 쓰지 않는다** — FastAPI 가 그대로 서빙해 빌드·CORS·별도 배포가 불필요하고, 폐쇄망에서도 동작하며, CDN 의 가용성·버전 변동·공급망 위험을 끌어들이지 않는다. 대가는 **JS 자동 테스트 부재**(수동 확인 의존) |
| 테스트 | pytest (658개) · ruff · GitHub Actions CI 4게이트 |

---

## 6. API

| Method | Endpoint | 설명 |
|---|---|---|
| GET | `/` | 최소 UI(입력/결과/최종/이력 4화면) |
| GET | `/admin` | 관리자·데모 도구(임시) — 특정 Agent를 일부러 실패시켜 정직한 미완성 안내 시연. **`ENABLE_DEMO_TOOLS=1` 일 때만 제공(기본 404)** |
| GET | `/health` | 상태 · 더미 여부 · provider · 기본 모델 |
| GET | `/models` | 현재 provider에서 선택 가능한 모델 목록 |
| GET | `/projects` | 저장된 프로젝트 이력 목록(최신순). `limit`은 1~100(기본 50) |
| GET | `/projects/{id}` | 저장된 프로젝트 상세(전체 실행 결과) |
| POST | `/run` | 아이디어 입력 → 전체 워크플로 실행, Agent별 결과 + 관측치 + 실행 품질(run_status) 반환 (이력 자동 저장) |
| POST | `/revise` | Human-in-the-Loop 수동 수정요청 반영 재작성. `project_id`를 주면 저장된 상태를 근거로 삼아 이력을 갱신(없는 id면 404). 응답은 `/run`과 동일한 `RunResult` |
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

## 7-1. 배포 절차 (GCP Cloud Run)

배포 경로는 두 가지입니다. 자세한 배경은 [`DEPLOY.md`](DEPLOY.md) 참고.

### A. 승인형 CD — GitHub Actions (main 자동 트리거)

`.github/workflows/deploy-cloudrun.yml` — **main 에 머지되면 배포 파이프라인이 자동으로
이어집니다.** 되돌리기·더미 확인용 `workflow_dispatch` 수동 경로도 함께 있습니다.
문서만 바뀐 커밋(`**.md`·`docs/**`)은 `paths-ignore` 로 건너뜁니다 — 이미지 동작이 같은데
Revision 과 빌드 비용만 늘기 때문입니다.

흐름: **게이트(ruff + pytest) → 승인 대기 → GCP 인증(WIF) → Cloud Run 새 Revision → 원격 `/health`**
게이트가 실패하면 배포 잡은 시작하지 않고, `concurrency` 로 배포가 겹치지 않습니다.

> ⚠️ **'자동'의 범위를 정확히 적습니다.** 트리거와 파이프라인 진행은 자동이지만, 실서비스에
> 반영되는 마지막 단계에는 **사람 승인이 남아 있습니다** — `production` 환경에 Required
> reviewers 가 걸려 있어 배포 잡이 승인 대기로 멈춥니다. 실 LLM 키가 붙은 공개 서비스라
> 일부러 남긴 게이트입니다. 완전 무인 배포로 바꾸려면 아래 3번 설정을 지우면 됩니다
> (코드 변경이 아니라 **저장소 설정**입니다).
>
> 그래서 다음 두 표현은 **둘 다 틀립니다**:
> - "승인 없이 자동 배포된다" → 승인 게이트가 있습니다.
> - "승인 없이는 배포가 불가능하다" → `can_admins_bypass=true` 라 관리자는 우회할 수 있습니다.
>   1인 개발이라 `prevent_self_review=true` 로 두면 **아무도 승인할 수 없어 배포가 영구 정지**되므로
>   자기 승인을 허용했습니다.

**사전 설정(저장소 관리자, 1회):**

1. **Secrets** (Settings → Secrets and variables → Actions) — 셋 다 필요합니다
   | 이름 | 용도 |
   |---|---|
   | `GCP_PROJECT` | GCP 프로젝트 ID |
   | `GCP_WORKLOAD_IDENTITY_PROVIDER` | WIF 공급자 리소스 이름 |
   | `GCP_SERVICE_ACCOUNT` | WIF 로 가장할 서비스 계정 이메일 |

   **인증은 WIF 하나만 지원합니다.** 서비스 계정 키(JSON) 폴백을 두지 않은 이유는 장기
   크리덴셜을 저장소에 두면 유출 시 회수가 어렵고, 검증하지 못한 분기를 늘리지 않기 위해서입니다.

   WIF 설정(예시 — 프로젝트·풀 이름은 환경에 맞게):
   ```bash
   gcloud iam workload-identity-pools create github --location=global
   gcloud iam workload-identity-pools providers create-oidc github-actions \
     --location=global --workload-identity-pool=github \
     --issuer-uri=https://token.actions.githubusercontent.com \
     --attribute-mapping=google.subject=assertion.sub,attribute.repository=assertion.repository \
     --attribute-condition="assertion.repository=='<OWNER>/<REPO>'"
   # 출력된 provider 리소스 이름을 GCP_WORKLOAD_IDENTITY_PROVIDER 에 넣습니다
   ```
2. **Secret Manager** 에 `OPENAI_API_KEY`·`TAVILY_API_KEY` 생성(`scripts/deploy_cloudrun.sh` 가 만들어 줍니다)
3. **승인 게이트**: Settings → Environments → `production` 생성 → **Required reviewers** 지정.
   ⚠️ 이 설정이 없으면 승인 없이 바로 배포됩니다. 비공개 저장소는 **요금제에 따라 Required
   reviewers 를 못 쓸 수 있으니** 공개 여부와 플랜도 확인하세요.
4. 서비스 계정 권한: `roles/run.admin` · `roles/cloudbuild.builds.editor` ·
   `roles/iam.serviceAccountUser` · `roles/secretmanager.secretAccessor`

**자동 실행**: main 에 코드가 머지되면 바로 시작됩니다 → Actions 에서 *Review deployments* 로 승인.

**수동 실행**: Actions → *Cloud Run CD (main 자동 + 수동 트리거)* → Run workflow → `confirm` 에
**`deploy`** 입력(오타 배포 방지). `dummy_mode` 를 켜면 `USE_DUMMY=1` 로 올려 키 없이 화면·계약만
확인합니다.

> ⚠️ **`dummy_mode` 로 배포하면 공개 URL 이 더미로 덮입니다.** 2026-07-28 에 실제로 그렇게 됐고,
> 배포 단계는 전부 success 인데 `/health` 가 `"dummy_mode":true` 였습니다. **배포 성공 ≠ 실 모드**
> 이므로 확인은 항상 `/health` 의 `dummy_mode` 필드로 합니다. 되돌리려면 main 에 코드 커밋이
> 머지되게 하거나(자동 배포가 실 모드로 올립니다) `dummy_mode` 를 끄고 재-dispatch 합니다.

**검증 이력(2026-07-28)**: dispatch 로 전 경로 성공 — WIF 인증 → Cloud Build → Revision
`ai-planning-agent-00003-7bs` → 원격 `/health` ok. 그전 두 번의 실패는 둘 다 이 파일의 결함이었고
(`if: ${{ secrets.X }}` · ruff 미설치 exit 127), **이 워크플로는 PR CI 에서 돌지 않으므로 YAML
파싱만으로는 검증되지 않습니다.**

### B. 수동 스크립트 (검증된 경로)

```bash
gcloud auth login                    # 대화형 — 직접 실행 필요
export GCP_PROJECT=your-project-id
bash scripts/deploy_cloudrun.sh      # Secret Manager 등록 + Cloud Run 배포 + URL 출력
```

`--source .` 를 쓰므로 **로컬 Docker 데몬이 필요 없습니다**(Cloud Build 가 이미지를 만듭니다).

### 두 경로 공통 주의

- **`--max-instances=1` 은 필수**입니다. `public_guard` 의 요청·비용 카운터가 프로세스 메모리라,
  인스턴스가 늘면 상한이 인스턴스 수만큼 곱해져 방어가 조용히 약해집니다.
- **이력은 비영속**입니다(SQLite 파일 → 재시작 시 소실). 사용자에게 산출물 다운로드를 안내합니다.
- **GCS FUSE + SQLite 조합은 금지**입니다(파일 잠금 문제로 DB 손상).
- 배포 후 `/health` 의 `public_limits` 로 공개 상한이 실제로 걸렸는지 확인하세요.

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
 │   ├─ research_gap.py     # 보고된 근거 공백에만 추가 조사(제한적 동적 실행)
 │   ├─ kosena_industry.py  # KOSENA M1 — Porter·Value Chain·KSF·시사점
 │   ├─ kosena_model.py     # KOSENA M1 — HMW·아이디어 발산/수렴·Lean Canvas·핵심 가설
 │   ├─ kosena_research.py  # KOSENA M2 — 페르소나·CJM·TAM/SAM/SOM·경쟁사 비교표·포지셔닝
 │   ├─ kosena_roadmap.py   # KOSENA M3 — VPC·기능·MOSCOW·Kano·MVP·Epic-Story-AC·와이어프레임
 │   ├─ draft_writer.py     # 기획서 작성 + 재작성 + 섹션 단위 보완 + polish(일관성 편집)
 │   ├─ reviewer.py         # 평가(구조화 issues — 섹션 단위 보완의 입력)
 │   ├─ verifier.py         # 근거 일치성 검증(주장↔조사결과 대조)
 │   └─ single_agent.py     # 단일 LLM 기준선(비교용)
 ├─ services/
 │   ├─ llm.py              # LLM 래퍼 (provider/모델·재시도·fallback·관측 record)
 │   ├─ search.py           # Tavily 웹 검색
 │   ├─ evidence.py         # 통합 근거 레지스트리(evidence_id 부여·주장 역인덱스)
 │   ├─ kosena.py           # KOSENA 28개 요구항목 준수 검사(결정적·LLM 없음)
 │   ├─ kosena_doc.py       # KOSENA 7종 산출물 문서·발표자료 조립
 │   ├─ ai_log.py           # AI 활용 로그(프롬프트·입력·산출·검증·채택 여부)
 │   ├─ quality_gate.py     # 출력 가능 여부 게이트
 │   ├─ reliability.py      # 검증 범위·한계 문구 단일 소스(UI·내보내기·JSON 공통)
 │   ├─ sections.py         # 14섹션 stable ID ↔ 제목 단일원천(왕복 byte 동일)
 │   ├─ budget.py           # 실행별 예산·시간 상한
 │   ├─ public_guard.py     # 공개 배포 상한(IP 빈도·전역 일일 실행수·비용)
 │   ├─ migrate.py          # State 스키마 버전·옛 기록 읽기 정규화
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
tests/                      # pytest 658개 (LLM 호출 없이 검증 로직·라우트 테스트)
run_compare.py              # 단일 vs 멀티 비교실험 CLI
run_multimodel.py           # 생성 모델별 비교실험 CLI
run_parallel_bench.py       # 직렬 vs 병렬 비교실험 CLI (wall time·품질·비용)
run_demo.py                 # 파이프라인 관통 데모 CLI
data/projects.db            # 프로젝트 이력 DB (실행 시 생성)
docs/                       # PRD, 로드맵, 비교결과
```

---

## 9. 현재 범위와 향후

구현한 것과 앞으로 할 것을 섞지 않습니다. 평가 기준별 상세 매핑은
[`docs/평가기준_매핑표.md`](docs/평가기준_매핑표.md)에 있습니다.

### 구현 완료

Multi-Agent 22노드(직렬·병렬) · 조건부 분기 · 실패 격리 · 웹 검색 grounding · 통합 근거 레지스트리 ·
근거 일치성 검증 · Reviewer 평가와 섹션 단위 보완 · 최고 버전 채택 · 출력 가능 여부 게이트 ·
**KOSENA 방법론 산출물 7종 + 준수 자체점검 28항목 + AI 활용 로그** · DOCX/PPTX/MD/JSON 산출 ·
이력 저장·재조회 · 관측성(토큰·비용·지연·stage) · 예산 상한 · 공개 배포 상한 ·
**CI 4게이트 + main 브랜치 보호** · Docker 컨테이너 · GCP Cloud Run 배포(수동 스크립트 = 검증된 경로, + **승인형 CD** — main 머지 시 자동 트리거, 실서비스 반영 직전 `production` 승인 게이트. 2026-07-28 dispatch 로 전 경로 실행 검증됨).

> 초기 구상에서 **RAG·로그인은 여전히 제외**입니다(범위 관리). CI/CD 는 처음엔 제외했다가
> 이후 CI(4게이트)와 Staging 파이프라인을 추가했습니다 — 아래 '향후'에 남은 부분을 적었습니다.

### 알려진 한계 (정직 표기)

- **사실 검증은 검색 요약 기준**입니다. URL 원문의 사실성은 재검증하지 않습니다.
  `unsupported` 는 '거짓'이 아니라 '현재 근거에서 확인하지 못함'입니다.
- **출처 검사는 실행 전체 기준**입니다. KOSENA 산출물의 항목별 근거 연결은 미구현입니다.
- **본문 분량이 요건(A4 30~50쪽) 미달**입니다(약 12.2쪽). 분량을 위한 분량은 넣지 않았습니다 —
  근거는 [`docs/kosena-compliance.md`](docs/kosena-compliance.md)에 있습니다.
- **이력은 비영속**입니다(SQLite 파일 → 컨테이너 재시작 시 소실).
- **인터뷰·설문 등 1차 자료는 만들 수 없습니다.** 지어내지 않고 **가설임을 명시**하며,
  그 표기 여부 자체를 검사합니다.
- **JS 자동 테스트가 없습니다.** UI 변경은 브라우저 수동 확인에 의존합니다.

### 향후 후보

배포 워크플로의 Required reviewers 설정 + 실제 실행 검증 · KOSENA 산출물 항목별 근거 연결 · URL 원문 대조 검증 · 선택적 재실행(변경된 Agent 만) ·
사람 기획서 기준선 보정 · 관측성 per-Agent 분해. 자세한 배경은 [`ROADMAP.md`](ROADMAP.md) 참고.
