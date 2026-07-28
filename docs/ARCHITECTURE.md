# 아키텍처 · 설계 결정(ADR) · 주요 코드

> 갱신: 2026-07-27 · 대상: 현재 main · 코드 근거를 함께 표기
> 관련 문서: [`../README.md`](../README.md) · [`개발_로드맵_v2.md`](개발_로드맵_v2.md) · [`PRD.md`](PRD.md) · [`정보신뢰성_전략.md`](정보신뢰성_전략.md) · [`병렬화_측정결과_및_PR7_계획.md`](병렬화_측정결과_및_PR7_계획.md) · [`phase2-2-artifact-plan.md`](phase2-2-artifact-plan.md)

이 문서는 **"왜 이렇게 만들었는가"(설계 결정)**와 **"어디를 보면 되는가"(주요 코드)**를 한곳에 모은 개발자용 레퍼런스입니다.

---

## 1. 개요

아이디어 한 줄 → 여러 AI Agent가 분석을 쌓아 근거 있는 서비스 기획서를 생성하는 도구.
FastAPI + LangGraph 기반 **22개 노드**이며, **실제 웹 검색으로 근거를 확보하고 그 출처를 기획서에
인용**하고, **주장을 근거와 대조 검증**하며, **출력 가능 여부를 게이트로 판정**하고,
**기획 방법론(KOSENA) 준수 여부를 코드로 결정적 점검**하는 것이 핵심.

현재 규모: 노드 22(한 실행에 20개 실행 — 조건부 분기) · 테스트 **675개**(실 LLM 호출 없음) ·
커버리지 **96.90%** · CI 4게이트 · 실측 모델 `gpt-4o-mini`.
**코드 동결 = `v1.0.1-submission`**(2026-07-28, `v1.0.0-submission` 은 문서 기준 동결로 남겨 둠) —
동결 지점·측정값·발표 표현 주의는 [`동결_기록.md`](동결_기록.md) 참고.

**설계를 관통하는 원칙**
1. **완주 보장** — 어떤 LLM 오류가 나도 파이프라인은 처음~끝까지 돈다(`_safe`).
2. **스키마 정합성** — 각 Agent 출력은 강제 검증되어, 다음 Agent는 항상 온전한 입력을 받는다(`_validate`).
3. **정직성** — 더미/실제/fallback·검증 범위·게이트 미충족을 로그·State·UI에 정직하게 표기한다.
4. **측정 가능성** — 병렬화·문서재생성·신뢰도 개선은 전부 동일 벤치/평가로 전후 비교한다(트랙 C).

> 로드맵 v2 실행 이력(Evidence Registry·Tier 2·PR-7/8·품질 게이트·State 버전)은 [`개발_로드맵_v2.md`](개발_로드맵_v2.md) 참조.

---

## 2. 아키텍처

### 2.1 계층 구조

```text
┌─ API 계층 ────────────────────────────────────────────────┐
│ app/main.py (UI 서빙·OpenAPI) · app/api/routes.py (엔드포인트)│
└──────────────────────────────┬────────────────────────────┘
                               │
┌─ 오케스트레이션 계층 ──────────▼────────────────────────────┐
│ app/graph/workflow.py  LangGraph StateGraph                 │
│   직렬 그래프 / 병렬 그래프(WORKFLOW_MODE) · _safe 래핑       │
└──────────────────────────────┬────────────────────────────┘
                               │  (공유 State: ProjectState)
┌─ Agent 계층 ──────────────────▼────────────────────────────┐
│ app/agents/*.py  research·competitor·customer·pestel·swot   │
│   ·business_model·risk·draft_writer(draft/revise/section_    │
│   revise/polish)·reviewer(reviewer/final_reviewer)·verifier  │
│   (+ preprocess 함수, single_agent 비교기준)                 │
└──────────────────────────────┬────────────────────────────┘
                               │
┌─ 서비스 계층 ──────────────────▼────────────────────────────┐
│ llm(provider·재시도·fallback·관측) · search(Tavily)          │
│ evidence(근거 레지스트리) · sections(섹션 파서/조립)         │
│ quality_gate(출력 게이트) · migrate(State 버전) · reliability │
│ timing(단계 계측) · usage(토큰·비용) · tracing(Langfuse)     │
│ store(SQLite) · markdown/docx/pptx_export · suggest          │
│ [평가] compare · evaluation · eval_set · gt_eval · polish_eval│
│ [벤치] parallel_bench · artifact_real_check                  │
└────────────────────────────────────────────────────────────┘
       ▲ 계층을 가로지름: schemas/artifact.py (Artifact Contract, §4.11)
         Agent 가 쓰고(Dual Write) 소비자가 읽는(selector) 표준 봉투
```

### 2.2 워크플로 (직렬/병렬 공통 마무리)

`app/graph/workflow.py` — `build_serial_graph` / `build_parallel_graph`, `WORKFLOW_MODE`(env/인자)로 선택. 기본 직렬.

**분석 구간(직렬):**
```text
START → preprocess → research → research_gap → competitor → customer → pestel → swot
      → business_model → risk
      → kosena_industry → kosena_model → kosena_research → kosena_roadmap
      → draft → [마무리]
```

**분석 구간(병렬, `build_parallel_graph`):** Research 이후 독립 4분기를 동시 실행 →
**`kosena_industry` 에서 fan-in join**(draft 가 아니다 — KOSENA M1 이 분석 4분기 결과를 모두 받아야 한다).
```text
research → research_gap ┬→ competitor → swot ┐
                        ├→ customer          ├→ (모두 완료 후 1회) kosena_industry
                        ├→ pestel → risk     │      → kosena_model → kosena_research
                        └→ business_model ────┘      → kosena_roadmap → draft → [마무리]
```
- Agent 입력·프롬프트·결과 구조는 직렬과 **동일**, 실행 순서만 다르다(비열등성 전제). 지연 차이만 병렬화 효과.
- fan-in: `add_edge(["swot","customer","risk","business_model"], "kosena_industry")` — 깊이 다른
  분기의 조기/중복 실행 방지. ⚠️ 이 문서가 한동안 fan-in 대상을 `draft` 로 잘못 적고 있었다
  (2026-07-28 구조도 작업 중 코드와 대조해 발견·정정). 실제 코드는 `workflow.py:266`.
- **KOSENA 4노드는 fan-in 뒤 순차**다(`industry → model → research → roadmap`). 뒤 노드가 앞 결과를 이어받아야 평가표의 'Lean Canvas 블록 간 일관성'·'VPC Fit'이 성립하기 때문에 정확성을 우선해 병렬화하지 않았다(대가: LLM 호출 +4, 지연 +20~30초).

**마무리 구간(공통, `_add_finish_edges`):**
```text
draft → reviewer → _route_revision ┬─ finalize ──────┐
                                   ├─ section_revise ─┤
                                   └─ revise(전체) ────┤
                                       → polish(조건부) → final_reviewer
                                       → select_best → verify → END
```
- **`_route_revision`**(3분기): 총점 `< PASS_SCORE(90)` && `revision_count < 1` 이면 재작성, 아니면 `finalize`. 재작성 가능하면 섹션 단위 수정 가능 여부(`plan_section_revision`)로 `section_revise`/`revise`(전체) 선택. 자동 재작성 **최대 1회**.
- **`section_revise`**(PR-7): 문제 섹션만 담당 Agent가 보완. 런타임 실패 시 전체 `revise`로 fallback. → §4.6.
- **`polish`**(조건부, PR-8): 표현 이슈 없고 구조 정상이면 생략. → §4.7.
- **`final_reviewer`**: 최종본 재채점(표시 점수). Writer/Reviewer 모델 분리 가능(`reviewer_model`). → §4.8.
- **`select_best`**(Phase 4): 재작성본이 초안보다 낮으면 초안 채택(되돌림). → §4.9.
- **`verify`**: 채택된 문서의 주장을 근거와 대조(Tier 2 유형 분류·근거 상태). → §4.5.
- 실행 종료 후 `_finalize_run`: 근거 레지스트리 확정·usage·timing·run_status·**quality_gate**·**state_version** 부착.
- 이어서 `_finalize_kosena`: AI 활용 로그 → **KOSENA 문서 조립 → 준수 판정 → 재조립** → 발표자료. 조립을 두 번 하는 이유는 **순환 의존**이다 — 판정은 조립된 본문에서 분량·가설 표기를 재고, 본문은 판정을 표로 싣는다. `/revise` 후에도 같은 함수를 부른다(안 부르면 KOSENA 산출물만 수정 전 내용으로 남는다). → §4.12

### 2.3 노드별 역할

| 노드 | 파일:심볼 | 역할 | 핵심 출력 키 |
|---|---|---|---|
| preprocess | `agents/preprocess.py` | 입력 구조화(함수) | `structured_input` |
| research | `agents/research.py` | 웹검색 grounding + 시장조사 + 근거 방출 + 근거 공백 보고 | `research_result`·`evidence_registry`·`evidence_gaps` |
| research_gap | `research.py:research_gap` | 보고된 근거 공백에만 추가 검색·보강(2-5) | `research_result`(보강)·`evidence_registry`·`dynamic_research` |
| competitor | `agents/competitor.py` | 경쟁사 분석(+검색 출처) | `competitor_result`·`competitor_sources`·`evidence_registry` |
| customer | `agents/customer.py` | 페르소나·Pain·니즈·JTBD | `customer_result` |
| pestel | `agents/pestel.py` | PESTEL 6요인×4항목 | `pestel_result` |
| swot | `agents/swot.py` | SWOT | `swot_result` |
| business_model | `agents/business_model.py` | 수익원·가격·비용·지표 | `business_model_result` |
| risk | `agents/risk.py` | 리스크(가능성·영향·대응) | `risk_result` |
| kosena_industry | `agents/kosena_industry.py` | Critical Uncertainties Top3·Porter·Value Chain·KSF 5·시사점 3 | `kosena`(얕은 병합) |
| kosena_model | `agents/kosena_model.py` | HMW 5 → 아이디어 25+ → 압축 3 → 컨셉 1 · Lean Canvas 9블록 · 핵심 가설 3 | `kosena` |
| kosena_research | `agents/kosena_research.py` | 페르소나 2종·CJM·TAM/SAM/SOM 교차검증·경쟁사 3·2·1 + 비교표·포지셔닝 맵 | `kosena` |
| kosena_roadmap | `agents/kosena_roadmap.py` | VPC·기능 5~7·Use Case 3·MOSCOW·Kano·MVP·Epic-Story-AC·와이어프레임 | `kosena` |
| draft | `draft_writer.py:draft` | 고정 14섹션 기획서 + 실제 출처 인용 | `draft` |
| reviewer | `reviewer.py:reviewer` | 초안 5항목 100점 + 개선지시 + 구조화 issues | `review_result`·`initial_review_result` |
| revise | `draft_writer.py:revise` | 전체 재작성(full-revise fallback) | `final_draft`·`revision_strategy=full` |
| section_revise | `draft_writer.py:section_revise` | 문제 섹션만 수정(PR-7) | `final_draft`·`revision_strategy=section`·`revised_section_ids` |
| finalize | `workflow.py:_finalize` | 재작성 없이 초안 확정 | `final_draft`·`revision_strategy=none` |
| polish | `draft_writer.py:polish` | 조건부 일관성 편집(PR-8) | `final_draft`·`polish_applied` |
| final_reviewer | `reviewer.py:final_reviewer` | 최종본 재평가(표시 점수) | `final_review_result` |
| select_best | `workflow.py:_select_best` | 재작성본 vs 초안 최고 점수 채택 | `best_version`·`reverted_from_revision` |
| verify | `agents/verifier.py` | 근거 일치성 검증(주장 유형·근거 상태·evidence_id 연결) | `verification_result` |

---

## 3. 공유 State

모든 노드는 하나의 `ProjectState`(TypedDict, `total=False`)를 읽고, 갱신할 키만 반환한다. `logs`·`timing_events`·`evidence_registry`는 **reducer 필드**(`Annotated[list, operator.add]`)라 병렬 노드가 동시에 방출해도 유실 없이 누적된다.

`app/schemas/state.py` (주요 키)
```python
# 입력·모델
user_input · model · reviewer_model            # reviewer_model: 심판 전용 모델(Phase 4)
# Agent 산출물
structured_input · research_result · competitor_result · competitor_sources
customer_result · swot_result · business_model_result · risk_result · pestel_result
evidence_registry: Annotated[list, operator.add]   # 통합 근거(2-1), 종료 시 normalize
evidence_gaps · dynamic_research                   # 근거 공백 보고 / 추가 조사 내역(2-5)
artifacts: Annotated[list, merge_artifacts]        # 표준 봉투 7개(2-2). id 기준 병합 reducer
artifact_parity · artifact_read                    # 정합성 자기점검 / 읽기 모드·폴백 계측(→§4.11)
# 문서·평가
draft · review_result · initial_review_result
final_draft · revision_count
revision_strategy · revised_section_ids · revision_fallback_reason   # PR-7
polish_applied · polish_skip_reason                                  # PR-8
best_version · reverted_from_revision                               # Phase 4 최고 버전 채택
final_review_result
verification_result · verification_summary                          # 검증 결과·한계 문구
quality_gate                                                        # 출력 게이트(Phase 4)
# 관측·품질·버전
logs · timing_events(reducer) · timing · usage · workflow_mode
run_status · failed_nodes · fallback_nodes · fallback_reasons
state_version                                                       # State 스키마 버전(Phase 5)
```

API 응답은 `RunResult`(pydantic, `api/routes.py:_result_payload`), 이력 저장 키는 `markdown_export._RUN_KEYS`, 재조회 정규화는 `migrate.upgrade_state`가 담당(→ §4.10).
**`/run`·`/run/stream`·`/revise` 가 모두 같은 `_result_payload`** 를 쓴다(리뷰3 D-1) — `/revise` 가 수동 dict 를 조립하던 동안 새 State 필드(`revision_strategy`·`polish_applied`·`best_version`·`state_version` 등)가 수정 응답에서 빠져, 수정 후 다운로드에 옛 값이 남았다. State 에 필드를 추가할 때 손볼 곳은 `ProjectState`·`RunResult`·`_RUN_KEYS`·`migrate._DEFAULTS` 넷뿐이다.

API 하드닝(리뷰3 D-4): `/projects?limit=` 은 `Query(50, ge=1, le=100)`(0·음수는 SQLite 에서 무제한이 된다), `/revise` 의 `project_id` 가 없으면 신규 저장이 아니라 **404**(이력이 조용히 쪼개지는 것 방지), `/run/stream` 은 이벤트 공백 구간에 `: keep-alive` SSE comment 를 흘린다(`_with_heartbeat` — 블로킹 생성기를 워커 스레드로 옮기고 소비자는 큐를 타임아웃 폴링. 긴 노드 구간에 바이트가 안 나가 reverse proxy 가 끊는 것 방지).

---

## 4. 핵심 설계 패턴

### 4.1 노드 완주 보장 — `_safe` 래핑
모든 노드는 `_safe(name, fn)`로 감싼다: 예외가 나도 로그만 남기고 진행(`{"logs":[...]}`), 단계 계측(`timing_events`)을 부착한다. 한 노드가 죽어도 완주하며, 다음 노드는 각자의 fallback으로 빈 입력을 견딘다.

### 4.2 Agent 공통 3단 패턴 — fallback → 검증 → 스키마 고정
모든 LLM Agent: ① `_dummy` fallback 준비 → ② `llm.complete_json(..., fallback=..., status=...)`(실패해도 예외 없이 fallback) → ③ `_validate(raw, fallback)`로 키 누락/타입오류를 중립 빈값으로 채워 다음 Agent가 항상 온전한 스키마를 받게 한다. 더미 문구(`[더미]…`)가 실제 응답에 새지 않도록 누락 키는 `expected()` 빈값으로 채운다.

### 4.3 LLM 래퍼 — 재시도·fallback·모델 방어·관측
`services/llm.py`: `complete_json`/`complete_text`(실패 시 fallback, JSON 파싱 실패 시 1회 재호출), `_extract_json`(코드펜스/중괄호 추출), `resolve_model`(허용목록 방어), `is_dummy`(`USE_DUMMY=1`/키 없음), `mode_label`(더미/실제·모델/fallback·사유 정직 표기).

### 4.4 관측성 — 실행별 격리(contextvar) + 단계 계측 + 트레이스
- `usage.py`: 호출마다 토큰·지연·fallback 기록, `contextvars`로 실행별 격리, 종료 시 호출수·토큰·**추정 비용(USD)**·지연 집계.
- `timing.py`: 노드 진입/종료 시각(상대 ms)으로 단계별 wall time·critical path·coverage 집계. 병렬 `analysis_block`은 4분기의 실제 대기시간(겹침 반영). 재작성 단계는 `section_revise`/`revise`/`finalize`를 `revise_or_finalize` 버킷으로 계측.
- `tracing.py`: Langfuse 콜백(키 없으면 무영향).

### 4.5 근거 파이프라인 — 웹검색 → Evidence Registry → 주장 검증(Tier 2)
- `search.web_search` 히트를 `<검색결과>`로 감싸 프롬프트 근거로 주입(§ADR-12 인젝션 방어). `search.build_source_objects`가 `{title,url,snippet,source_type,content_scope,original_text_extracted}` 객체 생성.
- **Evidence Registry**(`evidence.py`, 2-1): 분산 근거(`research.source_objects`+`competitor_sources`)를 단일 레지스트리로 통합. 항목 `{evidence_id, source_agents[], queries[], url, title, snippet, source_type, used_by_claims[]}`. `evidence_id`는 URL 최초 등장순 결정론(`ev1…`). 종료 시 `normalize`(URL 중복 제거)·`link_claims`(주장→근거 역인덱스).
- **verifier**(Tier 2): 기획서 주장을 뽑아 ① `claim_type`(fact/inference/proposal) 분류 → ② **사실 주장만** 근거로 판정. `status`=supported/unsupported/contradicted/uncertain(+비-사실 not_applicable), `evidence_ids`로 근거 인용(레지스트리에 없는 id는 필터). 지표: `fact_support_rate`·`evidence_link_rate`·`contradicted` 분리. **URL 원문 접속은 하지 않음**(검색 요약 근거 대조, `verification_scope=search_snippet_only`). `judge_claim`은 단일 주장 판정(GT 평가 재사용).
  - **판정 근거의 범위**(리뷰3 B-1·B-2): 검증 근거는 **레지스트리·검색 스니펫(외부 수집분)만**이고, `research_result`·`competitor_result`는 앞 단계 LLM의 2차 생성물이라 프롬프트에서 '참고 문맥'으로 분리한다(자기확인 차단). `_validate`의 근거 인자는 서로 독립: `valid_ids`(실존 id만 통과 — 레지스트리 없으면 빈 집합이라 지어낸 `ev999`는 전부 제거) / `require_evidence_link`(레지스트리가 있으면 연결 없는 `supported`를 uncertain 강등) / `evidence_available`(근거 텍스트가 전무하면 `supported` 불인정).

### 4.5b 제한된 동적 실행 — `research_gap` (로드맵 2-5)
"근거가 부족하면 더 찾는다"를 **자유 재량이 아니라 통제된 1스텝**으로 구현한다.
- **트리거**: Research 가 자기 출력에 `evidence_gaps: [{topic, query}]`(최대 2)로 **스스로 보고한 공백**뿐. 별도 판정 LLM 호출이 없어 감지 비용 0. 보고가 없으면 노드는 아무 것도 하지 않는다(호출 0).
- **상한**: 추가 검색 `DYNAMIC_MAX_GAP_SEARCHES`(기본 2·`0`=비활성) + 보강 LLM **1회**. 진입 전 `budget.should_skip_call()`로, 실제 호출은 `check_and_reserve()`로 예산에 걸린다(→ 트랙 C).
- **격리**: `evidence_gaps` 는 `research_result` 밖(state 별도 키)에 둔다 — 조사 결과에 남기면 Draft·분석 프롬프트에 '근거가 부족하다'는 메타가 섞여 문서에 새어든다.
- **보강 범위**: 새로 확보한 URL(기존 근거와 중복 제외)만 `sources`·`source_objects`·`evidence_registry`(`source_agents=["research_gap"]`)에 추가하고, 문자열 배열 4필드(`industry_trends`·`customer_needs`·`opportunities`·`risks`)에만 **덧붙인다**. 기존 값을 덮지 않으므로 LLM 실패(fallback `{}`)여도 조사 결과가 훼손되지 않는다.
- **정직 보고**: `dynamic_research{reported, searches[], new_sources, added_findings, applied, skip_reason}`. 생략 사유(`근거 공백 보고 없음`/`비활성`/`더미 모드`/`검색 비활성`/`예산 상한 도달`/`새 근거 없음`)를 남겨, '안 한 것'과 '해서 못 찾은 것'이 구분된다. UI 는 실제로 새 근거가 있었을 때만 칩을 띄운다.

### 4.6 섹션 단위 수정 (PR-7)
`sections.py`가 14섹션 stable ID↔제목(단일 원천 `SECTION_SPECS`, `draft_writer.SECTIONS`가 파생)·heading 파서(`parse_sections`)·조립기(`assemble`)를 제공. **미수정 섹션은 원문 raw 그대로 이어붙여 byte 동일**, 참고자료 등 밖 블록 보존. `plan_section_revision`이 라우팅 판정(구조화 issues의 critical/major 대상, `MAX_REVISED_SECTIONS=4` 초과·파싱 실패·자유형 요청이면 전체 재작성). `section_revise`는 대상 섹션 원문+이슈+관련 분석+앞뒤 요약만 입력. 런타임 실패(생성·조립 손상) 시 full-revise fallback(`revision_fallback_reason` 기록).

### 4.7 조건부 Polish (PR-8)
`_polish_skip_reason`: 전체 재작성(full)·표현 이슈(`_is_style_issue`: 문체/중복/가독성)·구조 이상이면 실행, 그 외(섹션단위·재작성없음 + 내용 이슈만 + 구조 정상)면 **생략**(문서 전체 재편집 LLM 호출 절감). 안전 편향(애매하면 실행). 실측: polish 병렬 21.3s→0.1ms, 생략이 읽기 품질을 해치지 않음(블라인드 tie 4/4, `polish_eval`).

### 4.8 Writer/Reviewer 모델 분리 (Phase 4)
`reviewer._reviewer_model` = `reviewer_model`(API 필드 또는 env `REVIEWER_MODEL`) 우선, 없으면 작성 `model`. reviewer·final_reviewer가 이 모델로 채점 → 자기 채점 편향 완화. 미지정 시 폴백이라 회귀 없음.

### 4.9 최고 버전 채택 (Phase 4)
`_select_best`(final_reviewer→**select_best**→verify): 재작성본(`final_review_result`) < 초안(`initial_review_result`)이면 `final_draft`를 초안으로 되돌리고 표시 점수도 초안 점수로 정정(verify가 뒤에서 채택 문서 검증). 동점·점수 없음·재작성 없음은 유지. 수동 `/revise`는 제외(사용자 의도 존중).

### 4.10 품질 게이트 & State 버전 (Phase 4·5)
- **quality_gate**(`quality_gate.py`): `release_ready = 총점≥80 · 치명 이슈 0 · 주요 이슈≤1 · 서식 정상 · 근거 충족률(fact_support_rate)≥0.8`. `blocking_reasons`·`unresolved_issues`(최종본 critical/major)로 무엇을 고칠지 안내. 임계값은 사람 보정 전 잠정값(`thresholds.calibrated=false`). state/응답/UI에 표면화. **사실 주장 0건이면 근거 충족률이 공허 충족(1.0)으로 자동 통과**하므로, `metrics.verifiable_claims`·`metrics.fact_total`·`na_checks`·`warnings`로 '검증 통과'와 '검증 대상 없음'을 구분해 보고한다(리뷰3 B-4 — release_ready 는 막지 않음, UI 는 해당 체크를 N/A 로 표시).
- **State 버전/재조회 정규화**(`migrate.py`, Phase 5): `STATE_VERSION`. SQLite JSON blob이라 DDL migration 대신 **읽기 시점** `upgrade_state`가 옛 기록의 누락 필드에 안전 기본값 주입 + `quality_gate` 소급 계산 + 버전 태깅(멱등). `store.get_project`·`_finalize_run`에서 적용.
  - 저장 payload(`store._payload`)는 **State 에 없는 키를 넣지 않는다.** `None` 으로 채우면 재조회 쪽 `state.get(k, {})` 가 기본값이 아니라 `None` 을 받아 `{**None}` → 500 이 된다(PR 5e).

### 4.11 Artifact Contract — 표준 봉투 + 읽기 모드 (로드맵 2-2)

Agent 결과는 원래 State 최상위의 **평면 키 7개**(`*_result`)로만 존재해, 누가 만들었는지·무엇에
의존하는지·어느 근거를 썼는지·어느 섹션을 책임지는지가 코드를 읽어야만 드러났다. 이 정보를
공통 봉투로 표준화한 것이 `schemas/artifact.py` 다. **`content` 내부 스키마는 Agent 마다 제각각인
채로 둔다** — 내용까지 통일하려면 7개 Agent 와 그 소비자를 한꺼번에 건드려야 한다.

```python
{artifact_id, artifact_type, owner_agent, schema_version,
 content, evidence_ids[], depends_on[], target_sections[], status, metadata}
```

- **통짜 교체가 아니라 Strangler 점진 전환**(사용자 결정). 평면 키 7개는 **그대로 두고** 같은
  내용을 병행 기록한다(Dual Write). 평면 키 제거는 이번 Phase 의 목표가 아니다.
- **`artifact_id` 는 랜덤·시간이 아니라 고정 상수**다(`evidence_id` 와 같은 이유) — 같은 State 면
  항상 같은 결과라야 테스트가 재현되고 재조회해도 id 가 흔들리지 않는다.
- **reducer `merge_artifacts`** — `operator.add` 를 쓰면 안 된다. 한 Agent 가 두 번 방출하거나
  (`research` 뒤 `research_gap` 이 보강본을 다시 냄) 재실행되면 중복된다. id 기준 덮어쓰기 +
  출력 순서를 위상 순서로 고정(병렬 도착 순서에 결과가 흔들리지 않도록).
- **`reconcile`(finalize)** 이 최종 확정: 평면 결과에서 파생 → **Agent 가 직접 쓴 것으로 덮고**
  → `evidence_ids`·`status` 를 이 시점 값으로 재확정. Agent 는 실행 시점에 둘 다 알 수 없다
  (`evidence_id` 는 종료 시 `normalize` 가 부여, failed/fallback 은 `_assess_quality` 가 판정).
- **`check_parity`(PR 3)** 가 평면 결과와 content 동등성을 자기점검하되 **실행을 실패시키지
  않는다** — 아직 그림자 구조인데 멀쩡한 실행을 죽이면 손해가 크다. `artifact_parity` 와 로그로
  표면화한다.

**읽기 모드 `ARTIFACT_READ_MODE`** — 소비자는 평면 키를 직접 읽지 않고 `artifact.read(state, type)`
단일 창구를 쓴다(평면 키 이름이 호출부에 나타나지 않아 명세와 어긋날 여지가 없다).

| 모드 | 동작 |
|---|---|
| `legacy`(기본) | 평면 키만 — 전환 전과 **완전히 동일** |
| `prefer_artifact` | Artifact 우선, 못 쓰면 평면 키로 폴백(사유 `missing`/`empty`/`failed` 기록) |
| `artifact_only` | Artifact 만. 못 쓰면 `ArtifactUnavailable` 로 **명시적 실패**(검증 전용) |

오타·미지값은 **가장 안전한 `legacy`** 로 떨어지되 조용히 넘어가지 않는다 — warning 로그 +
`/health.artifact_read_mode_invalid` + 실행 기록. **rollback = `ARTIFACT_READ_MODE=legacy` + 재시작**
('즉시'가 아니다 — `.env` 는 import 시 1회만 읽힌다).

**런타임 폴백 계측(PR 5d)** — `usage.py` 와 같은 contextvar 방식으로 읽기 호출을 실행 단위로 세어
`artifact_read.runtime` 에 싣는다. 가용성 스냅샷("쓸 수 있었나")과 실제 호출("몇 번 읽고 몇 번
떨어졌나")은 다른 질문이다. **shadow 측정**: `legacy` 는 Artifact 를 보지도 않아 폴백이 원리적으로
0 이므로, legacy 읽기마다 Artifact 쪽을 **관측만** 해서(반환값 불변) "전환했다면 떨어졌을" 횟수를
남긴다 — 운영 트래픽을 실제로 넘겨 보지 않고 준비도를 잰다. `measured=False` 는 "폴백 0"이 아니라
**"측정 안 함"**이다.

⚠️ **평면 키를 계속 직접 읽는 곳 = `api/routes.py`·`services/parallel_bench.py`(표시·집계)이며
의도된 것**이다(외부 API 는 평면 필드를 그대로 제공한다). `test_unconverted_readers_are_known`
이 이 목록을 고정해, 새 직접 읽기가 생기면 실패한다.

⚠️ **검증 방법론(중요)**: 더미 모드에서 **최종 문서 해시로는 분석 Agent 의 읽기 경로가 검증되지
않는다.** `_dummy()` 가 앞 Agent 결과를 실제로 쓰는 건 `competitor` 뿐이고 더미 초안은
research·pestel 만 반영하므로, "swot 이 빈 경쟁사 분석을 읽고 만든 SWOT"이 산출물 비교로는
정상으로 보인다(돌연변이 테스트로 실증). 그래서 **관통 실행의 LLM 프롬프트 스트림을 세 모드에서
대조**하는 것이 실질 검증이다.

### 4.12 KOSENA 방법론 산출물 (체크포인트 3)

기획 방법론 템플릿(KOSENA)은 문서 디자인이 아니라 **거쳐야 할 분석 프레임워크와 산출물 구조**를
규정한다. 구현은 세 부분으로 갈린다:

| 부분 | 파일 | 성격 |
|---|---|---|
| 생성 | `agents/kosena_{industry,model,research,roadmap}.py` | LLM 호출 4회. `state["kosena"]` 에 **얕은 병합 reducer**(`merge_kosena`)로 각자 자기 키만 |
| 판정 | `services/kosena.py` | **결정적·LLM 없음.** 28개 요구항목을 `ok`/`partial`/`missing` 으로 |
| 조립 | `services/kosena_doc.py`·`ai_log.py` | 7종 산출물 문서 + 발표자료 + AI 활용 로그. 순수 변환 |

**기존 14섹션 기획서를 재구성하지 않는다.** `final_draft` 를 KOSENA 구조로 갈아엎으면
`sections.py` 왕복 byte 동일 불변식 · `section_revise` · `quality_gate` 의 서식 체크 ·
`parallel_bench` 가 한꺼번에 깨진다. 반면 `docx_bytes(markdown)`·`pptx_bytes(markdown)` 는
**markdown 만 받는 순수 함수**라, 별도 조립본을 같은 함수에 넣으면 기존 경로를 한 줄도 건드리지
않고 산출물이 나온다.

**순환 의존을 조립 두 번으로 끊는다.** 판정은 조립된 본문에서 분량·가설 표기를 재고, 조립은 판정을
표로 싣는다. `_finalize_kosena` 가 **조립 → 판정 → 재조립 → 발표자료** 순으로 처리하고,
`_compliance_section` 이 판정 전에도 같은 행 수로 렌더링해 **두 조립의 줄 수를 같게** 만든다
(그래야 판정이 말하는 분량이 실제 내려받는 문서의 분량이다).

**허위 충족을 세 겹으로 막는다.** 각 Agent 의 `_dummy()` 는 검사를 통과할 만큼 구조가 완전해서,
실 LLM 실패 시 그것이 폴백으로 들어가면 "방법론을 지켰다"는 잘못된 판정이 나온다.
① `llm.dummy_fallback()` — 실모드 폴백은 빈 결과 ② `evaluate` 의 `data_source` — `is_dummy()` 가
아니라 **내용의 `[더미]` 표식**으로 판정(저장 기록 재판정 대비) ③ 항목별 `nodes` 매핑 — 기여 노드가
실패·폴백하면 `ok` → `partial` 상한.

**모자란 개수를 지어내 채우지 않는다.** KSF 가 4개면 4개로 두고 검사가 부분 충족을 말한다.
빈 문자열로 5개를 맞추면 검사만 통과하고 문서엔 빈칸이 남는 최악의 결과가 된다.

**구조적으로 채울 수 없는 항목은 가설 표기 자체를 검사한다.** 평가표 '고객 이해' 우수 기준은
1차 인터뷰·설문이고 AI 는 이를 만들 수 없다. 지어내면 KOSENA 원칙에 위배되므로, 목표를 '충족'이
아니라 **가설임을 명시하는 것**으로 두고 `hypothesis_labeling` 이 그 표기를 검사한다.

상세·항목별 매핑 = [`kosena-compliance.md`](kosena-compliance.md) ·
평가 기준 전체 매핑 = [`평가기준_매핑표.md`](평가기준_매핑표.md)

---

## 5. 설계 결정 기록 (ADR) — 요약

| # | 결정 | 핵심 결과·트레이드오프 |
|---|---|---|
| 1 | LangGraph StateGraph + 전 노드 `_safe` | 한 노드 죽어도 완주. 빈 입력은 ADR-3로 보완 |
| 2 | 단일 공유 State(TypedDict, reducer 필드) | 확장 쉬움·병렬 안전. State 결합도 존재 |
| 3 | fallback + `_validate` 이중 방어 | 항상 온전한 타입·더미 유출 방지. 검증 코드 중복 |
| 4 | 더미 모드(키 없이 골격 검증) | CI/개발 무비용·결정적. 더미 산출물은 품질 검증 불가 |
| 5 | 웹검색 grounding + 실제 출처 병합 | 추적 가능한 출처(비교 0 vs 5). Tavily 비용 |
| 6 | 비교 채점: 심판 N회 평균 + 하드 URL 카운트 | 노이즈 완화 + LLM 비의존 객관 지표 |
| 7 | verifier 명칭 정직화("근거 일치성 검증") | 구현 수준과 명칭 일치(URL 접속 아님) |
| 8 | 실행 품질 표면화(run_status) | fallback/더미 정직 경고. 로그 휴리스틱 의존 |
| 9 | 관측성 contextvar 격리 + 단가표 근사 | 실행별 정확 집계. 미등록 모델 비용 0 |
| 10 | 이력 SQLite(JSON blob + 조회 컬럼) | 의존성 0·완전 복원. 블롭 내부 쿼리 불가 |
| 11 | provider/모델 허용목록 방어 | 유연 선택 + 런타임 안전 |
| 12 | 검색결과 프롬프트 인젝션 방어 | `<검색결과>` 격리·가드 문구(저비용·고효과) |
| **13** | **병렬 그래프 + WORKFLOW_MODE Feature Flag** | 직렬/병렬 동일 산출물, 순서만 다름 → 비열등성 하에 latency만 비교. 실측 wall −16~23% |
| **14** | **Evidence Registry(단일 근거 소스)** | 분산 근거 통합 → 주장-근거 연결·Tier 2 재작업 없이 이어짐. 추가형(회귀 0) |
| **15** | **신뢰도 Tier 2(주장 유형/근거 상태 분리)** | 사실만 검증·반대근거↔미확인 분리. 같은 콜 재사용(비용 0). GT 허위통과 0/4 |
| **16** | **PR-7 섹션 단위 수정 + full-revise fallback** | 재작성 24.4s→8.5s(−65%), 미수정 섹션 byte 동일. reviewer 구조화 issues 필요 |
| **17** | **PR-8 조건부 Polish** | polish 생략(21.3s→0.1ms), 품질 손해 없음(블라인드 tie). reviewer 표현 이슈 신호 의존 |
| **18** | **Phase 4 품질 게이트 + 최고 버전 채택 + 모델 분리** | 출력 가능 여부·미해결 이슈 표면화, 나쁜 재작성 되돌림, 자기 채점 편향 완화 |
| **19** | **Phase 5 State 버전 + 읽기 시점 정규화** | 옛 프로젝트 재조회 호환(누락 필드·게이트 소급). DDL migration 없음(JSON blob) |
| **20** | **2-5 제한된 동적 실행(`research_gap`)** | 트리거를 'Agent 가 보고한 근거 공백'으로 한정 + 검색·LLM 상한 + 예산 연동 → 자유 동적 실행의 비용 폭주 없이 근거를 보강. 항상 도는 no-op 노드라 그래프가 갈라지지 않음(대신 노드 1개 상시 추가) |
| **21** | **2-2 Artifact Contract를 통짜 교체가 아닌 Strangler 점진 전환으로** | 평면 키 7개가 운영 17파일 90회 참조돼 통짜 교체 위험 8~9/10. 병행 기록(Dual Write) + 읽기 모드 플래그로 위험을 PR 단위로 쪼갬. 대가: 같은 내용이 두 곳에 존재(저장 +29.6%)하고 전환이 길어짐 |
| **22** | **읽기 전환을 env 플래그(`ARTIFACT_READ_MODE`) 뒤에 두고 기본은 `legacy`** | 코드 되돌리기 없이 rollback(재시작 필요). 오타는 가장 안전한 legacy 로 떨어지되 경고·`/health` 로 표면화 — 조용히 무시하면 운영자가 전환됐다고 오해 |
| **23** | **가용성 스냅샷과 별개로 런타임 폴백 카운터 + legacy 의 shadow 측정** | "쓸 수 있었나"와 "실제로 몇 번 떨어졌나"는 다른 질문. shadow 로 **전환 전에** 준비도를 재 운영 트래픽을 실험대에 올리지 않음. `measured=False`(측정 안 함)를 0(폴백 없음)과 구분 |

> ADR-1~12의 상세 배경은 git 이력 및 이전 문서 버전 참조. 자동 재작성 1회 상한(구 ADR-8)은 `_route_revision`의 `revision_count<1`로 유지.

---

## 6. 주요 코드 지도

| 하고 싶은 것 | 파일:심볼 |
|---|---|
| 워크플로 노드·엣지·분기 | `graph/workflow.py:build_serial_graph`·`build_parallel_graph`·`_add_finish_edges`·`_route_revision` |
| 재작성 통과 점수 / 실행 모드 | `workflow.py:PASS_SCORE` · `_resolve_mode`(`WORKFLOW_MODE`) |
| 섹션 단위 수정 라우팅·수정 | `draft_writer.py:plan_section_revision`·`section_revise` · `services/sections.py` |
| 조건부 Polish | `draft_writer.py:polish`·`_polish_skip_reason`·`_is_style_issue` |
| 최고 버전 채택 | `workflow.py:_select_best` |
| 심사(초안/최종)·모델 분리 | `reviewer.py:reviewer`·`final_reviewer`·`_reviewer_model`; 구조화 issues `_validate_issues` |
| 근거 레지스트리 | `services/evidence.py:entries_from`·`normalize`·`for_prompt`·`link_claims` |
| Artifact 명세·의존 관계 | `schemas/artifact.py:LEGACY_ARTIFACT_SPECS`(위상 순서·`depends_on`·`target_sections`) |
| Artifact 쓰기(Dual Write)·병합·확정 | `artifact.py:make_artifact`·`merge_artifacts`(reducer)·`reconcile`·`check_parity` |
| Artifact 읽기(소비자 단일 창구) | `artifact.py:read`·`get_artifact_content`·`read_mode`(`ARTIFACT_READ_MODE`) |
| 읽기 폴백 계측·shadow | `artifact.py:reads_start`·`reads_summary`·`read_status`; 부착 `workflow._finalize_artifacts` |
| 근거 검증(Tier 2) | `agents/verifier.py:verify`·`_validate`·`judge_claim` |
| 품질 게이트 | `services/quality_gate.py:evaluate`(임계값 상수) |
| State 버전·재조회 정규화 | `services/migrate.py:STATE_VERSION`·`upgrade_state` |
| LLM 호출/재시도/파싱 | `services/llm.py:complete_json`·`_extract_json`·`resolve_model` |
| 웹검색·출처 객체 | `agents/research.py` · `services/search.py:build_source_objects` |
| 이력 저장/조회 | `services/store.py:save_run`·`update_run`·`get_project`; 저장 키 `markdown_export._RUN_KEYS` |
| 관측치·단계 계측 | `services/usage.py` · `services/timing.py:summarize` |
| API 엔드포인트·응답 스키마 | `api/routes.py` · `schemas/state.py:RunResult` |
| 병렬 벤치 | `run_parallel_bench.py` · `services/parallel_bench.py` |
| 평가·게이트 실측 | `run_eval.py`(`evaluation`·`eval_set`) · `run_gt_eval.py`(`gt_eval`) · `run_polish_eval.py`(`polish_eval`) |
| Artifact 실 LLM 검증 | `run_artifact_real_check.py` · `services/artifact_real_check.py:prompt_parity`·`summarize` |

---

## 7. 데이터 모델 (이력 DB)

`services/store.py` — `data/projects.db` (실행 시 생성)
```sql
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_name TEXT, model TEXT, total_score INTEGER,
    created_at TEXT,           -- UTC ISO8601
    state_json TEXT            -- 전체 실행 상태(_RUN_KEYS, 복원용)
);
```
`/run`이 `save_run(state)`로 저장, `/projects`·`/projects/{id}`로 조회. 재조회 시 `migrate.upgrade_state`로 현재 스키마 정규화(옛 기록 호환). API 스키마 버전은 `main.py` FastAPI `version` + State `state_version`.

---

## 8. 새 Agent 추가 절차

1. `agents/<name>.py` — 공통 3단 패턴(`_dummy` → `llm.complete_json` → `_validate`).
2. 출력 키를 `schemas/state.py:ProjectState`(+필요 시 `RunResult`·`_RUN_KEYS`·`migrate._DEFAULTS`)에 추가.
3. `workflow.py`의 직렬·병렬 그래프 양쪽에 `add_node`(반드시 `_safe`) + 엣지 등록.
4. 검색 근거를 쓰면 `evidence.entries_from`로 `evidence_registry` 방출.
5. `draft_writer`에서 결과를 서식에 반영(필요 시 `sections.SECTION_SPECS`).
6. **Artifact(§4.11)**: `LEGACY_ARTIFACT_SPECS` 에 명세 추가(`depends_on` 은 상상이 아니라 **실제
   읽는 유형**으로 — `test_declared_depends_on_matches_actual_runtime_reads` 가 대조한다) +
   반환에 `artifact.make_artifact(...)` 방출(Dual Write). 앞 Agent 결과를 읽을 때는 평면 키를
   직접 읽지 말고 **`artifact.read(state, type)`**.
7. `tests/`에 `_validate` 중심 테스트 추가(LLM 없이).

---

## 9. 실행·검증 빠른 참조

```bash
uvicorn app.main:app --reload            # 서버 → http://localhost:8000/ · /docs(OpenAPI)
WORKFLOW_MODE=parallel uvicorn ...        # 병렬 그래프로 실행(기본 serial)
ARTIFACT_READ_MODE=prefer_artifact ...    # Artifact 우선 읽기(기본 legacy) — §4.11
curl localhost:8000/health                # 현재 읽기 모드·오타 여부 확인
python run_parallel_bench.py --topics 3 --reps 2 --fresh   # 직렬 vs 병렬 실측(유료)
python run_eval.py --topics 5 --samples 2 # 8기준 루브릭 평가(유료)
python run_gt_eval.py                     # 신뢰도 GT 스모크셋(유료, 소액)
python run_polish_eval.py                 # PR-8 Polish 품질 블라인드 검증(유료, 소액)
python run_artifact_real_check.py --topics 6   # Artifact 읽기 전환 실 LLM 검증(유료, 소액)
pytest -q                                 # 회귀 테스트(무비용·USE_DUMMY/mock)
ruff check .                              # 정적 검사(무비용)
```
CI(`.github/workflows/ci.yml`): PR/main push마다 **4잡** — ruff+pytest(커버리지 하한 90%)·
gitleaks·pip-audit·docker build. 실 LLM 미호출. main 브랜치 보호에 4개 모두 required check
(⚠️ 잡 `name:` 을 바꾸면 브랜치 보호 설정도 함께 갱신해야 한다).
⚠️ 로컬 `.env` 에 `WORKFLOW_MODE=parallel` 이 있으면 직렬을 가정한 테스트 1건이 실패한다
(CI 는 `WORKFLOW_MODE=serial` 을 명시한다).
