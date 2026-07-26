# Phase 2-2 Artifact Contract — 점진적 전환 계획 (2026-07-26)

> **결정: 통짜 교체가 아니라 Strangler 방식의 점진 전환.**
> 기존 7개 결과 키를 **유지한 채** 동일 내용을 표준 Artifact로 **병행 생성**하고,
> 검증이 끝난 소비자부터 단계적으로 전환한다.
>
> **첫 성공 기준은 "기존 키 삭제"가 아니라 "Artifact가 기존 결과와 100% 동일하게 생성되는 것"이다.**

---

## 0. 왜 통짜 교체를 하지 않는가 (실측)

`{research,competitor,customer,swot,business_model,risk,pestel}_result` 7개 평면 키의 직접 참조:

| 구분 | 파일 수 | 출현 수 |
|---|---|---|
| 운영 코드 (`app/`·`scripts/`·`run_*.py`) | 17 | **90** |
| 테스트 | 9 | **34** |

> 측정 명령(2026-07-26, main `6a56a05`):
> `grep -rIohE '(research|competitor|customer|swot|business_model|risk|pestel)_result' app/ scripts/ run_*.py | wc -l`
> (파일 수는 `-l`). 앞선 세션 대화에서 언급한 "100군데"는 출현 수가 아니라 **매칭된 줄 수**였다 —
> 같은 줄에 두 키가 나오면 1로 세어진다. 위 표가 정확한 수치다.

이미 다음과 전부 결합돼 있다: State 버전 관리(`migrate.STATE_VERSION=2`) · API 응답(`RunResult`) ·
저장·복원(`markdown_export._RUN_KEYS`) · UI(`index.html`) · 성능 벤치(`parallel_bench`) ·
제한적 동적 실행(`research_gap`). **완전 교체의 위험은 이전보다 커졌다.**

반면 2-5(`research_gap`)가 들어오면서 Artifact Contract의 **가치도 같이 커졌다**. 이제는 구조를
예쁘게 통일하는 작업이 아니라 다음 기능들의 기반이다:

- 이슈를 담당 Agent로 라우팅
- 변경된 Artifact에 의존하는 결과만 재실행
- Agent별 입력·출력과 근거 추적
- 동일 유형 Artifact의 버전 관리
- 동적 Agent 실행의 범용화

**결론: 통짜 교체는 반대, 점진 개혁은 찬성.**

---

## 1. Artifact Contract v1

초기 계약은 복잡하게 만들지 않는다. **content 내부 스키마는 Agent마다 제각각인 채로 두고,
공통 봉투만 통일한다.**

```python
class Artifact(TypedDict):
    artifact_id: str
    artifact_type: str
    owner_agent: str
    schema_version: int

    content: dict | str

    evidence_ids: list[str]
    depends_on: list[str]
    target_sections: list[str]

    status: str
    metadata: dict
```

### 원칙

- `evidence_ids` 에는 **직접 사용한 근거만** 기록한다.
- 상위 Agent의 근거를 단순 상속한 경우는 `depends_on` 으로 표현한다.
- 초기 버전에서는 **Artifact 수정 이력까지 한꺼번에 구현하지 않는다.**
- `artifact_id` 는 랜덤이 아니라 **실행 내에서 결정적인 값**을 쓴다(테스트 재현성 — 2-1
  `evidence_id` 가 URL 최초 등장 순서를 쓴 것과 같은 이유).
- Agent마다 제각각인 `content` 내부 스키마는 **그대로 유지**한다.

### 확정된 매핑 (코드 대조 결과)

`depends_on` 은 상상이 아니라 **각 Agent 노드가 실제로 읽는 State 키**에서 도출했다:

| legacy_key | artifact_id | artifact_type | owner_agent | depends_on (근거: 코드) | target_sections |
|---|---|---|---|---|---|
| `research_result` | `artifact-research` | `research_analysis` | `research` | — | `market_analysis` |
| `competitor_result` | `artifact-competitor` | `competitor_analysis` | `competitor` | research (`competitor.py:56`) | `differentiation` |
| `customer_result` | `artifact-customer` | `customer_analysis` | `customer` | research (`customer.py:43`) | `target_user` |
| `pestel_result` | `artifact-pestel` | `pestel_analysis` | `pestel` | research (`pestel.py:54`) | `pestel` |
| `swot_result` | `artifact-swot` | `swot_analysis` | `swot` | research + competitor (`swot.py:28-29`) | `swot` |
| `business_model_result` | `artifact-business-model` | `business_model_analysis` | `business_model` | research (`business_model.py:38`) | `revenue_model` |
| `risk_result` | `artifact-risk` | `risk_analysis` | `risk` | research + pestel (`risk.py:47-48`) | `risk` |

- **목록 순서 = 위상 순서**(의존 대상이 항상 먼저 나온다). `pestel` 을 `swot` 보다 앞에 둔 이유는
  `risk` 가 pestel에 의존하기 때문이다. 이 순서 자체가 결정적이므로 생성 결과도 결정적이다.
- `owner_agent` 값은 **LangGraph 노드 이름과 정확히 일치**한다(`workflow.py:154-164`).
  덕분에 `failed_nodes`·`fallback_nodes` 와 그대로 대조해 `status` 를 유도할 수 있다.
- `target_sections` 의 ID는 `sections.SECTION_SPECS`(14섹션 단일 진실원천)의 실제 ID다.
- 근거를 직접 확보하는 Agent는 **research·competitor 둘뿐**이다(나머지는 검색하지 않는다).
  `evidence_ids` 는 `evidence_registry` 항목의 `source_agents` 로 귀속시키며,
  **`research_gap` 이 확보한 근거는 research 것으로 귀속**한다(2-5의 추가 검색은 Research의 연장).

---

## 2. PR 단위 실행 전략

각 묶음은 **별도 PR**, main 기준 브랜치, 커밋→PR→머지 후 다음 묶음(**스택 금지** — 이 저장소는
같은 사고를 2번 겪었다).

### PR 1. Artifact 타입과 변환기만 추가 — 위험도 2/10 — ✅ 완료 (PR #96)

- `app/schemas/artifact.py` 추가(타입·`LEGACY_ARTIFACT_SPECS`·`build_artifacts_from_legacy`·selector)
- **기존 State·API·UI는 전혀 변경하지 않음** — 아무도 호출하지 않는 순수 모듈

**완료 기준**: 기존 State를 넣으면 7개 Artifact가 결정적으로 생성됨 / 기존 코드 동작 변화 없음 /
LLM·검색 호출 추가 없음 / 기존 테스트 결과 변화 없음.

### PR 2. Shadow Artifact 생성 — 위험도 3/10 — ✅ 완료

> **구현 시 판단 2건**
> ① **생성 지점을 `migrate` 가 아니라 `workflow._finalize_artifacts` 로 분리**했다. `migrate.upgrade_state`
> 는 "비어 있을 때만 채우는" 멱등 정규화라, 거기서만 만들면 재작성(`/revise`) 후 **옛 Artifact 가
> 그대로 남는다**. 그래서 신규 실행·`/revise` 는 `_finalize_artifacts` 가 **매번 재생성**하고,
> `migrate` 는 **옛 기록(v2) 재조회 때만** 채운다. `migrate` 쪽을 `setdefault` 로 둔 또 다른 이유는
> PR 4에서 **Agent 가 직접 쓴 Artifact 를 legacy 파생본으로 되돌리지 않기** 위해서다.
> ② **`_RUN_KEYS` 에 넣어 실제로 저장**한다. 지금은 평면 결과에서 파생되므로 재조회 시 재생성만
> 해도 되지만, PR 4의 Agent 작성 Artifact 는 파생으로 복원할 수 없다. 여기 빠지면 저장 때 사라진
> 뒤 읽을 때 조용히 legacy 파생본으로 덮인다 — 이 저장소가 `_RUN_KEYS` 누락으로 **이미 두 번 겪은
> 유실 유형**(외부 리뷰 P0-1·B-3)이라 지금 함께 넣었다.
>
> **실측(더미 1건, `USE_DUMMY=1`)**
> - 저장 JSON: 17,499 → 22,677 bytes = **+5,178 bytes(+29.6%)**
> - `build_artifacts_from_legacy` 1회: **0.042 ms**(n=200 평균) — 기준(50ms 또는 wall 1%) 대비 무시 가능.
>   더미 전체 실행 wall 1,549ms의 **0.003%**.
> - 크기 증가는 7개 결과를 그대로 한 벌 더 쓰기 때문이다. 더미 실행은 Agent 결과가 짧은
>   placeholder 라 실제 실행에서의 비율은 다를 수 있다(측정값은 더미 기준임을 명시).
>
> **호출 순서 제약**: `_finalize_evidence`(evidence_id 확정) → `_assess_quality`(failed/fallback 확정)
> → **`_finalize_artifacts`** → `migrate.upgrade_state`. 앞의 둘보다 먼저 부르면 근거 참조와 status 가
> 비거나 틀린다.
>
> **API 응답은 바뀌지 않았다** — `RunResult` 에 `artifacts` 를 넣지 않았으므로 `/run`·`/revise` 응답은
> 그대로다(소비자 전환은 PR 5). JSON 다운로드에는 `artifacts` 키가 **추가**되며 기존 키는 전부 그대로다.

워크플로 종료 시 기존 결과로부터 파생 생성:

```python
state["artifacts"] = build_artifacts_from_legacy(state)
```

이 단계에서도 **기존 소비자는 여전히 평면 키를 읽는다.** `artifacts` 는 저장·검증용으로만 존재.

병렬 Agent가 Artifact를 직접 쓰기 전까지는 **reducer가 필요 없다**(최종 단계에서 한 번 생성 →
충돌 없음).

**버전 관리**: 저장 State에 `artifacts` 가 포함되므로 `STATE_VERSION = 3`.

```python
def upgrade_v2_to_v3(state: dict) -> dict:
    state.setdefault("artifacts", build_artifacts_from_legacy(state))
    return state
```

기존 7개 키를 유지하므로 이전 프로젝트의 조회·수정·다운로드 영향은 사실상 없다.

**완료 기준**: v2 프로젝트를 열면 Artifact 자동 생성 / 신규 저장 후 재조회 시 유지 /
`/run`·`/revise`·JSON 다운로드 결과가 기존과 동일 / 평면 키와 Artifact content가 7개 모두 일치.

### PR 3. Artifact 정합성 검증 — 위험도 2/10 — ✅ 완료

> **구현**: `artifact.check_parity(state)` → `{expected, generated, matched, mismatched[], ok}`.
> `mismatched` 항목은 `{artifact_id, reason, detail}` 이며 reason 6종 —
> `content_mismatch`(가장 중요) · `missing_artifact` · `unknown_artifact` · `duplicate_id` ·
> `missing_dependency` · `unknown_evidence_id`.
> `workflow._finalize_artifacts` 가 생성 직후 호출해 `state["artifact_parity"]` 로 표면화하고,
> 어긋나면 `[artifact] 정합성 불일치 N건 (matched x/7): reasons` 로그를 남긴다.
> **실행은 실패시키지 않는다.** `migrate` 는 옛 기록(v2) 재조회 때 판정을 소급해 채운다 —
> '옛 기록이라 판정이 없음'과 '판정했는데 통과'를 구분하기 위해.
>
> **테스트 태도**: 통과 경로만 보면 항상 `ok=True` 를 뱉는 검사기도 통과한다. 그래서 reason
> 6종을 **각각 일부러 깨뜨려** 실제로 잡히는지 확인했다(18건).
>
> **실측(더미 1건)**
> - 더미 전체 흐름 `matched = 7/7`, `ok = True`
> - `build_artifacts_from_legacy` 0.042ms + `check_parity` **0.005ms** = **0.047ms**(n=200 평균)
>   → 완료 기준 "wall time 증가가 사실상 없음" 충족(더미 wall 약 1.5s 의 0.003%)
> - `artifact_parity` 자체 저장 크기 **75 bytes**. 저장 JSON 총 증가는 PR 2·3 합쳐 **+30.5%**
>   (대부분 PR 2 의 artifacts content 몫)
> - serial↔parallel 동일 집합은 PR 2 테스트(`test_artifact_shadow.py`)에서 이미 고정됨
> - v2→v3 마이그레이션 반복 실행 결과 불변(멱등) 확인

Shadow 결과가 기존 결과와 같은지 자동 검사한다.

```python
artifact_parity = {"expected": 7, "generated": 7, "matched": 7, "mismatched": []}
```

검사 항목: Artifact 수 · `legacy_key` ↔ content 일치 · `artifact_id` 중복 ·
`depends_on` 대상 존재 · `evidence_ids` 가 Evidence Registry에 실제 존재 ·
**serial·parallel에서 동일한 Artifact 집합 생성**.

> 정합성이 깨져도 **본 실행은 실패시키지 않는다.** 로그와 테스트에서 먼저 발견하는 편이 낫다.
> (2-5의 `dynamic_research` 가 '안 한 것 vs 해서 못 찾은 것'을 구분해 표면화한 것과 같은 태도.)

**완료 기준**: 더미 전체 흐름에서 `matched=7` / serial·parallel 집합 일치 /
v2→v3 반복 실행 결과 불변(멱등) / Artifact 추가에 따른 wall time 증가가 사실상 없음.

> **여기까지 완료하면 추가형 Artifact Contract의 기반은 완성이다.**

### PR 4. Agent별 Dual Write — PR당 위험도 4/10

정합성 확인 후 Agent가 기존 결과와 Artifact를 함께 반환한다.

```python
return {"research_result": result,
        "artifacts": [make_research_artifact(result, evidence_ids)]}
```

이때 `artifacts` 는 병렬 reducer 필드가 되므로 **단순 `operator.add` 는 안 된다**
(동일 Artifact 재실행 시 중복). 전용 reducer:

```python
def merge_artifacts(left: list, right: list) -> list:
    by_id = {a["artifact_id"]: a for a in [*left, *right]}
    return list(by_id.values())
```

**한 PR에서 7개를 다 바꾸지 않는다:**
1. Research · Competitor (Evidence Registry와 직접 연결 → 먼저) — ✅ 완료
2. Customer · PESTEL — ✅ 완료
3. SWOT · Risk · Business Model

> **1묶음(Research·Competitor) 구현 시 드러난 것 3건 — 나머지 묶음에도 그대로 적용된다**
>
> ① **Agent 는 `evidence_ids` 도 `status` 도 알 수 없다.** `evidence_id` 는 실행 종료 시
> `evidence.normalize()` 가 URL 최초 등장 순서로 부여하고, `failed`/`fallback` 은
> `_assess_quality` 가 로그를 보고 정한다. 그래서 `make_artifact` 는 둘을 비워 두고
> **`reconcile()` 이 finalize 시점 값으로 재확정**한다. 이게 없으면 *옮긴 Agent 만* 근거 연결이
> 비고 status 가 틀리는 회귀가 생긴다.
>
> ② **`research_gap`(2-5)이 `research_result` 를 갱신하므로 Artifact 도 재방출해야 한다.**
> `research` 가 쓴 보강 전 봉투가 남으면 정합성 검사가 `content_mismatch` 로 잡는다.
> reducer 가 나중 것을 채택하므로 보강본이 최종이 된다. **한 노드가 다른 노드의 결과 키를
> 갱신하는 경로가 있으면 그 노드도 Dual Write 대상이다.**
>
> ③ **`reconcile` 에서 살리는 것은 `source=agent` 뿐이다.** 처음엔 "기존 Artifact 가 이긴다"로
> 짰다가, 이전 실행에서 **파생된** 항목까지 살아남아 평면 결과가 바뀌어도 옛 파생본이 계속
> 이기는 버그를 테스트가 잡았다. 파생본은 평면 키의 사본일 뿐이므로 매번 다시 만든다.
>
> **발산 시 태도**: Dual Write 된 Agent 의 Artifact 와 평면 결과가 어긋나면 **파생본으로 덮어
> 감추지 않고 `content_mismatch` 로 보고**한다. 조용히 맞춰버리면 정합성 검사가 무의미해진다
> (검사기가 항상 통과를 뱉게 된다).
>
> **1묶음 검증**: 더미 serial·parallel 모두 Artifact 7개·중복 0·`parity 7/7 ok`,
> `source=agent` 는 research·competitor 2건·나머지 5건은 `legacy_derived`.
> 395 passed, coverage 96.13%. API 응답 무변경.
>
> **2묶음(Customer·PESTEL)**: 규칙 ②는 해당 없음 — 두 결과 키를 쓰는 곳은 각자 한 군데뿐이고
> 자체 검색도 없다(`evidence_agents=[]`). 규칙 ①·③만 적용된다.
> **여기서 처음으로 reducer 가 실제 동시 방출을 받는다** — 병렬 그래프에서 `competitor`·
> `customer`·`pestel` 은 `research_gap` 이후 **서로 다른 분기에서 동시에** 실행된다
> (`add_edge` 기준: research_gap → competitor·customer·pestel·business_model 4분기).
> 1묶음의 두 Agent 는 순차 구간(research)·단일 분기(competitor)라 진짜 동시성이 없었다.
> **검증**: serial·parallel 모두 7개·중복 0·`parity 7/7 ok`,
> `source=agent` 4건(research·competitor·customer·pestel) / `legacy_derived` 3건
> (swot·risk·business_model). 399 passed, coverage 96.17%.

### PR 5. Artifact Selector 도입 — 위험도 5/10

소비자가 State 키를 직접 읽는 대신 selector를 쓴다.

```python
def get_artifact_content(state: dict, artifact_type: str, legacy_key: str) -> dict:
    artifact = find_artifact(state, artifact_type)
    if artifact:
        return artifact.get("content") or {}
    return state.get(legacy_key) or {}
```

**전환 순서**: `draft_writer.py` → `verifier.py` → 섹션 단위 수정의 `_SECTION_EVIDENCE` →
`routes.py`·내보내기 → UI.

`draft_writer.py` 는 **7개 결과를 모두 소비**하므로 첫 전환의 영향이 가장 크다.
**반드시 feature flag 아래에서** 전환한다.

### PR 6. 제한적 동적 실행과 연결 — 위험도 6/10

**Artifact Contract의 실제 가치는 여기서 발생한다.**

```python
SECTION_ARTIFACT_MAP = {
    "market_analysis": "research_analysis",
    "target_user": "customer_analysis",
    "pestel": "pestel_analysis",
    "swot": "swot_analysis",
    "revenue_model": "business_model_analysis",
    "risk": "risk_analysis",
}
```

라우팅 흐름: Reviewer Issue → `target_section_id` → 관련 Artifact → `owner_agent` →
의존 Artifact 변경 여부 → **필요한 Agent만 재실행** → 영향받은 하위 Artifact만 무효화·재생성.

처음부터 모든 Agent를 동적으로 실행하지 않고 **1개 경로만 PoC**:

> `market_analysis` 근거 부족 → Research 재실행 → Research Artifact 교체 →
> Draft의 `market_analysis` 섹션만 재작성

`research_gap` 이 이미 있어 가장 자연스럽게 확장된다.

---

## 3. 기능 플래그 전략

```
ARTIFACT_CONTRACT_MODE=shadow
ARTIFACT_READ_MODE=legacy
```

| 단계 | Contract mode | Read mode |
|---|---|---|
| 초기 생성 검증 | `shadow` | `legacy` |
| Agent 이중 기록 | `dual` | `legacy` |
| 내부 소비자 전환 | `dual` | `prefer_artifact` |
| 최종 전환 | `primary` | `prefer_artifact` |
| 완전 제거 후 | `primary` | `artifact_only` |

읽기 모드: `legacy`(기존 키만) / `prefer_artifact`(Artifact 우선, 없으면 기존 키) /
`artifact_only`(Artifact만).

**`ARTIFACT_READ_MODE=legacy` 로 되돌리는 것이 사실상 rollback 장치다.**

---

## 4. 절대 한 PR에 함께 넣지 않을 것

- 기존 7개 State 키 삭제
- Tier 3 URL 원문 추출
- 완전한 Supervisor 추가
- Artifact 버전 이력
- 전체 UI 개편
- API 응답 스키마 제거·변경
- 기존 Evidence Registry 구조 변경
- 모든 Agent의 동적 재실행
- State 저장 구조를 JSON blob → 별도 테이블로 전환

> 특히 **Artifact Contract와 동적 Supervisor를 같은 PR에 넣지 않는다.**
> 먼저 데이터를 표준화하고, 그다음 표준 데이터를 이용해 실행을 동적으로 만든다.

---

## 5. 단계별 중단 조건

다음 중 하나라도 발생하면 해당 단계에서 **primary 전환을 중단**한다.

- 기존 프로젝트 재조회 또는 `/revise` 실패
- serial·parallel 결과 구조 불일치
- 더미 실행에서 기존 결과와 Artifact content 불일치
- 14개 섹션 완성률 하락
- fallback 또는 failed node 증가
- **추가 LLM·검색 호출 발생**
- API 응답 필드 누락
- 저장 후 Artifact ID나 의존 관계 변경
- Evidence Registry에 없는 `evidence_id` 가 Artifact에 포함

**성능 기준**: Shadow Artifact 생성에 따른 추가 처리 시간 **50ms 이하 또는 전체 wall time의 1% 이하**.
LLM을 호출하지 않는 단순 변환이므로 사실상 0에 가까워야 한다.

---

## 6. 기존 키는 언제 제거하는가

**이번 Phase 2에서는 제거하지 않는다.** 다음을 모두 만족한 뒤 **별도 Phase로 분리**한다.

- 모든 Agent가 Artifact를 직접 생성
- 모든 내부 소비자가 `prefer_artifact` 사용
- 기존 프로젝트 v2→v3 재조회·수정 테스트 통과
- UI와 내보내기가 Artifact 기반으로 동작
- 실제 API 실행과 벤치마크에서 회귀 없음
- 최소 1개 이상의 선택적 Agent 재실행 경로 동작

그 이후에도 legacy 키를 바로 삭제하지 말고 **Artifact로부터 파생해서 제공**한다:

```python
state["research_result"] = artifact_content(state, "research_analysis")
```

외부 계약을 유지한 채 내부만 Artifact 구조로 바꾸는 방법이다.

---

## 7. 리스크 평가

| 방식 | 위험도 | 얻는 가치 | 판단 |
|---|---|---|---|
| 기존 키를 한 번에 Artifact로 교체 | 8~9/10 | 빠른 완전 전환 | **비추천** |
| finalize 시 Artifact만 파생 생성 | 2~3/10 | 계약·추적 기반 확보 | **적극 추천** |
| Agent 이중 기록 + legacy 읽기 | 4/10 | 실제 생산 경로 검증 | 추천 |
| Artifact 우선 읽기 + legacy fallback | 5~6/10 | 내부 구조 실질 전환 | 검증 후 진행 |
| 기존 키 완전 제거 | 8/10 | 구조 정리 | 현재는 불필요 |

---

## 8. 최종 권고

우선 여기까지만 진행한다:

> **Artifact Contract v1 정의 → Shadow Artifact 생성 → v3 저장·마이그레이션 →
> 정합성 검증 → Agent별 Dual Write** (PR 1~4)

여기까지는 기존 기능을 깨뜨리지 않으면서도 실질적인 구조 개혁이다.

그다음 **Research 재실행 → 관련 섹션만 갱신** 경로 하나를 Artifact 기반으로 구현해,
Contract가 실제 동적 실행에 가치가 있다는 것을 **증명**한다. 증명된 뒤 나머지 Agent를 옮긴다.

**이번 결정 = "통짜 2-2를 다시 추진한다"가 아니라
"추가형 2-2를 시작하고, 성공 게이트를 통과할 때만 단계적으로 primary 구조로 전환한다".**
