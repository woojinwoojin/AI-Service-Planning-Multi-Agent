# AI 서비스 기획 보조 Multi-Agent — Staging/Production 이미지 (로드맵 Phase 8)
#
# 키가 없거나 USE_DUMMY=1 이면 더미 모드로 완주하므로, 키 없이도 build→run→health→smoke 가
# 검증된다. 실제 실행 시에는 -e OPENAI_API_KEY=... 등으로 키를 주입한다.
FROM python:3.13-slim

# 파이썬 런타임 위생: .pyc 미생성, 로그 즉시 flush(컨테이너 로그 확인성).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 의존성만 먼저 복사해 레이어 캐시 활용(코드만 바뀌면 재설치 안 함).
# requirements-lock.txt 는 머신 전체 freeze 라 여기서는 깨끗한 requirements.txt 를 쓴다.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 애플리케이션 코드(런타임에 필요한 것만; tests·docs 등은 .dockerignore 로 제외).
COPY app ./app

# SQLite 이력(data/)·산출물(outputs/) 기록 경로. 볼륨으로 마운트해 영속화.
RUN mkdir -p data outputs

# 비루트 유저로 실행(권한 최소화). 쓰기 경로 소유권 이전.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# 헬스체크: /health 200 이면 healthy. slim 이미지에 curl 이 없어 파이썬 stdlib 로 확인.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=4).status==200 else 1)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
