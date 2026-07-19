# 아키텍처 · 설계 결정(ADR) · 주요 코드

> 갱신: 2026-07-19 · 대상: 현재 main(11-노드 Multi-Agent) · 코드 근거를 함께 표기
> 관련 문서: [`../README.md`](../README.md) · [`ROADMAP.md`](ROADMAP.md) · [`PRD.md`](PRD.md)

이 문서는 **"왜 이렇게 만들었는가"(설계 결정)**와 **"어디를 보면 되는가"(주요 코드)**를 한곳에 모은 개발자용 레퍼런스입니다.

---

## 1. 개요

아이디어 한 줄 → 여러 AI Agent가 순차로 분석을 쌓아 근거 있는 서비스 기획서를 생성하는 도구. FastAPI + LangGraph 기반이며, **실제 웹 검색으로 근거를 확보하고 그 출처를 기획서에 인용**하는 것이 핵심 차별점.

**설계를 관통하는 3원칙**
1. **완주 보장** — 어떤 LLM 오류가 나도 파이프라인은 처음~끝까지 돈다.
2. **스키마 정합성** — 각 Agent 출력은 강제 검증되어, 다음 Agent는 항상 온전한 입력을 받는다.
3. **정직성** — 더미/실제/fallback을 로그·관측치에 정직하게 구분 표기한다.

---

## 2. 아키텍처

### 2.1 계층 구조

```text
┌─ API 계층 ────────────────────────────────────────────────┐
│ app/main.py (UI 서빙) · app/api/routes.py (엔드포인트)        │
└──────────────────────────────┬────────────────────────────┘
                               │
┌─ 오케스트레이션 계층 ──────────▼────────────────────────────┐
│ app/graph/workflow.py  LangGraph StateGraph (노드·엣지·_safe)│
└──────────────────────────────┬────────────────────────────┘
                               │  (공유 State: ProjectState)
┌─ Agent 계층 ──────────────────▼────────────────────────────┐
│ app/agents/*.py  research·competitor·customer·pestel·swot   │
│                  ·business_model·risk·draft_writer·reviewer  │
│                  ·verifier  (+ preprocess 함수, single_agent) │
└──────────────────────────────┬────────────────────────────┘
                               │
┌─ 서비스 계층 ──────────────────▼────────────────────────────┐
│ llm(provider·재시도·fallback·관측) · search(Tavily)          │
│ compare(비교·채점) · store(SQLite) · usage(관측성)           │
│ markdown_export · docx_export                                │
└────────────────────────────────────────────────────────────┘
```

### 2.2 워크플로 (11-노드)

`app/graph/workflow.py:build_graph`

```text
START → preprocess → research → competitor → customer → pestel → swot
      → business_model → risk → draft → reviewer
      → (reviewer 총점 < 90 이고 재작성 0회면) revise ─┐
      → (아니면)                              finalize ─┤
                                                       └→ polish → verify → END
```

- **조건 분기**(`_needs_revision`): Reviewer 총점 `< PASS_SCORE(90)` && `revision_count < 1` 이면 `revise`, 아니면 `finalize`. 자동 재작성은 **최대 1회**.
- **분기 후 합류**: `revise`/`finalize` 모두 `polish → verify`로 합류해, 재작성 여부와 무관하게 일관성 편집·출처 검증을 거친다.

### 2.3 노드별 역할

| 노드 | 파일 | 역할 | 핵심 출력 키 |
|---|---|---|---|
| preprocess | `agents/preprocess.py` | 입력 구조화(함수) | `structured_input` |
| research | `agents/research.py` | 웹검색 grounding + 시장조사 | `research_result`(+`sources`) |
| competitor | `agents/competitor.py` | 경쟁사 분석 | `competitor_result` |
| customer | `agents/customer.py` | 페르소나·Pain·니즈·JTBD | `customer_result` |
| pestel | `agents/pestel.py` | PESTEL 6요인×4항목 | `pestel_result` |
| swot | `agents/swot.py` | SWOT | `swot_result` |
| business_model | `agents/business_model.py` | 수익원·가격·비용·지표 | `business_model_result` |
| risk | `agents/risk.py` | 리스크(가능성·영향·대응) | `risk_result` |
| draft | `agents/draft_writer.py:draft` | 고정 14섹션 기획서 + 출처 인용 | `draft` |
| reviewer | `agents/reviewer.py` | 5항목 100점 + 개선지시 | `review_result` |
| revise | `agents/draft_writer.py:revise` | 개선지시 반영 1회 재작성 | `final_draft` |
| finalize | `graph/workflow.py:_finalize` | 재작성 없이 초안 확정 | `final_draft` |
| polish | `agents/draft_writer.py:polish` | 섹션 중복 제거·연결 보강 | `final_draft` |
| verify | `agents/verifier.py` | 주장↔근거 대조, 지지율 | `verification_result` |

---

## 3. 공유 State

모든 노드는 하나의 `ProjectState`(TypedDict)를 읽고, 갱신할 키만 dict로 반환한다. LangGraph가 반환 dict를 State에 병합한다.

`app/schemas/state.py`
```python
class ProjectState(TypedDict, total=False):
    user_input: dict
    model: str                    # 이번 실행 LLM 모델 id(빈 값이면 env 기본)
    structured_input: dict
    research_result: dict
    competitor_result: dict
    customer_result: dict
    swot_result: dict
    business_model_result: dict
    risk_result: dict
    pestel_result: dict
    draft: str
    review_result: dict
    final_draft: str
    revision_count: int
    verification_result: dict
    logs: list                    # 실행 로그 / 진행 상태
```

실행 종료 후 `run_workflow`가 `state["usage"]`(관측치)를 덧붙여 반환한다(`workflow.py:110`).

---

## 4. 핵심 설계 패턴

### 4.1 노드 완주 보장 — `_safe` 래핑

모든 노드는 `_safe()`로 감싸 예외가 나도 로그만 남기고 진행한다.

`app/graph/workflow.py:36`
```python
def _safe(name, fn):
    def wrapped(state):
        try:
            return fn(state)
        except Exception as exc:
            logs = state.get("logs", []) + [f"[{name}] 오류로 건너뜀 ({type(exc).__name__}: {exc})"]
            return {"logs": logs}
    return wrapped
```

### 4.2 Agent 공통 3단 패턴 — fallback → 검증 → 스키마 고정

모든 LLM Agent는 동일 골격을 따른다(예: `research.py`):
1. **fallback(`_dummy`) 준비** — 더미 모드/오류 시 반환할 골격 값.
2. **`llm.complete_json(..., fallback=..., status=...)` 호출** — 실패해도 예외 없이 fallback 반환.
3. **`_validate(raw, fallback)`** — 키 누락/타입오류를 중립 빈값(`""`/`[]`)으로 채워 **다음 Agent가 항상 온전한 스키마를 받도록** 보장.

`app/agents/research.py:29` (검증 패턴)
```python
def _validate(result, fallback):
    if not isinstance(result, dict):
        return dict(fallback)
    out = {}
    for key, expected in _SCHEMA.items():       # (키, 기대 타입)
        value = result.get(key)
        out[key] = value if isinstance(value, expected) and value else expected()
    return out
```

> 포인트: fallback의 더미 문구(`[더미]…`)가 **실제 응답에 새어들지 않도록**, 누락 키는 fallback 값이 아니라 `expected()`(빈 문자열/빈 리스트)로 채운다.

### 4.3 LLM 래퍼 — 재시도·fallback·모델 방어·관측

`app/services/llm.py`

- `complete_json` / `complete_text`: 호출 실패 시 예외 전파 없이 fallback 반환. JSON은 파싱 실패 시 "유효한 JSON만 출력" 안내로 **1회 재호출** 후 fallback.
- `_extract_json`: ` ```json ``` ` 코드펜스/중괄호 블록을 뽑아 파싱(LLM이 잡설을 붙여도 견딤).
- `resolve_model`: 요청 모델이 현재 provider **허용목록에 있을 때만** 사용, 아니면 env 기본값 → 잘못된 모델로 런타임에 죽지 않음.
- `is_dummy`: `USE_DUMMY=1`이거나 키가 없으면 True → 키 없이도 전체 흐름 검증 가능.
- `mode_label`: 로그에 `더미 / 실제 LLM·{모델} / fallback·{사유}` 를 정직하게 구분.

### 4.4 관측성 — 실행별 격리(contextvar)

`app/services/usage.py`

- `llm._timed_invoke`가 매 호출마다 `usage.record(모델, in/out 토큰, 지연, fallback여부)`.
- `run_workflow`가 `start()`→실행→`summary()`로 호출수·토큰·**추정 비용(USD)**·총 지연·fallback 수 집계.
- `contextvars`로 실행(호출 스택)별 격리 → 동시 실행이 섞이지 않음.

### 4.5 웹검색 grounding — 실제 출처 보장

`app/agents/research.py`

- `search.web_search(query)`로 검색 → 히트를 프롬프트에 1차 근거로 주입.
- `_merge_sources`가 **실제 검색 출처(제목—URL)를 sources 앞쪽에 보장**하고 LLM이 적은 것과 중복 없이 병합.
- 이 sources가 `draft_writer._append_references`를 통해 최종 문서 `## 참고자료`로 인용됨 → 비교실험의 객관 지표(출처 수)의 근원.

---

## 5. 설계 결정 기록 (ADR)

각 항목: **결정 / 배경 / 결과·트레이드오프**.

### ADR-1. LangGraph StateGraph + 전 노드 `_safe` 래핑
- **결정**: 오케스트레이션은 LangGraph `StateGraph`, 모든 노드를 `_safe`로 감싼다.
- **배경**: 발표/데모에서 한 Agent의 일시적 LLM 오류가 전체를 중단시키면 안 됨.
- **결과**: 한 노드가 죽어도 완주. 대신 실패 노드는 State를 갱신하지 않으므로 다음 노드가 **빈 입력**을 받을 수 있어 → ADR-3(스키마 강제)로 보완.

### ADR-2. 단일 공유 State (TypedDict, `total=False`)
- **결정**: 노드 간 데이터는 하나의 `ProjectState`로 공유, 각 노드는 갱신 키만 반환.
- **배경**: Agent가 앞 단계 결과를 근거로 삼는 누적형 파이프라인.
- **결과**: 추가가 쉽다(키만 늘리면 됨). 대신 State가 커지고 결합도가 있음 — 규모가 커지면 네임스페이스 분리 고려.

### ADR-3. fallback + `_validate` 이중 방어로 스키마 강제
- **결정**: LLM 출력은 항상 `_validate`로 정규화, 실패/누락은 중립 빈값으로 채움.
- **배경**: LLM은 유효 JSON이어도 키 누락/타입 흔들림이 잦다. `_safe`로 건너뛴 노드도 있음.
- **결과**: 다음 Agent가 **항상 온전한 타입**을 받음(견고). 더미 문구 유출 방지. 대신 Agent마다 스키마 정의·검증 코드 중복.

### ADR-4. 더미 모드 (키 없이 골격 검증)
- **결정**: 키가 없거나 `USE_DUMMY=1`이면 각 호출부의 fallback을 그대로 반환.
- **배경**: API 비용 없이 파이프라인 흐름·테스트를 돌려야 함(pytest 44개가 LLM 없이 검증).
- **결과**: CI/개발이 무비용·결정적. 대신 더미 산출물은 품질 검증엔 못 씀.

### ADR-5. 웹검색 grounding + 실제 출처 강제 병합
- **결정**: 검색 히트를 프롬프트 근거로 넣고, `_merge_sources`로 실제 URL을 sources에 보장.
- **배경**: "근거 있는 기획서"가 핵심 가치. LLM 지식만으로는 추적 가능한 출처가 안 남음.
- **결과**: 최종 문서에 실제 출처 URL이 남음 → **비교실험 결정타(고유 출처 URL 0 vs 5)**. 대신 Tavily 키·검색 지연 비용.
- **주의(정직성)**: `count_citations`는 URL의 **포함 개수**만 세며 접속 가능성·내용 사실성은 검증하지 않는다(→ item 7). 명칭은 "고유 출처 URL 수"로 표기.

### ADR-6. 비교 채점: 심판 3회 평균 + 하드 URL 카운트
- **결정**: LLM 심판을 플랜당 `JUDGE_SAMPLES=3`회 반복 평균, 출처 수는 정규식 하드 카운트.
- **배경**: 작은 LLM 심판은 변동이 크고, "유창하지만 근거 없는 글"을 잘 못 거른다.
- **결과**: 점수 노이즈 완화 + **LLM에 의존하지 않는 객관 지표** 확보. `compare.count_citations`는 고유 URL 집합 크기.

### ADR-7. 다중 모델 비교는 심판 '고정'
- **결정**: 생성 모델만 바꾸고 심판 모델은 하나로 고정(`run_multimodel.JUDGE_MODEL`).
- **배경**: "모델이 강해져도 Multi-Agent 우위가 유지되는가"를 공정하게 보려면 채점 기준이 일정해야 함.
- **결과**: 행 간 비교가 공정. 관찰: 강한 모델일수록 점수 격차는 줄지만 출처 격차는 유지(0 vs 5).

### ADR-8. 자동 재작성 1회 + polish 안전장치
- **결정**: 총점 90 미만이면 1회만 재작성. polish는 편집 후 **14섹션 구조가 깨지면 원본 유지**.
- **배경**: 무한 재작성 방지(비용·시간), 편집이 문서 구조를 훼손하는 리스크 차단.
- **결과**: 예측 가능한 실행 비용·시간. 대신 1회로 못 고치는 품질 문제는 Human-in-the-Loop(`/revise`)에 위임.

### ADR-9. 관측성은 contextvar로 실행별 격리
- **결정**: 토큰·지연을 전역이 아닌 `contextvars`에 실행 단위로 모은다.
- **배경**: 서버가 동시에 여러 실행을 처리할 수 있음.
- **결과**: 실행별 정확 집계. 단, 추정 비용은 `usage.PRICES` 단가표 기반 근사치(미등록 모델은 0).

### ADR-10. 이력 저장은 SQLite (JSON blob + 조회 컬럼)
- **결정**: 전체 실행 상태는 `state_json` 블롭으로, 조회용(이름·모델·총점·시각)은 별도 컬럼.
- **배경**: 별도 DB 서버·ORM 없이 로컬에서 이력·재조회 필요.
- **결과**: 의존성 0(파이썬 내장 sqlite3), 상세는 블롭으로 완전 복원. 대신 블롭 내부는 쿼리 불가(조회 컬럼으로만 필터).

### ADR-11. provider/모델 선택 + 허용목록 방어
- **결정**: `/models`로 provider별 모델 노출, `resolve_model`이 허용목록 외 요청을 기본값으로 흡수.
- **배경**: 사용자가 모델을 고를 수 있게 하되, 잘못된/타 provider 모델로 죽으면 안 됨.
- **결과**: 유연한 모델 선택 + 런타임 안전. 새 모델은 `AVAILABLE_MODELS`에만 추가하면 반영.

---

## 6. 주요 코드 지도

무엇을 고치려면 어디를 보면 되는가.

| 하고 싶은 것 | 파일:심볼 |
|---|---|
| 워크플로 노드·엣지·분기 수정 | `graph/workflow.py:build_graph`, `_needs_revision` |
| 재작성 통과 점수 조정 | `graph/workflow.py:PASS_SCORE` |
| 새 Agent 추가 | `agents/<new>.py` + `workflow.py`에 노드·엣지 등록 |
| LLM 호출/재시도/파싱 동작 | `services/llm.py:complete_json`, `_extract_json`, `_invoke_with_retry` |
| 사용 가능 모델·단가 | `services/llm.py:AVAILABLE_MODELS`, `services/usage.py:PRICES` |
| 웹검색 쿼리·출처 병합 | `agents/research.py:_build_query`, `_merge_sources` |
| 기획서 서식(14섹션) | `agents/draft_writer.py:SECTIONS`, `draft`, `_append_references` |
| 일관성 편집 안전장치 | `agents/draft_writer.py:polish`, `_missing_sections` |
| 심사 기준·점수 | `agents/reviewer.py`, 비교 기준은 `services/compare.py:CRITERIA` |
| 비교 채점·집계 | `services/compare.py:judge`, `run_topic`, `aggregate`, `count_citations` |
| 이력 저장/조회 | `services/store.py:save_run`, `list_projects`, `get_project` |
| 관측치 집계 | `services/usage.py:start`, `record`, `summary` |
| API 엔드포인트 | `api/routes.py` |
| State/입출력 스키마 | `schemas/state.py` |

### 6.1 대표 코드 — 조건 분기

`app/graph/workflow.py:52`
```python
def _needs_revision(state):
    review = state.get("review_result", {})
    score = review.get("total_score", 0)
    already = state.get("revision_count", 0)
    if already < 1 and score < PASS_SCORE:      # PASS_SCORE = 90
        return "revise"
    return "finalize"
```

### 6.2 대표 코드 — 비교 실행 한 주제

`app/services/compare.py:66`
```python
def run_topic(topic, model="", judge_model=None):
    jm = judge_model if judge_model is not None else model   # 다중모델 비교 시 심판 고정
    multi_state = run_workflow({**topic, "model": model})
    multi_plan = multi_state.get("final_draft", "")
    si = multi_state.get("structured_input", topic)          # 동일 입력으로 공정 비교
    single_plan = single_agent.generate(si, model=model)
    return {
        "topic": topic.get("project_name", ""),
        "single": {"plan": single_plan, "judge": judge(single_plan, model=jm),
                   "citations": count_citations(single_plan)},
        "multi":  {"plan": multi_plan,  "judge": judge(multi_plan,  model=jm),
                   "citations": count_citations(multi_plan)},
    }
```

### 6.3 대표 코드 — 객관 지표(출처 수)

`app/services/compare.py:18`
```python
_URL_RE = re.compile(r"https?://[^\s)\]]+")

def count_citations(plan_text):
    """LLM 채점이 아닌 하드 카운트 — 고유 URL 수."""
    return len(set(_URL_RE.findall(plan_text or "")))
```

---

## 7. 데이터 모델 (이력 DB)

`app/services/store.py` — `data/projects.db` (실행 시 생성)

```sql
CREATE TABLE IF NOT EXISTS projects (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project_name TEXT,
    model        TEXT,
    total_score  INTEGER,
    created_at   TEXT,      -- UTC ISO8601
    state_json   TEXT       -- 전체 실행 상태(복원용)
);
```

`/run`이 실행 후 `save_run(state)`로 자동 저장, `/projects`·`/projects/{id}`로 조회.

---

## 8. 새 Agent 추가 절차

1. `app/agents/<name>.py` 작성 — 공통 3단 패턴(fallback `_dummy` → `llm.complete_json` → `_validate`).
2. 출력 키를 `schemas/state.py:ProjectState`(및 필요 시 `RunResult`)에 추가.
3. `graph/workflow.py:build_graph`에 `add_node` + `add_edge`로 흐름에 삽입(반드시 `_safe`로 래핑).
4. `agents/draft_writer.py`에서 새 결과를 기획서 서식에 반영(필요 시 `SECTIONS`).
5. `tests/`에 검증 로직 테스트 추가(LLM 호출 없이 `_validate` 중심).

---

## 9. 실행·검증 빠른 참조

```bash
uvicorn app.main:app --reload     # 서버 → http://localhost:8000/
python run_compare.py             # 단일 vs 멀티 (6주제)
python run_multimodel.py          # 생성 모델별 (심판 고정)
python run_demo.py                # 파이프라인 관통 데모
pytest -q                         # 회귀 테스트 44개(무비용)
```
