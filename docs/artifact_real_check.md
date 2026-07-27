# Artifact 읽기 전환 · 실 LLM 검증 리포트

> 모델 `gpt-4o-mini` · 실행 구조 `parallel` · 커밋 `a6b0ba4` · 총 18회 · 비용 $0.2326

> **실 LLM 은 확률적이라 모드 간 산출물 동일성을 쓸 수 없다.** 비교는 두 층이다 — 품질·비용의 **비열등성**(아래 표)과, 같은 State 를 세 모드로 읽었을 때의 **프롬프트 동일성**(결정적·비용 0).

> 표본이 작으므로 통계가 아니라 **스모크**다. 값은 평균이 아니라 실행별로 나열한다.

> ⚠️ **`사실 검증률`은 모드 비교에 쓰지 말 것.** 이 지표는 verify 가 문서에서 주장을 **매번 새로 추출해** 판정하므로 주장 집합 자체가 흔들린다. 같은 문서·같은 프롬프트로 9회 재판정했을 때 0.2~0.9 로 벌어졌다(모드 내 반복 폭이 모드 간 차이만큼 크다). 모드 간 차이가 보이더라도 **읽기 경로가 원인일 수 없다** — 아래 '프롬프트 동일성'이 같은 State 에서 세 모드의 프롬프트가 동일함을 결정적으로 보이기 때문이다.


## 요약

- legacy: 6회 · run_status=['success'] · failed 0 · parity_ok=True · 14섹션 완전=True · 점수 [67, 75, 70, 60, 65, 63] · 읽기 [18, 20, 19, 19, 19, 20](Artifact [0, 0, 0, 0, 0, 0]) · 폴백 [0, 0, 0, 0, 0, 0] · shadow [0, 0, 0, 0, 0, 0] · $0.073
- prefer_artifact: 6회 · run_status=['success'] · failed 0 · parity_ok=True · 14섹션 완전=True · 점수 [70, 71, 71, 65, 66, 65] · 읽기 [19, 19, 19, 20, 18, 20](Artifact [19, 19, 19, 20, 18, 20]) · 폴백 [0, 0, 0, 0, 0, 0] · shadow [0, 0, 0, 0, 0, 0] · $0.0778
- artifact_only: 6회 · run_status=['success'] · failed 0 · parity_ok=True · 14섹션 완전=True · 점수 [70, 70, 65, 55, 65, 73] · 읽기 [19, 19, 19, 20, 19, 20](Artifact [19, 19, 19, 20, 19, 20]) · 폴백 [0, 0, 0, 0, 0, 0] · shadow [0, 0, 0, 0, 0, 0] · $0.0818
- 프롬프트 동일성(결정적): 6주제 중 불일치 0건 
- 총 비용 $0.2326 / 18회

## 모드별 비교

| 항목 | legacy | prefer_artifact | artifact_only |
|---|---|---|---|
| 실행 수 | 6 | 6 | 6 |
| run_status | ["success"] | ["success"] | ["success"] |
| failed 노드 합 | 0 | 0 | 0 |
| fallback 노드 합 | 0 | 0 | 0 |
| parity 전부 ok | True | True | True |
| parity 불일치 사유 | [] | [] | [] |
| Artifact status | {"complete": 42} | {"complete": 42} | {"complete": 42} |
| 읽기 수 | [18, 20, 19, 19, 19, 20] | [19, 19, 19, 20, 18, 20] | [19, 19, 19, 20, 19, 20] |
| 그중 Artifact | [0, 0, 0, 0, 0, 0] | [19, 19, 19, 20, 18, 20] | [19, 19, 19, 20, 19, 20] |
| 실제 폴백 | [0, 0, 0, 0, 0, 0] | [0, 0, 0, 0, 0, 0] | [0, 0, 0, 0, 0, 0] |
| 폴백 사유 | {} | {} | {} |
| shadow 폴백 | [0, 0, 0, 0, 0, 0] | [0, 0, 0, 0, 0, 0] | [0, 0, 0, 0, 0, 0] |
| shadow 사유 | {} | {} | {} |
| 14섹션 전부 완전 | True | True | True |
| 빈 섹션 합 | 0 | 0 | 0 |
| 고유 출처 URL | [8, 19, 9, 9, 7, 18] | [18, 19, 9, 17, 7, 17] | [15, 19, 19, 19, 7, 17] |
| 총점 | [67, 75, 70, 60, 65, 63] | [70, 71, 71, 65, 66, 65] | [70, 70, 65, 55, 65, 73] |
| 사실 검증률 | [0.2, 1.0, 0.8, 0.8, 0.9, 0.8] | [0.4, 0.6, 0.4, 0.3, 0.4, 0.4] | [0.4, 0.2, 0.6, 0.6, 0.44, 0.4] |
| LLM 호출 | [13, 15, 14, 14, 13, 15] | [14, 15, 14, 14, 13, 14] | [14, 14, 15, 15, 14, 15] |
| LLM fallback 합 | 0 | 0 | 0 |
| 비용(USD) | 0.073 | 0.0778 | 0.0818 |
| wall(ms) | [65816.3, 83256.3, 75886.1, 68609.2, 78071.2, 79716.3] | [71965.1, 87821.7, 67926.2, 68025.1, 65201.8, 79655.6] | [64961.9, 75648.8, 79092.5, 78831.8, 75045.8, 79490.1] |

## 프롬프트 동일성(결정적·비용 0)

실제 실행이 남긴 State 를 고정하고 세 모드로 각 소비자의 프롬프트를 만들어 대조한다. State 가 고정이면 프롬프트 생성은 결정적이므로 **정확히 같아야 한다** — 읽기 경로가 갈리면 반드시 여기서 잡힌다.

| 주제 | 소비자 수 | 결과 | 불일치 | 프롬프트 0건 |
|---|---|---|---|---|
| career-univ | 8 | ✅ 동일 | — | — |
| smb-inventory | 8 | ✅ 동일 | — | — |
| senior-medication | 8 | ✅ 동일 | — | — |
| pet-health | 8 | ✅ 동일 | — | — |
| used-fraud | 8 | ✅ 동일 | — | — |
| meeting-summary | 8 | ✅ 동일 | — | — |

## 실행별 원자료

| 주제 | 모드 | status | parity | 읽기(Artifact) | 폴백 | shadow | 섹션 | 점수 | $ |
|---|---|---|---|---|---|---|---|---|---|
| career-univ | legacy | success | True | 18(0) | 0 | 0 | 14/14 | 67 | 0.0095 |
| career-univ | prefer_artifact | success | True | 19(19) | 0 | 0 | 14/14 | 70 | 0.0128 |
| career-univ | artifact_only | success | True | 19(19) | 0 | 0 | 14/14 | 70 | 0.0128 |
| smb-inventory | legacy | success | True | 20(0) | 0 | 0 | 14/14 | 75 | 0.0153 |
| smb-inventory | prefer_artifact | success | True | 19(19) | 0 | 0 | 14/14 | 71 | 0.0157 |
| smb-inventory | artifact_only | success | True | 19(19) | 0 | 0 | 14/14 | 70 | 0.0152 |
| senior-medication | legacy | success | True | 19(0) | 0 | 0 | 14/14 | 70 | 0.0106 |
| senior-medication | prefer_artifact | success | True | 19(19) | 0 | 0 | 14/14 | 71 | 0.0099 |
| senior-medication | artifact_only | success | True | 19(19) | 0 | 0 | 14/14 | 65 | 0.0152 |
| pet-health | legacy | success | True | 19(0) | 0 | 0 | 14/14 | 60 | 0.0112 |
| pet-health | prefer_artifact | success | True | 20(20) | 0 | 0 | 14/14 | 65 | 0.0154 |
| pet-health | artifact_only | success | True | 20(20) | 0 | 0 | 14/14 | 55 | 0.0141 |
| used-fraud | legacy | success | True | 19(0) | 0 | 0 | 14/14 | 65 | 0.0105 |
| used-fraud | prefer_artifact | success | True | 18(18) | 0 | 0 | 14/14 | 66 | 0.0095 |
| used-fraud | artifact_only | success | True | 19(19) | 0 | 0 | 14/14 | 65 | 0.0103 |
| meeting-summary | legacy | success | True | 20(0) | 0 | 0 | 14/14 | 63 | 0.0159 |
| meeting-summary | prefer_artifact | success | True | 20(20) | 0 | 0 | 14/14 | 65 | 0.0145 |
| meeting-summary | artifact_only | success | True | 20(20) | 0 | 0 | 14/14 | 73 | 0.0142 |
