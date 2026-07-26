"""Artifact Contract v1 — Agent 결과의 표준 봉투 (로드맵 v2 2-2, PR 1).

지금까지 각 Agent 결과는 State 최상위의 **평면 키 7개**(`research_result`·`competitor_result`·
`customer_result`·`pestel_result`·`swot_result`·`business_model_result`·`risk_result`)로만
존재했다. 어느 Agent가 만들었는지·무엇에 의존하는지·어느 근거를 썼는지·기획서의 어느 섹션을
책임지는지는 코드를 읽어야만 알 수 있었다.

이 모듈은 그 정보를 **공통 봉투**로 표준화한다. 다만 봉투만 통일하고 **`content` 내부 스키마는
Agent마다 제각각인 채로 둔다** — 내용까지 통일하려 들면 7개 Agent와 그 소비자를 한꺼번에
건드려야 한다.

**이 PR(1)의 범위: 타입·매핑·변환기·selector 뿐이다. 아무도 이 모듈을 호출하지 않는다.**
State·API·UI·워크플로는 전혀 바뀌지 않으며, 따라서 동작 변화도 없다. 실제 생성(Shadow)은
PR 2, 정합성 검증은 PR 3, Agent별 Dual Write는 PR 4다. 상세: `docs/phase2-2-artifact-plan.md`.

설계 메모:
- `artifact_id`는 랜덤·시간이 아니라 **고정 상수**다. 2-1의 `evidence_id`(URL 최초 등장 순서)와
  같은 이유 — 같은 State를 넣으면 항상 같은 결과가 나와야 테스트가 재현되고, 저장 후 재조회해도
  ID가 흔들리지 않는다.
- `LEGACY_ARTIFACT_SPECS`의 **순서 = 위상 순서**(의존 대상이 항상 먼저 나온다). 그래서
  `depends_on`이 가리키는 Artifact는 목록에서 항상 자기보다 앞에 있다.
- `depends_on`은 상상이 아니라 **각 Agent 노드가 실제로 읽는 State 키**에서 도출했다
  (`competitor.py`·`customer.py`·`pestel.py`가 research를, `swot.py`가 research+competitor를,
  `business_model.py`가 research를, `risk.py`가 research+pestel을 읽는다).
- `owner_agent`는 **LangGraph 노드 이름과 정확히 일치**한다(`workflow.py`의 `add_node`).
  덕분에 `failed_nodes`·`fallback_nodes`와 그대로 대조해 `status`를 유도할 수 있다.
- **근거를 직접 확보하는 Agent는 research·competitor 둘뿐**이다(나머지는 검색하지 않는다).
  `research_gap`(2-5)이 확보한 근거는 **research 것으로 귀속**한다 — 추가 검색은 Research의
  연장이지 별도 Artifact가 아니다.
- `evidence_ids`에는 **직접 사용한 근거만** 담는다. 상위 Agent의 근거를 물려받은 관계는
  `depends_on`으로 표현한다(근거를 중복 계상하면 추적성이 오히려 흐려진다).
"""
from __future__ import annotations

from copy import deepcopy
from typing import TypedDict

# 봉투 자체의 버전. content 내부 스키마 버전이 아니다(그건 Agent별로 다르다).
ARTIFACT_SCHEMA_VERSION = 1

# status 값 — owner 노드의 실행 결말을 그대로 옮긴다.
STATUS_COMPLETE = "complete"    # 정상 산출
STATUS_FALLBACK = "fallback"    # 산출은 됐지만 fallback/오류 흡수 경로였음
STATUS_FAILED = "failed"        # owner 노드가 예외로 건너뛰어짐(_safe)
STATUS_MISSING = "missing"      # 해당 결과가 State에 없음(옛 기록·미실행)


class Artifact(TypedDict, total=False):
    """Agent 산출물의 표준 봉투 (Contract v1)."""

    artifact_id: str            # 실행 내 안정 id(고정 상수 — 랜덤·시간 미사용)
    artifact_type: str          # research_analysis 등 유형
    owner_agent: str            # 생산 Agent = LangGraph 노드 이름
    schema_version: int         # 봉투 버전(ARTIFACT_SCHEMA_VERSION)
    content: dict | str         # Agent별 원본 결과 — 내부 스키마는 통일하지 않음
    evidence_ids: list[str]     # 직접 사용한 근거(evidence_registry의 evidence_id)
    depends_on: list[str]       # 입력으로 삼은 다른 Artifact의 artifact_id
    target_sections: list[str]  # 이 산출물이 책임지는 기획서 섹션(sections.SECTION_SPECS의 ID)
    status: str                 # complete / fallback / failed / missing
    metadata: dict              # 부가정보(현재는 legacy_key)


class ArtifactSpec(TypedDict):
    """평면 결과 키 하나를 Artifact로 옮기기 위한 고정 명세."""

    legacy_key: str
    artifact_id: str
    artifact_type: str
    owner_agent: str
    depends_on: list[str]
    target_sections: list[str]
    evidence_agents: list[str]  # evidence_registry의 source_agents 중 이 Artifact에 귀속시킬 값


# 순서 = 위상 순서(의존 대상이 항상 먼저). pestel이 swot보다 앞인 이유는 risk가 pestel에 의존해서다.
LEGACY_ARTIFACT_SPECS: list[ArtifactSpec] = [
    {
        "legacy_key": "research_result",
        "artifact_id": "artifact-research",
        "artifact_type": "research_analysis",
        "owner_agent": "research",
        "depends_on": [],
        "target_sections": ["market_analysis"],
        # research_gap(2-5)이 찾은 근거도 Research 것으로 귀속한다.
        "evidence_agents": ["research", "research_gap"],
    },
    {
        "legacy_key": "competitor_result",
        "artifact_id": "artifact-competitor",
        "artifact_type": "competitor_analysis",
        "owner_agent": "competitor",
        "depends_on": ["artifact-research"],
        "target_sections": ["differentiation"],
        "evidence_agents": ["competitor"],
    },
    {
        "legacy_key": "customer_result",
        "artifact_id": "artifact-customer",
        "artifact_type": "customer_analysis",
        "owner_agent": "customer",
        "depends_on": ["artifact-research"],
        "target_sections": ["target_user"],
        "evidence_agents": [],
    },
    {
        "legacy_key": "pestel_result",
        "artifact_id": "artifact-pestel",
        "artifact_type": "pestel_analysis",
        "owner_agent": "pestel",
        "depends_on": ["artifact-research"],
        "target_sections": ["pestel"],
        "evidence_agents": [],
    },
    {
        "legacy_key": "swot_result",
        "artifact_id": "artifact-swot",
        "artifact_type": "swot_analysis",
        "owner_agent": "swot",
        "depends_on": ["artifact-research", "artifact-competitor"],
        "target_sections": ["swot"],
        "evidence_agents": [],
    },
    {
        "legacy_key": "business_model_result",
        "artifact_id": "artifact-business-model",
        "artifact_type": "business_model_analysis",
        "owner_agent": "business_model",
        "depends_on": ["artifact-research"],
        "target_sections": ["revenue_model"],
        "evidence_agents": [],
    },
    {
        "legacy_key": "risk_result",
        "artifact_id": "artifact-risk",
        "artifact_type": "risk_analysis",
        "owner_agent": "risk",
        "depends_on": ["artifact-research", "artifact-pestel"],
        "target_sections": ["risk"],
        "evidence_agents": [],
    },
]

# 조회용 파생 인덱스(단일 진실원천은 위 목록).
SPEC_BY_TYPE: dict[str, ArtifactSpec] = {s["artifact_type"]: s for s in LEGACY_ARTIFACT_SPECS}
SPEC_BY_LEGACY_KEY: dict[str, ArtifactSpec] = {s["legacy_key"]: s for s in LEGACY_ARTIFACT_SPECS}
ARTIFACT_IDS: list[str] = [s["artifact_id"] for s in LEGACY_ARTIFACT_SPECS]
LEGACY_KEYS: list[str] = [s["legacy_key"] for s in LEGACY_ARTIFACT_SPECS]


def evidence_ids_for(registry: list, evidence_agents: list[str]) -> list[str]:
    """레지스트리에서 해당 Agent(들)가 확보한 근거의 evidence_id 를 등장 순서대로 모은다.

    - 아직 `normalize()` 를 거치지 않아 `evidence_id` 가 없는 원시 항목은 건너뛴다
      (id 는 normalize 가 부여한다 — 여기서 임의로 매기면 나중 값과 어긋난다).
    - 순서는 레지스트리 순서를 그대로 따르므로 결정적이다.
    """
    if not evidence_agents:
        return []
    wanted = set(evidence_agents)
    out: list[str] = []
    for e in registry or []:
        if not isinstance(e, dict):
            continue
        eid = e.get("evidence_id")
        if not eid or eid in out:
            continue
        if wanted & set(e.get("source_agents") or []):
            out.append(eid)
    return out


def _status_for(state: dict, owner_agent: str, legacy_key: str) -> str:
    """owner 노드의 실행 결말로 Artifact status 를 정한다.

    failed(예외로 건너뜀) > fallback(오류 흡수) 순으로 본다 — fallback 은 산출물이 있어도
    붙으므로, 내용 유무보다 '어떻게 만들어졌는지'를 먼저 알려주는 편이 정직하다.
    """
    if owner_agent in (state.get("failed_nodes") or []):
        return STATUS_FAILED
    if owner_agent in (state.get("fallback_nodes") or []):
        return STATUS_FALLBACK
    if not state.get(legacy_key):
        return STATUS_MISSING
    return STATUS_COMPLETE


def build_artifacts_from_legacy(state: dict) -> list[Artifact]:
    """기존 평면 결과 키로부터 Artifact 목록을 파생 생성한다(읽기 전용·결정적).

    - **입력 State 를 변경하지 않는다.** `content` 는 deepcopy 라 이후 어느 쪽을 고쳐도
      다른 쪽이 따라 바뀌지 않는다(숨은 결합 방지).
    - 결과 키가 없어도 **항상 7개를 생성**하고 `status="missing"` 으로 표시한다. 개수를 고정해야
      정합성 검사(PR 3)가 `expected == generated` 로 단순해지고, '없다'는 사실 자체도 기록된다.
    - LLM·검색 호출 없음. 순수 변환이라 같은 입력이면 항상 같은 출력이다.
    """
    if not isinstance(state, dict):
        return []
    registry = state.get("evidence_registry") or []
    artifacts: list[Artifact] = []
    for spec in LEGACY_ARTIFACT_SPECS:
        legacy_key = spec["legacy_key"]
        raw = state.get(legacy_key)
        content: dict | str = deepcopy(raw) if isinstance(raw, (dict, str)) else {}
        artifacts.append({
            "artifact_id": spec["artifact_id"],
            "artifact_type": spec["artifact_type"],
            "owner_agent": spec["owner_agent"],
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "content": content,
            "evidence_ids": evidence_ids_for(registry, spec["evidence_agents"]),
            "depends_on": list(spec["depends_on"]),
            "target_sections": list(spec["target_sections"]),
            "status": _status_for(state, spec["owner_agent"], legacy_key),
            "metadata": {"legacy_key": legacy_key},
        })
    return artifacts


# ---- selector (소비자용) ----

def find_artifact(state: dict, artifact_type: str) -> Artifact | None:
    """State 의 artifacts 목록에서 유형으로 하나를 찾는다. 없으면 None."""
    if not isinstance(state, dict):
        return None
    for a in state.get("artifacts") or []:
        if isinstance(a, dict) and a.get("artifact_type") == artifact_type:
            return a
    return None


def get_artifact_content(state: dict, artifact_type: str, legacy_key: str) -> dict | str:
    """Artifact 우선으로 내용을 읽되, 없으면 기존 평면 키로 폴백한다.

    Artifact 가 아직 생성되지 않은 옛 프로젝트·PR 2 이전 State 에서도 그대로 동작한다
    (= 소비자를 미리 옮겨둬도 회귀가 없다). 읽기 모드 전환(`ARTIFACT_READ_MODE`)은 PR 5.
    """
    a = find_artifact(state, artifact_type)
    if a is not None:
        content = a.get("content")
        if content:
            return content
    if not isinstance(state, dict):
        return {}
    return state.get(legacy_key) or {}
