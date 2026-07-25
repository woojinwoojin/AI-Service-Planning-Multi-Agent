#!/usr/bin/env python3
"""배포 스모크 테스트 — 기동한 서버가 핵심 계약을 지키는지 최소 확인 (로드맵 Phase 8).

CD(build→run→health→smoke)와 로컬 staging 양쪽에서 동일하게 쓴다. 표준 라이브러리만
사용해(추가 설치 없이) 어디서나 실행된다. 더미 모드(USE_DUMMY=1)로 기동된 서버를 대상으로
하므로 API 키·네트워크 비용이 필요 없다.

사용:
    python scripts/smoke_test.py                       # 기본 http://localhost:8000
    python scripts/smoke_test.py --base http://host:8000 --timeout 60
    python scripts/smoke_test.py --allow-real          # 실 키 서버 대상(비용 발생 동의)

동작:
    1) /health 가 200 이고 status=="ok"          (기동·라우팅 확인)
    2) 대상이 더미 모드인지 확인(dummy_mode==true) — 아니면 중단          (비용 사고 방지)
    3) POST /run(더미 입력)이 200 이고 project_id·final_draft 반환   (관통 실행 확인)
    4) GET /projects 목록에 그 project_id 존재     (이력 저장 확인)
실패 시 사유를 출력하고 종료코드 1(비정상). 서버 기동 대기는 --timeout 초까지 폴링.

/run 은 전체 워크플로를 실제로 실행하므로, 실 키 서버를 대상으로 돌리면 LLM 비용이 발생한다.
그래서 기본은 더미 모드 서버만 허용하고, 실모드는 --allow-real 로 명시해야 진행한다.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

# 콘솔 인코딩 견고화: Windows 기본 콘솔(cp949 등)에서 한글·em-dash 출력이 깨지지 않도록
# utf-8 로 재설정한다(CD 의 utf-8 환경에선 사실상 무영향). 지원 안 하면 조용히 통과.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def _request(method: str, url: str, payload: dict | None = None, timeout: float = 10.0) -> tuple[int, dict]:
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (신뢰된 자기 서버)
        body = resp.read().decode("utf-8")
        return resp.status, (json.loads(body) if body else {})


def _wait_healthy(base: str, timeout: float) -> dict:
    """서버가 뜰 때까지 /health 를 폴링하고 health 본문을 돌려준다(컨테이너 start-period 대비)."""
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            status, body = _request("GET", f"{base}/health", timeout=4)
            if status == 200 and body.get("status") == "ok":
                print(f"[smoke] health OK — provider={body.get('provider')} dummy={body.get('dummy_mode')}")
                return body
            last = f"status={status} body={body}"
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last = str(exc)
        time.sleep(1.5)
    _fail(f"서버가 {timeout}s 안에 healthy 상태가 되지 않음 (마지막: {last})")
    return {}   # 도달하지 않음(_fail 이 종료). 타입 명확성용.


def _require_dummy(health: dict, allow_real: bool) -> None:
    """실 키 서버에 /run 을 쏴 비용을 발생시키는 사고를 막는다(기본 더미만 허용)."""
    if health.get("dummy_mode") is True:
        return
    if allow_real:
        print(f"[smoke] ⚠ 실모드 서버 대상 실행(--allow-real) — provider={health.get('provider')} "
              "LLM 호출 비용이 발생합니다")
        return
    _fail(
        f"대상 서버가 더미 모드가 아님(dummy_mode={health.get('dummy_mode')!r}, "
        f"provider={health.get('provider')!r}). 스모크는 /run 으로 전체 워크플로를 실행하므로 "
        "실 키 서버에서는 LLM 비용이 발생합니다. 더미로 기동(USE_DUMMY=1)하거나, "
        "비용을 감수하고 진행하려면 --allow-real 을 주세요."
    )


def _fail(msg: str) -> None:
    print(f"[smoke] FAIL — {msg}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser(description="배포 스모크 테스트")
    ap.add_argument("--base", default="http://localhost:8000", help="대상 서버 base URL")
    ap.add_argument("--timeout", type=float, default=60.0, help="기동 대기 최대 초")
    ap.add_argument("--allow-real", action="store_true",
                    help="더미가 아닌(실 키) 서버도 허용 — /run 실행에 LLM 비용이 발생함")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    # 1) 기동·헬스
    health = _wait_healthy(base, args.timeout)

    # 2) 더미 모드 확인 — 실 키 서버 오호출(비용) 방지
    _require_dummy(health, args.allow_real)

    # 3) 관통 실행(/run) — 더미 모드라 키 없이 완주
    try:
        status, run = _request("POST", f"{base}/run",
                               {"project_name": "스모크", "problem": "P"}, timeout=90)
    except (urllib.error.URLError, TimeoutError) as exc:
        _fail(f"/run 요청 실패: {exc}")
    if status != 200:
        _fail(f"/run 상태코드 {status}")
    pid = run.get("project_id")
    if not (isinstance(pid, int) and pid > 0):
        _fail(f"/run 응답에 유효한 project_id 없음: {run.get('project_id')!r}")
    if not run.get("final_draft"):
        _fail("/run 응답에 final_draft 없음")
    print(f"[smoke] run OK — project_id={pid} run_status={run.get('run_status')}")

    # 4) 이력 저장 확인
    try:
        status, projects = _request("GET", f"{base}/projects", timeout=10)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        _fail(f"/projects 요청 실패: {exc}")
    if status != 200:
        _fail(f"/projects 상태코드 {status}")
    ids = [p.get("id") for p in projects.get("projects", [])]
    if pid not in ids:
        _fail(f"이력 목록에 project_id={pid} 없음 (목록 {ids[:5]}…)")
    print(f"[smoke] history OK — project_id={pid} 저장 확인")

    print("[smoke] PASS — 배포 서버 핵심 계약 충족")


if __name__ == "__main__":
    main()
