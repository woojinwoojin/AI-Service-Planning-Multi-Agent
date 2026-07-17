# PRD v1 — AI 서비스 기획 보조 Multi-Agent (13일 MVP)

> 2~3페이지 분량. 상세 배경은 `README.md`(풀버전 기획)와 `ROADMAP.md`(압축 일정) 참고.

---

## 1. 프로젝트 목적

사용자가 사업/서비스 아이디어를 입력하면, 4개의 AI Agent가 **시장조사 → PESTEL 분석 → 기획서 작성 → 평가 및 1회 개선**을
순차 수행하여 출처 기반의 구조화된 기획서 초안을 생성한다.
발표 목표는 완성형 서비스가 아니라 **Multi-Agent 구조의 필요성과 단일 LLM 대비 품질 개선을 입증**하는 것이다.

## 2. 목표 사용자

- 1차: AI/IT 프로젝트를 수행하는 대학생, 부트캠프 수강생, 졸업프로젝트 팀
- 사용 상황: 과제, 공모전, 부트캠프·졸업 프로젝트
- 니즈: 빠른 시장조사·PESTEL·기획서 초안 / 기대 결과: 출처 포함 기획서 초안과 분석표

## 3. 핵심 사용자 시나리오

1. 사용자가 프로젝트명·아이디어·목표 사용자·해결 문제를 입력한다.
2. "생성" 클릭 → Research → PESTEL → Draft → Reviewer가 순차 실행되고 진행 상태가 표시된다.
3. Agent별 결과(시장조사/PESTEL/초안/평가)를 탭으로 확인한다.
4. Reviewer의 개선 지시가 반영된 최종 기획서를 확인한다.
5. (선택) 사용자가 수정 요청을 입력하면 Draft Writer가 재작성한다.
6. (선택) 최종 기획서를 Markdown으로 저장한다.

## 4. 기능 요구사항

### Must
- 아이디어 입력 (프로젝트명·설명·목표 사용자·해결 문제)
- 입력 구조화 (전처리 함수)
- Research → 시장조사 결과 생성
- PESTEL 분석 생성 (Research 결과만 근거)
- 기획서 초안 생성 (고정 서식 1종)
- 평가 기준 기반 Reviewer 검토
- 검토 결과 반영 1회 재작성
- 최종 결과 화면 표시 + Agent별 결과 확인
- 실행 로그 / 진행 상태 표시

### Should
- 사용자 수정 요청 → 재작성
- Markdown 저장
- 단일 vs Multi-Agent 비교 스크립트

### Won't (13일 내 제외)
- 로그인/회원, 협업, 권한관리, PPT 자동생성, 고급 DOCX,
  방법론 선택, 외부 연동, 버전관리, 실시간 스트리밍

## 5. Agent 구성

| Agent | 입력 | 출력 |
|---|---|---|
| (전처리) 입력 구조화 | user_input | structured_input |
| Research | structured_input | research_result (JSON) |
| PESTEL | research_result | pestel_result (JSON) |
| Draft Writer | structured_input, research, pestel (+개선지시) | draft / final_draft (Markdown) |
| Reviewer | draft | review_result (JSON) |

## 6. 입력 및 출력 형태

### 입력 (user_input)
```json
{
  "project_name": "AI 기반 대학생 진로 설계 서비스",
  "description": "전공·역량·관심 직무를 분석해 학습·취업 로드맵 제공",
  "target_user": "전공과 진로를 고민하는 대학생",
  "problem": "자신의 역량과 진로에 맞는 준비 방법을 찾기 어렵다",
  "keywords": ["진로", "대학생", "취업", "역량 분석"]
}
```

### Research 출력 (research_result)
```json
{
  "market_overview": "", "industry_trends": [], "customer_needs": [],
  "competitors": [], "opportunities": [], "risks": [], "sources": []
}
```

### PESTEL 출력 (pestel_result)
```json
{
  "Political":      { "content": "", "opportunity": "", "threat": "", "response": "" },
  "Economic":       { "content": "", "opportunity": "", "threat": "", "response": "" },
  "Social":         { "content": "", "opportunity": "", "threat": "", "response": "" },
  "Technological":  { "content": "", "opportunity": "", "threat": "", "response": "" },
  "Environmental":  { "content": "", "opportunity": "", "threat": "", "response": "" },
  "Legal":          { "content": "", "opportunity": "", "threat": "", "response": "" }
}
```

### Reviewer 출력 (review_result)
```json
{
  "total_score": 78,
  "strengths": [], "weaknesses": [], "unsupported_claims": [],
  "revision_instructions": [],
  "section_scores": {
    "problem_clarity": 0, "market_validity": 0, "solution_specificity": 0,
    "differentiation": 0, "feasibility": 0
  }
}
```
평가 항목(각 20점): 문제정의 명확성 / 시장분석 타당성 / 해결방안 구체성 / 서비스 차별성 / 실행 가능성.

### 최종 출력 (final_draft)
고정 서식 Markdown: 프로젝트 개요 / 추진 배경 / 문제 정의 / 목표 사용자 / 시장·산업 분석 /
PESTEL 분석 / 제안 서비스 / 핵심 기능 / 차별성 / 기대효과 / 추진 계획 / 위험요인·대응.

## 7. State 구조

```python
class ProjectState(TypedDict):
    user_input: dict
    structured_input: dict
    research_result: dict
    pestel_result: dict
    draft: str
    review_result: dict
    final_draft: str
    revision_count: int
```

## 8. 완료 기준

- 테스트 입력 1건이 처음부터 끝까지 실행되어 시장조사·PESTEL·초안·평가·최종본이 모두 생성된다. (8일 차)
- 단일 Agent와 Multi-Agent 결과를 동일 기준으로 비교한 표가 있다. (10일 차)
- 라이브 데모 실패 대비 백업(캡처·영상·JSON·최종 기획서)이 있다. (13일 차)

## 9. 기술 스택

FastAPI · LangGraph · LangChain · (LLM: Anthropic Claude / OpenAI, 키 없으면 더미 모드) ·
Markdown Export · 최소 HTML UI. DB는 MVP에서 State로 대체(후순위).
