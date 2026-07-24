# 아키텍처 · 설계 결정(ADR) · 주요 코드

> 갱신: 2026-07-24 · 대상: 현재 main · 코드 근거를 함께 표기
> 관련 문서: [`../README.md`](../README.md) · [`개발_로드맵_v2.md`](개발_로드맵_v2.md) · [`PRD.md`](PRD.md) · [`정보신뢰성_전략.md`](정보신뢰성_전략.md) · [`병렬화_측정결과_및_PR7_계획.md`](병렬화_측정결과_및_PR7_계획.md)

이 문서는 **"왜 이렇게 만들었는가"(설계 결정)**와 **"어디를 보면 되는가"(주요 코드)**를 한곳에 모은 개발자용 레퍼런스입니다.

---

## 1. 개요

아이디어 한 줄 → 여러 AI Agent가 분석을 쌓아 근거 있는 서비스 기획서를 생성하는 도구. FastAPI + LangGraph 기반이며, **실제 웹 검색으로 근거를 확보하고 그 출처를 기획서에 인용**하고, **주장을 근거와 대조 검증**하며, **출력 가능 여부를 게이트로 판정**하는 것이 핵심.

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
│ [벤치] parallel_bench                                        │
└────────────────────────────────────────────────────────────┘
```

### 2.2 워크플로 (직렬/병렬 공통 마무리)

`app/graph/workflow.py` — `build_serial_graph` / `build_parallel_graph`, `WORKFLOW_MODE`(env/인자)로 선택. 기본 직렬.

**분석 구간(직렬):**
```text
START → preprocess → research → competitor → customer → pestel → swot
      → business_model → risk → draft → [마무리]
```

**분석 구간(병렬, `build_parallel_graph`):** Research 이후 독립 4분기를 동시 실행 → Draft 에서 fan-in join.
```text
research ┬→ competitor → swot ┐
         ├→ customer          ├→ (모두 완료 후 1회) draft → [마무리]
         ├→ pestel → risk     │
         └→ business_model ────┘
```
- Agent 입력·프롬프트·결과 구조는 직렬과 **동일**, 실행 순서만 다르다(비열등성 전제). 지연 차이만 병렬화 효과.
- fan-in: `add_edge(["swot","customer","risk","business_model"], "draft")` — 깊이 다른 분기의 조기/중복 실행 방지.

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

### 2.3 노드별 역할

| 노드 | 파일:심볼 | 역할 | 핵심 출력 키 |
|---|---|---|---|
| preprocess | `agents/preprocess.py` | 입력 구조화(함수) | `structured_input` |
| research | `agents/research.py` | 웹검색 grounding + 시장조사 + 근거 방출 | `research_result`·`evidence_registry` |
| competitor | `agents/competitor.py` | 경쟁사 분석(+검색 출처) | `competitor_result`·`competitor_sources`·`evidence_registry` |
| customer | `agents/customer.py` | 페르소나·Pain·니즈·JTBD | `customer_result` |
| pestel | `agents/pestel.py` | PESTEL 6요인×4항목 | `pestel_result` |
| swot | `agents/swot.py` | SWOT | `swot_result` |
| business_model | `agents/business_model.py` | 수익원·가격·비용·지표 | `business_model_result` |
| risk | `agents/risk.py` | 리스크(가능성·영향·대응) | `risk_result` |
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

### 4.6 섹션 단위 수정 (PR-7)
`sections.py`가 14섹션 stable ID↔제목(단일 원천 `SECTION_SPECS`, `draft_writer.SECTIONS`가 파생)·heading 파서(`parse_sections`)·조립기(`assemble`)를 제공. **미수정 섹션은 원문 raw 그대로 이어붙여 byte 동일**, 참고자료 등 밖 블록 보존. `plan_section_revision`이 라우팅 판정(구조화 issues의 critical/major 대상, `MAX_REVISED_SECTIONS=4` 초과·파싱 실패·자유형 요청이면 전체 재작성). `section_revise`는 대상 섹션 원문+이슈+관련 분석+앞뒤 요약만 입력. 런타임 실패(생성·조립 손상) 시 full-revise fallback(`revision_fallback_reason` 기록).

### 4.7 조건부 Polish (PR-8)
`_polish_skip_reason`: 전체 재작성(full)·표현 이슈(`_is_style_issue`: 문체/중복/가독성)·구조 이상이면 실행, 그 외(섹션단위·재작성없음 + 내용 이슈만 + 구조 정상)면 **생략**(문서 전체 재편집 LLM 호출 절감). 안전 편향(애매하면 실행). 실측: polish 병렬 21.3s→0.1ms, 생략이 읽기 품질을 해치지 않음(블라인드 tie 4/4, `polish_eval`).

### 4.8 Writer/Reviewer 모델 분리 (Phase 4)
`reviewer._reviewer_model` = `reviewer_model`(API 필드 또는 env `REVIEWER_MODEL`) 우선, 없으면 작성 `model`. reviewer·final_reviewer가 이 모델로 채점 → 자기 채점 편향 완화. 미지정 시 폴백이라 회귀 없음.

### 4.9 최고 버전 채택 (Phase 4)
`_select_best`(final_reviewer→**select_best**→verify): 재작성본(`final_review_result`) < 초안(`initial_review_result`)이면 `final_draft`를 초안으로 되돌리고 표시 점수도 초안 점수로 정정(verify가 뒤에서 채택 문서 검증). 동점·점수 없음·재작성 없음은 유지. 수동 `/revise`는 제외(사용자 의도 존중).

### 4.10 품질 게이트 & State 버전 (Phase 4·5)
- **quality_gate**(`quality_gate.py`): `release_ready = 총점≥80 · 치명 이슈 0 · 주요 이슈≤1 · 서식 정상 · 근거 충족률(fact_support_rate)≥0.8`. `blocking_reasons`·`unresolved_issues`(최종본 critical/major)로 무엇을 고칠지 안내. 임계값은 사람 보정 전 잠정값(`thresholds.calibrated=false`). state/응답/UI에 표면화.
- **State 버전/재조회 정규화**(`migrate.py`, Phase 5): `STATE_VERSION`. SQLite JSON blob이라 DDL migration 대신 **읽기 시점** `upgrade_state`가 옛 기록의 누락 필드에 안전 기본값 주입 + `quality_gate` 소급 계산 + 버전 태깅(멱등). `store.get_project`·`_finalize_run`에서 적용.

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
6. `tests/`에 `_validate` 중심 테스트 추가(LLM 없이).

---

## 9. 실행·검증 빠른 참조

```bash
uvicorn app.main:app --reload            # 서버 → http://localhost:8000/ · /docs(OpenAPI)
WORKFLOW_MODE=parallel uvicorn ...        # 병렬 그래프로 실행(기본 serial)
python run_parallel_bench.py --topics 3 --reps 2 --fresh   # 직렬 vs 병렬 실측(유료)
python run_eval.py --topics 5 --samples 2 # 8기준 루브릭 평가(유료)
python run_gt_eval.py                     # 신뢰도 GT 스모크셋(유료, 소액)
python run_polish_eval.py                 # PR-8 Polish 품질 블라인드 검증(유료, 소액)
pytest -q                                 # 회귀 테스트 242개(무비용·USE_DUMMY/mock)
ruff check .                              # 정적 검사(무비용)
```
CI(`.github/workflows/ci.yml`): PR/main push마다 ruff + pytest(실 LLM 미호출).
