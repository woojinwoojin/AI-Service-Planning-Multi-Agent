"""평가 세트 & 루브릭 (로드맵 v2 · Phase 1 — 개편 前 평가 기준선).

목적: 구조를 크게 바꾸기 *전에* "정말 좋아졌는가"를 측정할 고정 기반.
- TOPICS: 고정 평가 주제(순서·구성 고정 = '시드'). 각 주제에 안정적 id.
- RUBRIC: 서비스용 8개 기준(compare.py의 발표용 5개 기준과 분리·버전화).
- 버전 상수: 루브릭/판정 프롬프트가 바뀌면 올려서 옛 리포트와의 직접 비교를 막는다.

주의: compare.CRITERIA(발표 비교실험 5개 기준)는 그대로 둔다. 여기 8개 기준은
서비스 품질 게이트를 향한 별도 축이며, 실험 서명에 rubric_version 으로 고정된다.
"""
from __future__ import annotations

# 루브릭/프롬프트 버전 — 기준·프롬프트가 바뀌면 반드시 올린다(옛 리포트와 섞이지 않게).
RUBRIC_VERSION = "eval-rubric-v1"
PROMPT_VERSION = "eval-judge-v1"

# 기준당 만점. 8개 기준 × 20 = 160(raw). 리포트는 100점 환산(total_100)도 함께 낸다.
CRITERION_MAX = 20

# 서비스용 8개 기준: key → (한글 라벨, 채점 기준 설명).
# 설명은 EVAL_JUDGE 프롬프트의 채점 앵커로도 쓰이므로 구체적으로 적는다.
RUBRIC: dict[str, dict[str, str]] = {
    "problem_definition": {
        "label": "문제 정의",
        "desc": "해결하려는 문제가 구체적이고 실재하며, 왜 지금 중요한지 분명한가.",
    },
    "customer_specificity": {
        "label": "고객 구체성",
        "desc": "목표 고객이 뭉뚱그려지지 않고 세분화되어 있으며, 그들의 상황·니즈가 드러나는가.",
    },
    "market_analysis": {
        "label": "시장 분석",
        "desc": "시장 규모·성장성·구조·트렌드가 근거와 함께 서술되고 PESTEL이 채워졌는가.",
    },
    "competitive_differentiation": {
        "label": "경쟁 차별성",
        "desc": "경쟁자를 실제로 파악하고, 이 서비스만의 차별점이 설득력 있게 제시되는가.",
    },
    "revenue_model": {
        "label": "수익 모델",
        "desc": "수익 구조가 명확하고 타깃 고객·가치와 일관되며 현실적인가.",
    },
    "feasibility": {
        "label": "실행 가능성",
        "desc": "추진 계획·핵심 기능·위험 대응이 구체적이고 실행 가능한 수준인가.",
    },
    "logical_consistency": {
        "label": "논리 일관성",
        "desc": "섹션 간 주장이 서로 모순되지 않고 문제→해결→시장→수익이 하나로 이어지는가.",
    },
    "evidence_usage": {
        "label": "근거 활용",
        "desc": "핵심 주장에 실제 출처·데이터가 붙어 있고, 수치가 날조 없이 근거에 기반하는가.",
    },
}


# 고정 평가 주제 세트(순서 고정). id는 리포트·이어하기·사람 보정 매칭에 쓰는 안정 키.
TOPICS: list[dict] = [
    {
        "id": "career-univ",
        "project_name": "AI 기반 대학생 진로 설계 서비스",
        "description": "전공·역량·관심 직무를 분석해 학습/취업 로드맵을 제공",
        "target_user": "진로를 고민하는 대학생",
        "problem": "자신의 역량과 진로에 맞는 준비 방법을 찾기 어렵다",
        "keywords": ["진로", "대학생", "취업"],
    },
    {
        "id": "smb-inventory",
        "project_name": "소상공인 AI 재고관리 SaaS",
        "description": "판매 패턴을 학습해 발주 시점을 추천",
        "target_user": "동네 소매점 사장",
        "problem": "재고 과잉과 품절이 반복된다",
        "keywords": ["재고", "소상공인", "수요예측"],
    },
    {
        "id": "senior-medication",
        "project_name": "AI 시니어 복약 관리 알림",
        "description": "복약 시간을 음성으로 알리고 보호자에게 확인 전송",
        "target_user": "만성질환 고령자와 보호자",
        "problem": "복약 누락·중복으로 인한 건강 위험",
        "keywords": ["헬스케어", "고령자", "복약"],
    },
    {
        "id": "pet-health",
        "project_name": "AI 반려동물 건강 모니터링",
        "description": "웨어러블 센서로 반려견 활동·건강 지표를 추적하고 이상을 조기 경고",
        "target_user": "반려견 보호자",
        "problem": "질병 조기 발견이 어렵고 병원비 부담이 크다",
        "keywords": ["펫테크", "헬스케어", "IoT"],
    },
    {
        "id": "used-fraud",
        "project_name": "AI 중고거래 사기 탐지",
        "description": "거래 패턴을 분석해 사기 위험을 실시간 경고",
        "target_user": "중고거래 플랫폼 이용자",
        "problem": "중고거래 사기 피해가 지속 증가한다",
        "keywords": ["중고거래", "사기탐지", "핀테크"],
    },
    {
        "id": "meeting-summary",
        "project_name": "AI 회의록 자동 요약",
        "description": "회의 녹음을 요약하고 액션 아이템을 자동 추출",
        "target_user": "회의가 잦은 직장인",
        "problem": "회의록 정리와 후속 관리에 시간이 많이 든다",
        "keywords": ["생산성", "요약", "협업"],
    },
    {
        "id": "farm-disease",
        "project_name": "AI 작물 병해충 진단",
        "description": "잎 사진으로 병해충을 진단하고 방제법을 안내",
        "target_user": "중소 규모 농가",
        "problem": "병해충 초기 대응이 늦어 수확 손실이 크다",
        "keywords": ["애그테크", "이미지진단", "농업"],
    },
    {
        "id": "kids-reading",
        "project_name": "AI 아동 독서 코칭",
        "description": "읽기 수준을 진단해 맞춤 도서를 추천하고 독해 질문을 생성",
        "target_user": "초등 저학년 자녀를 둔 학부모",
        "problem": "아이 수준에 맞는 책 선택과 독해 지도가 어렵다",
        "keywords": ["에듀테크", "독서", "아동"],
    },
    {
        "id": "local-tour",
        "project_name": "AI 로컬 여행 코스 설계",
        "description": "취향·동선·예산을 반영해 지역 소상공인 중심 여행 코스를 구성",
        "target_user": "국내 자유여행객",
        "problem": "획일적 관광지 위주라 취향 맞는 로컬 경험을 찾기 어렵다",
        "keywords": ["트래블테크", "추천", "로컬"],
    },
    {
        "id": "clinic-noshow",
        "project_name": "AI 병·의원 노쇼 예측 관리",
        "description": "예약 이력으로 노쇼 위험을 예측하고 리마인드·대기자 배정을 자동화",
        "target_user": "동네 병·의원 원무 담당자",
        "problem": "예약 부도(노쇼)로 진료 슬롯과 매출이 낭비된다",
        "keywords": ["헬스케어", "예약관리", "예측"],
    },
    {
        "id": "esg-report",
        "project_name": "중소기업 AI ESG 보고 자동화",
        "description": "사내 데이터로 ESG 지표를 집계하고 공시용 초안을 생성",
        "target_user": "ESG 대응이 필요한 중소기업 실무자",
        "problem": "ESG 공시 요구는 늘지만 전담 인력과 방법론이 부족하다",
        "keywords": ["ESG", "규제대응", "자동화"],
    },
    {
        "id": "freelancer-tax",
        "project_name": "AI 프리랜서 세무 도우미",
        "description": "수입·경비를 자동 분류해 예상 세액과 절세 팁을 안내",
        "target_user": "1인 프리랜서·N잡러",
        "problem": "세무 지식 부족으로 신고 실수와 과다 납부가 잦다",
        "keywords": ["핀테크", "세무", "프리랜서"],
    },
]


def get_topics(limit: int | None = None) -> list[dict]:
    """고정 세트에서 앞에서부터 limit개(없으면 전체)를 순서 그대로 반환."""
    return TOPICS if limit is None else TOPICS[: max(0, limit)]
