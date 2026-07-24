# 병렬화 측정 결과 & PR-7 계획 (세션 핸드오프)

> 기준 커밋: `main @ 0319f61` (PR #41~#46 병합 완료)
> 측정: 실제 LLM(`gpt-4o-mini`) + Tavily · 3주제 × 직렬/병렬 = 6회 · `run_parallel_bench.py --topics 3 --reps 1 --fresh`
> 상태: PR-1~PR-6 머지 완료. **PR-7 (섹션 단위 수정) 구현 완료** (`feat/pr7-section-revise`, 로드맵 2-3/2-4).

> **✅ PR-7 구현 완료 (2026-07-24)** — 아래 2.1~2.7 전부 구현. 신규 `app/services/sections.py`(14섹션
> stable ID·파서·조립기, 미수정 섹션 byte 동일), `reviewer` 구조화 issues, `draft_writer.section_revise`
> +full-revise fallback, `workflow._route_revision` 3분기, state `revision_strategy`·`revised_section_ids`·
> `revision_fallback_reason`, `parallel_bench` 재작성 계측. 테스트 29건(계획 3절 10항목 커버, E2E 직렬·병렬).
> 전체 190 passed·ruff 통과.
>
> **✅ 성능 실측 완료 (2026-07-24, 3주제×2, gpt-4o-mini)** — `docs/parallel_bench_result.md`:
> - **재작성 단계(revise_or_finalize): baseline-v1 전체재작성 24.4s(병렬) → 섹션 단위 8.5s(병렬) ≈ −65%.** 목표(−40%+)를 상회.
> - 재작성 전략 **section 12/12(100%)**, 평균 수정 섹션 2.7~2.8/14 (전체 재작성 대신 문제 섹션만).
> - 품질 비열등성: 14섹션 100%·순서 100%·PESTEL 100%·fallback 0·전부 success. wall 병렬 −17.2%(직렬 대비).
> - ⚠️ **주의(트랙 C)**: baseline-v1(6주제)과 주제 세트·프롬프트(Tier2 verify·reviewer issues)가 달라 **완전한 A/B는 아님** — revise 단계 절감은 강하게 지지되나 절대 wall 비교는 참고치. polish(≈21s)가 이제 최대 단계 → PR-8(조건부 Polish) 후보.

---

## 1. 단계별 latency 측정 결과 (3주제 중앙값)

| 단계 | 직렬(s) | 병렬(s) | 병렬 wall 비중 |
|---|---|---|---|
| research | 9.6 | 8.1 | 7.7% |
| **analysis_block** | **31.8** | **16.4** | 15.5% |
| draft | 15.7 | 18.6 | 17.5% |
| initial_review | 4.0 | 4.5 | 4.2% |
| **revise_or_finalize** | 23.7 | **26.9** | **25.3%** |
| polish | 16.6 | 18.6 | 17.5% |
| final_review | 4.3 | 4.3 | 4.1% |
| verify | 5.0 | 6.7 | 6.3% |
| **전체 wall** | **122.7** | **106.2** | 100% |

- **병렬화 효과 확인**: `analysis_block` 31.8s → **16.4s (약 −48%)**. 병렬화 대상 구간에서 기대대로 절반 가까이 단축.
- **전체 wall −13.5%** (스모크 n=3, 변동 있음).
- **품질·비용 동등(구조적 비열등성)**: 14섹션 완성률 1.0/1.0, 고유 출처 9/9, run_status 전부 success, 토큰 +1.3%, 총비용 $0.0746.

### 측정 신뢰성 (리뷰 caveat 2건 확인 완료)
1. **revise_or_finalize는 전부 revise 경로** — 6/6 실행 모두 `revise` 노드 실행(코드 `critical_path`로 확인). finalize(≈0s) 경로가 섞이지 않았으므로 26.9s는 순수 revise 시간. *(주제별 revise stage: 병렬 26.9 / 19.8 / 30.6s)*
2. **coverage는 per-run 계산** — `aggregate.timing_coverage_median`은 각 실행의 `stage_sum/wall`을 구한 뒤 median (sum-of-medians 아님). per-run 값 0.969~0.999 → 단계 측정이 wall을 사실상 100% 설명. *(1절 '비중' 열만 표시 편의상 stage중앙값/ wall중앙값으로 근사 — 정식 지표는 per-run coverage)*

### 결론
병렬화는 분석 구간을 정확히 겨냥해 성공적으로 단축했고, **병목은 이제 "문서 전체를 반복 생성하는 구간"으로 이동**:
`draft(17.5%) + revise(25.3%) + polish(17.5%) = 60%+` — 14섹션 전체를 LLM으로 다시 쓰는 3번의 풀-패스. 그중 **revise가 최대(25.3%)**.

---

## 2. PR-7 — 구조화된 리뷰 이슈 기반 섹션 단위 수정

> 정의: "토큰 줄이는 PR"이 아니라 **Reviewer 결과가 실제 수정 범위를 제어**하도록 만드는 PR.
> 장기 방향(Issue Router · 섹션 소유권 · 선택적 Agent 재호출)의 기반.
> **원칙: PR-7에서는 revise만 바꾼다. Polish 최적화는 PR-8로 분리**(효과 분리 측정).

### 2.1 Reviewer 출력에 수정 대상 섹션 추가
```json
{
  "score": 82,
  "decision": "revise",
  "issues": [
    {
      "issue_type": "insufficient_evidence",
      "severity": "major",
      "target_section_id": "market_analysis",
      "description": "시장 규모 근거 부족",
      "revision_instruction": "검색 근거로 시장 현황 구체화"
    }
  ]
}
```
- `target_section_id`는 자유 문자열 대신 **14섹션 내부 ID**(예: `revenue_model`). 표시 이름은 별도 매핑.
- 섹션 ID ↔ 표시 제목 매핑 상수 필요 (`draft_writer.SECTIONS`와 연결).

### 2.2 Markdown → 섹션 객체 파서
- heading 기반 parser (단순 `replace()` 금지). 검사 조건:
  - 14섹션 모두 존재 / 중복 heading 없음 / 순서 유지
  - **참고자료·한계 문구는 별도 보존**(재작성 대상 제외)
  - 수정 안 한 섹션은 원문 그대로(byte 동일)

### 2.3 section_reviser (대상 섹션만 생성)
- 프롬프트 입력 제한: 프로젝트 기본정보 + 대상 섹션 원문 + 해당 섹션 이슈 + 필요한 Agent 분석 + 관련 evidence + 앞뒤 섹션 짧은 요약. (전체 기획서 재전달 금지 → 입력 토큰 절감)
- 출력도 전체 md 아님:
  ```json
  {"section_id": "revenue_model", "heading": "11. 수익 모델", "content": "..."}
  ```

### 2.4 수정 섹션 수 제한 + 라우팅
- `MAX_REVISED_SECTIONS = 4`
- critical/major만 자동 수정, minor는 Polish로 전달, 같은 섹션 여러 이슈는 1작업 병합, **5개 이상이면 full revise fallback**.

### 2.5 안전한 full revise fallback (필수)
다음이면 기존 전체 revise로 복귀:
- Reviewer가 대상 섹션 식별 실패 / parser 실패 / 수정결과 schema 오류 / 대상 과다 / 전략 방향 변경 필요 / 병합 후 구조 검사 실패

```
Reviewer → 수정 필요?
  ├─ 아니오 → Finalize
  └─ 예 → 섹션 수정 가능?
           ├─ 예 → Section Revise
           └─ 아니오 → Full Revise
```
State 기록:
```json
{"revision_strategy": "none|section|full",
 "revised_section_ids": ["market_analysis","revenue_model"],
 "revision_fallback_reason": null}
```

### 2.6 수정 후 흐름 (유지)
문서 조립 → 구조 Validator → Polish → Final Reviewer → Verifier.
- **PR-7에서 Polish는 건드리지 않음**(revise 효과 분리).
- 한계 문구는 지금처럼 출력 경계에서 첨부, 참고자료는 재작성 제외.

### 2.7 벤치에 추가할 계측 (리뷰 제안)
`run_once` 결과에 다음 기록 → 집계 분리:
```json
{"review_decision":"revise|finalize", "revision_executed":true, "revision_scope":"full|section"}
```
집계: revision 실행률 / revise 실행 시 중앙시간 / finalize 경로 중앙시간.

---

## 3. PR-7 테스트 (최소)
1. Reviewer가 2섹션 지정 → 그 2섹션만 변경
2. 지정 안 된 12섹션 **byte 동일**
3. 같은 섹션 다중 이슈 → 1회만 수정
4. 참고자료·신뢰성 문구 유지
5. section revise 실패 → full revise fallback
6. 대상 한도 초과 → full revise
7. 수정 후 14섹션 순서·개수 유지
8. 수정 후 Final Reviewer·Verifier가 새 문서 평가
9. `revision_strategy`·수정 섹션 ID 저장·조회
10. 직렬·병렬 그래프에서 동일 동작

## 4. PR-7 성공 지표 (A: full revise vs B: section revise, 동일 벤치)
- 성능: **revise 단계 중앙시간 −40%+**, 전체 wall −10%+, 입출력 토큰 감소
- 품질: 14섹션 완성률 유지, Reviewer 점수 하락 없음, 원지적 이슈 해결률 80%+, 새 major 이슈 없음, 근거·참고자료 보존
- 현실 인식: LLM 기본 지연·문맥 입력 때문에 26.9s가 0이 되진 않음. 2~3섹션만 수정 시 revise를 **대략 절반 이하**로 줄일 가능성 → 동일 벤치로 실측 확인.

---

## 5. 이후 로드맵
- **PR-8 — 조건부 Polish ✅ 구현(`feat/pr8-conditional-polish`)** (저위험, PR-7의 구조화 issues 재사용):
  문체·중복·가독성 이슈(issue_type/설명에 style 힌트) 있으면 Polish 실행 / 전체 재작성(full)이면 실행 /
  내용 이슈만 + 구조 정상 + 섹션단위·재작성없음이면 **Polish 생략**(문서 전체 재편집 LLM 호출 절감).
  안전 편향(애매하면 실행)·구조 이상 시 실행. `draft_writer._polish_skip_reason`, state `polish_applied`·
  `polish_skip_reason`, bench `polish_applied_rate`.
  - **✅ 실측(2026-07-24, 3주제×2, gpt-4o-mini)**: Polish 실행률 **직렬 1/6·병렬 0/6**(거의 전부 생략),
    polish 단계 **21.3s → 0.1ms**. wall 중앙값 PR-7만(병렬 84.0s) → PR-8(병렬 **64.3s**, **−23.5%**).
    품질 비열등성 유지(14섹션 100%·순서 100%·fallback 0·전부 success, 사실 검증률·근거 연결률 동등).
  - **누적(baseline-v1 병렬 106.8s → 64.3s ≈ −40%)** — 단, 주제 세트·프롬프트 상이로 완전 A/B 아님(참고치).
    ⚠️ 한계: 구조 지표는 동등하나 polish 가 하던 '섹션 간 흐름·중복 정리'의 내용 품질 영향은 구조 검사로
    측정되지 않음 → 필요 시 소규모 블라인드 평가로 확인. polish 생략 후 최대 단계는 다시 analysis_block(병렬 ~16.6s).
- 필요 시: 본 실험 6주제×2(AB/BA), max_concurrency 실험, UI 병렬 ETA/노드시간 문구, `docs/ARCHITECTURE.md` 최신화.

## 6. 재현 방법
```bash
python run_parallel_bench.py --topics 3 --reps 1 --fresh   # 실제 LLM(유료 ~$0.07)
# 결과: docs/parallel_bench_result.md · outputs/parallel_bench.json (signature에 git commit 포함)
```

## 7. 병렬화 플랜 PR 현황
| PR | 내용 | 상태 |
|---|---|---|
| #41 | 관측 지표(wall/llm-sum, workflow_mode) | ✅ 머지 |
| #42 | logs reducer + 자기 로그만 반환 | ✅ 머지 |
| #43 | 병렬 그래프 + WORKFLOW_MODE | ✅ 머지 |
| #44 | 직렬/병렬 비교 도구 | ✅ 머지 |
| #45 | 병렬 usage·동시성 검증 + 벤치 재현성 | ✅ 머지 |
| #46 | 단계별 latency 계측 + critical path | ✅ 머지 |
| **PR-7** | **구조화 리뷰 이슈 기반 섹션 단위 수정** | ✅ 구현(`feat/pr7-section-revise`) |
| PR-8 | 조건부 Polish | 대기 |
