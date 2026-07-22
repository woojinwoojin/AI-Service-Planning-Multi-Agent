# baseline-v1 — 성능·품질 기준선 (커밋 고정)

> 로드맵 v2 **Phase 0** 완료 산출물. 이후 모든 개편(Phase 2~)은 이 기준선과 **동일 벤치·동일 조건**으로
> 전후를 비교한다. 원자료는 같은 폴더의 [`baseline-v1.json`](./baseline-v1.json)에 고정되어 있다.

## 실험 서명 (재현 조건)

| 항목 | 값 |
|---|---|
| git commit | `ce7049e` |
| model | `gpt-4o-mini` |
| temperature | 0.3 (고정) |
| 주제 수 | 6 (`run_compare.TOPICS` 앞 6개) |
| topics_hash | `a98a5a28c023` |
| 반복(reps) | 2 (AB/BA 교차) |
| 총 실행 | 24회 (6주제 × 2반복 × serial/parallel) |
| workflow version | `parallel-v1` |

> **프롬프트 버전은 git commit `ce7049e`에 고정된다** — 프롬프트가 모두 버전 관리되므로 커밋이
> 프롬프트를 핀한다. 프롬프트를 바꾼 뒤에는 이 기준선과 **직접 비교 금지**(로드맵 트랙 C).
> 이 벤치는 결정론적 구조 검사 기반이라 LLM 심판 루브릭은 쓰지 않는다(그 축은 `docs/평가_루브릭_v1.md`).

## 재현 명령

```bash
python run_parallel_bench.py --topics 6 --reps 2 --fresh
# 산출물: docs/parallel_bench_result.md · outputs/parallel_bench.json
# 이 기준선 고정본: docs/baseline-v1.md · docs/baseline-v1.json
```

동일 서명이면 중단 후 재실행 시 `outputs/parallel_bench_partial.json`에서 이어서 진행한다.

## 성능·비용·품질 요약 (24회)

| 지표 | 직렬 | 병렬 |
|---|---|---|
| 실행 횟수 | 12 | 12 |
| **wall time 중앙값(ms) ↓** | **127,355** | **106,808** |
| wall time p95(ms) | 149,044 | 123,682 |
| wall time 최대(ms) | 153,866 | 133,099 |
| LLM 호출시간 합 중앙값(ms) | 124,563 | 121,546 |
| 평균 LLM 호출 수 | 13.0 | 13.0 |
| 평균 토큰 | 49,125 | 48,377 |
| 평균 비용(USD) | 0.012058 | 0.011825 |
| 실행 품질 분포 | success 12 | success 12 |
| 14섹션 완성률 | 1.0 | 1.0 |
| 섹션 순서 정상률 | 1.0 | 1.0 |
| PESTEL 표 정상률 | 1.0 | 1.0 |
| 평균 빈 섹션 수 | 0.0 | 0.0 |
| 평균 고유 출처 URL 수 | 9.0 | 9.0 |
| fallback 총계 | 0 | 0 |

**wall time 감소율(직렬→병렬): -16.1%** · 토큰 차이 -1.5%(병렬화는 호출 수를 줄이지 않으므로 비용·토큰은 유사한 것이 정상).

## 단계별 wall time 중앙값 (병목 위치)

| 단계 | 직렬(ms) | 병렬(ms) |
|---|---|---|
| preprocess | 0 | 0 |
| research | 8,669 | 8,254 |
| **analysis_block** | **35,703** | **20,986** |
| draft | 17,251 | 16,695 |
| initial_review | 5,788 | 4,860 |
| **revise_or_finalize** | **24,471** | **24,435** |
| **polish** | **18,316** | **17,038** |
| final_review | 5,147 | 4,668 |
| verify | 7,699 | 8,092 |
| _coverage(대 wall)_ | 1.0 | 1.0 |

- 병렬화 효과는 `analysis_block`에 집중(35.7s→21.0s, -41%). 분석 4분기를 병렬 실행한 결과.
- **다음 병목은 문서 재생성 구간**: `revise_or_finalize`(24.5s) + `polish`(18s) + `draft`(17s). 병렬화로 줄지 않는다(순차·단일 문서 생성). → Phase 2 PR-7(섹션 단위 수정)의 타깃.
- coverage=1.0 → 측정 단계가 전체 wall time을 사실상 100% 설명(프레임워크 오버헤드 무시 가능).

## 근거 확인율 (verification)

24회 전체 주장 분포: **supported 185 · unsupported 32 · uncertain 0** (총 217건).
- 근거 확인율(supported/전체) = **85.3%**.
- 범위 주의: 검증은 `search_snippet_only`(실제 검색 스니펫 대조)이며, 원문 대조(Tier 3)는 미포함(`docs/정보신뢰성_전략.md`).

## 기준선 해석 (개편 판단 기준)

- 병렬화는 **품질을 떨어뜨리지 않고**(구조 100%·fallback 0·근거율 유지) wall time을 -16.1% 줄였다 → 비열등성 + 성능 개선 확인.
- 이후 개편의 성공 판단: 이 표 대비 **구조 품질·근거율 하락 없이** 병목(문서 재생성) latency를 줄이거나, 근거율·내용 품질을 올렸는가.
- 내용 품질(논리·구체성)은 이 결정론 벤치가 측정하지 않는다 → `docs/평가_루브릭_v1.md`(8기준 LLM 심판)로 보완.
