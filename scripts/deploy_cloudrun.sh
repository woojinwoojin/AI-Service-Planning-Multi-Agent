#!/usr/bin/env bash
# GCP Cloud Run 배포 (로드맵 Phase 8 · Phase 7 사용자 테스트용 공개 배포)
#
# 왜 Cloud Run 인가: 이미 있는 Dockerfile 을 그대로 쓰고, 요청 없을 때 0 으로 줄어들며,
# HTTPS·도메인이 자동으로 붙는다. `--source` 배포는 **Cloud Build** 가 이미지를 만들므로
# **로컬 Docker 데몬이 꺼져 있어도** 된다.
#
# 안전장치(공개 주소 전제):
#   - 키는 이미지·env 가 아니라 **Secret Manager** 에서 주입
#   - `--max-instances=1` : 인스턴스가 늘면 앱 내부 카운터(요청 상한)가 인스턴스 수만큼 곱해진다.
#     부하 관측상 한 인스턴스가 동시 5를 지연 증가 없이 처리하므로 5~10명 테스트에 충분하다.
#   - `PUBLIC_MAX_*` : IP 당 빈도 + 전역 일일 실행 수 + 전역 일일 비용 상한
#   - `ENABLE_DEMO_TOOLS=0` : /admin·장애 주입 차단(기본값이지만 명시한다)
#
# 사용:
#   export GCP_PROJECT=your-project-id
#   bash scripts/deploy_cloudrun.sh              # 배포
#   bash scripts/deploy_cloudrun.sh --secrets    # 시크릿만 먼저 생성/갱신
#
# 사전 준비(1회, 대화형이라 사람이 직접):
#   gcloud auth login
#   gcloud config set project "$GCP_PROJECT"
#   gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
#                          secretmanager.googleapis.com artifactregistry.googleapis.com
#
# ⚠ IAM 도 함께 — 빼면 반드시 막힌다(실제로 둘 다 겪음). 상세·명령은 DEPLOY.md 참고:
#   ① Compute 기본 SA 에 roles/cloudbuild.builds.builder  (없으면 소스 zip 403)
#   ② 같은 SA 에 시크릿별 roles/secretmanager.secretAccessor
#      (없으면 빌드는 성공하는데 컨테이너가 키를 못 읽는다 — 발견이 늦다)
#   ②는 시크릿 생성 후에 걸어야 하므로 `--secrets` 로 먼저 만들고 → IAM → 본 배포 순서.
set -euo pipefail

PROJECT="${GCP_PROJECT:?GCP_PROJECT 환경변수를 설정하세요 (예: export GCP_PROJECT=my-proj)}"
REGION="${GCP_REGION:-asia-northeast3}"          # 서울
SERVICE="${CLOUD_RUN_SERVICE:-ai-planning-agent}"

# --- 상한 기본값 (필요하면 환경변수로 덮어쓴다) ------------------------------
# 실행 1건 ≈ LLM 13~15콜 ≈ $0.012 (실측). 아래 기본값은 하루 최대 ≈ $1.2 로 묶는다.
MAX_RUNS_PER_IP="${PUBLIC_MAX_RUNS_PER_IP:-5}"
IP_WINDOW_SEC="${PUBLIC_IP_WINDOW_SEC:-3600}"
MAX_RUNS_PER_DAY="${PUBLIC_MAX_RUNS_PER_DAY:-100}"
MAX_COST_PER_DAY="${PUBLIC_MAX_COST_PER_DAY_USD:-2.0}"

create_secret() {   # 이름, 값
  local name="$1" value="$2"
  if [ -z "$value" ]; then
    echo "  - $name: 값이 비어 건너뜀"
    return
  fi
  if gcloud secrets describe "$name" --project "$PROJECT" >/dev/null 2>&1; then
    printf '%s' "$value" | gcloud secrets versions add "$name" --data-file=- --project "$PROJECT" >/dev/null
    echo "  - $name: 새 버전 추가"
  else
    printf '%s' "$value" | gcloud secrets create "$name" --data-file=- --replication-policy=automatic --project "$PROJECT" >/dev/null
    echo "  - $name: 생성"
  fi
}

echo "== 시크릿 등록 (로컬 .env 값을 사용) =="
# .env 를 읽되 **export 하지 않는다** — 값이 로그·자식 프로세스로 새지 않게.
if [ -f .env ]; then
  OPENAI_API_KEY="$(grep -E '^OPENAI_API_KEY=' .env | head -1 | cut -d= -f2- || true)"
  TAVILY_API_KEY="$(grep -E '^TAVILY_API_KEY=' .env | head -1 | cut -d= -f2- || true)"
fi
create_secret OPENAI_API_KEY "${OPENAI_API_KEY:-}"
create_secret TAVILY_API_KEY "${TAVILY_API_KEY:-}"

if [ "${1:-}" = "--secrets" ]; then
  echo "시크릿만 처리하고 종료합니다."
  exit 0
fi

echo
echo "== Cloud Run 배포 (source → Cloud Build → 이미지 → 배포) =="
gcloud run deploy "$SERVICE" \
  --source . \
  --project "$PROJECT" \
  --region "$REGION" \
  --allow-unauthenticated \
  --max-instances=1 \
  --min-instances=0 \
  --concurrency=8 \
  --cpu=1 --memory=1Gi \
  --timeout=600 \
  --set-secrets="OPENAI_API_KEY=OPENAI_API_KEY:latest,TAVILY_API_KEY=TAVILY_API_KEY:latest" \
  --set-env-vars="^@^USE_DUMMY=0@LLM_PROVIDER=openai@OPENAI_MODEL=gpt-4o-mini@WORKFLOW_MODE=parallel@ENABLE_DEMO_TOOLS=0@ARTIFACT_READ_MODE=legacy@PUBLIC_MAX_RUNS_PER_IP=${MAX_RUNS_PER_IP}@PUBLIC_IP_WINDOW_SEC=${IP_WINDOW_SEC}@PUBLIC_MAX_RUNS_PER_DAY=${MAX_RUNS_PER_DAY}@PUBLIC_MAX_COST_PER_DAY_USD=${MAX_COST_PER_DAY}"

URL="$(gcloud run services describe "$SERVICE" --project "$PROJECT" --region "$REGION" --format='value(status.url)')"
echo
echo "배포 URL: $URL"
echo
echo "== 배포 확인 =="
echo "  상한 설정 확인:  curl -s $URL/health | python -m json.tool"
echo "  스모크(실 키라 비용 발생): python scripts/smoke_test.py --base $URL --allow-real"
echo
echo "⚠ 이력(SQLite)은 컨테이너와 함께 사라진다 — 참가자에게 결과 다운로드를 안내할 것."
echo "⚠ 결제 예산 알림을 반드시 함께 걸 것 (DEPLOY.md 의 'GCP 비용 안전' 참고)."
