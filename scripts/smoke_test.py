#!/usr/bin/env python3
"""배포 스모크 테스트 — 기동한 서버가 핵심 계약을 지키는지 최소 확인 (로드맵 Phase 8).

CD(build→run→health→smoke)와 로컬 staging 양쪽에서 동일하게 쓴다. 표준 라이브러리만
사용해(추가 설치 없이) 어디서나 실행된다. 더미 모드(USE_DUMMY=1)로 기동된 서버를 대상으로
하므로 API 키·네트워크 비용이 필요 없다.

사용:
    python scripts/smoke_test.py                       # 기본 http://localhost:8000
    python scripts/smoke_test.py --base http://host:8000 --timeout 60

동작:
    1) /health 가 200 이고 status=="ok"          (기동·라우팅 확인)
    2) POST /run(더미 입력)이 200 이고 project_id·final_draft 반환   (관통 실행 확인)
    3) GET /projects 목록에 그 project_id 존재     (이력 저장 확인)
실패 시 사유를 출력하고 종료코드 1(비정상). 서버 기동 대기는 --timeout 초까지 폴링.
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


def _wait_healthy(base: str, timeout: float) -> None:
    """서버가 뜰 때까지 /health 를 폴링한다(컨테이너 start-period 대비)."""
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            status, body = _request("GET", f"{base}/health", timeout=4)
            if status == 200 and body.get("status") == "ok":
                print(f"[smoke] health OK — provider={body.get('provider')} dummy={body.get('dummy_mode')}")
                return
            last = f"status={status} body={body}"
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last = str(exc)
        time.sleep(1.5)
    _fail(f"서버가 {timeout}s 안에 healthy 상태가 되지 않음 (마지막: {last})")


def _fail(msg: str) -> None:
    print(f"[smoke] FAIL — {msg}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser(description="배포 스모크 테스트")
    ap.add_argument("--base", default="http://localhost:8000", help="대상 서버 base URL")
    ap.add_argument("--timeout", type=float, default=60.0, help="기동 대기 최대 초")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    # 1) 기동·헬스
    _wait_healthy(base, args.timeout)

    # 2) 관통 실행(/run) — 더미 모드라 키 없이 완주
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

    # 3) 이력 저장 확인
    status, projects = _request("GET", f"{base}/projects", timeout=10)
    if status != 200:
        _fail(f"/projects 상태코드 {status}")
    ids = [p.get("id") for p in projects.get("projects", [])]
    if pid not in ids:
        _fail(f"이력 목록에 project_id={pid} 없음 (목록 {ids[:5]}…)")
    print(f"[smoke] history OK — project_id={pid} 저장 확인")

    print("[smoke] PASS — 배포 서버 핵심 계약 충족")


if __name__ == "__main__":
    main()
