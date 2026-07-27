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

## GCP Cloud Run 공개 배포 (Phase 7 사용자 테스트용)

5~10명이 실제로 써 보는 **사용자 테스트**를 위해 공개 주소로 띄운다. 스크립트:
`scripts/deploy_cloudrun.sh`.

### 왜 Cloud Run 인가
이미 있는 `Dockerfile` 을 그대로 쓰고, 요청이 없으면 0 으로 줄며, HTTPS·도메인이 자동으로
붙는다. `--source` 배포는 **Cloud Build** 가 이미지를 만들므로 **로컬 Docker 데몬이 꺼져
있어도 된다**.

### 사전 준비(1회 — 대화형이라 사람이 직접)

```bash
gcloud auth login
gcloud config set project "$GCP_PROJECT"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
                       secretmanager.googleapis.com artifactregistry.googleapis.com
```

**IAM — 이걸 빼면 반드시 막힌다(2026-07-27 실제로 둘 다 겪음).** 최근 GCP 프로젝트는
Compute 기본 서비스 계정에 아래 권한이 **기본으로 붙어 있지 않다.**

```bash
PROJNUM="$(gcloud projects describe "$GCP_PROJECT" --format='value(projectNumber)')"
SA="serviceAccount:${PROJNUM}-compute@developer.gserviceaccount.com"

# ① 빌드용 — 없으면 소스 zip 을 못 읽어 403 으로 배포 실패
gcloud projects add-iam-policy-binding "$GCP_PROJECT" \
  --member="$SA" --role=roles/cloudbuild.builds.builder --condition=None

# ② 런타임용 — 없으면 빌드는 성공하는데 컨테이너가 키를 못 읽는다(발견이 늦는 유형)
for s in OPENAI_API_KEY TAVILY_API_KEY; do
  gcloud secrets add-iam-policy-binding "$s" \
    --member="$SA" --role=roles/secretmanager.secretAccessor --project "$GCP_PROJECT"
done
```

> ②는 시크릿을 먼저 만든 뒤에 걸어야 한다 — `deploy_cloudrun.sh --secrets` 로 시크릿만
> 생성한 다음 위 루프를 돌리고, 그 뒤에 본 배포를 하면 순서가 맞는다.

### 컨테이너는 `PORT` 를 존중해야 한다

Cloud Run 은 자기가 정한 포트(기본 **8080**)를 `PORT` 환경변수로 주입하고 **그 포트만**
헬스체크한다. 이미지가 포트를 고정하면 앱이 정상 기동해도
`container failed to start and listen on PORT` 로 배포가 실패한다(로그에는
`Uvicorn running on http://0.0.0.0:8000` 과 `Application startup complete` 이 멀쩡히 찍힌다).

`Dockerfile` 은 `CMD ["sh","-c","exec uvicorn ... --port ${PORT:-8000}"]` 로 이를 따른다.
기본값이 8000 이라 `docker compose`·로컬 실행은 그대로다.

> ⚠️ **CI 의 `docker build` 잡은 이걸 못 잡는다** — 빌드만 하고 컨테이너를 띄우지 않기
> 때문이다. 로컬 Docker 데몬이 없으면 배포해 봐야 드러난다.

### 배포

```bash
export GCP_PROJECT=your-project-id
bash scripts/deploy_cloudrun.sh            # 시크릿 등록 + 배포
bash scripts/deploy_cloudrun.sh --secrets  # 키만 갱신
```

### 공개 주소이므로 반드시 함께 켜는 것들

| 장치 | 값 | 무엇을 막는가 |
|---|---|---|
| `--max-instances=1` | 1 | 인스턴스가 늘면 **앱 내부 카운터가 인스턴스 수만큼 곱해진다**(카운터는 프로세스 메모리). 부하 관측상 한 인스턴스가 동시 5를 지연 증가 없이 처리하므로 5~10명에 충분 |
| `PUBLIC_MAX_RUNS_PER_IP` | 5 / 1시간 | 한 사람의 연타 |
| `PUBLIC_MAX_RUNS_PER_DAY` | 100 | **전역** 실행 수 — IP 를 바꿔도 걸린다 |
| `PUBLIC_MAX_COST_PER_DAY_USD` | 2.0 | 실행이 예상보다 비쌀 때의 최종 방어선 |
| `ENABLE_DEMO_TOOLS=0` | 0 | `/admin`·장애 주입(기본값이지만 명시) |
| Secret Manager | — | 키를 이미지·env 에 굽지 않는다 |

실행 1건 ≈ LLM 13~15콜 ≈ **$0.012**(실측). 위 기본값이면 하루 최대 **≈$1.2** 로 묶인다.
현재 상태는 `GET /health` 의 `public_limits` 에서 확인한다(남은 여유가 안 보이면 운영자가
'왜 429 가 나는지' 알 수 없다).

> ⚠️ **IP 제한은 보증이 아니다.** `X-Forwarded-For` 는 클라이언트가 위조할 수 있다. 실제
> 보증은 **전역 일일 상한**이 한다(IP 와 무관하게 걸린다). 카운터는 프로세스 메모리라
> **재시작하면 초기화**된다.

### GCP 비용 안전 (앱 밖 방어선)

앱 상한이 전부 실패해도 결제는 막아야 한다. **예산 알림을 반드시 함께 건다.**

```bash
gcloud billing budgets create \
  --billing-account="$(gcloud billing projects describe "$GCP_PROJECT" --format='value(billingAccountName)' | sed 's|.*/||')" \
  --display-name="ai-planning-agent" \
  --budget-amount=10USD \
  --threshold-rule=percent=50 --threshold-rule=percent=90
```

> 예산 알림은 **알림일 뿐 자동 차단이 아니다.** OpenAI 쪽 상한(계정 usage limit)도 함께
> 걸어 두는 편이 안전하다.

### 이력은 남지 않는다 (설계 선택)

Cloud Run 은 컨테이너 파일시스템이 **비영속**이고 인스턴스 간 공유도 되지 않는다. 이력
(SQLite `data/projects.db`)은 컨테이너가 재활용되면 사라진다. 사용자 테스트에서는 이를
**감수하고**, 참가자에게 **결과 다운로드(MD/JSON/DOCX/PPTX)를 안내**한다. 설문은 별도 폼으로 받는다.

> SQLite 파일을 GCS FUSE 로 마운트해 영속화하는 방법은 **권하지 않는다** — 네트워크
> 파일시스템에서 SQLite 잠금이 제대로 동작하지 않아 파일이 손상될 수 있다. 영속이 꼭
> 필요해지면 실행 결과 JSON 을 GCS 에 업로드하거나 Cloud SQL 로 옮기는 편이 옳다.

### ⚠️ 이력은 사실상 공개다 (인증이 없으므로)

`/projects`·`/projects/{id}` 에는 **접근 제어가 없다.** 게다가 프로젝트 id 가
`INTEGER PRIMARY KEY AUTOINCREMENT` 라 **1, 2, 3… 으로 열거 가능**하다 — 목록 화면을 감춰도
주소를 직접 치면 남의 결과가 열린다. 즉 **"이력 목록이 공유된다"가 아니라 "입력과 결과가
다른 참가자에게 공개된다"**가 정확한 표현이다.

사용자 테스트(2026-07-27)는 **막지 않고 고지로 처리**하기로 했다(참가자가 소수의 아는
사람이라 판단). 공개 범위가 넓어지거나 민감한 입력이 예상되면 다음 중 하나가 필요하다:

- 조회 엔드포인트 2개를 옵트인 플래그로 404 처리(`/admin` 게이트와 같은 패턴).
  ⚠️ 목록만 막으면 소용없다 — **`/projects/{id}` 도 함께** 막아야 한다.
  `/revise` 는 `store.get_project()` 를 함수로 직접 호출하므로 이 조치에 영향받지 않는다.
- 저장 자체를 끄기. 단 `/revise` 가 저장된 조사 결과를 되살려 쓰므로, 끄면 **근거 없이 초안
  텍스트만으로 수정**하게 되어 '수정 편의' 평가가 열화된 기능을 대상으로 하게 된다.

### 참가자 안내에 넣을 것

- **입력한 내용과 생성된 기획서가 다른 참가자에게 그대로 보인다** → 공개해도 괜찮은 아이디어로만 테스트
- 입력한 내용은 **OpenAI·Tavily 로 전송**된다 → 개인정보·기밀 입력 금지
- 실행에 **약 70~90초** 걸린다(부하 관측 실측)
- 결과는 **저장을 보장하지 않으므로**(컨테이너 재시작 시 소실) 필요하면 다운로드할 것
- 1시간에 5회까지 실행 가능

## 현재 한계 (정직)

- **실 호스트 배포는 미연결** — 레지스트리/호스트/시크릿이 없어 위 확장점으로 남겨둔다. CD 는
  '배포 가능한 이미지가 뜨고 스모크를 통과함'까지 검증한다. 자리표시 잡은 켜면 실패하도록 두어,
  미연결 상태가 성공으로 보이지 않게 한다.
- **인증·인가 없음** — 공개 주소에 띄우면 누구나 `/run` 을 호출할 수 있다(LLM 비용). 데모 도구는
  기본 차단돼 있고, 요청 상한(`PUBLIC_MAX_*`, 위 GCP 섹션)으로 **폭주는 막지만** 이는
  **비용 상한이지 인증이 아니다.** 누가 썼는지는 알 수 없고, 상한 안에서는 누구나 쓸 수 있다.
  장기 공개 운영에는 여전히 접근 제어가 필요하다.
- **이력이 사실상 공개** — 조회 엔드포인트에 접근 제어가 없고 id 가 열거 가능하다(위 ⚠️ 절).
  2026-07-27 사용자 테스트는 고지로 처리하기로 했으나, **공개 운영에서는 반드시 막아야 한다.**
- **요청 상한은 단일 인스턴스 전제** — 카운터가 프로세스 메모리에 있어 인스턴스가 늘면 전역
  상한이 곱해지고, 재시작하면 초기화된다. `--max-instances=1` 과 짝으로만 유효하다.
- **Production CD**(release tag → 배포 → health → rollback)는 UI·E2E 성숙 후 별도(Phase 8 후반).
