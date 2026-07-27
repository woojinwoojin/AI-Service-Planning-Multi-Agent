"""동시 실행 부하 관측 (로드맵 Phase 7).

`/run` 은 웹검색 + LLM 13~15콜 + SQLite 쓰기를 포함한 **가장 무거운 엔드포인트**다.
동시 요청이 늘 때 무엇이 먼저 무너지는지를 본다:

  1) **provider 레이트리밋** — fallback 사유 `혼잡`(429/503/529/overloaded)이 늘어나는가
  2) **응답 지연** — 동시성에 따라 wall time 이 어떻게 늘어나는가(선형? 급증?)
  3) **비용** — 동시 실행이 실행당 비용을 바꾸는가(예산 상한 정책과 연계)
  4) **이력 저장(SQLite)** — 동시 쓰기에서 유실·잠금이 없는가
     (`sqlite3.connect` 기본 timeout 5s·WAL 미설정이라 실제로 확인이 필요하다)
  5) **완주 보장** — 부하에서도 `_safe`·fallback 이 실행을 끝까지 끌고 가는가

**두 단계로 나눈다.** 더미(무비용)로 먼저 돌려 *앱 자체의* 동시성(스레드풀·SQLite)을 보고,
그 다음 실 LLM 으로 provider 쪽(레이트리밋·비용)을 본다. 더미에서 이미 깨지면 실 LLM 결과는
해석할 수 없다.

실행:
    python run_load_test.py                    # 더미(무비용) 1→3→5
    python run_load_test.py --real             # 실 LLM (비용 발생)
    python run_load_test.py --levels 1 3 5 10  # 동시성 지정

서버는 이 스크립트가 직접 띄우고 끝나면 내린다(포트 기본 8021, 임시 DB).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from app.services.eval_set import TOPICS  # noqa: E402


def _p(msg: str) -> None:
    print(msg, flush=True)


def _req(method: str, url: str, payload: dict | None = None, timeout: float = 30.0):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (자기 서버)
        body = resp.read().decode("utf-8")
        return resp.status, (json.loads(body) if body else {})


def _wait_healthy(base: str, timeout: float = 90.0) -> dict:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            status, body = _req("GET", f"{base}/health", timeout=4)
            if status == 200 and body.get("status") == "ok":
                return body
            last = f"{status} {body}"
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            last = str(exc)
        time.sleep(1.0)
    raise SystemExit(f"서버가 {timeout}s 안에 뜨지 않음 (마지막: {last})")


def _one_run(base: str, topic: dict, timeout: float) -> dict:
    """요청 1건. 실패도 **기록**한다 — 부하 테스트에서 실패는 결과지 예외가 아니다."""
    payload = {k: v for k, v in topic.items() if k != "id"}
    t0 = time.perf_counter()
    try:
        status, body = _req("POST", f"{base}/run", payload, timeout=timeout)
        err = ""
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
        status, body, err = getattr(exc, "code", 0), {}, f"{type(exc).__name__}: {exc}"
    elapsed = (time.perf_counter() - t0) * 1000
    usage = body.get("usage") or {}
    return {
        "topic": topic.get("id"),
        "http": status,
        "error": err,
        "client_ms": round(elapsed, 1),
        "project_id": body.get("project_id"),
        "run_status": body.get("run_status"),
        "failed_nodes": body.get("failed_nodes") or [],
        "fallback_reasons": body.get("fallback_reasons") or {},
        "calls": usage.get("calls"),
        "fallback_calls": usage.get("fallback_calls"),
        "est_cost_usd": usage.get("est_cost_usd"),
        "server_wall_ms": usage.get("wall_time_ms"),
        "sections_ok": bool(body.get("final_draft")),
    }


def _level(base: str, n: int, timeout: float) -> dict:
    """동시성 n 으로 n 건을 **동시에** 쏜다. 주제는 서로 다르게(캐시·중복 효과 배제)."""
    topics = [TOPICS[i % len(TOPICS)] for i in range(n)]
    before = _req("GET", f"{base}/projects?limit=100")[1].get("projects", [])
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=n) as ex:
        rows = list(ex.map(lambda t: _one_run(base, t, timeout), topics))
    level_ms = round((time.perf_counter() - t0) * 1000, 1)
    after = _req("GET", f"{base}/projects?limit=100")[1].get("projects", [])

    ok = [r for r in rows if r["http"] == 200 and r["sections_ok"]]
    lat = sorted(r["client_ms"] for r in ok)
    busy = sum(1 for r in rows for v in r["fallback_reasons"].values() if v == "혼잡")
    return {
        "concurrency": n,
        "requests": n,
        "http_200": sum(1 for r in rows if r["http"] == 200),
        "completed_with_draft": len(ok),
        "errors": [r["error"] for r in rows if r["error"]],
        "level_wall_ms": level_ms,
        "latency_p50_ms": lat[len(lat) // 2] if lat else None,
        "latency_max_ms": lat[-1] if lat else None,
        "run_status": sorted({r["run_status"] for r in rows if r["run_status"]}),
        "failed_nodes_total": sum(len(r["failed_nodes"]) for r in rows),
        "rate_limit_nodes": busy,                       # fallback 사유 '혼잡' = 429/503/과부하
        "fallback_calls_total": sum(r["fallback_calls"] or 0 for r in rows),
        "cost_total_usd": round(sum(r["est_cost_usd"] or 0 for r in rows), 4),
        "cost_per_run_usd": round(sum(r["est_cost_usd"] or 0 for r in rows) / max(len(ok), 1), 5),
        # SQLite 동시 쓰기: 요청 수만큼 이력이 늘었는가(유실·잠금 탐지)
        "history_before": len(before),
        "history_after": len(after),
        "history_gained": len(after) - len(before),
        "rows": rows,
    }


def verdicts(levels: list[dict]) -> list[str]:
    """관측치에서 판정 문장을 만든다. **수기로 쓰지 않는다** — 다시 돌리면 자동으로 갱신되어야
    하고, 손으로 쓴 해석은 다음 실행이 조용히 덮어써 데이터와 어긋난다."""
    out: list[str] = []
    base = levels[0]["latency_p50_ms"] if levels and levels[0]["latency_p50_ms"] else None
    if base and len(levels) > 1:
        ratios = [(v["concurrency"], v["latency_p50_ms"] / base)
                  for v in levels if v["latency_p50_ms"]]
        worst = max(r for _, r in ratios)
        detail = ", ".join(f"{c}건 {r:.2f}배" for c, r in ratios)
        out.append(
            f"**지연**: 동시성 1 대비 p50 최대 **{worst:.2f}배**({detail}). "
            + ("동시성이 올라가도 지연이 늘지 않는다 → 자원 포화가 아니며 병목은 LLM 응답 대기(I/O)다."
               if worst < 1.3 else
               "동시성에 따라 지연이 뚜렷이 증가한다 → 이 구간에서 자원이 포화된다."))
    busy = sum(v["rate_limit_nodes"] for v in levels)
    peak = max((v["concurrency"] for v in levels), default=0)
    out.append(f"**레이트리밋**: 사유 `혼잡`(429/503/과부하) **{busy}건**. "
               + ("동시성 %d(요청당 LLM 13~15콜이므로 순간 최대 ~%d콜)에서도 관측되지 않았다."
                  % (peak, peak * 15) if busy == 0 else "동시성이 올라가며 발생했다 → 상한 필요."))
    lost = [(v["concurrency"], v["requests"] - v["history_gained"]) for v in levels
            if v["history_gained"] != v["requests"]]
    out.append("**이력 저장(SQLite 동시 쓰기)**: "
               + ("유실 0 — `sqlite3.connect` 기본 timeout 5s·WAL 미설정 조합에서도 문제없었다."
                  if not lost else f"**유실 발생** {lost} → WAL·timeout 조정 필요."))
    incomplete = sum(v["requests"] - v["completed_with_draft"] for v in levels)
    out.append(f"**완주 보장**: 미완주 **{incomplete}건**"
               + ("— 부하에서도 모든 요청이 문서를 산출했다." if incomplete == 0 else " → 조사 필요."))
    costs = [v["cost_per_run_usd"] for v in levels if v["cost_per_run_usd"]]
    if costs:
        out.append(f"**비용**: 실행당 ${min(costs)}~${max(costs)} — "
                   + ("동시 실행이 실행당 비용을 바꾸지 않는다."
                      if max(costs) <= min(costs) * 1.5 else "동시성에 따라 실행당 비용이 변동한다."))
    return out


def _write_report(levels: list[dict], real: bool, model: str, health: dict) -> None:
    docs, out = Path("docs"), Path("outputs")
    docs.mkdir(exist_ok=True)
    out.mkdir(exist_ok=True)
    mode = "실 LLM" if real else "더미(무비용)"
    total_cost = round(sum(v["cost_total_usd"] for v in levels), 4)
    md = [
        "# 동시 실행 부하 관측 (Phase 7)\n",
        f"> 대상 `/run`(웹검색 + LLM 13~15콜 + SQLite 쓰기) · {mode} · 모델 `{model or '-'}`"
        + (f" · provider `{health['provider']}`" if health.get("provider") not in (None, "-") else "")
        + f" · 총 비용 ${total_cost}\n",
        "> 동시성마다 **서로 다른 주제**를 동시에 쏜다. 표본이 작으므로 통계가 아니라 **관측**이다.\n",
        "\n## 결과\n",
        "| 동시성 | 200/요청 | 완주(문서 생성) | p50 지연 | 최대 지연 | 구간 wall | 레이트리밋(혼잡) | LLM fallback | 실패 노드 | 이력 증가 | 실행당 비용 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for v in levels:
        md.append(
            f"| {v['concurrency']} | {v['http_200']}/{v['requests']} | {v['completed_with_draft']} "
            f"| {v['latency_p50_ms']} | {v['latency_max_ms']} | {v['level_wall_ms']} "
            f"| {v['rate_limit_nodes']} | {v['fallback_calls_total']} | {v['failed_nodes_total']} "
            f"| {v['history_gained']}/{v['requests']} | ${v['cost_per_run_usd']} |")
    md += ["\n(지연 단위 ms. '이력 증가'는 SQLite 동시 쓰기 유실 여부 — 요청 수와 같아야 한다.)\n"]

    md += ["\n## 해석\n", *[f"- {ln}" for ln in verdicts(levels)],
           "\n> ⚠️ 각 동시성 단계는 **1회 관측**이다. 그리고 '동시에 N건을 한 번 쏜 것'이지 "
           "**지속 부하(sustained)가 아니다.** 레이트리밋은 provider 계정 tier·시간대에 따라 "
           "달라지므로 이 결과가 '레이트리밋이 없다'는 보증은 아니다.\n"]

    errs = [e for v in levels for e in v["errors"]]
    md += ["\n## 오류\n", ("- 없음" if not errs else "\n".join(f"- `{e}`" for e in errs)), "\n"]
    (docs / "load_test_result.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    (out / "load_test.json").write_text(
        json.dumps({"real": real, "model": model, "provider": health.get("provider"),
                    "levels": levels}, ensure_ascii=False, indent=2),
        encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="동시 실행 부하 관측(Phase 7)")
    ap.add_argument("--levels", nargs="*", type=int, default=[1, 3, 5], help="동시성 단계")
    ap.add_argument("--real", action="store_true", help="실 LLM 사용(비용 발생). 기본은 더미")
    ap.add_argument("--port", type=int, default=8021)
    ap.add_argument("--timeout", type=float, default=420.0, help="요청 1건 타임아웃(초)")
    ap.add_argument("--report-only", action="store_true",
                    help="저장된 outputs/load_test.json 으로 리포트만 다시 생성(무비용)")
    args = ap.parse_args()

    if args.report_only:
        saved = json.loads(Path("outputs/load_test.json").read_text(encoding="utf-8"))
        _write_report(saved["levels"], saved["real"], saved["model"],
                      {"provider": saved.get("provider", "-")})
        _p("리포트 재생성: docs/load_test_result.md")
        return

    base = f"http://127.0.0.1:{args.port}"
    env = {**os.environ, "USE_DUMMY": "0" if args.real else "1",
           # 부하 관측은 실행 구조와 무관하게 비교 가능해야 하므로 고정한다.
           "WORKFLOW_MODE": "parallel",
           # 이력 DB 를 임시 경로로 격리(실제 data/projects.db 오염 방지)
           "PROJECTS_DB_PATH": str(Path(tempfile.mkdtemp()) / "load.db")}
    _p(f"{'실 LLM' if args.real else '더미'} 모드 · 동시성 {args.levels} · 포트 {args.port}")
    if args.real:
        _p("⚠ 실제 LLM·검색 호출이 발생합니다(비용).")

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
         "--port", str(args.port), "--log-level", "warning"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        health = _wait_healthy(base)
        _p(f"  서버 기동 OK — dummy={health.get('dummy_mode')} provider={health.get('provider')} "
           f"model={health.get('default_model')}")
        if args.real and health.get("dummy_mode"):
            raise SystemExit("--real 인데 서버가 더미 모드다(키 확인). 중단.")

        levels: list[dict] = []
        for n in args.levels:
            _p(f"  동시성 {n} 실행 중…")
            v = _level(base, n, args.timeout)
            levels.append(v)
            _p(f"    200 {v['http_200']}/{n} · 완주 {v['completed_with_draft']} · "
               f"p50 {v['latency_p50_ms']}ms · 최대 {v['latency_max_ms']}ms · "
               f"혼잡 {v['rate_limit_nodes']} · 이력 +{v['history_gained']}/{n} · "
               f"${v['cost_total_usd']}")
            if v["errors"]:
                _p(f"    오류: {v['errors']}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()

    _write_report(levels, args.real, health.get("default_model", ""), health)
    _p("\n저장: docs/load_test_result.md · outputs/load_test.json")

    # 지연 증가율(동시성 1 대비) — 선형이면 자원 포화, 완만하면 여유
    if len(levels) > 1 and levels[0]["latency_p50_ms"]:
        b = levels[0]["latency_p50_ms"]
        ratios = ", ".join(f"{v['concurrency']}x→{v['latency_p50_ms'] / b:.2f}배"
                           for v in levels if v["latency_p50_ms"])
        _p(f"p50 지연 증가(동시성 1 기준): {ratios}")
    lost = [v for v in levels if v["history_gained"] != v["requests"]]
    _p(f"이력 유실: {'없음' if not lost else [(v['concurrency'], v['history_gained']) for v in lost]}")
    _p(f"레이트리밋(혼잡) 합계: {sum(v['rate_limit_nodes'] for v in levels)}")
    _p(f"미완주 합계: {sum(v['requests'] - v['completed_with_draft'] for v in levels)}")


if __name__ == "__main__":
    main()
