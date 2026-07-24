# 신뢰도 Tier 2 · Ground Truth 스모크셋 리포트

> 모델: `gpt-4o-mini` · 표본 10건(균형 세트) · 판정=verifier.judge_claim

> 비율은 백분율이 아니라 n/N 로 보고한다(표본 수를 함께 봐야 오해가 없음).

## 지표

- 표본: 10건 (균형 세트)
- 허위 통과(위험): 0/4  — 근거 없음·반대인데 supported 로 통과
- 반대 근거 탐지: 2/2
- 근거 있는 주장 통과: 2/2
- 주장 유형 분류 정확: 9/10
- 추론·제안 검증 제외(not_applicable): 3/3

## 항목별 판정

| id | 분류 | 기대(유형/상태) | 예측(유형/상태) | 주장 |
|---|---|---|---|---|
| g1 | supported | fact/supported | fact/supported | 국내 반려동물 양육 가구가 증가하는 추세다 |
| g2 | supported | fact/supported | fact/supported | 전기차 판매량이 늘고 있다 |
| g3 | unsupported | fact/unsupported | fact/unsupported | 본 앱 사용자의 90%가 만족한다고 응답했다 |
| g4 | unsupported | fact/unsupported | fact/contradicted | 이 시장에는 경쟁 서비스가 전혀 없다 |
| g5 | contradicted | fact/contradicted | fact/contradicted | 해당 시장 규모는 매년 감소하고 있다 |
| g6 | contradicted | fact/contradicted | fact/contradicted | 이 서비스의 사용자 이탈률은 업계 평균보다 낮다 |
| g7 | uncertain | fact/uncertain | inference/not_applicable | 향후 관련 규제가 완화될 가능성이 있다 |
| g8 | inference | inference/not_applicable | inference/not_applicable | 따라서 본 서비스는 시장에서 성공할 것으로 예상된다 |
| g9 | proposal | proposal/not_applicable | proposal/not_applicable | 본 서비스는 AI 기반 맞춤 추천 기능을 제공한다 |
| g10 | proposal | proposal/not_applicable | proposal/not_applicable | 월 9,900원 구독 모델로 제공할 계획이다 |
