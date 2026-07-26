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
  **PR 5c 이후로는 선언이 아니라 검증된 사실이다** — Agent 간 읽기가 전부 `read()`를 지나므로,
  `test_declared_depends_on_matches_actual_runtime_reads`가 호출을 기록해 이 선언과 대조한다.
  (어긋나면 PR 6의 선택적 재실행이 **잘못된 Agent를 재실행**하므로 중요하다.)
- `owner_agent`는 **LangGraph 노드 이름과 정확히 일치**한다(`workflow.py`의 `add_node`).
  덕분에 `failed_nodes`·`fallback_nodes`와 그대로 대조해 `status`를 유도할 수 있다.
- **근거를 직접 확보하는 Agent는 research·competitor 둘뿐**이다(나머지는 검색하지 않는다).
  `research_gap`(2-5)이 확보한 근거는 **research 것으로 귀속**한다 — 추가 검색은 Research의
  연장이지 별도 Artifact가 아니다.
- `evidence_ids`에는 **직접 사용한 근거만** 담는다. 상위 Agent의 근거를 물려받은 관계는
  `depends_on`으로 표현한다(근거를 중복 계상하면 추적성이 오히려 흐려진다).
"""
from __future__ import annotations

import logging
import os
from copy import deepcopy
from typing import TypedDict

_log = logging.getLogger("app.artifact")

# 봉투 자체의 버전. content 내부 스키마 버전이 아니다(그건 Agent별로 다르다).
ARTIFACT_SCHEMA_VERSION = 1

# 읽기 모드(로드맵 2-2 PR 5). 기본은 legacy — 전환 전과 동작이 완전히 같다.
READ_MODE_ENV = "ARTIFACT_READ_MODE"
READ_LEGACY = "legacy"                  # 평면 키만
READ_PREFER_ARTIFACT = "prefer_artifact"  # Artifact 우선, 없으면 평면 키
READ_ARTIFACT_ONLY = "artifact_only"    # Artifact 만(폴백 없음)
READ_MODES = (READ_LEGACY, READ_PREFER_ARTIFACT, READ_ARTIFACT_ONLY)

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
            "metadata": {"legacy_key": legacy_key, "source": SOURCE_LEGACY},
        })
    return artifacts


# ---- Dual Write (로드맵 2-2 PR 4) ----

# Artifact 를 누가 썼는지. 이행 진행도를 눈으로 확인하는 용도이기도 하다.
SOURCE_AGENT = "agent"            # Agent 가 직접 작성(Dual Write 완료)
SOURCE_LEGACY = "legacy_derived"  # 아직 평면 결과에서 파생


def make_artifact(artifact_type: str, content: dict | str) -> Artifact:
    """Agent 가 자기 산출물을 Artifact 봉투로 감싼다(Dual Write 용).

    **`evidence_ids` 와 `status` 는 여기서 채우지 않는다.** Agent 가 실행되는 시점에는
    둘 다 알 수 없기 때문이다:
      - `evidence_id` 는 실행 종료 시 `evidence.normalize()` 가 URL 최초 등장 순서로 부여한다.
        노드 안에서 임의로 매기면 최종 id 와 어긋난다.
      - `failed_nodes`·`fallback_nodes` 는 `_assess_quality`(finalize)가 로그를 보고 정한다.
    둘 다 `reconcile()` 이 실행 종료 시점 값으로 확정한다.
    """
    spec = SPEC_BY_TYPE[artifact_type]
    return {
        "artifact_id": spec["artifact_id"],
        "artifact_type": spec["artifact_type"],
        "owner_agent": spec["owner_agent"],
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "content": deepcopy(content) if isinstance(content, (dict, str)) else {},
        "evidence_ids": [],                       # reconcile 이 확정
        "depends_on": list(spec["depends_on"]),
        "target_sections": list(spec["target_sections"]),
        "status": STATUS_COMPLETE,                # reconcile 이 확정
        "metadata": {"legacy_key": spec["legacy_key"], "source": SOURCE_AGENT},
    }


def merge_artifacts(left: list, right: list) -> list[Artifact]:
    """State reducer — 같은 `artifact_id` 는 **나중 것(right)이 이긴다**.

    단순 `operator.add` 를 쓰면 안 된다. 한 Agent 가 두 번 방출하거나(예: `research` 뒤
    `research_gap` 이 보강본을 다시 내보내는 2-5 경로) 재실행되면 같은 Artifact 가 **중복**되기
    때문이다. id 기준 덮어쓰기라 마지막 값이 남는다.

    출력 순서는 `LEGACY_ARTIFACT_SPECS`(위상 순서)로 고정하고, 명세에 없는 것은 첫 등장 순서로
    뒤에 붙인다 — 병렬 분기의 도착 순서에 결과가 흔들리면 안 되기 때문(결정성).
    """
    by_id: dict[str, Artifact] = {}
    extra_order: list[str] = []
    for a in [*(left or []), *(right or [])]:
        if not isinstance(a, dict):
            continue
        aid = a.get("artifact_id") or ""
        if aid not in by_id and aid not in set(ARTIFACT_IDS):
            extra_order.append(aid)
        by_id[aid] = a
    ordered = [by_id[aid] for aid in ARTIFACT_IDS if aid in by_id]
    ordered += [by_id[aid] for aid in extra_order if aid in by_id]
    return ordered


def reconcile(state: dict) -> list[Artifact]:
    """실행 종료 시 Artifact 목록을 확정한다(로드맵 2-2 PR 2~4 공통 경로).

    1) 평면 결과에서 7개를 파생(아직 Dual Write 안 된 Agent 를 메운다)
    2) **Agent 가 직접 쓴 Artifact 로 덮어쓴다** — 생산 경로가 우선이다
    3) `evidence_ids` 와 `status` 는 **누가 썼든 여기서 다시 확정**한다.
       Agent 는 실행 시점에 둘 다 알 수 없다(`make_artifact` 주석 참고). 이 재확정이 없으면
       Dual Write 로 옮긴 Agent 만 근거 연결이 비고 status 가 틀리는 회귀가 생긴다.

    2)에서 살리는 것은 **`source=agent` 인 것과 명세 밖 항목뿐**이다. 이전 실행에서 파생된
    (`source=legacy_derived`) 항목까지 살리면 평면 결과가 바뀌어도 옛 파생본이 계속 이겨
    영원히 갱신되지 않는다 — 파생본은 평면 키의 사본일 뿐이므로 매번 다시 만든다.
    """
    if not isinstance(state, dict):
        return []
    existing = [a for a in (state.get("artifacts") or []) if isinstance(a, dict)]
    keep = [a for a in existing
            if (a.get("metadata") or {}).get("source") == SOURCE_AGENT
            or a.get("artifact_type") not in SPEC_BY_TYPE]
    merged = merge_artifacts(build_artifacts_from_legacy(state), keep)
    registry = state.get("evidence_registry") or []
    out: list[Artifact] = []
    for a in merged:
        spec = SPEC_BY_TYPE.get(a.get("artifact_type") or "")
        if spec is None:          # 명세 밖 Artifact 는 손대지 않는다(정합성 검사가 잡는다)
            out.append(a)
            continue
        item = dict(a)
        item["evidence_ids"] = evidence_ids_for(registry, spec["evidence_agents"])
        item["status"] = _status_for(state, spec["owner_agent"], spec["legacy_key"])
        out.append(item)  # type: ignore[arg-type]
    return out


# ---- 정합성 검증 (로드맵 2-2 PR 3) ----

def check_parity(state: dict) -> dict:
    """Shadow Artifact 가 기존 평면 결과와 어긋나지 않는지 검사한다(읽기 전용).

    Artifact 를 **실제로 소비하기 전에**(PR 5) 병행 기록이 믿을 만한지 먼저 확인하기 위한
    자기점검이다. 반환:

        {"expected": 7, "generated": 7, "matched": 7, "mismatched": [{...}], "ok": True}

    `mismatched` 항목은 `{"artifact_id", "reason", "detail"}`. reason 종류:
      - `missing_artifact`     명세에 있는 Artifact 가 생성되지 않음
      - `unknown_artifact`     명세에 없는 Artifact 가 섞여 있음
      - `duplicate_id`         같은 artifact_id 가 두 번 이상
      - `content_mismatch`     평면 결과 키와 내용이 다름(가장 중요한 검사)
      - `missing_dependency`   depends_on 이 존재하지 않는 Artifact 를 가리킴
      - `unknown_evidence_id`  Evidence Registry 에 없는 근거를 참조

    **판정이 깨져도 실행을 실패시키지 않는다.** 여기서 하드 실패시키면 아직 아무도 쓰지 않는
    그림자 구조 때문에 멀쩡한 실행이 죽는다. 대신 State·로그로 표면화해 테스트와 사람이
    먼저 발견하게 한다(2-5의 `dynamic_research` 가 '안 한 것 vs 해서 못 찾은 것'을 구분해
    표면화한 것과 같은 태도).
    """
    expected = len(LEGACY_ARTIFACT_SPECS)
    if not isinstance(state, dict):
        return {"expected": expected, "generated": 0, "matched": 0,
                "mismatched": [{"artifact_id": "", "reason": "missing_artifact",
                                "detail": "state 가 dict 가 아님"}], "ok": False}

    artifacts = [a for a in (state.get("artifacts") or []) if isinstance(a, dict)]
    mismatched: list[dict] = []

    def bad(artifact_id: str, reason: str, detail: str = "") -> None:
        mismatched.append({"artifact_id": artifact_id, "reason": reason, "detail": detail})

    by_id: dict[str, dict] = {}
    for a in artifacts:
        aid = a.get("artifact_id") or ""
        if aid in by_id:
            bad(aid, "duplicate_id", "같은 artifact_id 가 두 번 이상 있음")
            continue
        by_id[aid] = a
        if aid not in set(ARTIFACT_IDS):
            bad(aid, "unknown_artifact", f"명세에 없는 artifact_id: {aid}")

    known_evidence = {e.get("evidence_id") for e in (state.get("evidence_registry") or [])
                      if isinstance(e, dict) and e.get("evidence_id")}

    matched = 0
    for spec in LEGACY_ARTIFACT_SPECS:
        aid = spec["artifact_id"]
        a = by_id.get(aid)
        if a is None:
            bad(aid, "missing_artifact", f"{spec['legacy_key']} 에 대응하는 Artifact 없음")
            continue

        ok = True
        # 가장 중요한 검사 — 병행 기록이 원본과 같은가.
        legacy_value = state.get(spec["legacy_key"]) or {}
        if a.get("content") != legacy_value:
            bad(aid, "content_mismatch", f"{spec['legacy_key']} 와 content 불일치")
            ok = False
        for dep in a.get("depends_on") or []:
            if dep not in by_id:
                bad(aid, "missing_dependency", f"의존 대상 없음: {dep}")
                ok = False
        for eid in a.get("evidence_ids") or []:
            if eid not in known_evidence:
                bad(aid, "unknown_evidence_id", f"레지스트리에 없는 근거: {eid}")
                ok = False
        if ok:
            matched += 1

    return {"expected": expected, "generated": len(artifacts), "matched": matched,
            "mismatched": mismatched, "ok": not mismatched and matched == expected}


# ---- selector (소비자용) ----

def find_artifact(state: dict, artifact_type: str) -> Artifact | None:
    """State 의 artifacts 목록에서 유형으로 하나를 찾는다. 없으면 None."""
    if not isinstance(state, dict):
        return None
    for a in state.get("artifacts") or []:
        if isinstance(a, dict) and a.get("artifact_type") == artifact_type:
            return a
    return None


class ArtifactUnavailable(RuntimeError):
    """`artifact_only` 모드에서 쓸 수 있는 Artifact 가 없을 때. 다른 모드에서는 발생하지 않는다.

    이 모드는 '평면 키 없이도 정말 도는가'를 확인하는 **검증용**이다. 없는 Artifact 를 빈
    dict 로 넘겨 파이프라인을 계속 돌리면 확인 자체가 무의미해지므로 명시적으로 실패시킨다.
    """


def read_mode_info() -> dict:
    """`ARTIFACT_READ_MODE` 를 해석하고 **원본 값과 폴백 여부까지** 알려준다.

    알 수 없는 값·미설정이면 가장 안전한 `legacy` 로 떨어지되, **조용히 넘어가지 않는다.**
    `ARTIFACT_READ_MODE=prefer_artifcat` 같은 오타를 무시만 하면 운영자는 Artifact 모드로
    돌고 있다고 믿는데 실제로는 계속 평면 키를 읽는다 — 알아차릴 방법이 없는 게 문제다.
    그래서 원본(`raw`)과 폴백 여부(`invalid`)를 함께 돌려주고, 호출부가 로그·`/health`·
    실행 State 에 노출한다.
    """
    raw = os.getenv(READ_MODE_ENV, "") or ""
    normalized = raw.strip().lower()
    if normalized in READ_MODES:
        return {"mode": normalized, "raw": raw, "invalid": False}
    # 미설정은 정상(기본값 사용). 값이 있는데 못 알아들은 경우만 invalid.
    return {"mode": READ_LEGACY, "raw": raw, "invalid": bool(normalized)}


def read_mode() -> str:
    """현재 읽기 모드. 알 수 없는 값·미설정이면 `legacy`.

    이 값 하나가 **rollback 장치**다. 전환 후 문제가 생기면 코드를 되돌리지 않고
    `ARTIFACT_READ_MODE=legacy` 로 바꾼 뒤 **애플리케이션을 재시작**하면 기존 경로로
    복귀한다(‘즉시’가 아니다 — `.env` 는 `load_dotenv()` 가 모듈 import 시 1회만 읽는다).
    """
    info = read_mode_info()
    if info["invalid"]:
        _log.warning(
            "%s=%r 를 알 수 없어 %s 로 동작합니다(오타 확인). 유효값: %s",
            READ_MODE_ENV, info["raw"], READ_LEGACY, ", ".join(READ_MODES))
    return info["mode"]


def _usable_content(state: dict, artifact_type: str):
    """Artifact 에서 쓸 수 있는 content 를 꺼낸다. 못 쓰면 `(None, 사유)`.

    '없음'과 '있는데 못 씀'을 구분한다 — 후자는 단순 미생성이 아니라 Agent 오류·직렬화
    실패·status=failed 같은 **실제 문제**일 수 있어서, 조용히 평면 키로 떨어지면 그 문제가
    숨는다.
    """
    a = find_artifact(state, artifact_type)
    if not isinstance(a, dict):
        return None, "missing"
    if a.get("status") == STATUS_FAILED:
        return None, "failed"
    content = a.get("content")
    if not content:
        return None, "empty"
    return content, None


def get_artifact_content(state: dict, artifact_type: str, legacy_key: str,
                         mode: str | None = None) -> dict | str:
    """소비자가 Agent 산출물을 읽는 **단일 창구**(로드맵 2-2 PR 5).

    모드(`ARTIFACT_READ_MODE`, 인자로 덮어쓸 수 있음):
      - `legacy`(기본)   평면 키만 읽는다 — 전환 전과 **완전히 동일한 동작**
      - `prefer_artifact` Artifact 우선, 못 쓰면 평면 키로 폴백(사유를 로그로 남긴다)
      - `artifact_only`   Artifact 만 읽는다. 못 쓰면 `ArtifactUnavailable` 로 **명시적 실패**

    소비자를 이 함수로 옮겨 두면 기본값(`legacy`)에서는 아무것도 바뀌지 않고,
    준비가 됐을 때 **환경변수만으로**(+재시작) 읽기 경로를 통째로 전환·되돌릴 수 있다.
    """
    if not isinstance(state, dict):
        return {}
    m = mode if mode in READ_MODES else read_mode()
    if m == READ_LEGACY:
        return state.get(legacy_key) or {}
    content, reason = _usable_content(state, artifact_type)
    if content:
        return content
    if m == READ_ARTIFACT_ONLY:
        raise ArtifactUnavailable(f"{artifact_type}: Artifact 를 쓸 수 없음({reason})")
    # prefer_artifact 폴백 — 조용히 넘어가면 Artifact 쪽 오류가 묻힌다.
    _log.warning("Artifact %s 를 쓸 수 없어 평면 키 %s 로 폴백합니다(사유: %s)",
                 artifact_type, legacy_key, reason)
    return state.get(legacy_key) or {}


def read(state: dict, artifact_type: str) -> dict:
    """소비자가 쓰는 짧은 형태 — 유형만 주면 평면 키는 명세에서 찾아 쓴다.

    `get_artifact_content` 은 legacy_key 를 인자로 받는데, 소비자마다 그 이름을 적어 두면
    평면 키가 두 곳(명세·호출부)에 존재해 어긋날 수 있다. 유형 하나만 받아 명세에서 꺼내면
    호출부에는 평면 키가 아예 나타나지 않는다. 결과가 dict 가 아니면(옛 기록의 문자열 등)
    빈 dict — 소비자는 전부 dict 를 기대한다.
    """
    spec = SPEC_BY_TYPE[artifact_type]
    data = get_artifact_content(state, artifact_type, spec["legacy_key"])
    return data if isinstance(data, dict) else {}


def read_status(state: dict) -> dict:
    """실행 종료 시점의 읽기 모드·Artifact 가용성 스냅샷(관측용).

    `prefer_artifact` 로 전환하기 전에 **얼마나 폴백이 날지**를 미리 보는 용도다.
    `unusable` 이 비어 있어야 전환해도 평면 키에 기대지 않는다.

    ⚠️ 런타임 카운터가 아니라 **finalize 시점 스냅샷**이다. Agent 는 Artifact 를 추가만
    하므로 실행 중 가용성이 줄지는 않지만, '실제로 몇 번 폴백했는지'를 세지는 않는다
    (실 전환 단계에서 호출 단위 계측이 필요하면 usage·budget 처럼 별도로 붙인다).
    """
    info = read_mode_info()
    unusable: list[dict] = []
    for spec in LEGACY_ARTIFACT_SPECS:
        content, reason = _usable_content(state if isinstance(state, dict) else {},
                                          spec["artifact_type"])
        if not content:
            unusable.append({"artifact_id": spec["artifact_id"], "reason": reason})
    return {
        "mode": info["mode"],
        "raw": info["raw"],
        "invalid": info["invalid"],
        "expected": len(LEGACY_ARTIFACT_SPECS),
        "usable": len(LEGACY_ARTIFACT_SPECS) - len(unusable),
        "unusable": unusable,
    }
