# 외부 코드리뷰 3차 — 실행 계획 (2026-07-25)

> 최신 ZIP 기준 외부 리뷰. **구현은 후속 세션**에서 A→B→C→D 순서로 진행한다(사용자 결정:
> "A부터 순서대로, 일단 나중에"). **이 항목들을 이전에 하기로 했던 로드맵 후속(2-5 동적 실행·
> 2-2 Artifact·Tier 3·Verifier 근거 분리·Phase 6 후속)보다 먼저** 처리한다.
>
> 배경: 오늘 Docker/Staging(#84)·예산 정책(#86)이 들어오며, "로컬 데모엔 안 보이고 Staging에서
> 터지는" 배포 보안·예산 제어 이슈가 배포 전 우선 보완 대상이 됐다. 리뷰는 코드를 정확히 짚었고
> 대부분 유효하다(아래 뉘앙스 포함). 검증: Python compileall·JS node --check·langgraph 비의존
> 테스트 95 통과(전체 테스트는 리뷰 환경에 langgraph 미설치로 미완주).

각 묶음은 **별도 PR**, main 기준 브랜치, 커밋→PR→머지 후 다음 묶음(스택 금지).

---

## A. 배포/운영 안전 (최우선 — 배포 전 필수) — ✅ 구현 완료 (2026-07-25)

> 구현 시 조정 1건: A-1 은 `/admin` **라우트를 조건부 등록**하는 대신 항상 등록하되 게이트 off 면
> 핸들러가 404 를 반환하고 OpenAPI 에서 숨긴다(`include_in_schema=False`). 외부에서 관측되는
> 결과(404·문서 미노출)는 같고, 재기동 없이 켜고 끌 수 있으며 양쪽 상태를 테스트로 덮을 수 있다.
> 요청 단위 주입 무시(2겹 게이트)는 계획대로 `demo._reason_for` 에서 처리.


### A-1. 데모 장애 주입 기능 운영 차단
- **문제**: `ProjectInput.demo_fail_nodes`/`demo_fail_reason`가 공개 API에 노출되고 `/admin`이 무조건 서빙됨(`app/main.py`). 외부 사용자가 Research·Draft·Reviewer 등을 의도적으로 실패시킬 수 있음. Docker/Staging 생기며 운영 보안 문제로 격상.
- **파일**: `app/main.py`·`app/schemas/state.py`·`app/services/demo.py`·`app/static/admin.html`
- **조치**: `ENABLE_DEMO_TOOLS` 환경변수(기본 `0`=off). `1`일 때만 `/admin` 라우트 등록. off면 `demo._reason_for`가 요청의 `demo_fail_nodes`를 무시(또는 400 거부). env 경로(`DEMO_FAIL_NODES`)는 운영자 명시 설정이라 유지 가능하나 문서화.
- **테스트**: off일 때 `/admin` 404 + `demo_fail_nodes` 넣어도 fallback_reasons에 안 잡힘.

### A-2. Docker 볼륨 쓰기 권한 (Linux)
- **문제**: 이미지가 비루트 UID 10001로 실행되는데 compose가 host bind mount(`./data`·`./outputs`)라 소유권이 host 권한으로 덮여 SQLite/산출물 쓰기 실패 가능. `/health`는 파일을 안 써서 정상, `/run`·`/run/save`에서만 실패.
- **파일**: `docker-compose.yml`·`Dockerfile`·`DEPLOY.md`
- **조치(권장)**: named volume 전환(`project-data:/app/data`·`project-outputs:/app/outputs` + top-level `volumes:`). bind mount 유지 시 `DEPLOY.md`에 `sudo chown -R 10001:10001 data outputs` 사전 설정 명시.

### A-3. deploy-staging false green
- **문제**: `STAGING_DEPLOY_ENABLED=true`면 실제 배포 없이 안내 문구만 출력하고 job 성공 → Actions에서 "Staging 배포 성공"처럼 보임.
- **파일**: `.github/workflows/deploy-staging.yml`
- **조치**: placeholder job을 명시적 실패(`exit 1`)시키거나 job 이름을 `deployment-placeholder`로, 또는 deploy job을 예제 파일로 분리.

### A-4. smoke_test 실서버 오호출 방지
- **문제**: 문서엔 더미 대상이라 적혔지만 `/health`의 `dummy_mode==true` 확인 안 함 → 실 키 서버에 실행 시 실제 LLM 비용 발생. `/projects`도 예외 처리 없어 traceback 노출 가능.
- **파일**: `scripts/smoke_test.py`
- **조치**: 기본은 dummy만 허용(health의 `dummy_mode` 확인), 실모드는 `--allow-real` 옵션. `/projects` 요청 예외 처리.

---

## B. 정확성 / 정합성 (데이터·검증) — ✅ 구현 완료 (2026-07-25)

> 구현 시 추가 1건: B-4 의 N/A 표면화를 백엔드(`na_checks`·`warnings`·`metrics.verifiable_claims`)에
> 그치지 않고 결과 화면에도 반영했다(통과 배너에 경고 문구, 게이트 체크칩에 `(N/A)` 표시).
> 백엔드만 고치면 사용자는 여전히 "근거 기준 충족"으로 읽게 되므로 정직 표면화가 완성되지 않는다.


### B-1. verifier: Registry 미존재 시 가짜 evidence_id 통과
- **문제**: 레지스트리 없으면 `valid_ids=None` → `_clean_evidence_ids`가 모든 문자열 ID 허용(모델이 지어낸 `ev999`도 통과). 주석("연결 생략")과 실제 동작(임의 ID 허용) 불일치.
- **파일**: `app/agents/verifier.py`
- **조치**: `require_evidence_link = bool(registry)`와 `valid_ids`를 분리. 레지스트리 없으면 `valid_ids=set()`(가짜 ID 전부 제거). supported→uncertain 강등은 `require_evidence_link`일 때만.

### B-2. verifier: 2차 생성물 자기확인 (이전에도 플래그)
- **문제**: verify 프롬프트가 검색 스니펫 외 `research_result`·`competitor_result`(앞선 LLM의 2차 생성물)까지 근거로 받음 → 자기확인.
- **조치**: 검증 근거=Evidence Registry 스니펫만, research/competitor=분석 문맥으로만. supported는 실제 evidence_id 있을 때만 인정. (B-1과 함께 처리 권장.)

### B-3. `_RUN_KEYS`에 model·user_input 누락
- **문제**: `model`은 DB 별도 컬럼에만 저장되고 `get_project()`가 state에 되돌려 넣지 않음 → 재조회→`/revise` 시 원 모델 유실(서버 기본 모델로 수정됨). `user_input`도 미저장이라 최초 입력 문맥 유실. (내 P2-8은 DB 컬럼 갱신만 고쳤고 state 복원은 미해결.)
- **파일**: `app/services/markdown_export.py`(`_RUN_KEYS`)·`app/services/store.py`
- **조치**: `_RUN_KEYS`에 `"user_input"`·`"model"` 추가. 기존 DB 호환 위해 `get_project()`에서 `state.setdefault("model", d.get("model",""))` 보완.

### B-4. quality_gate: fact_total==0 자동 통과
- **문제**: 사실 주장 0개면 근거 충족률 1.0으로 자동 통과 → verifier가 모든 주장을 inference/proposal로 분류하면 사실 검증 없이 게이트 통과.
- **파일**: `app/services/quality_gate.py`
- **조치(뉘앙스: 하드 실패보다 표면화)**: `verifiable_claims`(fact_total>0)를 metrics/checks에 별도 표시. 사실 주장이 없는 게 정상인 기획서도 있으므로 N/A로 구분해 release 판단에 반영(자동 통과를 '검증 없음'으로 정직하게 노출).

---

## C. budget 견고화 (2-5 동적 실행 전 필수) — ✅ 구현 완료 (2026-07-25)

> 구현 시 조정 1건: C-2 의 "예약·**기록**을 chat.invoke 직전으로"에서 **기록(usage 집계)은 옮기지
> 않았다**. 예산 상한은 예약 카운터(=provider 호출 시도)로 정확히 세고, `usage` 는 기존 의미
> (논리 호출·토큰·비용, UI·비용 표시 계약)를 유지한다. 시도 수와 논리 호출 수의 차이는
> `budget.status()["spent"]["provider_attempts"]` 로 노출한다. usage 쪽 의미를 바꾸면
> `calls`·`fallback_calls` 를 쓰는 UI·벤치·기존 테스트 계약이 함께 흔들리는데, 상한 정확성에는
> 예약 카운터만으로 충분하다.


> 뉘앙스: **캡 자체는 병렬에서도 작동**한다(`usage._calls`가 공유 리스트라 `live_spend`·
> `should_skip_call`이 스레드 간 정확). 깨지는 건 `enforced` **플래그 보고**뿐(`.set(True)`가
> 부모 컨텍스트로 전파 안 됨). 원자적 예약은 병렬 4분기 고정+넉넉한 기본값이라 지금은 초과폭이
> 제한적이나, 동적 실행 확대 전엔 반드시 보완.

### C-1. enforced 병렬 전파
- **문제**: `_enforced`가 bool을 담는 ContextVar. 자식 스레드의 `.set(True)`가 부모로 전파 안 됨 → 실제로 예산 때문에 호출 생략됐어도 state/UI엔 `enforced=false` 기록 가능.
- **파일**: `app/services/budget.py`
- **조치**: 실행별 공유 가변 객체(dataclass `BudgetState{enforced, reserved_calls, lock}`)를 ContextVar에 저장(usage의 공유 리스트와 동일 원리).

### C-2. 원자적 호출 예약 + attempt별 집계
- **문제**: "확인→호출"이 비원자적이라 병렬 Agent가 동시에 통과해 상한 초과 가능(예: 상한 10, 현재 9, 4개 동시 통과→13). 또 `complete_json`은 함수 시작에 1회만 예산 확인 → JSON 파싱 재호출(2번째 `_timed_invoke`) 전 재확인 없음. `_invoke_with_retry`는 provider를 최대 2회 호출하나 성공분만 집계 → "호출 수"가 논리 호출에 가까움(실제 청구와 차이).
- **파일**: `app/services/budget.py`·`app/services/llm.py`
- **조치**: `check_and_reserve()`(lock으로 한도 내면 `reserved_calls+=1`). 예약·기록을 `chat.invoke()` **직전**으로 이동(파싱 재호출·provider 재시도 각각 별도 호출로 집계).

### C-3. 테스트 보강
- 병렬 ContextVar 상태 전파 · 여러 병렬 호출의 원자적 예약 · JSON 파싱 재호출 예산 차감 · provider 재시도 실제 호출 수 집계. (budget 핵심 목적이 병렬·동적 폭주 방지이므로 이 테스트가 있어야 기능 완료.)

---

## D. 프런트 / API 정합성

### D-1. `/revise` 응답 모델화 (누락 메타 근본 해결)
- **문제**: `/run`은 `RunResult`, `/revise`는 수동 dict → 신규 State 필드 추가 시 누락 쉬움. 실제로 `revision_strategy`·`revised_section_ids`·`revision_fallback_reason`·`polish_applied`·`polish_skip_reason`·`best_version`·`reverted_from_revision`·`failed_nodes`·`state_version`가 응답/`lastRun`에 미반영 → 수정 후 JSON 다운로드에 옛 값 잔존.
- **파일**: `app/api/routes.py`·`app/static/index.html`
- **조치**: `ReviseResult` 모델 또는 `_result_payload()` 재사용. 프런트 revise 핸들러에서 위 필드 전부 `lastRun`에 반영.

### D-2. 통일 오류 메시지 프런트 사용
- **문제**: 백엔드 `app/api/errors.py`는 `{error:{code,message,details}}`를 주지만 프런트는 `throw new Error("서버 오류 " + r.status)`만.
- **조치**: `apiError(response)` 공통 함수(응답 JSON의 `error.message` 우선) 도입, 모든 fetch 실패에 사용.

### D-3. 진행 화면 정합
- ① 실행 모드/예상시간 고정 문구("병렬로 수행 약 30초~1분") vs 기본 serial → SSE `start`의 `workflow_mode`를 읽어 동적 문구. ② 병렬에서 뒤 노드 하나 완료 시 앞 단계 전부 완료 처리(`si>curStage`) → 완료 노드 집합 관리, 단계 필수 노드 전부 완료 시에만 완료.
- **파일**: `app/static/index.html`

### D-4. API 하드닝
- `/projects` `limit`에 경계(`Query(50, ge=1, le=100)`). `/revise`에 `project_id` 명시됐는데 없으면 신규 저장 대신 404. `/run/stream` SSE heartbeat(`: keep-alive` 주기 comment)로 reverse proxy 연결 유지.
- **파일**: `app/api/routes.py`

### D-5. 테스트 이름
- `tests/test_e2e_full_flow.py`는 브라우저 E2E가 아니라 FastAPI TestClient API 통합 → `test_api_journey.py`로 리네임. (선택) Playwright 브라우저 테스트 1~2개 추가로 프런트 문제(진행단계·수정 메타·오류 메시지) 커버.

---

## 잘 된 부분 (리뷰 확인)
저장·재조회 불일치 보완, 반대 근거 게이트 반영(contradicted), 조건부 Polish·섹션 수정 메타 보존,
통일 API 오류 형식, 예산 상한 구조, Docker·Staging CD·스모크, API 통합 테스트 확대 — 이전보다
확실히 서비스 운영 단계에 근접.
