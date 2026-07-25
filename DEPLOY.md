# 배포 가이드 (Staging) — 로드맵 Phase 8

`main merge → Docker build → 실행 → health check → smoke test` 흐름으로 **외부에서 실제 실행
가능한 형태**를 확보한다. 키가 없거나 `USE_DUMMY=1` 이면 더미 모드로 완주하므로, 키·비용 없이도
빌드·기동·검증이 가능하다.

## 구성 파일

| 파일 | 역할 |
|---|---|
| `Dockerfile` | `python:3.13-slim` 기반 런타임 이미지. 비루트 유저·`/health` HEALTHCHECK·uvicorn 실행 |
| `.dockerignore` | 빌드 컨텍스트 축소·비밀/데이터 제외(`.env`·`data/`·`outputs/`·`tests/`·`docs/`) |
| `docker-compose.yml` | 로컬 staging 기동(포트 8000·named volume `project-data`/`project-outputs`·헬스체크) |
| `scripts/smoke_test.py` | 기동 서버 스모크(표준 라이브러리만): `/health` → 더미 모드 확인 → `POST /run` → `GET /projects` |
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
`WORKFLOW_MODE`(serial|parallel)·`BUDGET_MAX_*`(실행별 상한)·`TAVILY_API_KEY`(웹검색)·
`LANGFUSE_*`(관측성)·`ENABLE_DEMO_TOOLS`(데모 도구, 아래).

## 운영 안전 — 데모 도구 차단 (기본값)

`/admin` 페이지와 요청 단위 장애 주입(`demo_fail_nodes`)은 **`ENABLE_DEMO_TOOLS=1` 일 때만**
동작한다. 기본(`0`)에서는

- `GET /admin` → **404** (페이지 자체가 노출되지 않음)
- `POST /run` payload 의 `demo_fail_nodes`/`demo_fail_reason` → **무시**

공개 주소로 띄운 서버에서 외부 사용자가 Research·Draft·Reviewer 등을 의도적으로 실패시키는 것을
막기 위한 조치다. 시연·발표 때만 `ENABLE_DEMO_TOOLS=1` 로 켠다.

> 운영자가 서버 환경에 직접 주는 `DEMO_FAIL_NODES`/`DEMO_FAIL_REASON` 은 명시적 설정이므로 이
> 게이트와 무관하게 동작한다(운영 환경에는 설정하지 말 것).

## 데이터 영속화 (볼륨)

이력(SQLite `data/`)·산출물(`outputs/`)은 **named volume**(`project-data`·`project-outputs`)에
쓴다. 이미지가 비루트(UID 10001)로 실행되기 때문에, host bind mount 를 쓰면 Linux 에서 host
디렉터리 소유권이 그대로 컨테이너에 적용되어 쓰기가 거부될 수 있다(`/health` 는 파일을 쓰지 않아
통과하고 `/run`·`/run/save` 에서만 실패해 발견이 늦다). named volume 은 Docker 가 이미지 쪽
소유권으로 초기화하므로 사전 준비 없이 동작한다.

```bash
docker compose exec app ls -l /app/outputs          # 산출물 확인
docker compose cp app:/app/outputs ./outputs        # host 로 복사
docker volume ls | grep project-                    # 볼륨 확인
docker compose down -v                              # 볼륨까지 삭제(이력 초기화)
```

host 디렉터리에 직접 쓰고 싶다면 bind mount 로 바꾸고, **먼저 소유권을 맞춘다**:

```bash
mkdir -p data outputs && sudo chown -R 10001:10001 data outputs
# docker-compose.yml 의 volumes 를 ./data:/app/data · ./outputs:/app/outputs 로 교체
```

## CD 워크플로 (`.github/workflows/deploy-staging.yml`)

`main` push·수동 실행 시:

1. `docker build` — 이미지 빌드
2. `docker run` (USE_DUMMY=1) — 컨테이너 기동
3. **health check** — `/health` 200 될 때까지 폴링
4. **smoke test** — `scripts/smoke_test.py` 로 핵심 계약 확인
5. 컨테이너 로그 출력 후 정리(always)

여기까지는 시크릿 없이 항상 검증된다.

### 스모크 테스트와 비용 안전

`smoke_test.py` 는 `/run` 으로 **전체 워크플로를 실제로 실행**한다. 따라서 실 키 서버를 대상으로
돌리면 LLM 비용이 발생한다. 이를 막기 위해 `/health` 의 `dummy_mode` 를 확인해 **더미 서버만
기본 허용**하고, 실모드 대상은 명시적으로 동의해야 진행한다:

```bash
python scripts/smoke_test.py                    # 더미 서버만 통과(아니면 실행 전 중단)
python scripts/smoke_test.py --allow-real       # 실 키 서버 대상(비용 발생 경고 후 진행)
```

### 실 호스트 배포(옵트인 확장점 — 아직 미연결)

`deployment-placeholder` 잡은 기본 **스킵**된다. 레지스트리/호스트 시크릿을 갖춘 뒤 repo 변수
`STAGING_DEPLOY_ENABLED=true` 로 켜고, 잡 안에 다음을 연결한다:

1. 레지스트리 로그인(`secrets.REGISTRY_TOKEN`)
2. `docker tag`/`push`(GHCR 또는 사설 레지스트리)
3. 호스트에서 `pull` & 재기동(compose/ssh/k8s 등)
4. 원격 `/health` 확인 → 실패 시 이전 이미지로 rollback

> ⚠ 배포 단계를 연결하기 **전에** `STAGING_DEPLOY_ENABLED=true` 를 켜면 이 잡은 **의도적으로
> 실패**한다(`exit 1`). 안내 문구만 출력하고 성공으로 끝나면 Actions 에서 '배포 성공'처럼 보여
> (false green) 실제로 배포되지 않은 것을 배포됐다고 오인하기 때문이다. 연결 전에는 변수를 켜지
> 말고, 켤 때는 반드시 실제 배포 스텝과 함께 켠다.

## 현재 한계 (정직)

- **실 호스트 배포는 미연결** — 레지스트리/호스트/시크릿이 없어 위 확장점으로 남겨둔다. CD 는
  '배포 가능한 이미지가 뜨고 스모크를 통과함'까지 검증한다. 자리표시 잡은 켜면 실패하도록 두어,
  미연결 상태가 성공으로 보이지 않게 한다.
- **인증·인가 없음** — 공개 주소에 띄우면 누구나 `/run` 을 호출할 수 있다(LLM 비용). 데모 도구는
  기본 차단했지만, 실제 공개 운영에는 접근 제어·레이트리밋이 별도로 필요하다.
- **Production CD**(release tag → 배포 → health → rollback)는 UI·E2E 성숙 후 별도(Phase 8 후반).
