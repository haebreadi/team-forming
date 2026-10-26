"""AI 실패 처리 시험: 가짜 오픈라우터 서버로 1차→2차→3차→앱 자체 배정을 확인함.

모델마다 제한 시간을 3초로 줄여 돌린다(앱은 120초). 실행: python3 tests/test_ai_fallback.py
"""
from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_scenarios as ts  # noqa: E402  (가짜 해시, DB 준비)
from psqldb import PsqlDB  # noqa: E402
from tf import ai, jobs, rules, service  # noqa: E402

GOOD_PLAN: dict = {}


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert self.headers["Authorization"] == "Bearer test-key"
        assert body["reasoning"] == {"effort": "low"}
        mode = body["model"]
        if mode == "slow":            # 응답이 시작되지 않음
            time.sleep(6)
            return self._json(200, {"choices": [{"message": {"content": "{}"}}]})
        if mode == "trickle":         # 바이트가 조금씩 계속 옴(읽기 제한 시간으로는 못 끊음)
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
            for _ in range(12):
                self.wfile.write(b" "); self.wfile.flush(); time.sleep(0.5)
            return
        if mode == "429":
            return self._json(429, {"error": {"message": "rate limited"}})
        if mode == "empty":
            return self._json(200, {"choices": [{"message": {"content": ""}}]})
        if mode == "bad":             # 한 학생을 빠뜨리고 팀 번호도 틀림
            items = [{"student_no": no, "team": 99} for no in list(GOOD_PLAN)[1:]]
            return self._json(200, {"choices": [{"message": {"content": json.dumps({"assignments": items})}}]})
        if mode == "good":
            items = [{"student_no": no, "team": f"팀{t}"} for no, t in GOOD_PLAN.items()]
            text = "```json\n" + json.dumps({"assignments": items}, ensure_ascii=False) + "\n```"
            return self._json(200, {"choices": [{"message": {"content": text}}]})
        self._json(500, {"error": {"message": "unknown"}})

    def _json(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)


def run_job(db, ctx, fallback, models, url, timeout=3.0):
    service.clear_plan(db)
    assert jobs.start(db, ctx, fallback, models, "test-key", timeout_sec=timeout, url=url)
    seen = set()
    t0 = time.monotonic()
    while jobs.is_running():
        s = jobs.status()
        if s.get("attempt"):
            seen.add(s["attempt"])
        time.sleep(0.2)
    return jobs.status(), time.monotonic() - t0, seen


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}/api/v1/chat/completions"

    db = ts.fresh_db()
    # 어려움 시나리오를 마감까지 진행(확정 전)
    r = ts.Runner(db, "어려움.csv", 4)
    for no, t in [("2026301", 1), ("2026302", 2), ("2026311", 3), ("2026310", 4)]:
        r.apply(no, t, "승인")
    service.close_recruiting(db)
    ctx = service.assign_context(db)
    fallback = rules.solve(ctx.teams, ctx.unassigned)
    GOOD_PLAN.update(fallback)
    job_db = PsqlDB(*ts.PSQL_ARGS)  # 작업 스레드는 따로 연결(실제 앱은 연결 풀)

    fails = 0
    cases = [
        ("1차 시간 초과 → 2차 429 → 3차 성공", ["slow", "429", "good"], ["3초 안에 응답하지 않음", "사용량 초과(HTTP 429)", "성공"], "3차 모델"),
        ("조금씩 오는 응답도 3초에 끊음 → 빈 응답 → 규칙 어긴 배정안 → 앱 자체 배정",
         ["trickle", "empty", "bad"], ["3초 안에 응답하지 않음", "빈 응답", "배정안 검사 실패"], "앱이 스스로 배정"),
        ("1차 성공", ["good", "slow", "slow"], ["성공"], "1차 모델"),
    ]
    for label, models, want, src in cases:
        st, took, seen = run_job(job_db, ctx, fallback, models, url)
        got = [h["outcome"] for h in st["history"]]
        plan = service.load_view(db, None).plan
        ok = got == want and plan and plan["source"].startswith(src) and took < 3 * 3 + 3
        if plan:
            errs = rules.validate_plan(ctx.teams, ctx.team_names, ctx.unassigned, list(plan["assignments"].items()))
            ok = ok and not errs
        fails += not ok
        print(("OK " if ok else "XX ") + f"{label}: {got} / 출처 {plan and plan['source']} / {took:.1f}초 / 진행 표시 {sorted(seen)}")
        for h in st["history"]:
            if h["outcome"] == "배정안 검사 실패":
                print("     검사 실패 내용:", h["detail"][:120])
    logs = service.load_view(db, None, with_logs=True).ai_logs
    print(f"AI 호출 기록 {len(logs)}행, 최근: {[(l['attempt'], l['outcome']) for l in logs[:3]]}")
    print(f"실패 {fails}건")
    srv.shutdown()
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
