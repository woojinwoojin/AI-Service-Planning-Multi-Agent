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
3. SWOT · Risk · Business Model — ✅ 완료 (**PR 4 전체 완료**)

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
>
> **3묶음(SWOT·Risk·Business Model) — PR 4 전체 완료**: 규칙 ②는 여기서도 해당 없음
> (세 결과 키를 쓰는 곳도 각자 한 군데뿐). **`depends_on` 이 2개인 첫 사례** —
> `artifact-swot`=[research, competitor], `artifact-risk`=[research, pestel].
> 정합성 검사가 `missing_dependency` 없이 통과하는지까지 확인했다.
> **검증**: serial·parallel 모두 7개·중복 0·`parity 7/7 ok`·**`source=agent` 7/7**
> (파생본 0). 404 passed, coverage 96.14%.
>
> **파생 경로의 위치가 바뀌었다**: 신규 실행에는 파생본이 더 이상 없다. `build_artifacts_from_legacy`
> 는 이제 **옛 기록(v2) 재조회 전용 폴백**이다. 다만 삭제하지 않는다 — v2 기록은 계속 열리고,
> `reconcile` 이 파생본을 매번 갱신하는 성질(규칙 ③)도 그 경로에서 여전히 필요하다.
> 그 회귀를 지키는 테스트를 옛 기록 맥락으로 옮겨 두었다.

### PR 5. Artifact Selector 도입 — 위험도 5/10 — ✅ 완료(백엔드 소비자)

> **PR 1~4 는 전부 추가형이라 읽는 쪽을 건드리지 않았다. 여기서 처음 읽기 경로가 바뀐다.**
>
> **전환한 소비자**: `draft_writer`(초안 7개 결과 · 섹션 수정 근거 `_relevant_analysis` ·
> 참고자료 폴백 `_real_sources`) · `verifier`(research·competitor 분석 문맥).
> **남은 것은 표시 계층**(`routes.py`·내보내기·UI) — 이들은 State 를 그대로 직렬화해 보여줄 뿐
> 문서 내용을 만들지 않으므로 뒤로 미뤘다.
>
> **모드**: `legacy`(기본·전환 전과 동일) / `prefer_artifact`(Artifact 우선, 없으면 폴백) /
> `artifact_only`(폴백 없음). 알 수 없는 값·오타는 **가장 안전한 `legacy`** 로 떨어진다 —
> 오타가 조용히 Artifact 경로를 켜면 안 된다.
> `_SECTION_EVIDENCE` 는 평면 키 대신 **Artifact 유형**을 들고, 평면 키는 명세에서 얻는다
> (두 곳에 적으면 한쪽만 고쳐 어긋난다).
>
> **핵심 검증 — 세 모드에서 산출물이 같은가**
> 더미 실행 6조합(serial·parallel × 3모드) 전부 `final_draft` + `verification_result` 의
> SHA-256 **접두 16자리가 동일**(`2cce7b78d329739f`), `artifact_parity ok` 전부 True.
>
> **⚠️ 범위 정정(2026-07-26)**: 초기 서술 "`artifact_only` 까지 통과했다 = Artifact 만으로
> 파이프라인이 돈다"는 **과장이었다.** 정확히는 —
> **`artifact_only` 모드에서 문서 생성(draft_writer)·검증(verifier)의 핵심 소비 경로가
> 평면 키 폴백 없이 정상 동작함을 확인**한 것이다.
> 아직 평면 키를 직접 읽는 곳이 남아 있다:
> - **Agent 간 읽기 7곳** — `competitor:57`·`customer:44`·`pestel:55`·`swot:29-30`·
>   `business_model:39`·`risk:48-49`·`research(_gap):226,283` (뒤 Agent 가 앞 Agent 결과를 읽는 경로)
>   → 이 7곳을 **PR 5c** 에서 세 묶음으로 옮긴다. `research(_gap)` 은 5c-1 에서 전환 완료.
> - **표시 계층** — `routes._result_payload:89-95`, `parallel_bench:69`
>
> 즉 `artifact_only` 가 통과한 데에는 **이 경로들이 selector 를 아예 타지 않는다**는 사정도
> 있다. 평면 키를 지우면 파이프라인은 멈춘다. **`prefer_artifact` 전환 전에 Agent 간 읽기를
> 먼저 옮겨야 한다** — 지금 상태로 수집한 폴백 지표는 실제 준비도를 과대평가한다.
>
> **→ 해소됨(PR 5c-1~5c-3)**: Agent 간 읽기 7곳 전부 전환 완료. 이제 `artifact_only` 통과는
> 분석 파이프라인 전체에 대한 진술이다. 단 **표시 계층은 여전히 평면 키를 읽으며, 이는 의도된
> 것**이다(외부 API 호환). 런타임 폴백 계측은 PR 5d 에서 붙였고, **실 LLM 기준 검증은 아직
> 남아 있다.**
>
> **⚠️ 이 동일성 검증의 한계(정직 표기)**: 더미 모드에서 초안은 `_dummy_draft(si, research,
> pestel)` 로 만들어지므로 **research·pestel 만 산출물에 실제로 반영**된다. 나머지 5개
> (competitor·customer·swot·business_model·risk)는 프롬프트에만 들어가고 더미 LLM 이 무시하므로,
> 6조합 해시 동일성만으로는 그 5개의 읽기 경로가 검증되지 않는다. 그래서 **프롬프트 문자열을
> 직접 확인하는 테스트**(`_generate`·`complete_json` 을 가로채 Artifact 값이 들어갔는지)를 함께
> 두었다. 실 LLM 기준 동일성은 미측정.
>
> **→ PR 5d 에서 관통 실행 수준으로 닫음**: 실행 전체의 LLM 프롬프트 스트림을 세 모드에서
> 대조한다(아래 5d). 실 LLM 기준 동일성은 여전히 미측정.
>
> **rollback**: `ARTIFACT_READ_MODE=legacy` 로 바꾼 뒤 **애플리케이션을 재시작하면** 코드 변경
> 없이 기존 읽기 경로로 복귀한다. '즉시'가 아니다 — `.env` 는 `load_dotenv()` 가 모듈 import
> 시 1회만 읽으므로 실행 중인 프로세스에는 반영되지 않는다(`llm.py:21` 등).
> 현재 서버가 어느 모드로 도는지는 **`/health` 의 `artifact_read_mode`** 로 확인한다.
> 기본값이 `legacy` 이므로 **이 PR 자체는 동작을 바꾸지 않는다**(전환은 별도 결정).
>
> **관측성(PR 5b)**: 잘못된 모드 값은 `legacy` 로 떨어지되 **조용히 넘어가지 않는다** —
> warning 로그 + `/health.artifact_read_mode_invalid` + 실행 로그·`state["artifact_read"]`
> 에 원본 값과 폴백 여부를 남긴다. 오타(`prefer_artifcat`)를 무시만 하면 운영자는 Artifact
> 모드로 믿는데 실제로는 평면 키를 계속 읽는다.
> `prefer_artifact` 폴백도 **사유별로 구분**한다(`missing` / `empty` / `failed`) — Artifact 가
> 있는데 비었거나 실패한 건 단순 미생성이 아니라 실제 오류일 수 있어서, 조용히 평면 키로
> 떨어지면 그 오류가 묻힌다. `artifact_only` 에서는 폴백 대신 `ArtifactUnavailable` 로
> **명시적 실패**한다(빈 dict 로 계속 돌리면 검증 모드의 의미가 없다).
> `state["artifact_read"]` = `{mode, raw, invalid, expected, usable, unusable[]}` —
> 전환 전에 '얼마나 폴백이 날지' 미리 보는 지표. 단 **finalize 시점 스냅샷이지 런타임
> 카운터가 아니다**(실제 폴백 횟수를 세지는 않는다).
> → **PR 5d 에서 `runtime` 하위 키로 실제 호출 카운터를 함께 싣는다.**

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

### PR 5c. Agent 간 읽기 전환 — 위험도 4~5/10 — 🔄 진행 중(3묶음)

PR 5 가 남긴 **Agent 간 읽기 7곳**(뒤 Agent 가 앞 Agent 결과를 읽어 자기 프롬프트를 만드는
경로)을 옮긴다. 이 경로는 표시용이 아니라 **문서 내용을 결정하는 데이터 경로**이므로, 여기까지
옮겨야 `artifact_only` 통과가 분석 파이프라인 전체에 대해 의미를 갖는다. 한 PR 에서 7개 파일을
동시에 바꾸지 않고 **의존 관계가 단순한 쪽부터** 세 묶음으로 나눈다.

| 묶음 | 대상 | 의존 | 상태 |
|---|---|---|---|
| 5c-1 | `research_gap` → research | 1개 (자기 갱신) | ✅ 완료 |
| 5c-2 | `competitor`·`customer`·`pestel`·`business_model` → research | 1개 | ✅ 완료 |
| 5c-3 | `swot` → research+competitor · `risk` → research+pestel | 2개 | ✅ 완료 |

**→ PR 5c 완료: Agent 간 읽기 7곳 전부 전환.** 문서 내용을 만드는 경로에는 평면 키 직접
읽기가 남지 않았고, 남은 것은 표시·집계 계층(`routes.py`·`parallel_bench.py`)뿐이다 —
이들은 외부 호환용 평면 필드를 그대로 제공해야 하므로 전환 대상이 아니다.

> **5c-1 (`feat/artifact-read-research-gap`)**: `research_gap` 이 보강 대상인 기존 조사 결과를
> selector 로 읽는다. 이 경로를 **가장 먼저** 옮기는 이유는 `research_gap` 이 Research 결과를
> *갱신*하기 때문이다 — 뒤 Agent 들이 읽어야 하는 것은 보강 **후**의 Artifact 이므로, 이 노드가
> 안정되지 않으면 5c-2/5c-3 전환이 보강 전 값을 읽을 위험이 있다.
>
> 읽기는 **검색·LLM 호출보다 먼저** 한다 — `artifact_only` 에서 Artifact 가 없으면 비용을 쓰기
> 전에 `ArtifactUnavailable` 로 실패해야 한다. 생략 경로(공백 보고 없음·더미·예산 초과)는
> Artifact 를 읽지 않아 불필요한 실패를 만들지 않는다. 조사 결과를 실행당 **한 번만** 읽어
> `_known_urls` 에 넘긴다(두 번 읽으면 `prefer_artifact` 폴백 경고도 두 번 난다).
>
> 소비자가 3곳으로 늘어 읽기 창구를 `artifact.read(state, artifact_type)` 로 공용화했다
> (draft_writer·verifier 의 사설 `_content` 를 대체). 평면 키 이름이 호출부에 나타나지 않아
> 명세와 어긋날 여지가 없다.
>
> **검증**: 평면·Artifact 에 서로 다른 값을 넣고 세 모드에서 보강이 **모드에 맞는 쪽 위에**
> 쌓이는지 / 중복 URL 판정도 selector 를 타는지 / 평면 키가 **아예 없어도** `artifact_only` 로
> 완주하며 기존·신규 근거가 모두 남는지 / 보강본이 평면 키와 Artifact 에 **같은 내용**으로
> 나가는지(Dual Write 유지) / Artifact 없는 `artifact_only` 에서 검색·LLM 호출 0회로 실패하는지.
> 테스트 7건 추가(443 passed), ruff clean. 기본 모드가 `legacy` 라 **이 PR 도 동작을 바꾸지 않는다.**
>
> **아직 남은 것(정직 표기)**: 5c-2/5c-3 의 6개 Agent 는 여전히 평면 키를 직접 읽는다.
> `test_unconverted_readers_are_known` 이 그 목록을 고정하고 있어, 전환이 진행되면 목록이 줄고
> 새 직접 읽기가 생기면 실패한다.

> **5c-2 (`feat/artifact-read-analysis-agents`)**: Research 하나만 의존하는 4개 Agent
> (`competitor`·`customer`·`pestel`·`business_model`)를 `artifact.read` 로 옮겼다. 각 1줄.
>
> **읽는 값은 보강 *후* 조사 결과다** — `research_gap` 이 직렬·병렬 두 그래프 모두에서 fan-out
> **앞**에 있다(`workflow.py:199,227-233`). 5c-1 을 먼저 한 이유가 여기서 실현된다.
>
> **검증 — 최종 문서 해시로는 이 4개 경로가 검증되지 않는다.** 더미 초안은
> `_dummy_draft(si, research, pestel)` 로 만들어져 competitor·customer·business_model 의 결과는
> 산출물에 반영되지 않는다(PR 5 의 한계 표기와 같은 이유). 그래서 **각 Agent 의 LLM 프롬프트를
> 직접 가로채** 확인한다:
> - 평면·Artifact 에 다른 값 → 세 모드에서 **모드에 맞는 쪽**이 프롬프트에 들어간다(4×3=12건)
> - 평면 키가 **아예 없어도** Artifact 만으로 동작한다(4건)
> - `artifact_only` 에서 의존 Artifact 가 없으면 **LLM 호출 전에** `ArtifactUnavailable`(4건)
> - `status=failed` Artifact 를 정상값처럼 소비하지 않는다(4건)
>
> 여기에 더미 관통 실행을 **serial·parallel × 3모드 6조합**으로 확장해
> `failed_nodes` 가 비는지 본다 — 병렬에서 Artifact 가 fan-out 경계를 넘어 보이지 않으면
> `artifact_only` 에서 4개 노드가 실패하므로, 이 조합이 fan-out 가시성의 실증이다.
>
> 테스트 27건 추가(470 passed), ruff clean. 기본 모드 `legacy` 이므로 동작 변화 없음.
>
> **남은 것**: `swot`·`risk` 2개(복수 의존) → 5c-3. 이 둘이 끝나야 `depends_on` 선언이 실제
> 런타임 입력 관계와 일치하는지 처음으로 직접 검증된다.

> **5c-3 (`feat/artifact-read-multi-dep`)**: 의존이 **2개**인 `swot`(research+competitor)·
> `risk`(research+pestel)를 전환. Agent 간 읽기 7곳이 이로써 전부 selector 를 탄다.
>
> **이 묶음의 고유한 가치 — `depends_on` 이 선언에서 검증된 사실로 바뀐다.**
> 지금까지 `depends_on` 은 코드를 읽고 사람이 적은 값이었다(`artifact.py` 설계 메모).
> 이제 모든 Agent 간 읽기가 `artifact.read` 를 지나므로 **호출을 기록해 선언과 대조**할 수
> 있다 — `test_declared_depends_on_matches_actual_runtime_reads` 가 6개 Agent 각각에 대해
> `{읽은 유형} == set(depends_on)` 을 확인한다. 어긋나면 **PR 6(선택적 Agent 재실행)이 잘못된
> Agent 를 재실행**하므로, PR 6 의 전제조건을 여기서 확보한 셈이다.
>
> **검증(11건 추가)**: 두 의존이 **둘 다** Artifact 쪽에서 오는가(2모드×2Agent) / legacy 에서는
> 둘 다 평면 키인가 / 평면 키 없이 Artifact 둘만으로 동작하는가 / **의존 하나만 빠져도**
> LLM 호출 전에 `ArtifactUnavailable` 인가(조용히 진행하면 '경쟁사 분석을 안 보고 만든 SWOT'이
> 정상 산출물처럼 저장된다) / depends_on ↔ 런타임 읽기 일치.
>
> 481 passed(470 → +11), ruff clean. 기본 모드 `legacy` 이므로 동작 변화 없음.

### PR 5d. 런타임 폴백 계측 + 비교 항목 확대 — 위험도 2/10 — ✅ 완료

전환 판단에 쓸 **숫자**를 만든다. 코드 경로는 그대로고 관측만 붙으므로 위험이 낮다.

> **왜 필요한가 — 스냅샷과 실제 호출은 다른 질문이다.**
> PR 5b 의 `read_status` 는 finalize 시점 가용성만 답한다("7개 다 쓸 수 있었다"). 정작
> 전환에 필요한 건 **실제로 몇 번 읽었고 몇 번 떨어졌는가**인데, 스냅샷에서는 *아무도 안 읽는
> Artifact* 와 *10번 읽히는 Artifact* 가 똑같이 `usable 1` 로 보인다. 어느 쪽이 깨지느냐에
> 따라 영향이 전혀 다른데도 구분이 안 됐다.
>
> **① 런타임 계측** — `usage.py` 와 같은 contextvar 방식으로 `get_artifact_content` 호출을
> 실행 단위로 센다(`artifact.reads_start()` → `reads_summary()`). 결과는
> `state["artifact_read"]["runtime"]` 에 실린다. 진입점은 `/run`(`workflow.py`)과
> `/revise`(`routes.py`) 둘 다 — **요청마다 초기화**하지 않으면 수정 실행의 폴백률에 원 실행
> 값이 섞인다.
>
> **② shadow 측정 — legacy 로 돌면서 '전환하면 어땠을지'를 잰다.**
> `legacy` 모드는 Artifact 를 보지도 않으므로 폴백이 원리적으로 0 이다. 그 0 을 준비도로 읽으면
> 안 된다. 그래서 legacy 읽기마다 Artifact 쪽을 **관측만** 해보고(`_shadow_reason`, 반환값은
> 건드리지 않는다) '전환했다면 떨어졌을' 횟수를 `shadow_fallbacks` 로 남긴다. 이게 없으면
> 준비도를 알려고 **운영 트래픽을 실제로 `prefer_artifact` 로 넘겨 봐야** 한다.
> `test_shadow_measurement_predicts_actual_fallbacks_after_switching` 이 shadow 값이 곧
> 전환 후 실제 폴백값임을 고정한다.
>
> **`measured=False` 는 '폴백 0'이 아니라 '측정 안 함'이다.** `reads_start()` 없이 호출된
> 경로에서 0 을 성공으로 읽으면 근거 없는 안심을 하게 되므로 플래그로 구분한다.
>
> **실측(더미 6조합, serial·parallel × 3모드)**: 실행당 읽기 **20회**, `by_type` 은
> research 10 · competitor 4 · pestel 2 · 나머지 각 1. `prefer_artifact`·`artifact_only`
> 에서 **20/20 이 Artifact 경로, 폴백 0**. `legacy` 의 `shadow_fallbacks` 도 **0** →
> *지금 전환해도 평면 키로 떨어지는 읽기가 없다*(더미 기준). 직렬·병렬 건수가 같아
> **contextvar 가 fan-out 스레드 경계를 넘는다**는 것도 함께 실증됐다(넘지 못하면 분석 4분기
> 읽기가 통째로 누락돼 '폴백 0'이 실제보다 좋아 보인다).
>
> **③ 비교 항목 확대 — 그리고 그 확대가 더미 모드에서 대체로 공허하다는 사실.**
> 기존 동일성 검사는 `final_draft`·`verification_result` 만 봤다. **7개 분석 결과·근거 계열·
> 실행 결말·Artifact content** 까지 `_MODE_INVARIANT_KEYS` 로 넓혔는데, 넓히고 나서 실제로
> 재보니 **더미 모드에서는 대부분 공허**했다:
>
> | 돌연변이(읽기를 고의로 비움) | 프롬프트 스트림 | 7개 산출물 | 최종 문서 해시 |
> |---|---|---|---|
> | 모든 읽기를 `{}` 로 | 잡음 | 잡음(`competitor_result` 만) | 잡음 |
> | `competitor_analysis` 만 `{}` (= swot 의 입력) | **잡음** | 못 잡음 | 못 잡음 |
>
> 앞 Agent 결과를 `_dummy()` 가 실제로 쓰는 건 `competitor` 뿐이고(나머지 5개는 입력을 무시한
> 고정값을 낸다), 더미 초안은 research·pestel 만 반영한다. 즉 **'swot 이 빈 경쟁사 분석을 읽고
> 만든 SWOT'이 산출물 비교로는 정상으로 보인다.**
>
> 그래서 **관통 실행의 LLM 프롬프트 스트림을 세 모드에서 대조**한다
> (`test_every_agent_receives_identical_input_in_every_mode`, serial·parallel). 프롬프트에는
> 읽어 온 값이 그대로 직렬화돼 들어가므로 다른 값을 읽으면 반드시 다르다. 5c-2/5c-3 이 Agent
> 단위로 하던 가로채기를 손으로 만든 State 가 아니라 **실제 실행**에 대해 하는 것이다.
> 확대한 키 비교는 그대로 두되(실 LLM 에서는 유효하고, 지금도 competitor 는 잡는다),
> **더미 기준 실질 검증력은 프롬프트 대조에 있다**는 점을 위 표로 남긴다.
>
> **LLM 호출 수도 세 모드에서 같아야 한다** — 이 작업은 읽는 *방식*만 바꾸므로 호출이 늘면
> 그 자체로 실패다(selector 를 잘못 끼워 앞 Agent 를 다시 부르는 회귀를 잡는다).
>
> 테스트 **497건(481 → +16)** 중 496 통과, ruff clean. 기본 모드 `legacy` 이므로 **동작 변화
> 없음**(계측은 반환값을 바꾸지 않는다). ⚠️ 남는 1건은 실행 환경에 따라 달라지며 **이 변경과
> 무관**하다 — 로컬 `.env` 의 `WORKFLOW_MODE=parallel` 로 인한
> `test_run_persists_and_reports_quality`, 또는 전체 부하에서 시간이 흔들리는
> `test_parallel_faster_than_serial`. 둘 다 해당 파일만 돌리면 통과한다.
>
> **아직 남은 것(정직 표기)**: 위 수치는 전부 **더미 기준**이다. 실 LLM 에서는 Agent 가 빈
> 응답·fallback 을 내 `status=fallback`/`empty` 인 Artifact 가 생길 수 있고, 그때 처음으로
> 폴백이 0 이 아니게 된다. 옛 v2 프로젝트(Artifact 가 아예 없는 기록) 경로도 미검증.
>
> **다음**: 옛 v2 프로젝트·`/revise` 검증(PR 5e) → 실 LLM 1~2주제 소규모 검증(여기서 나온
> `shadow_fallbacks` 가 전환 판단의 근거) → Staging 에서 `prefer_artifact` 적용.

### PR 5e. 옛 기록 경로 검증 — 위험도 2/10 — ✅ 완료

PR 5a~5d 의 검증은 전부 **새로 실행한** State 기준이었다. 그런데 운영에서 `prefer_artifact`
를 켜면 그날부터 열리는 기록의 대부분은 **그 전에 저장된 것**이다. 그 기록에는 `artifacts` 가
아예 없고 `migrate.upgrade_state` 가 평면 결과에서 파생해 채운다. 이 경로가 세 모드에서 어떻게
되는지는 확인된 적이 없었다. 체인 전체를 본다:
**옛 v2 기록 → migrate → 세 모드 → `/revise` → 재검증 → 저장 → 재조회.**

> **결과 ① 옛 기록은 세 모드 모두에서 정상 수정된다.** migrate 가 7개를 파생(`status=complete`,
> `source=legacy_derived`)하고, `artifact_only` 에서도 **읽기 전부가 Artifact 경로**(폴백 0)로
> 완주한다. 저장·재조회 후 `state_version=3`·`artifact_parity ok` 유지.
>
> **결과 ② 그런데 `/revise` 는 7개 중 2개만 읽는다** — `research_analysis`×2 ·
> `competitor_analysis`×1(총 3회). 그래서 *결과 키가 빠진 옛 기록*(`pestel_result={}`)이나
> *`failed_nodes` 가 기록된 옛 기록*도 세 모드에서 통과하는데, 이는 **결손 Artifact 를 안전하게
> 소비해서가 아니라 애초에 읽지 않아서**다. 이 구분을 흐리면 "결손 기록도 괜찮다"는 잘못된
> 안심이 된다. `test_revise_reads_only_research_and_competitor` 가 읽기 범위를 고정해, PR 6 에서
> 범위가 넓어지면 관련 테스트가 함께 깨지도록 했다.
>
> **결과 ③ 세 모드가 갈리는 유일한 지점 = `project_id` 없는 `/revise`.**
> 저장된 base 가 없으니 State 에 Artifact 도 평면 결과도 없다.
>
> | 모드 | 결과 |
> |---|---|
> | `legacy` | 완주(degraded). shadow_fallbacks **3**(missing 3) |
> | `prefer_artifact` | 완주(degraded), **legacy 와 동일한 문서**. fallbacks **3**(missing 3) |
> | `artifact_only` | `revise`·`verify` 실패, `run_status=failed`, **최종본이 빈 문자열** |
>
> `artifact_only` 의 실패는 의도된 것이지만(검증 전용 모드), **HTTP 는 200 인 채 내용만 비므로
> 더 눈에 안 띈다.** 이것이 `artifact_only` 를 운영에 켜면 안 되는 구체적 근거이고, 전환
> 대상인 `prefer_artifact` 는 이 경로에서도 안전하다는 근거이기도 하다.
>
> **결과 ④ shadow 지표가 실경로에서 검증됐다.** 5d 는 이 성질을 손으로 만든 State 로만
> 고정했고, 관통 실행의 폴백은 늘 0 이라 **0 이 아닌 값을 옳게 세는지 볼 기회가 없었다.**
> 여기서 처음으로 폴백이 실제 발생하는 경로가 나와 `legacy` 의 shadow 3 == `prefer_artifact`
> 의 실제 폴백 3(사유까지 `{missing: 3}` 일치)임이 확인됐다.
>
> **함께 고친 것 — 저장 계층이 없는 키를 `None` 으로 굳히던 문제.**
> `save_run`/`update_run` 이 `{k: state.get(k) for k in _RUN_KEYS}` 로 payload 를 만들어,
> State 에 **없던** 키가 `None` 값으로 저장됐다. 그러면 재조회 쪽 `state.get("user_input", {})`
> 가 기본값이 아니라 `None` 을 받아 `{**None}` → **500** 이 된다(`/revise`·`_result_payload`).
> 없는 키는 저장하지 않도록 바꿨다(`_payload`) — 명시적 `None`(`revision_fallback_reason` 등)은
> 키가 있으므로 그대로 저장되어 **정상 실행 기록의 저장 내용은 바뀌지 않는다.**
> ⚠️ **정직 표기**: 이 500 은 **정상 `/run` 경로에서는 재현되지 않았다.** 장애를 주입해
> (`DEMO_FAIL_NODES`) 7개 노드를 죽여 봐도 `_safe`·fallback 이 흡수해 키가 항상 채워졌다.
> 일부 키만 있는 기록(외부 도구·수기 이관·다른 버전)이 들어올 때만 발생하는 **잠재 결함**이다.
>
> 테스트 16건 추가(497 → **513 passed, 실패 0**), ruff clean. 읽기 동작은 바꾸지 않았다.
>
> **아직 남은 것**: 실 LLM 미측정은 그대로다. 옛 기록 검증도 **더미 기준**이다.
>
> **다음**: 실 LLM 검증(PR 5f) → Staging 에서 `prefer_artifact` 적용.

### PR 5f. 실 LLM 검증 — 위험도 1/10(측정만) — ✅ 완료

PR 5a~5e 는 **전부 더미 기준**이었다. 더미는 결과가 짧은 placeholder 이고 LLM 이 늘 성공해서,
①실제 내용으로도 Dual Write 가 정확한지 ②실전에서 폴백이 정말 0 인지를 알 수 없었다.
**6주제 × 3모드 = 18회**(gpt-4o-mini, parallel, `a6b0ba4`, **$0.2326**) 실측.

> **측정 설계 — 실 LLM 에서는 산출물 동일성을 쓸 수 없다.** LLM 이 확률적이라 같은 모드로 두 번
> 돌려도 문서가 다르다. 그래서 두 층으로 나눴다:
> - **결정적 층(비용 0)**: 실제 실행이 남긴 State 를 고정하고 세 모드에서 소비자 8곳
>   (Agent 간 읽기 6 + draft + verify)의 **프롬프트를 대조**. State 가 고정이면 프롬프트 생성은
>   결정적이므로 정확히 같아야 한다. LLM 을 부르지 않아 추가 비용이 없다.
> - **비열등성 층(유료)**: 모드별 실행의 구조 품질·점수·폴백·비용. 동일성이 아니라 **하락 없음**.
>
> **결과 — 18/18 전부 통과.**
>
> | | legacy | prefer_artifact | artifact_only |
> |---|---|---|---|
> | run_status | success ×6 | success ×6 | success ×6 |
> | failed / fallback 노드 | 0 / 0 | 0 / 0 | 0 / 0 |
> | `artifact_parity` | **전부 ok** | 전부 ok | 전부 ok |
> | Artifact status | complete 42/42 | complete 42/42 | complete 42/42 |
> | 읽기(그중 Artifact) | 18~20 (**0**) | 18~20 (**전부**) | 18~20 (**전부**) |
> | 실제 폴백 / shadow | 0 / **0** | 0 / – | 0 / – |
> | 14섹션 완전 · 빈 섹션 | True · 0 | True · 0 | True · 0 |
> | 총점 | 60~75 | 65~71 | 55~73 |
> | 비용 | $0.073 | $0.0778 | $0.0818 |
>
> **프롬프트 동일성: 6주제 × 소비자 8곳, 불일치 0.** 실제 내용(길고 중첩된 한국어 dict)으로도
> 읽기 경로가 갈리지 않는다.
>
> **`shadow_fallbacks` = 0 (실 LLM 기준).** 전환 판단의 근거로 삼으려던 값이다 → **`prefer_artifact`
> 전환 조건 충족.**
>
> **⚠️ 그런데 shadow 0 의 의미를 좁게 읽어야 한다.** 18회 모두 `run_status=success`·fallback
> 노드 0 이라, **애초에 폴백이 날 상황이 발생하지 않았다.** 즉 "폴백 처리가 실전에서 검증됐다"가
> 아니라 **"폴백 조건이 한 번도 오지 않았다"**가 정확한 진술이다. 폴백 경로 자체의 동작은 여전히
> 더미·단위 테스트로만 확인돼 있다.
>
> **⚠️ 조사한 이상 신호 — `사실 검증률`의 모드별 차이는 읽기 경로 때문이 아니다.**
> 스윕에서 legacy [0.2~1.0] vs prefer [0.3~0.6] 로 **6주제 중 5개에서 legacy 가 높아** 방향이
> 한쪽으로 쏠려 보였다. 그냥 넘기지 않고 **같은 문서·같은 근거를 고정하고 verify 만 3모드 × 3회**
> 다시 돌렸다(≈$0.03):
> - 결과가 **스윕과 반대로** 나왔다(prefer 0.80 > legacy 0.65 > only 0.37) → 재현되지 않는다.
> - **모드 내 반복 폭(0.35)이 모드 간 차이(0.43)와 같은 크기**다.
> - 무엇보다 **기전이 닫혀 있다**: 프롬프트 동일성 검사가 같은 State 에서 세 모드의 verify
>   프롬프트가 동일함을 보였으므로, 읽기 모드가 이 지표에 영향을 줄 통로 자체가 없다.
> → 결론: **`사실 검증률`은 이 표본 크기에서 모드 비교에 쓸 수 없는 지표**다. 리포트에 경고를 박았다.
>
> **부수 발견(Artifact 와 무관, 별도 과제)**: 위 통제 실험에서 **같은 문서를 9회 재판정했더니
> 사실 검증률이 0.2~0.9 로 벌어졌다.** verify 가 주장을 **매번 새로 추출**해 판정하기 때문이다.
> 그런데 `quality_gate` 는 이 값 **≥0.8** 을 출력 가능 판정의 한 조건으로 쓴다 — 같은 문서가
> 9회 중 3회만 통과한다. **게이트 판정이 상당 부분 운에 좌우된다**는 뜻이므로 별도 과제로 남긴다
> (판정 N회 다수결·주장 추출 고정·임계값 재검토 중 택일).
>
> 하네스 자기검증 테스트 10건 추가(513 → **523**). 하네스는 **판정을 내리는 도구**라 통과 경로만
> 보면 안 된다 — 일부러 어긋뜨렸을 때 잡는지(`test_prompt_parity_catches_divergent_read`),
> 프롬프트가 0 건인 소비자를 ok 로 세지 않는지, LLM·검색을 정말 안 부르는지를 함께 고정했다.
>
> **다음**: Staging 에서 `prefer_artifact` 적용(코드 기본값은 `legacy` 유지) → 폴백 지표 관찰 → 동결.

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
