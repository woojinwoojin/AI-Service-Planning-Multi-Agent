# architecture — 발표용 구조도 (도판 6매)

`.mmd` = Mermaid 원본. **모든 도판은 코드에서 직접 대조해 그렸고**, 파일 첫 줄 주석에 근거가 되는
파일·줄 번호를 적어 뒀다. 질문이 오면 그 자리를 열 수 있어야 그림이 주장이 된다.

| 도판 | 내용 | 코드 근거 |
|---|---|---|
| `01-workflow-serial.mmd` | 전체 워크플로 — 직렬(기본) | `app/graph/workflow.py:222–237` |
| `02-workflow-parallel.mmd` | 전체 워크플로 — 병렬(배포 설정) | `app/graph/workflow.py:256–272` |
| `03-finish-loop.mmd` | Reviewer 비평·수정 루프 + `select_best` | `workflow.py:204–215`, `_route_revision:83–95` |
| `04-artifact-contract.mmd` | Artifact Contract(Dual Write · 3 읽기 모드) | `app/schemas/artifact.py:49–59` |
| `05-evidence-verify.mmd` | Evidence Registry → 사실 검증 | `app/services/evidence.py`, `app/agents/verifier.py:26,78–106` |
| `06-cicd.mmd` | 형상관리·배포(CI 4게이트 → 승인 → Cloud Run) | `.github/workflows/ci.yml`, `deploy-cloudrun.yml` |

## 렌더링

Mermaid 를 지원하는 곳(GitHub Markdown · VS Code Mermaid 확장 · mermaid.live)에 붙이면 그려진다.
슬라이드에 넣을 때는 렌더된 그림을 캡처해 쓴다.

## 그림과 함께 말해야 하는 것

도판은 흐름을 보여주지만 "그래서 자율적인가", "항상 이렇게 도는가" 에는 답하지 않는다.
발표에서 **그림만 띄우고 아래 문장을 빠뜨리면 과장으로 읽힌다.**

| 도판 | 반드시 함께 말할 것 |
|---|---|
| 1 | **그래프는 고정이다.** Agent 가 다음 할 일을 스스로 계획하지 않으므로 "자율 multi-agent"라고 말하면 틀린다 |
| 2 | 합류 지점은 `draft` 가 아니라 **`kosena_industry`** 다. KOSENA 4노드는 합류 뒤 **순차**(정확성 우선, 대가는 LLM +4회) |
| 3 | 자동 재작성은 **최대 1회**. `select_best` 되돌림은 표본 7건 중 **2건(29%)** 에서 실제로 일어났다 |
| 4 | 배포는 **`ARTIFACT_READ_MODE=legacy`** 다 → *"모든 Agent 가 Artifact Contract 만으로 통신한다"* 는 **과장**. `artifact_only` 는 검증 전용이며 운영 금지 |
| 5 | 검증 범위는 **검색 요약 기준**(`search_snippet_only`), URL 원문 미검증. 출처 검사는 **실행 전체 존재 검사**이며 항목별 연결은 미구현 |
| 6 | "승인 없이 자동 배포된다"와 "승인 없이는 배포 불가"는 **둘 다 틀리다**(승인 게이트 있음 + `can_admins_bypass=true`) |

## 이 작업 중 발견한 문서 오류

`docs/ARCHITECTURE.md` 의 병렬 다이어그램이 **fan-in 대상을 `draft` 로 잘못 적고 있었다.**
실제 코드(`workflow.py:266`)는 `kosena_industry` 다. 구조도를 그리려고 코드와 한 줄씩 대조하다
발견해 함께 고쳤다 — 도판을 코드에서 다시 그리는 작업 자체가 문서 검증이 됐다.

## 아직 없는 것

`screenshots/` · `qa-evidence/` 는 비어 있다. 화면 캡처는 사람이 만들어야 한다.
