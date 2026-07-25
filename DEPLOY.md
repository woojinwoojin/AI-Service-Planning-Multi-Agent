# 배포 가이드 (Staging) — 로드맵 Phase 8

`main merge → Docker build → 실행 → health check → smoke test` 흐름으로 **외부에서 실제 실행
가능한 형태**를 확보한다. 키가 없거나 `USE_DUMMY=1` 이면 더미 모드로 완주하므로, 키·비용 없이도
빌드·기동·검증이 가능하다.

## 구성 파일

| 파일 | 역할 |
|---|---|
| `Dockerfile` | `python:3.13-slim` 기반 런타임 이미지. 비루트 유저·`/health` HEALTHCHECK·uvicorn 실행 |
| `.dockerignore` | 빌드 컨텍스트 축소·비밀/데이터 제외(`.env`·`data/`·`outputs/`·`tests/`·`docs/`) |
| `docker-compose.yml` | 로컬 staging 기동(포트 8000·`data`/`outputs` 볼륨·헬스체크) |
| `scripts/smoke_test.py` | 기동 서버 스모크(표준 라이브러리만): `/health` → `POST /run` → `GET /projects` |
| `.github/workflows/deploy-staging.yml` | CD: build → run → health → smoke (실 호스트 배포는 옵트인 확장점) |

## 로컬 staging 실행

```bash
# 1) 빌드 + 기동 (기본 USE_DUMMY=1 → 키 없이 완주)
docker compose up --build -d

# 2) 스모크 테스트 (호스트에서 컨테이너로)
python scripts/smoke_test.py --base http://localhost:8000

# 3) UI/문서
#    http://localhost:8000/         (UI)
#    http://localhost:8000/docs     (OpenAPI)

# 4) 종료
docker compose down
```

### 실제 키로 실행

`.env` 를 채우거나(있으면 compose 가 자동 로드) 환경변수로 주입한다:

```bash
USE_DUMMY=0 OPENAI_API_KEY=sk-... docker compose up --build -d
```

주요 환경변수는 `.env.example` 참고: `LLM_PROVIDER`·`OPENAI_API_KEY`/`ANTHROPIC_API_KEY`·
`WORKFLOW_MODE`(serial|parallel)·`TAVILY_API_KEY`(웹검색)·`LANGFUSE_*`(관측성).

## CD 워크플로 (`.github/workflows/deploy-staging.yml`)

`main` push·수동 실행 시:

1. `docker build` — 이미지 빌드
2. `docker run` (USE_DUMMY=1) — 컨테이너 기동
3. **health check** — `/health` 200 될 때까지 폴링
4. **smoke test** — `scripts/smoke_test.py` 로 핵심 계약 확인
5. 컨테이너 로그 출력 후 정리(always)

여기까지는 시크릿 없이 항상 검증된다.

### 실 호스트 배포(옵트인 확장점)

`deploy` 잡은 기본 **스킵**된다. 레지스트리/호스트 시크릿을 갖춘 뒤 repo 변수
`STAGING_DEPLOY_ENABLED=true` 로 켜고, 잡 안에 다음을 연결한다:

1. 레지스트리 로그인(`secrets.REGISTRY_TOKEN`)
2. `docker tag`/`push`(GHCR 또는 사설 레지스트리)
3. 호스트에서 `pull` & 재기동(compose/ssh/k8s 등)
4. 원격 `/health` 확인 → 실패 시 이전 이미지로 rollback

> 켜지 않으면 검증되지 않은 '가짜 배포'가 일어나지 않도록 스킵된다.

## 현재 한계 (정직)

- **실 호스트 배포는 미연결** — 레지스트리/호스트/시크릿이 없어 위 확장점으로 남겨둔다. CD 는
  '배포 가능한 이미지가 뜨고 스모크를 통과함'까지 검증한다.
- **Production CD**(release tag → 배포 → health → rollback)는 UI·E2E 성숙 후 별도(Phase 8 후반).
